"""Tests for the human-tester behaviours: sanity checks, dialogs, assertions, re-runs, evidence in reports."""

from pathlib import Path

import pytest

from benchmarks.serve import serve_in_background
from diffqa.agent.actions import AgentAction, ExecutionStep
from diffqa.agent.browser import BrowserController
from diffqa.agent.runner import BrowserAgentRunner, FlowRunResult
from diffqa.agent.sanity import check_page_text
from diffqa.analyzer.flow_planner import TestFlowPlan, UserFlow
from diffqa.benchmark import build_testbed
from diffqa.config import Settings
from diffqa.judge.oracle import JudgeVerdict, RegressionJudge
from diffqa.pipeline import execute_plan
from diffqa.reporter.dashboard import DashboardGenerator
from diffqa.reporter.markdown import FlowReportItem, PRReportGenerator, reproduction_note

SCENARIOS = Path(__file__).resolve().parent.parent / "benchmarks" / "scenarios"


class TestSanityChecks:

    @pytest.mark.parametrize("text,label,hard", [
        ("Total: $NaN", "NaN rendered as a price or percentage", True),
        ("Hello [object Object]", "unrendered object", True),
        ("Welcome {{ user.name }}", "unrendered template placeholder", True),
        ("Subtotal $-299.00", "negative currency amount", False),
        ("Name: undefined", "'undefined' rendered", False),
        ("Internal Server Error", "error page or stack trace", False),
    ])
    def test_detects(self, text, label, hard):
        found = [f for f in check_page_text(text) if label in f.label]
        assert found and found[0].hard is hard

    def test_clean_text_has_no_findings(self):
        assert not check_page_text("Cart (2)  Subtotal $598.00  Checkout  Thank you for your order")
        assert not check_page_text("undefined-behaviour.html is a file; user.null.name; NaNoWriMo")

    def test_refund_is_only_a_soft_finding(self):
        findings = check_page_text("Refund -$20.00 applied")
        assert [f.label for f in findings] == ["negative currency amount"]
        assert findings[0].hard is False


class ScriptedLLM:
    """Plays a fixed list of agent actions, then terminates; answers the judge with a fixed verdict."""

    def __init__(self, actions, verdict="PASS"):
        self.actions = list(actions)
        self.verdict = verdict
        self.judge_prompts = []

    async def complete_json(self, messages, model=None):
        prompt = messages[-1]["content"]
        if "Evaluate whether this constitutes" in prompt:
            self.judge_prompts.append(prompt)
            return {"verdict": self.verdict, "title": "scripted"}
        if self.actions:
            return self.actions.pop(0)
        return {"action": "wait", "value": "100", "is_terminal": True}


async def _run_scripted(tmp_path, diff_name, actions, verdict="PASS"):
    site = tmp_path / "site"
    site.mkdir()
    build_testbed(SCENARIOS / diff_name if diff_name else None, site)
    settings = Settings(provider="mock", headless=True, max_steps_per_flow=10, rerun_failures=False)
    llm = ScriptedLLM(actions, verdict)
    server, url = serve_in_background(site)
    controller = BrowserController(settings=settings)
    try:
        try:
            await controller.start()
        except RuntimeError as e:
            pytest.skip(str(e))
        flow = UserFlow(flow_id="scripted", title="Scripted", goal="g", invariants=["Totals never negative"])
        runner = BrowserAgentRunner(controller=controller, llm_client=llm, settings=settings)
        result = await runner.run_flow(flow, base_url=url, artifacts_dir=tmp_path / "out")
        judge_verdict = await RegressionJudge(llm_client=llm, settings=settings).judge_flow(result)
        return result, judge_verdict, llm
    finally:
        await controller.close()
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_dialogs_are_read_and_shown_to_judge(tmp_path):
    actions = [
        {"action": "click", "target_id": "btn-add-to-cart-1"},
        {"action": "click", "target_id": "btn-checkout"},
    ]
    result, _, llm = await _run_scripted(tmp_path, None, actions)
    assert any("Order placed successfully" in d for d in result.steps[1].dialogs)
    assert "Dialog: alert: Order placed successfully" in llm.judge_prompts[0]


