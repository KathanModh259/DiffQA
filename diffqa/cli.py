"""Command Line Interface for DiffQA using Typer and Rich."""

import asyncio
import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure UTF-8 console encoding on Windows to prevent charmap UnicodeEncodeErrors
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from diffqa.agent.browser import BrowserController
from diffqa.agent.runner import BrowserAgentRunner
from diffqa.analyzer.diff_parser import DiffParser
from diffqa.analyzer.flow_planner import FlowPlanner
from diffqa.config import Settings
from diffqa.judge.oracle import RegressionJudge
from diffqa.llm.client import LLMClient
from diffqa.reporter.markdown import FlowReportItem, PRReportGenerator
from diffqa.synthesizer.test_writer import PlaywrightTestSynthesizer
from diffqa.benchmark import BenchmarkRunner
from benchmarks.serve import run_server
from diffqa.agent.explorer import SiteExplorer

app = typer.Typer(
    name="diffqa",
    help="Autonomous PR Regression Testing Agent powered by Meta Models & Playwright",
    add_completion=False,
)
console = Console()


@app.command()
def scan(
    diff: Path = typer.Option(..., "--diff", "-d", help="Path to unified git diff or patch file"),
    url: str = typer.Option("http://localhost:3000", "--url", "-u", help="Target preview URL"),
    provider: str = typer.Option("meta", "--provider", "-p", help="Model provider (meta, fireworks, baseten, modal, mock)"),
):
    """Scan a git diff and preview the planned test flows without running browser."""
    settings = Settings(provider=provider)  # type: ignore
    llm = LLMClient(settings=settings)
    parser = DiffParser()

    console.print(Panel.fit(f"[bold cyan]DiffQA Diff Scanner[/bold cyan]\nDiff: [yellow]{diff}[/yellow] | Preview: [green]{url}[/green]"))

    parsed = parser.parse_file(diff)
    console.print(f"[bold]Detected {len(parsed.files)} modified files (+{parsed.total_added}/-{parsed.total_removed}):[/bold]")
    for f in parsed.files:
        console.print(f"  • {f.path} [dim](UI: {f.is_ui_component})[/dim]")

    planner = FlowPlanner(llm_client=llm)
    with console.status("[bold green]Synthesizing test flows via Meta Muse Code...[/bold green]"):
        plan = asyncio.run(planner.plan_flows(parsed, preview_url=url))

    console.print(f"\n[bold green]Diff Summary:[/bold green] {plan.summary}")
    console.print(f"[bold red]Risk Assessment:[/bold red] {plan.risk_assessment}\n")

    table = Table(title="Planned Test Flows")
    table.add_column("Flow ID", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Route", style="yellow")
    table.add_column("Goal", style="white")

    for f in plan.flows:
        table.add_row(f.flow_id, f.flow_type, f.route, f.goal)
    console.print(table)


@app.command()
def run(
    diff: Path = typer.Option(..., "--diff", "-d", help="Path to unified git diff or patch file"),
    url: str = typer.Option("http://localhost:3000", "--url", "-u", help="Target preview URL"),
    output: Path = typer.Option(Path("./diffqa_output"), "--output", "-o", help="Output directory for reports and specs"),
    provider: str = typer.Option("meta", "--provider", "-p", help="Model provider (meta, fireworks, baseten, modal, ollama, mock)"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless mode"),
):
    """Execute DiffQA: parse diff, run autonomous browser flows, judge regressions, and synthesize tests."""
    asyncio.run(_run_pipeline(diff, url, output, provider, headless))


async def _run_pipeline(diff_path: Path, url: str, output_dir: Path, provider: str, headless: bool):
    settings = Settings(
        provider=provider,  # type: ignore
        headless=headless,
        output_dir=output_dir,
    )
    llm = LLMClient(settings=settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(
        f"[bold cyan]DiffQA Autonomous Regression Testing[/bold cyan]\n"
        f"Diff: [yellow]{diff_path}[/yellow] | Preview URL: [green]{url}[/green] | Provider: [magenta]{provider}[/magenta]"
    ))

    # 1. Parse Diff
    parser = DiffParser()
    parsed_diff = parser.parse_file(diff_path)
    console.print(f"✅ Parsed diff: {len(parsed_diff.files)} files modified.")

    # 2. Plan Flows
    planner = FlowPlanner(llm_client=llm)
    with console.status("[bold green]Planning user flows and invariants with Meta Muse Code...[/bold green]"):
        plan = await planner.plan_flows(parsed_diff, preview_url=url)
    console.print(f"📋 Generated [bold]{len(plan.flows)}[/bold] test flows.")

    # 3. Autonomous Browser Execution
    controller = BrowserController(settings=settings)
    await controller.start()

    runner = BrowserAgentRunner(controller=controller, llm_client=llm, settings=settings)
    judge = RegressionJudge(llm_client=llm, settings=settings)
    synthesizer = PlaywrightTestSynthesizer()

    report_items = []

    try:
        for flow in plan.flows:
            console.print(f"\n🚀 [bold]Executing Flow:[/bold] {flow.title} [dim]({flow.flow_type})[/dim]")
            run_result = await runner.run_flow(flow, base_url=url)

            # Judge regression
            verdict = await judge.judge_flow(run_result)

            spec_ts = None
            spec_py = None
            if not verdict.passed:
                console.print(f"  ❌ [bold red]REGRESSION DETECTED:[/bold red] {verdict.title}")
                # Synthesize Playwright test
                spec_ts_path = output_dir / "specs" / f"{flow.flow_id}.spec.ts"
                spec_ts = synthesizer.synthesize_typescript(
                    verdict=verdict,
                    steps=run_result.steps,
                    initial_url=f"{url.rstrip('/')}/{flow.route.lstrip('/')}",
                    flow_title=flow.title,
                    invariants=flow.invariants,
                    output_file=spec_ts_path,
                )
                console.print(f"  🧪 Synthesized Playwright spec: [yellow]{spec_ts_path}[/yellow]")
            else:
                console.print(f"  ✅ [bold green]PASSED[/bold green]")

            report_items.append(FlowReportItem(
                flow_id=flow.flow_id,
                title=flow.title,
                route=flow.route,
                flow_type=flow.flow_type,
                passed=verdict.passed,
                verdict=verdict,
                spec_code_ts=spec_ts,
                spec_code_py=spec_py,
                duration_seconds=run_result.duration_seconds,
            ))
    finally:
        await controller.close()

    # 4. Generate PR Markdown Report
    reporter = PRReportGenerator()
    report_md = reporter.generate_report(plan, report_items, preview_url=url)
    report_path = output_dir / "DIFFQA_REPORT.md"
    reporter.save_report(report_md, report_path)

    console.print(f"\n📄 Saved PR Report: [bold cyan]{report_path}[/bold cyan]")
    console.print("\n" + "=" * 60 + "\n")
    console.print(report_md)


@app.command()
def eval(
    benchmark: Path = typer.Option(Path("./benchmarks/scenarios"), "--benchmark", "-b", help="Path to directory containing seeded .diff scenarios"),
    url: str = typer.Option("http://localhost:3000", "--url", "-u", help="Testbed app preview URL"),
    provider: str = typer.Option("mock", "--provider", "-p", help="Model provider (meta, fireworks, baseten, modal, mock)"),
):
    """Run quantitative benchmark evaluation on seeded regression PR diffs."""
    console.print(Panel.fit(
        f"[bold cyan]DiffQA Benchmark Evaluation Harness[/bold cyan]\n"
        f"Scenarios: [yellow]{benchmark}[/yellow] | Provider: [magenta]{provider}[/magenta]"
    ))

    runner = BenchmarkRunner(scenarios_dir=benchmark, testbed_url=url, provider=provider)
    with console.status("[bold green]Evaluating benchmark scenarios...[/bold green]"):
        results = asyncio.run(runner.run_benchmark())

    runner.print_scorecard(results)


@app.command()
def serve(
    port: int = typer.Option(3000, "--port", "-p", help="Port to run the testbed web app on"),
):
    """Launch the local QuickShop testbed web application."""
    console.print(Panel.fit(
        f"[bold cyan]Launching DiffQA Testbed Web App[/bold cyan]\n"
        f"URL: [green]http://localhost:{port}[/green]"
    ))
    run_server(port=port)


@app.command()
def demo(
    port: int = typer.Option(3000, "--port", "-p", help="Port to run demo testbed on"),
    provider: str = typer.Option("mock", "--provider", help="Model provider to use (default: mock for instant offline demo)"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless or headed mode"),
):
    """Run an automated end-to-end demo of DiffQA against a seeded buggy PR."""
    import threading
    import socketserver
    from benchmarks.serve import Handler, DIRECTORY

    console.print(Panel.fit(
        "[bold cyan]DiffQA Live End-to-End Demo[/bold cyan]\n"
        "[dim]Spinning up local testbed server and running autonomous browser testing against buggy PR diff...[/dim]"
    ))

    # 1. Start background testbed server
    server = socketserver.TCPServer(("", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    console.print(f"⚡ Local testbed server active at [green]http://localhost:{port}[/green]")

    try:
        # 2. Run DiffQA pipeline against seeded regression diff
        diff_path = Path("benchmarks/scenarios/negative_cart_qty.diff")
        out_dir = Path("diffqa_output")

        asyncio.run(_run_pipeline(
            diff_path=diff_path,
            url=f"http://localhost:{port}",
            output_dir=out_dir,
            provider=provider,
            headless=headless,
        ))
    finally:
        server.shutdown()
        server.server_close()
        console.print("[dim]Local testbed server stopped.[/dim]")


@app.command()
def explore(
    url: str = typer.Argument(..., help="Target website URL to test (e.g. https://example.com)"),
    output: Path = typer.Option(Path("./diffqa_output"), "--output", "-o", help="Directory for reports and specs"),
    provider: str = typer.Option("meta", "--provider", "-p", help="Model provider (meta, fireworks, baseten, modal, mock)"),
    headless: bool = typer.Option(False, "--headless/--headed", help="Run browser in headless or headed mode (headed recommended for login)"),
    wait_for_login: bool = typer.Option(True, "--login/--no-login", help="Detect login page and wait for user authentication if needed"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Username or email for automated login"),
    password: Optional[str] = typer.Option(None, "--password", help="Password for automated login"),
    goal: Optional[str] = typer.Option(None, "--goal", "-g", help="Specific feature or scenario to test (e.g. 'Test checkout flow')"),
    auth_file: Path = typer.Option(Path(".diffqa_auth.json"), "--auth-file", help="Path to cached session cookies state"),
):
    """Autonomously explore and test ANY website like an expert QA engineer."""
    settings = Settings(
        provider=provider,  # type: ignore
        headless=headless,
        output_dir=output,
    )
    llm = LLMClient(settings=settings)
    explorer = SiteExplorer(
        settings=settings,
        llm_client=llm,
        auth_state_path=auth_file,
    )
    asyncio.run(explorer.explore(
        target_url=url,
        output_dir=output,
        username=user,
        password=password,
        wait_for_login=wait_for_login,
        custom_goal=goal,
    ))


if __name__ == "__main__":
    app()
