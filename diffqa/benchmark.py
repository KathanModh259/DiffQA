"""Benchmark harness: apply each seeded bug diff to the testbed, serve it, and run the real pipeline.

A clean (unpatched) baseline run is included so false positives show up in the scorecard.
"""

import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table

from benchmarks.serve import DIRECTORY as TESTBED_DIR, serve_in_background
from diffqa.agent.browser import BrowserController
from diffqa.analyzer.diff_parser import DiffParser, ParsedDiff
from diffqa.analyzer.flow_planner import FlowPlanner
from diffqa.config import Settings
from diffqa.llm.client import LLMClient
from diffqa.pipeline import execute_plan
from diffqa.reporter.markdown import FlowReportItem

console = Console()

BASELINE = "baseline_clean"


@dataclass
class ScenarioResult:
    scenario_name: str
    expected_bug: bool
    detected_bug: bool
    bug_title: Optional[str]
    severity: Optional[str]
    synthesized_spec: bool
    latency_seconds: float


def apply_testbed_diff(diff_text: str, html: str) -> str:
    """Apply a seeded diff to the testbed HTML by exact text match.

    Matching on hunk content instead of line numbers keeps hand-written scenario diffs
    valid even when their @@ headers drift from the file.
    """
    html = html.replace("\r\n", "\n")
    for hunk in re.split(r"^@@[^\n]*@@[^\n]*\n", diff_text.replace("\r\n", "\n"), flags=re.MULTILINE)[1:]:
        old: List[str] = []
        new: List[str] = []
        for line in hunk.rstrip("\n").split("\n"):
            if line.startswith("diff --git"):
                break
            tag, body = (line[:1], line[1:]) if line else (" ", "")
            if tag in " -":
                old.append(body)
            if tag in " +":
                new.append(body)
        old_block, new_block = "\n".join(old), "\n".join(new)
        if old_block not in html:
            raise ValueError(f"Hunk does not match testbed source:\n{old_block[:200]}")
        html = html.replace(old_block, new_block, 1)
    return html


def build_testbed(diff_path: Optional[Path], dest: Path) -> None:
    html = (TESTBED_DIR / "index.html").read_text(encoding="utf-8")
    if diff_path:
        html = apply_testbed_diff(diff_path.read_text(encoding="utf-8"), html)
    (dest / "index.html").write_text(html, encoding="utf-8")


class BenchmarkRunner:
    """Runs DiffQA end-to-end against a patched testbed for every seeded regression diff."""

    def __init__(self, scenarios_dir: Path | str, provider: str = "mock", headless: bool = True):
        self.scenarios_dir = Path(scenarios_dir)
        self.settings = Settings(provider=provider, headless=headless)  # type: ignore[arg-type]
        self.llm = LLMClient(settings=self.settings)

    async def run_benchmark(self) -> List[ScenarioResult]:
        diff_files = sorted(self.scenarios_dir.glob("*.diff"))
        if not diff_files:
            raise FileNotFoundError(f"No .diff files found in {self.scenarios_dir}")

        results = [await self._run_scenario(BASELINE, None)]
        for diff_path in diff_files:
            results.append(await self._run_scenario(diff_path.stem, diff_path))
        return results

    async def _run_scenario(self, name: str, diff_path: Optional[Path]) -> ScenarioResult:
        console.rule(f"[bold cyan]{name}")
        start = time.time()
        parsed = DiffParser().parse_file(diff_path) if diff_path else ParsedDiff()

        with tempfile.TemporaryDirectory() as tmp:
            site_dir, out_dir = Path(tmp) / "site", Path(tmp) / "out"
            site_dir.mkdir()
            build_testbed(diff_path, site_dir)
            server, url = serve_in_background(site_dir)
            servers = [server]
            compare_url = None
            if diff_path:
                # The unpatched store plays "production": failures it shares are not this diff's fault.
                base_dir = Path(tmp) / "base"
                base_dir.mkdir()
                build_testbed(None, base_dir)
                base_server, compare_url = serve_in_background(base_dir)
                servers.append(base_server)
            controller = BrowserController(settings=self.settings)
            try:
                plan = await FlowPlanner(llm_client=self.llm).plan_flows(parsed, preview_url=url)
                await controller.start()
                items = await execute_plan(plan, url, controller, self.llm, self.settings, out_dir, compare_url=compare_url)
            finally:
                await controller.close()
                for s in servers:
                    s.shutdown()
                    s.server_close()

        failed: List[FlowReportItem] = [i for i in items if i.blocks_merge]
        first = failed[0].verdict if failed else None
        return ScenarioResult(
            scenario_name=name,
            expected_bug=diff_path is not None,
            detected_bug=bool(failed),
            bug_title=first.title if first else None,
            severity=first.severity.value if first and first.severity else None,
            synthesized_spec=any(i.spec_code_ts for i in failed),
            latency_seconds=time.time() - start,
        )

    def print_scorecard(self, results: List[ScenarioResult]) -> None:
        table = Table(title="DiffQA Seeded Regression Benchmark")
        table.add_column("Scenario", style="cyan")
        table.add_column("Expected", justify="center")
        table.add_column("Detected", justify="center")
        table.add_column("Finding")
        table.add_column("Severity", style="magenta")
        table.add_column("Spec", justify="center")
        table.add_column("Time", justify="right")

        for r in results:
            correct = r.detected_bug == r.expected_bug
            table.add_row(
                r.scenario_name,
                "bug" if r.expected_bug else "clean",
                f"[{'green' if correct else 'red'}]{'YES' if r.detected_bug else 'NO'}[/]",
                (r.bug_title or "-")[:60],
                r.severity or "-",
                "yes" if r.synthesized_spec else "-",
                f"{r.latency_seconds:.1f}s",
            )
        console.print(table)

        seeded = [r for r in results if r.expected_bug]
        clean = [r for r in results if not r.expected_bug]
        caught = sum(r.detected_bug for r in seeded)
        false_pos = sum(r.detected_bug for r in clean)
        console.print(f"Recall: [bold]{caught}/{len(seeded)}[/bold] seeded regressions caught")
        console.print(f"False positives: [bold]{false_pos}/{len(clean)}[/bold] clean runs flagged")
        console.print(f"Provider: [magenta]{self.settings.provider}[/magenta]")
        if self.settings.provider == "mock":
            console.print("[dim]The mock provider only detects hard runtime/network errors; "
                          "invariant bugs need a real model.[/dim]")