@pytest.mark.asyncio
async def test_negative_total_is_observed_and_reported(tmp_path):
    actions = [
        {"action": "click", "target_id": "btn-add-to-cart-1"},
        {"action": "click", "target_id": "btn-qty-decrement"},
        {"action": "click", "target_id": "btn-qty-decrement"},
    ]
    result, _, llm = await _run_scripted(tmp_path, "negative_cart_qty.diff", actions)
    assert any("negative currency" in f for f in result.soft_findings)
    assert "$-299.00" in result.steps[-1].page_text_after
    assert "Looks wrong: negative currency amount" in llm.judge_prompts[0]
    assert "Page then showed:" in llm.judge_prompts[0]


@pytest.mark.asyncio
async def test_failed_assertion_is_evidence(tmp_path):
    actions = [
        {"action": "click", "target_id": "btn-add-to-cart-1"},
        {"action": "assert", "value": "Order placed"},
    ]
    result, verdict, _ = await _run_scripted(tmp_path, None, actions, verdict="PASS")
    assert result.steps[1].success is False
    assert "Assertion failed" in result.steps[1].error_message
    assert any("Assertion failed" in e for e in verdict.evidence_logs)


@pytest.mark.asyncio
async def test_repeated_failed_click_is_skipped_without_waiting(tmp_path):
    # The checkout button starts disabled (empty cart), so clicking it times out.
    click = {"action": "click", "target_id": "btn-checkout"}
    result, _, _ = await _run_scripted(tmp_path, None, [click, click])
    assert result.steps[0].success is False
    assert result.steps[1].error_message.startswith("Skipped: identical action already failed")
    assert result.steps[1].duration_ms < 5000


@pytest.mark.asyncio
async def test_javascript_urls_are_refused(tmp_path):
    actions = [{"action": "navigate", "value": "javascript:document.body.innerHTML=''"}]
    result, _, _ = await _run_scripted(tmp_path, None, actions)
    assert "non-http(s) URL" in result.steps[0].error_message


@pytest.mark.asyncio
async def test_guessed_urls_are_refused_but_start_page_is_allowed(tmp_path):
    # The store is one page with no /products link; the demo server would just serve the homepage there.
    actions = [{"action": "navigate", "value": "/products"}, {"action": "navigate", "value": "/"}]
    result, _, _ = await _run_scripted(tmp_path, None, actions)
    assert "the app does not link there" in result.steps[0].error_message
    assert result.steps[1].success is True, result.steps[1].error_message


@pytest.mark.asyncio
async def test_assertion_matches_text_split_across_elements(tmp_path):
    # "Subtotal:" and "$0.00" are separate flex children; a person reads them as one line.
    actions = [{"action": "assert", "value": "Subtotal: $0.00"}]
    result, _, _ = await _run_scripted(tmp_path, None, actions)
    assert result.steps[0].success is True, result.steps[0].error_message


@pytest.mark.asyncio
async def test_passing_assertion_and_missing_control_visible_to_judge(tmp_path):
    actions = [
        {"action": "click", "target_id": "btn-add-to-cart-1"},
        {"action": "assert", "value": "Cart (1)"},
    ]
    result, _, llm = await _run_scripted(tmp_path, "hidden_checkout_css.diff", actions)
    assert result.steps[1].success is True
    assert "btn-checkout" not in "\n".join(result.steps[-1].elements_after)
    assert "Interactive controls present at the end:" in llm.judge_prompts[0]


def _step(success=True, action="click"):
    return ExecutionStep(step_number=1, action=AgentAction(action=action, target_id="b"), url_before="http://x",
                         url_after="http://x", success=success)


