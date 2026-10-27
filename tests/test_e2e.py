"""End-to-end: real Chromium against the served testbed, offline mock model.

Skipped when Playwright's Chromium isn't installed (`playwright install chromium`).
"""

from pathlib import Path

import pytest

from benchmarks.serve import serve_in_background
from diffqa.agent.browser import BrowserController
from diffqa.analyzer.diff_parser import ParsedDiff
from diffqa.analyzer.flow_planner import FlowPlanner
from diffqa.benchmark import TESTBED_DIR, apply_testbed_diff, build_testbed
from diffqa.config import Settings
from diffqa.llm.client import LLMClient
from diffqa.pipeline import execute_plan

SCENARIOS = Path(__file__).resolve().parent.parent / "benchmarks" / "scenarios"


@pytest.mark.parametrize("diff", sorted(SCENARIOS.glob("*.diff")), ids=lambda p: p.stem)
def test_every_seeded_scenario_applies_and_changes_the_testbed(diff):
    html = (TESTBED_DIR / "index.html").read_text(encoding="utf-8")
    assert apply_testbed_diff(diff.read_text(encoding="utf-8"), html) != html.replace("\r\n", "\n")


async def _run(tmp_path: Path, diff_path):
    site = tmp_path / "site"
    site.mkdir()
    build_testbed(diff_path, site)
    settings = Settings(provider="mock", headless=True, max_steps_per_flow=12)
    llm = LLMClient(settings=settings)
    server, url = serve_in_background(site)
    controller = BrowserController(settings=settings)
    try:
        try:
            await controller.start()
        except RuntimeError as e:
            pytest.skip(str(e))
        plan = await FlowPlanner(llm_client=llm).plan_flows(ParsedDiff(), preview_url=url)
        return await execute_plan(plan, url, controller, llm, settings, tmp_path / "out")
    finally:
        await controller.close()
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_clean_testbed_passes(tmp_path):
    items = await _run(tmp_path, None)
    assert items and all(i.passed for i in items), [i.verdict.title for i in items]


@pytest.mark.asyncio
async def test_runtime_error_regression_is_caught_with_spec(tmp_path):
    items = await _run(tmp_path, SCENARIOS / "broken_promo_uncaught_error.diff")
    failed = [i for i in items if not i.passed]
    assert failed
    assert any("toUpperCase" in log for log in failed[0].verdict.evidence_logs)
    assert "#btn-apply-promo" in failed[0].spec_code_ts
    assert (tmp_path / "out" / "specs" / f"{failed[0].flow_id}.spec.ts").exists()
