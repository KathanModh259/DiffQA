"""Tests for RegressionJudge — functional, heuristic, and LLM-assisted verdicts."""

import pytest

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.agent.runner import FlowRunResult
from diffqa.analyzer.flow_planner import UserFlow
from diffqa.config import Settings
from diffqa.judge.oracle import RegressionJudge
from diffqa.llm.client import LLMClient


@pytest.fixture
def mock_judge():
    return RegressionJudge(
        llm_client=LLMClient(settings=Settings(provider="mock")),
    )


def _make_flow(flow_id: str = "test-flow", flow_type: str = "happy_path") -> UserFlow:
    return UserFlow(
        flow_id=flow_id,
        title=f"Test flow {flow_id}",
        route="/",
        flow_type=flow_type,
        goal="Test goal",
        invariants=["No errors"],
    )


def _make_result(
    flow: UserFlow | None = None,
    success: bool = True,
    uncaught_errors: list | None = None,
    network_failures: list | None = None,
    terminal: bool = True,
    steps: list | None = None,
) -> FlowRunResult:
    return FlowRunResult(
        flow=flow or _make_flow(),
        steps=steps or [],
        success=success,
        terminal_reached=terminal,
        duration_seconds=1.0,
        all_screenshots=[],
        uncaught_errors=uncaught_errors or [],
        network_failures=network_failures or [],
    )


class TestRegressionJudge:

    @pytest.mark.asyncio
    async def test_clean_run_passes(self, mock_judge):
        result = _make_result(success=True)
        verdict = await mock_judge.judge_flow(result)
        assert verdict.passed is True
        assert verdict.verdict == "PASS"

    @pytest.mark.asyncio
    async def test_uncaught_errors_trigger_fail(self, mock_judge):
        result = _make_result(
            success=False,
            uncaught_errors=["TypeError: Cannot read property 'x' of undefined"],
        )
        verdict = await mock_judge.judge_flow(result)
        assert verdict.passed is False

    @pytest.mark.asyncio
    async def test_network_500_triggers_fail(self, mock_judge):
        result = _make_result(
            success=False,
            network_failures=[{"url": "/api/orders", "status": 500, "method": "POST"}],
        )
        verdict = await mock_judge.judge_flow(result)
        assert verdict.passed is False

    @pytest.mark.asyncio
    async def test_verdict_has_repro_steps(self, mock_judge):
        step = ExecutionStep(
            step_number=1,
            action=AgentAction(thought="t", action=ActionType.CLICK, target_id="btn-buy"),
            url_before="http://x",
        )
        result = _make_result(
            success=False,
            uncaught_errors=["ReferenceError: x is not defined"],
            steps=[step],
        )
        verdict = await mock_judge.judge_flow(result)
        assert verdict.repro_steps == ["1. click on btn-buy"]

    @pytest.mark.asyncio
    async def test_verdict_has_evidence(self, mock_judge):
        result = _make_result(
            success=False,
            uncaught_errors=["Error: something broke"],
        )
        verdict = await mock_judge.judge_flow(result)
        assert len(verdict.evidence_logs) >= 1



class _StubLLM:
    """Records the judge prompt and answers with a fixed verdict."""

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.prompt = ""

    async def complete_json(self, messages, model=None):
        self.prompt = messages[-1]["content"]
        return {"verdict": self.verdict}


class TestJudgePolicy:

    @pytest.mark.asyncio
    async def test_flow_id_alone_never_fails_a_clean_run(self, mock_judge):
        flow = _make_flow(flow_id="flow-negative-qty", flow_type="adversarial")
        verdict = await mock_judge.judge_flow(_make_result(flow=flow))
        assert verdict.passed is True

    @pytest.mark.asyncio
    async def test_judge_sees_final_page_text(self):
        llm = _StubLLM("PASS")
        result = _make_result()
        result.final_page_text = "Subtotal: $-299.00"
        await RegressionJudge(llm_client=llm, settings=Settings(provider="mock")).judge_flow(result)
        assert "Subtotal: $-299.00" in llm.prompt

    @pytest.mark.asyncio
    async def test_uncaught_error_overrides_model_pass(self):
        judge = RegressionJudge(llm_client=_StubLLM("PASS"), settings=Settings(provider="mock"))
        verdict = await judge.judge_flow(_make_result(success=False, uncaught_errors=["TypeError: boom"]))
        assert verdict.passed is False