class TestRerunToConfirm:

    @pytest.mark.asyncio
    async def test_failure_is_rerun_and_marked(self, tmp_path, monkeypatch):
        runs = []

        async def fake_run_flow(self, flow, base_url, artifacts_dir=None, script=None):
            runs.append(artifacts_dir.name)
            return FlowRunResult(flow=flow, steps=[_step()], uncaught_errors=["TypeError: boom"], success=False)

        async def fake_judge(self, result):
            return JudgeVerdict(flow_id=result.flow.flow_id, passed=False, verdict="FAIL", title="boom")

        monkeypatch.setattr(BrowserAgentRunner, "run_flow", fake_run_flow)
        monkeypatch.setattr(RegressionJudge, "judge_flow", fake_judge)
        plan = TestFlowPlan(flows=[UserFlow(flow_id="f1")])
        settings = Settings(provider="mock", rerun_failures=True)
        items = await execute_plan(plan, "http://x", controller=None, llm=None, settings=settings, output_dir=tmp_path)
        assert runs == ["f1", "f1-rerun"]
        assert items[0].verdict.reproduced is True
        assert items[0].spec_code_ts

    @pytest.mark.asyncio
    async def test_intermittent_failure_lowers_confidence(self, tmp_path, monkeypatch):
        outcomes = iter([False, True])

        async def fake_run_flow(self, flow, base_url, artifacts_dir=None, script=None):
            return FlowRunResult(flow=flow, steps=[_step()])

        async def fake_judge(self, result):
            ok = next(outcomes)
            return JudgeVerdict(flow_id="f1", passed=ok, verdict="PASS" if ok else "FAIL", confidence=0.9)

        monkeypatch.setattr(BrowserAgentRunner, "run_flow", fake_run_flow)
        monkeypatch.setattr(RegressionJudge, "judge_flow", fake_judge)
        plan = TestFlowPlan(flows=[UserFlow(flow_id="f1")])
        items = await execute_plan(plan, "http://x", None, None, Settings(provider="mock"), tmp_path)
        assert items[0].verdict.reproduced is False
        assert items[0].verdict.confidence <= 0.5
        assert "intermittent" in reproduction_note(items[0].verdict)


class TestBaseComparison:

    async def _run(self, tmp_path, monkeypatch, base_fails):
        urls = []

        async def fake_run_flow(self, flow, base_url, artifacts_dir=None, script=None):
            urls.append(base_url)
            return FlowRunResult(flow=flow, steps=[_step()])

        async def fake_judge(self, result):
            broken = urls[-1] == "http://head" or base_fails
            return JudgeVerdict(flow_id="f1", passed=not broken, verdict="FAIL" if broken else "PASS", title="Cart resets")

        monkeypatch.setattr(BrowserAgentRunner, "run_flow", fake_run_flow)
        monkeypatch.setattr(RegressionJudge, "judge_flow", fake_judge)
        plan = TestFlowPlan(flows=[UserFlow(flow_id="f1")])
        items = await execute_plan(plan, "http://head", None, None, Settings(provider="mock", rerun_failures=False),
                                   tmp_path, compare_url="http://base")
        assert urls == ["http://head", "http://base"]
        return items[0], PRReportGenerator().generate_report(plan, items)

    @pytest.mark.asyncio
    async def test_failure_also_on_base_is_pre_existing_and_does_not_block(self, tmp_path, monkeypatch):
        item, md = await self._run(tmp_path, monkeypatch, base_fails=True)
        assert item.verdict.pre_existing is True and not item.blocks_merge
        assert "Already broken: Cart resets" in md
        assert "All planned flows passed with no regressions" in md
        assert "Issues that already existed before this change" in md

    @pytest.mark.asyncio
    async def test_failure_only_on_head_is_blamed_on_the_change(self, tmp_path, monkeypatch):
        item, md = await self._run(tmp_path, monkeypatch, base_fails=False)
        assert item.verdict.pre_existing is False and item.blocks_merge
        assert "introduced by this change" in md
        assert "1%20FAILED" in md


