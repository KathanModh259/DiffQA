"""Command Line Interface for DiffQA using Typer and Rich."""

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from benchmarks.serve import run_server, serve_in_background
from diffqa.agent.browser import BrowserController
from diffqa.agent.explorer import SiteExplorer
from diffqa.analyzer.diff_parser import DiffParser
from diffqa.analyzer.flow_planner import FlowPlanner, TestFlowPlan
from diffqa.benchmark import BenchmarkRunner, build_testbed
from diffqa.config import Settings
from diffqa.judge.superhuman import SuperhumanOracle
from diffqa.llm.client import LLMClient
from diffqa.pipeline import execute_plan
from diffqa.reporter.dashboard import DashboardGenerator
from diffqa.reporter.markdown import PRReportGenerator

# Windows consoles default to a legacy codepage that can't encode the report's emoji.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "scenarios"
PROVIDER_HELP = "Model provider (meta, fireworks, baseten, modal, ollama, mock)"

app = typer.Typer(
    name="diffqa",
    help="Autonomous PR Regression Testing Agent powered by Meta Models & Playwright",
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console()


@app.command()
def scan(
    diff: Path = typer.Option(..., "--diff", "-d", help="Path to unified git diff or patch file"),
    url: str = typer.Option("http://localhost:3000", "--url", "-u", help="Target preview URL"),
    provider: str = typer.Option("meta", "--provider", "-p", help=PROVIDER_HELP),
):
    """Scan a git diff and preview the planned test flows without running browser."""
    llm = LLMClient(settings=Settings(provider=provider))  # type: ignore[arg-type]

    console.print(Panel.fit(f"[bold cyan]DiffQA Diff Scanner[/bold cyan]\nDiff: [yellow]{diff}[/yellow] | Preview: [green]{url}[/green]"))

    parsed = DiffParser().parse_file(diff)
    console.print(f"[bold]Detected {len(parsed.files)} modified files (+{parsed.total_added}/-{parsed.total_removed}):[/bold]")
    for f in parsed.files:
        console.print(f"  • {f.path} [dim](UI: {f.is_ui_component})[/dim]")

    with console.status("[bold green]Planning test flows...[/bold green]"):
        plan = asyncio.run(FlowPlanner(llm_client=llm).plan_flows(parsed, preview_url=url))

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
    provider: str = typer.Option("meta", "--provider", "-p", help=PROVIDER_HELP),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless mode"),
):
    """Execute DiffQA: parse diff, run autonomous browser flows, judge regressions, and synthesize tests.

    Exits with status 1 when any flow fails, so it can gate CI.
    """
    failed = asyncio.run(_run_pipeline(diff, url, output, provider, headless))
    raise typer.Exit(code=1 if failed else 0)


async def _run_pipeline(diff_path: Path, url: str, output_dir: Path, provider: str, headless: bool) -> int:
    """Run the full pipeline and return the number of failed flows."""
    settings = Settings(provider=provider, headless=headless, output_dir=output_dir)  # type: ignore[arg-type]
    llm = LLMClient(settings=settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(
        f"[bold cyan]DiffQA Autonomous Regression Testing[/bold cyan]\n"
        f"Diff: [yellow]{diff_path}[/yellow] | Preview URL: [green]{url}[/green] | Provider: [magenta]{provider}[/magenta]"
    ))

    parsed_diff = DiffParser().parse_file(diff_path)
    console.print(f"Parsed diff: {len(parsed_diff.files)} files modified.")
    if not parsed_diff.files:
        console.print("[yellow]The diff contains no file changes; running a smoke test of the preview.[/yellow]")

    controller = BrowserController(settings=settings)
    await controller.start()
    try:
        # Fail fast on an unreachable preview before spending model credits on planning.
        try:
            await controller.navigate(url)
        except Exception as e:
            console.print(f"[bold red]Cannot reach preview URL {url}:[/bold red] {str(e).splitlines()[0]}")
            raise typer.Exit(code=2)

        with console.status("[bold green]Planning user flows and invariants...[/bold green]"):
            plan = await FlowPlanner(llm_client=llm).plan_flows(parsed_diff, preview_url=url)
        console.print(f"Generated [bold]{len(plan.flows)}[/bold] test flows.")

        report_items = await execute_plan(plan, url, controller, llm, settings, output_dir)
    finally:
        await controller.close()

    reporter = PRReportGenerator()
    report_md = reporter.generate_report(plan, report_items, preview_url=url)
    report_path = reporter.save_report(report_md, output_dir / "DIFFQA_REPORT.md")

    dashboard = DashboardGenerator()
    dash_path = dashboard.save_dashboard(
        dashboard.generate_html(plan, report_items, preview_url=url), output_dir / "DIFFQA_DASHBOARD.html"
    )

    console.print(f"\nSaved PR Report: [bold cyan]{report_path}[/bold cyan]")
    console.print(f"Saved Interactive Dashboard: [bold yellow]{dash_path}[/bold yellow]")
    console.rule()
    console.print(Markdown(report_md))
    return sum(1 for i in report_items if not i.passed)


@app.command()
def eval(
    benchmark: Path = typer.Option(SCENARIOS_DIR, "--benchmark", "-b", help="Directory of seeded .diff scenarios"),
    provider: str = typer.Option("mock", "--provider", "-p", help=PROVIDER_HELP),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless mode"),
):
    """Apply each seeded bug diff to the testbed, run the full pipeline, and score detection."""
    console.print(Panel.fit(
        f"[bold cyan]DiffQA Benchmark Evaluation Harness[/bold cyan]\n"
        f"Scenarios: [yellow]{benchmark}[/yellow] | Provider: [magenta]{provider}[/magenta]"
    ))
    runner = BenchmarkRunner(scenarios_dir=benchmark, provider=provider, headless=headless)
    runner.print_scorecard(asyncio.run(runner.run_benchmark()))


