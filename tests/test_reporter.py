"""Tests for PRReportGenerator — markdown output validation."""

import pytest

from diffqa.analyzer.flow_planner import TestFlowPlan, UserFlow
from diffqa.judge.oracle import BugSeverity, JudgeVerdict
from diffqa.reporter.markdown import FlowReportItem, PRReportGenerator


@pytest.fixture
def reporter():
    return PRReportGenerator()


@pytest.fixture
def sample_plan():
    return TestFlowPlan(
        summary="Cart modifications",
        affected_routes=["/cart"],
        risk_assessment="HIGH",
        flows=[
            UserFlow(flow_id="f1", title="Happy path", route="/cart", goal="Test cart"),
            UserFlow(flow_id="f2", title="Edge case", route="/cart", flow_type="adversarial", goal="Test boundary"),
        ],
    )


class TestPRReportGenerator:

    def test_all_pass_report(self, reporter, sample_plan):
        items = [
            FlowReportItem(
                flow_id="f1", title="Happy path", route="/cart", flow_type="happy_path",
                passed=True, verdict=JudgeVerdict(flow_id="f1", passed=True, verdict="PASS"),
            ),
            FlowReportItem(
                flow_id="f2", title="Edge case", route="/cart", flow_type="adversarial",
                passed=True, verdict=JudgeVerdict(flow_id="f2", passed=True, verdict="PASS"),
            ),
        ]
        md = reporter.generate_report(sample_plan, items, preview_url="http://localhost:3000")
        assert "PASSED" in md
        assert "no regressions" in md.lower()
        assert "| :---:" in md  # table formatting

    def test_failure_report(self, reporter, sample_plan):
        items = [
            FlowReportItem(
                flow_id="f1", title="Happy path", route="/cart", flow_type="happy_path",
                passed=True, verdict=JudgeVerdict(flow_id="f1", passed=True, verdict="PASS"),
            ),
            FlowReportItem(
                flow_id="f2", title="Edge case", route="/cart", flow_type="adversarial",
                passed=False,
                verdict=JudgeVerdict(
                    flow_id="f2", passed=False, verdict="FAIL",
                    title="Cart bug", severity=BugSeverity.CRITICAL,
                    root_cause_hypothesis="Missing guard",
                    repro_steps=["1. Click decrement", "2. Observe negative"],
                    evidence_logs=["Error: qty < 0"],
                ),
                spec_code_ts="test('repro', async () => {})",
            ),
        ]
        md = reporter.generate_report(sample_plan, items, preview_url="http://localhost:3000")
        assert "FAILED" in md
        assert "Cart bug" in md
        assert "Missing guard" in md
        assert "Click decrement" in md
        assert "Auto-Generated Playwright Test" in md

    def test_commit_sha_included(self, reporter, sample_plan):
        items = [
            FlowReportItem(
                flow_id="f1", title="Test", route="/", flow_type="happy_path",
                passed=True, verdict=JudgeVerdict(flow_id="f1", passed=True),
            ),
        ]
        md = reporter.generate_report(sample_plan, items, commit_sha="abc1234def5678")
        assert "abc1234" in md

    def test_save_report(self, reporter, sample_plan, tmp_path):
        md = "# Test Report"
        path = reporter.save_report(md, tmp_path / "report.md")
        assert path.exists()
        assert path.read_text() == "# Test Report"

