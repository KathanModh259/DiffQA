"""Autonomous Website Explorer — tests any website like an expert human QA engineer."""

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.agent.auth import AuthManager
from diffqa.agent.browser import BrowserController
from diffqa.agent.dom_observer import DOMObserver
from diffqa.agent.runner import BrowserAgentRunner, FlowRunResult
from diffqa.analyzer.flow_planner import TestFlowPlan, UserFlow
from diffqa.config import Settings, get_settings
from diffqa.judge.oracle import BugSeverity, JudgeVerdict, RegressionJudge
from diffqa.llm.client import LLMClient, get_llm_client
from diffqa.reporter.markdown import FlowReportItem, PRReportGenerator
from diffqa.reporter.dashboard import DashboardGenerator
from diffqa.judge.superhuman import SuperhumanOracle
from diffqa.synthesizer.test_writer import PlaywrightTestSynthesizer

logger = logging.getLogger("diffqa.explorer")
console = Console()

EXPLORATION_SYNTHESIS_PROMPT = """You are DiffQA Autonomous Site Explorer, powered by Meta's Muse Code and Muse Spark.
You are given an arbitrary live web application to test like an expert QA engineer.

Current URL: {url}
Page Title: {title}
Visible Interactive Elements:
{elements_summary}

Your goal:
Synthesize 2 to 4 high-value exploratory user journeys to thoroughly test this application:
1. Primary Happy Path (navigation, search, main features).
2. Input Validation / Form Edge Cases (submitting empty forms, boundary inputs, special characters).
3. Stateful Operations (creating, updating, deleting items, modals, drawers).
4. Adversarial / Boundary Tests (rapid clicks, back button, unexpected state transitions).

Respond strictly in valid JSON matching this schema:
{{
  "summary": "Overview of the website and key features to test",
  "affected_routes": ["/"],
  "risk_assessment": "Summary of likely fragile areas or bug risks",
  "flows": [
    {{
      "flow_id": "flow-slug",
      "title": "Clear description of the journey",
      "route": "/",
      "flow_type": "happy_path" | "adversarial" | "visual_regression",
      "goal": "What user journey or edge case this validates",
      "steps": ["Step 1", "Step 2"],
      "invariants": ["No uncaught exceptions", "Expected element visible"]
    }}
  ]
}}
"""


