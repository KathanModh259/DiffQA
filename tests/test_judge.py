"""Tests for RegressionJudge — functional, heuristic, and LLM-assisted verdicts."""

import pytest

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.agent.runner import FlowRunResult
from diffqa.analyzer.flow_planner import UserFlow
from diffqa.config import Settings
from diffqa.judge.oracle import BugSeverity, BugType, JudgeVerdict, RegressionJudge
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
) -> FlowRunResult:
    return FlowRunResult(
        flow=flow or _make_flow(),
        steps=[],
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
    async def test_adversarial_negative_flow_triggers_invariant_check(self, mock_judge):
        flow = _make_flow(flow_id="flow-negative-qty", flow_type="adversarial")
        result = _make_result(flow=flow, success=False)
        verdict = await mock_judge.judge_flow(result)
        assert verdict.passed is False

    @pytest.mark.asyncio
    async def test_verdict_has_repro_steps(self, mock_judge):
        result = _make_result(
            success=False,
            uncaught_errors=["ReferenceError: x is not defined"],
        )
        verdict = await mock_judge.judge_flow(result)
        assert len(verdict.repro_steps) >= 1

    @pytest.mark.asyncio
    async def test_verdict_has_evidence(self, mock_judge):
        result = _make_result(
            success=False,
            uncaught_errors=["Error: something broke"],
        )
        verdict = await mock_judge.judge_flow(result)
        assert len(verdict.evidence_logs) >= 1

