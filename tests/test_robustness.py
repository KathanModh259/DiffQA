"""Tests for tolerance of messy model output and flaky infrastructure."""

import httpx
import pytest

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.agent.runner import FlowRunResult, flow_url
from diffqa.analyzer.flow_planner import MAX_FLOWS, UserFlow, normalize_plan
from diffqa.config import Settings
from diffqa.judge.oracle import BugSeverity, BugType, JudgeVerdict, RegressionJudge
from diffqa.llm import client as client_mod
from diffqa.llm.client import LLMClient


class TestPlanNormalization:

    def test_flow_id_cannot_escape_output_dir(self):
        assert UserFlow(flow_id="../../etc/Passwd").flow_id == "etc-passwd"

    def test_unknown_flow_type_is_coerced(self):
        assert UserFlow(flow_type="Edge Case").flow_type == "adversarial"
        assert UserFlow(flow_type="smoke").flow_type == "happy_path"

    def test_duplicate_ids_are_made_unique(self):
        plan = normalize_plan({"flows": [{"flow_id": "a"}, {"flow_id": "a"}, {"flow_id": "a"}]})
        assert [f.flow_id for f in plan.flows] == ["a", "a-2", "a-3"]

    def test_empty_plan_gets_smoke_flow(self):
        assert [f.flow_id for f in normalize_plan({}).flows] == ["flow-smoke"]

    def test_plan_is_capped(self):
        plan = normalize_plan({"flows": [{"flow_id": f"f{i}"} for i in range(20)]})
        assert len(plan.flows) == MAX_FLOWS

    def test_scalar_lists_are_wrapped(self):
        assert UserFlow(invariants="No errors", steps=None).invariants == ["No errors"]


class TestModelOutputCoercion:

    def test_verdict_fields(self):
        v = JudgeVerdict(flow_id="f", passed=False, severity="high", bug_type="Runtime Crash", confidence="95%")
        assert v.severity == BugSeverity.HIGH
        assert v.bug_type == BugType.RUNTIME_CRASH
        assert v.confidence == 0.95

    def test_enum_values_pass_through_unchanged(self):
        v = JudgeVerdict(flow_id="f", passed=False, severity=BugSeverity.CRITICAL, bug_type=BugType.NETWORK_ERROR)
        assert v.severity == BugSeverity.CRITICAL and v.bug_type == BugType.NETWORK_ERROR
        assert AgentAction(action=ActionType.SCROLL).action == ActionType.SCROLL

    def test_unknown_verdict_values_fall_back(self):
        v = JudgeVerdict(flow_id="f", passed=False, severity="catastrophic", bug_type="weird", confidence="lots")
        assert v.severity == BugSeverity.HIGH
        assert v.bug_type == BugType.FUNCTIONAL_REGRESSION
        assert v.confidence == 0.5

    def test_action_synonyms_and_numbers(self):
        a = AgentAction(action="Type", target_id=12, value=300, thought=None)
        assert a.action == ActionType.FILL
        assert a.target_id == "12" and a.value == "300" and a.thought == ""


class TestFlowUrl:

    def test_relative_and_absolute_routes(self):
        assert flow_url("http://h:3/", "/cart") == "http://h:3/cart"
        assert flow_url("http://h:3", "http://h:3/cart") == "http://h:3/cart"
        assert flow_url("http://h:3", "https://evil.example/x") == "http://h:3"


def _result(network=None, steps=True, **kw) -> FlowRunResult:
    step = ExecutionStep(
        step_number=1, action=AgentAction(action="click", target_id="b"), url_before="http://app.test/"
    )
    return FlowRunResult(
        flow=UserFlow(flow_id="f", title="F"), steps=[step] if steps else [], network_failures=network or [], **kw
    )


class TestHardFailurePolicy:

    @pytest.mark.parametrize("entry,expected", [
        ({"url": "http://app.test/api/cart", "status": 404, "resource_type": "fetch"}, True),
        ({"url": "http://app.test/api/me", "status": 401, "resource_type": "xhr"}, False),
        ({"url": "http://app.test/favicon.ico", "status": 404, "resource_type": "image"}, False),
        ({"url": "http://cdn.other/api", "status": 404, "resource_type": "fetch"}, False),
        ({"url": "http://app.test/api", "error": "net::ERR_ABORTED"}, False),
        ({"url": "http://cdn.other/x", "status": 503, "resource_type": "fetch"}, True),
    ])
    def test_network_signals(self, entry, expected):
        assert RegressionJudge._hard_failure(_result([entry])) is expected

    @pytest.mark.asyncio
    async def test_infra_error_is_not_tested_not_a_bug(self):
        judge = RegressionJudge(llm_client=LLMClient(Settings(provider="mock")), settings=Settings(provider="mock"))
        verdict = await judge.judge_flow(_result(steps=False, infra_error="Could not load http://x"))
        assert verdict.passed is False
        assert verdict.verdict == "ERROR"
        assert verdict.bug_type is None


class TestHttpResilience:

    @pytest.fixture(autouse=True)
    def no_sleep(self, monkeypatch):
        async def _instant(_):
            return None
        monkeypatch.setattr(client_mod.asyncio, "sleep", _instant)

    def _use_transport(self, monkeypatch, handler):
        real = httpx.AsyncClient
        monkeypatch.setattr(client_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    @staticmethod
    def _ok(text):
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    @pytest.mark.asyncio
    async def test_retries_rate_limit_then_succeeds(self, monkeypatch):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429) if len(calls) < 3 else self._ok('{"ok": true}')

        self._use_transport(monkeypatch, handler)
        llm = LLMClient(Settings(provider="meta", meta_api_key="k"))
        assert await llm.complete_json([{"role": "user", "content": "x"}]) == {"ok": True}
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_drops_response_format_when_rejected(self, monkeypatch):
        bodies = []

        def handler(request):
            bodies.append(request.content)
            return httpx.Response(400, text="bad response_format") if b"response_format" in request.content \
                else self._ok('{"ok": 1}')

        self._use_transport(monkeypatch, handler)
        llm = LLMClient(Settings(provider="meta", meta_api_key="k"))
        assert await llm.complete_json([{"role": "user", "content": "x"}]) == {"ok": 1}
        assert len(bodies) == 2

    @pytest.mark.asyncio
    async def test_repairs_non_json_reply_once(self, monkeypatch):
        replies = iter(["Sure! Here you go.", '{"fixed": true}'])
        self._use_transport(monkeypatch, lambda request: self._ok(next(replies)))
        llm = LLMClient(Settings(provider="meta", meta_api_key="k"))
        assert await llm.complete_json([{"role": "user", "content": "x"}]) == {"fixed": True}

    @pytest.mark.asyncio
    async def test_auth_error_is_not_retried_and_shows_provider_message(self, monkeypatch):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(401, text='{"error": "invalid api key"}')

        self._use_transport(monkeypatch, handler)
        llm = LLMClient(Settings(provider="meta", meta_api_key="k"))
        with pytest.raises(httpx.HTTPStatusError, match="invalid api key"):
            await llm.complete([{"role": "user", "content": "x"}])
        assert len(calls) == 1