class TestEvidenceInReports:

    def _item(self, tmp_path):
        shot = tmp_path / "step_3.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        verdict = JudgeVerdict(
            flow_id="f1", passed=False, verdict="FAIL", title="Total is wrong", reproduced=True,
            evidence_logs=["Rendering defect at Step 2: NaN rendered"], repro_steps=["1. Add item", "2. Look at total"],
            screenshot_evidence=str(shot),
        )
        return FlowReportItem(flow_id="f1", title="Cart", route="/", flow_type="happy_path", passed=False,
                              verdict=verdict, steps_count=4, assertions_count=1, invariants=["Total == sum"])

    def test_markdown_has_reproduction_evidence_and_coverage(self, tmp_path):
        md = PRReportGenerator().generate_report(TestFlowPlan(), [self._item(tmp_path)])
        assert "Reproduced on re-run (2/2)" in md
        assert "Rendering defect at Step 2" in md
        assert "step_3.png" in md
        assert "| `f1` | 4 | 1 | Total == sum |" in md
        assert "Not covered by this run" in md

    def test_dashboard_embeds_screenshot_and_repro_steps(self, tmp_path):
        html = DashboardGenerator().generate_html(TestFlowPlan(), [self._item(tmp_path)])
        assert 'src="data:image/png;base64,iVBOR' in html
        assert "2. Look at total" in html
        assert "Reproduced on re-run" in html
        assert "Coverage: what this run checked" in html


@pytest.mark.asyncio
async def test_base_replay_that_cannot_repeat_the_steps_stays_blocking(tmp_path, monkeypatch):
    # e.g. the PR renamed a button: the replay on the base version can't click it, which proves nothing.
    async def fake_run_flow(self, flow, base_url, artifacts_dir=None, script=None):
        return FlowRunResult(flow=flow, steps=[_step(success=base_url != "http://base")])

    async def fake_judge(self, result):
        return JudgeVerdict(flow_id="f1", passed=False, verdict="FAIL", title="broken")

    monkeypatch.setattr(BrowserAgentRunner, "run_flow", fake_run_flow)
    monkeypatch.setattr(RegressionJudge, "judge_flow", fake_judge)
    items = await execute_plan(TestFlowPlan(flows=[UserFlow(flow_id="f1")]), "http://head", None, None,
                               Settings(provider="mock", rerun_failures=False), tmp_path, compare_url="http://base")
    assert items[0].verdict.pre_existing is None
    assert items[0].blocks_merge


@pytest.mark.asyncio
async def test_flow_stops_after_two_failed_assertions(tmp_path):
    actions = [{"action": "assert", "value": "nope 1"}, {"action": "assert", "value": "nope 2"},
               {"action": "click", "target_id": "btn-add-to-cart-1"}]
    result, _, _ = await _run_scripted(tmp_path, None, actions)
    assert len(result.steps) == 2


@pytest.mark.asyncio
async def test_replay_repeats_steps_without_agent_calls(tmp_path):
    actions = [{"action": "click", "target_id": "btn-add-to-cart-1"}, {"action": "assert", "value": "Cart (1)"}]
    first, _, _ = await _run_scripted(tmp_path, None, actions)

    class NoAgent(ScriptedLLM):
        async def complete_json(self, messages, model=None):
            assert "Evaluate whether this constitutes" in messages[-1]["content"], "replay asked the agent model"
            return await super().complete_json(messages, model)

    site = tmp_path / "site"
    settings = Settings(provider="mock", headless=True)
    server, url = serve_in_background(site)
    controller = BrowserController(settings=settings)
    try:
        await controller.start()
        runner = BrowserAgentRunner(controller=controller, llm_client=NoAgent([]), settings=settings)
        again = await runner.run_flow(first.flow, url, tmp_path / "replay", script=[s.action for s in first.steps])
    finally:
        await controller.close()
        server.shutdown()
        server.server_close()
    assert [s.success for s in again.steps] == [s.success for s in first.steps]
    assert all(s.success for s in again.steps)