@app.command()
def serve(
    port: int = typer.Option(3000, "--port", "-p", help="Port to run the testbed web app on"),
):
    """Launch the local QuickShop testbed web application."""
    run_server(port=port)


@app.command()
def demo(
    scenario: str = typer.Option("broken_promo_uncaught_error", "--scenario", "-s", help="Seeded bug diff to inject (file stem in benchmarks/scenarios)"),
    provider: str = typer.Option("mock", "--provider", help="Model provider (default: mock, fully offline)"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless or headed mode"),
    output: Path = typer.Option(Path("./diffqa_output"), "--output", "-o", help="Output directory for reports and specs"),
):
    """End-to-end demo: serve the testbed with a seeded bug applied, then run DiffQA against it."""
    diff_path = SCENARIOS_DIR / f"{scenario}.diff"
    if not diff_path.exists():
        raise typer.BadParameter(f"No scenario '{scenario}' in {SCENARIOS_DIR}")

    with tempfile.TemporaryDirectory() as site_dir:
        build_testbed(diff_path, Path(site_dir))
        server, url = serve_in_background(Path(site_dir))
        console.print(Panel.fit(
            f"[bold cyan]DiffQA Live Demo[/bold cyan]\n"
            f"Testbed with [yellow]{scenario}[/yellow] applied, serving at [green]{url}[/green]"
        ))
        try:
            asyncio.run(_run_pipeline(diff_path, url, output, provider, headless))
        finally:
            server.shutdown()
            server.server_close()


@app.command()
def explore(
    url: str = typer.Argument(..., help="Target website URL to test (e.g. https://example.com)"),
    output: Path = typer.Option(Path("./diffqa_output"), "--output", "-o", help="Directory for reports and specs"),
    provider: str = typer.Option("meta", "--provider", "-p", help=PROVIDER_HELP),
    headless: bool = typer.Option(False, "--headless/--headed", help="Run browser in headless or headed mode (headed recommended for login)"),
    wait_for_login: bool = typer.Option(True, "--login/--no-login", help="Detect login page and wait for user authentication if needed"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Username or email for automated login"),
    password: Optional[str] = typer.Option(None, "--password", help="Password for automated login"),
    goal: Optional[str] = typer.Option(None, "--goal", "-g", help="Specific feature or scenario to test (e.g. 'Test checkout flow')"),
    auth_file: Path = typer.Option(Path(".diffqa_auth.json"), "--auth-file", help="Path to cached session cookies state"),
):
    """Autonomously explore and test ANY website like an expert QA engineer."""
    settings = Settings(provider=provider, headless=headless, output_dir=output)  # type: ignore[arg-type]
    explorer = SiteExplorer(settings=settings, llm_client=LLMClient(settings=settings), auth_state_path=auth_file)
    asyncio.run(explorer.explore(
        target_url=url,
        output_dir=output,
        username=user,
        password=password,
        wait_for_login=wait_for_login,
        custom_goal=goal,
    ))


@app.command()
def audit(
    url: str = typer.Argument(..., help="Website URL to audit (e.g. https://example.com)"),
    output: Path = typer.Option(Path("./diffqa_output"), "--output", "-o", help="Directory for reports and dashboard"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser in headless or headed mode"),
):
    """Run an instant page audit (layout overflow, a11y, broken assets, network/console errors)."""
    asyncio.run(_run_audit(url, output, headless))


async def _run_audit(url: str, output_dir: Path, headless: bool):
    settings = Settings(headless=headless, output_dir=output_dir)
    controller = BrowserController(settings=settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(f"[bold cyan]DiffQA Page Audit[/bold cyan]\nAuditing: [green]{url}[/green]"))

    await controller.start()
    try:
        await controller.navigate(url)
        report = await SuperhumanOracle().audit_page(
            controller.page,
            network_errors=controller.network_errors,
            console_errors=controller.page_errors
            + [log["text"] for log in controller.console_logs if log["type"] == "error"],
        )

        table = Table(title=f"Audit Scorecard ({report.quality_score}/100)")
        table.add_column("Severity", style="bold")
        table.add_column("Category", style="cyan")
        table.add_column("Message", style="white")
        table.add_column("Selector", style="yellow")
        for issue in report.issues:
            sev_color = "red" if issue.severity in ("CRITICAL", "HIGH") else "yellow"
            table.add_row(f"[{sev_color}]{issue.severity}[/{sev_color}]", issue.category, issue.message[:80], issue.selector or "-")
        console.print(table)
        console.print(f"\n[bold]Quality Score:[/bold] [bold magenta]{report.quality_score}/100[/bold magenta] | Total Issues: [bold]{report.total_issues}[/bold]")

        dashboard = DashboardGenerator()
        plan = TestFlowPlan(summary=f"Page audit of {url}", affected_routes=["/"], risk_assessment="Audit", flows=[])
        dash_file = dashboard.save_dashboard(
            dashboard.generate_html(plan, [], preview_url=url, audit=report), output_dir / "DIFFQA_AUDIT_DASHBOARD.html"
        )
        console.print(f"Interactive Audit Dashboard: [bold yellow]{dash_file}[/bold yellow]")
    finally:
        await controller.close()


def main() -> None:
    """Console entry point: expected setup problems print one actionable line, not a traceback."""
    try:
        app()
    except (ValueError, RuntimeError, FileNotFoundError, httpx.HTTPError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
