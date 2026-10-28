"""Shared flow execution loop: run each planned flow, judge it, and synthesize a spec for failures."""

import logging
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from diffqa.agent.actions import ActionType
from diffqa.agent.browser import BrowserController
from diffqa.agent.runner import BrowserAgentRunner, FlowRunResult, flow_url
from diffqa.analyzer.flow_planner import TestFlowPlan
from diffqa.config import Settings
from diffqa.judge.oracle import RegressionJudge
from diffqa.llm.client import LLMClient
from diffqa.reporter.markdown import FlowReportItem
from diffqa.synthesizer.test_writer import PlaywrightTestSynthesizer

console = Console()
logger = logging.getLogger("diffqa.pipeline")


async def execute_plan(
    plan: TestFlowPlan,
    base_url: str,
    controller: BrowserController,
    llm: LLMClient,
    settings: Settings,
    output_dir: Path,
    compare_url: Optional[str] = None,
) -> List[FlowReportItem]:
    """Run, judge and report each flow. With ``compare_url`` (the app before this change), every
    failure is also tried there: if it fails there too, it is labelled pre-existing instead of
    blamed on the change."""
    runner = BrowserAgentRunner(controller=controller, llm_client=llm, settings=settings)
    judge = RegressionJudge(llm_client=llm, settings=settings)
    synthesizer = PlaywrightTestSynthesizer()
    items: List[FlowReportItem] = []

    for flow in plan.flows:
        console.print(f"\n[bold]Executing flow:[/bold] {flow.title} [dim]({flow.flow_type})[/dim]")
        try:
            result = await runner.run_flow(flow, base_url=base_url, artifacts_dir=output_dir / flow.flow_id)
        except Exception as e:
            # One crashed flow must not cost the other flows' results or the report.
            logger.exception("Flow %s crashed", flow.flow_id)
            result = FlowRunResult(flow=flow, success=False, infra_error=f"DiffQA crashed during this flow: {e}")
            await controller.recover()
        verdict = await judge.judge_flow(result)

        if not verdict.passed and verdict.verdict != "ERROR" and settings.rerun_failures:
            # A tester confirms a bug before reporting it. Re-run once and say whether it reproduced.
            console.print("  [yellow]Failure observed; re-running the flow to confirm...[/yellow]")
            try:
                second = await runner.run_flow(flow, base_url=base_url, artifacts_dir=output_dir / f"{flow.flow_id}-rerun")
                second_verdict = await judge.judge_flow(second)
                verdict.reproduced = not second_verdict.passed and second_verdict.verdict != "ERROR"
                if not verdict.reproduced:
                    verdict.confidence = min(verdict.confidence, 0.5)
            except Exception as e:
                logger.warning("Re-run of %s crashed: %s", flow.flow_id, e)
                await controller.recover()

        if not verdict.passed and verdict.verdict != "ERROR" and compare_url:
            console.print("  [yellow]Checking whether the base version has the same problem...[/yellow]")
            try:
                base = await runner.run_flow(flow, base_url=compare_url, artifacts_dir=output_dir / f"{flow.flow_id}-base")
                base_verdict = await judge.judge_flow(base)
                # A base run that couldn't test (ERROR) proves nothing either way: leave it unknown.
                if base_verdict.verdict != "ERROR":
                    verdict.pre_existing = not base_verdict.passed
            except Exception as e:
                logger.warning("Base run of %s crashed: %s", flow.flow_id, e)
                await controller.recover()

        spec_ts = None
        if verdict.passed:
            console.print("  [bold green]PASSED[/bold green]")
        elif verdict.verdict == "ERROR":
            console.print(f"  [bold yellow]NOT TESTED:[/bold yellow] {verdict.title}")
        else:
            repro = {True: " (reproduced on re-run)", False: " (did not reproduce on re-run: intermittent)"}.get(verdict.reproduced, "")
            repro += {True: " [already broken before this change]", False: " [introduced by this change]"}.get(verdict.pre_existing, "")
            console.print(f"  [bold red]FAILED:[/bold red] {verdict.title}{repro}")
            spec_path = output_dir / "specs" / f"{flow.flow_id}.spec.ts"
            spec_ts = synthesizer.synthesize_typescript(
                verdict=verdict,
                steps=result.steps,
                initial_url=flow_url(base_url, flow.route),
                flow_title=flow.title,
                invariants=flow.invariants,
                output_file=spec_path,
            )
            console.print(f"  Synthesized Playwright spec: [yellow]{spec_path}[/yellow]")

        items.append(FlowReportItem(
            flow_id=flow.flow_id,
            title=flow.title,
            route=flow.route,
            flow_type=flow.flow_type,
            passed=verdict.passed,
            verdict=verdict,
            spec_code_ts=spec_ts,
            duration_seconds=result.duration_seconds,
            steps_count=len(result.steps),
            assertions_count=sum(1 for s in result.steps if s.action.action == ActionType.ASSERT),
            invariants=flow.invariants,
        ))
    return items