class SiteExplorer:
    """Explores, tests, and finds bugs on any arbitrary website."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm_client: Optional[LLMClient] = None,
        auth_state_path: Optional[Path | str] = None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm_client or get_llm_client(self.settings)
        self.auth = AuthManager(auth_state_path=auth_state_path)
        self.dom = DOMObserver()
        self.synthesizer = PlaywrightTestSynthesizer()
        self.reporter = PRReportGenerator()
        self.dashboard = DashboardGenerator()
        self.superhuman = SuperhumanOracle()

    async def explore(
        self,
        target_url: str,
        output_dir: Optional[Path | str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        wait_for_login: bool = True,
        max_steps: int = 25,
        custom_goal: Optional[str] = None,
    ) -> Path:
        """Launch browser, handle authentication if needed, autonomously test the site, and generate a QA report."""
        out_path = Path(output_dir or self.settings.output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        console.print(Panel.fit(
            f"[bold cyan]DiffQA Autonomous Web Explorer[/bold cyan]\n"
            f"Target URL: [green]{target_url}[/green] | Provider: [magenta]{self.settings.provider}[/magenta]"
        ))

        # 1. Initialize browser with cached auth if available
        controller = BrowserController(settings=self.settings)
        cached_state = self.auth.auth_state_path if self.auth.has_cached_session() else None
        if cached_state:
            console.print(f"[Auth] Using cached login session from [yellow]{cached_state}[/yellow]")

        await controller.start(storage_state_path=cached_state)

        try:
            console.print(f"-> Navigating to [cyan]{target_url}[/cyan]...")
            await controller.navigate(target_url)

            # 2. Check for login requirements & handle authentication
            login_handled = await self.auth.handle_login(
                controller=controller,
                username=username,
                password=password,
                wait_interactive=wait_for_login,
            )

            # Re-land on target URL after login
            if login_handled:
                await controller.page.wait_for_load_state("domcontentloaded")
                await controller.page.wait_for_timeout(1000)

            # 3. Observe landing page interactive DOM
            elements = await self.dom.extract_interactive_elements(controller.page)
            elem_summary = self.dom.format_elements_for_prompt(elements[:35])
            page_title = await controller.page.title()

            console.print(f"Observed [bold]{len(elements)}[/bold] interactive elements on page: [italic]{page_title}[/italic]")

            # 4. Formulate test flows with Meta Model API
            with console.status("[bold green]Synthesizing autonomous test journeys via Meta Muse Code...[/bold green]"):
                plan = await self._plan_site_flows(target_url, page_title, elem_summary, custom_goal)

            console.print(f"\n[Plan] [bold]Autonomous QA Mission:[/bold] {plan.summary}")
            table = Table(title="Planned Exploratory Flows")
            table.add_column("Flow ID", style="cyan")
            table.add_column("Type", style="magenta")
            table.add_column("Goal", style="white")
            for f in plan.flows:
                table.add_row(f.flow_id, f.flow_type, f.goal)
            console.print(table)

            # 5. Execute flows autonomously
            runner = BrowserAgentRunner(controller=controller, llm_client=self.llm, settings=self.settings)
            judge = RegressionJudge(llm_client=self.llm, settings=self.settings)
            report_items: List[FlowReportItem] = []

            for flow in plan.flows:
                console.print(f"\n[Run] [bold]Executing Flow:[/bold] {flow.title} [dim]({flow.flow_type})[/dim]")
                run_result = await runner.run_flow(flow, base_url=target_url, artifacts_dir=out_path / flow.flow_id)

                verdict = await judge.judge_flow(run_result)

                spec_ts = None
                spec_py = None
                if not verdict.passed:
                    console.print(f"  [bold red]BUG DETECTED:[/bold red] {verdict.title}")
                    spec_path = out_path / "specs" / f"{flow.flow_id}.spec.ts"
                    spec_ts = self.synthesizer.synthesize_typescript(
                        verdict=verdict,
                        steps=run_result.steps,
                        initial_url=target_url,
                        flow_title=flow.title,
                        invariants=flow.invariants,
                        output_file=spec_path,
                    )
                    console.print(f"  [Spec] Synthesized Playwright test: [yellow]{spec_path}[/yellow]")
                else:
                    console.print("  [PASS] [bold green]PASSED[/bold green] (No errors or invariant violations)")

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

            # 6. Run Superhuman Invariant Audit
            audit_report = await self.superhuman.audit_page(
                controller.page,
                network_errors=controller.network_errors,
                console_errors=[log["text"] for log in controller.console_logs if log["type"] == "error"],
            )
            report_md = self.reporter.generate_report(plan, report_items, preview_url=target_url)
            report_file = out_path / "DIFFQA_EXPLORER_REPORT.md"
            self.reporter.save_report(report_md, report_file)

            dashboard_html = self.dashboard.generate_html(plan, report_items, preview_url=target_url, audit=audit_report)
            dashboard_file = out_path / "DIFFQA_DASHBOARD.html"
            self.dashboard.save_dashboard(dashboard_html, dashboard_file)
            console.print(f"  Interactive Dashboard: [bold yellow]{dashboard_file}[/bold yellow]")

            console.print(f"\n[Report] Saved Autonomous QA Report: [bold cyan]{report_file}[/bold cyan]")
            console.print("\n" + "=" * 60 + "\n")
            console.print(report_md)
            return report_file

        finally:
            await controller.close()

    async def _plan_site_flows(
        self,
        url: str,
        title: str,
        elements_summary: str,
        custom_goal: Optional[str] = None,
    ) -> TestFlowPlan:
        """Prompt Meta Muse Code to synthesize site test flows."""
        prompt = EXPLORATION_SYNTHESIS_PROMPT.format(
            url=url,
            title=title,
            elements_summary=elements_summary,
        )
        if custom_goal:
            prompt += f"\n\nSpecial User Directive / Test Goal: {custom_goal}"

        messages = [
            {"role": "system", "content": "You are DiffQA, an elite autonomous web QA agent."},
            {"role": "user", "content": prompt},
        ]

        data = await self.llm.complete_json(messages, model=self.settings.code_model)
        return TestFlowPlan(**data)
