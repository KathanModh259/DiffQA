"""Autonomous Website Explorer — tests any website like an expert human QA engineer."""

import logging
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from diffqa.agent.auth import AuthManager
from diffqa.agent.browser import BrowserController
from diffqa.agent.dom_observer import DOMObserver
from diffqa.analyzer.flow_planner import TestFlowPlan, normalize_plan
from diffqa.config import Settings, get_settings
from diffqa.llm.client import LLMClient, get_llm_client
from diffqa.pipeline import execute_plan
from diffqa.reporter.markdown import PRReportGenerator
from diffqa.reporter.dashboard import DashboardGenerator
from diffqa.judge.superhuman import SuperhumanOracle

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
            logged_in = await self.auth.handle_login(
                controller=controller,
                username=username,
                password=password,
                wait_interactive=wait_for_login,
            )

            # Sites usually redirect to a dashboard after login; come back to what we were asked to test.
            if logged_in:
                await controller.navigate(target_url)

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
            report_items = await execute_plan(plan, target_url, controller, self.llm, self.settings, out_path)

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
            console.print(Markdown(report_md))
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

        return normalize_plan(await self.llm.complete_json(messages, model=self.settings.code_model))
