"""Tests for interactive HTML DashboardGenerator."""

from diffqa.analyzer.flow_planner import TestFlowPlan, UserFlow
from diffqa.judge.oracle import BugSeverity, JudgeVerdict
from diffqa.reporter.markdown import FlowReportItem
from diffqa.reporter.dashboard import DashboardGenerator


class TestDashboardGenerator:

    def test_generate_html_dashboard(self, tmp_path):
        gen = DashboardGenerator()
        plan = TestFlowPlan(
            summary="Test suite plan",
            affected_routes=["/cart"],
            risk_assessment="High",
            flows=[UserFlow(flow_id="f1", title="Flow 1", route="/", goal="Test")],
        )
        items = [
            FlowReportItem(
                flow_id="f1",
                title="Flow 1",
                route="/",
                flow_type="happy_path",
                passed=False,
                verdict=JudgeVerdict(
                    flow_id="f1",
                    passed=False,
                    verdict="FAIL",
                    title="Cart quantity bug",
                    severity=BugSeverity.CRITICAL,
                    root_cause_hypothesis="Missing bounds check",
                ),
                spec_code_ts="test('repro', async () => {});",
                duration_seconds=1.5,
            )
        ]

        html = gen.generate_html(plan, items, preview_url="http://localhost:3000")
        assert "<!DOCTYPE html>" in html
        assert "DiffQA" in html
        assert "Cart quantity bug" in html
        assert "test(&#39;repro&#39;" in html
        assert "REGRESSIONS DETECTED" in html

        # Test file saving
        save_file = tmp_path / "dashboard.html"
        gen.save_dashboard(html, save_file)
        assert save_file.exists()
        assert len(save_file.read_text(encoding="utf-8")) > 500


    def test_untrusted_text_is_escaped(self):
        plan = TestFlowPlan(summary="s", risk_assessment="r")
        verdict = JudgeVerdict(flow_id="f", passed=False, title="<script>alert(1)</script>")
        item = FlowReportItem(flow_id="f", title="t", route="/", flow_type="happy_path", passed=False, verdict=verdict)
        html = DashboardGenerator().generate_html(plan, [item])
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
