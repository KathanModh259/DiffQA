"""Tests for FlowPlanner — verifies LLM-driven test flow generation."""

import pytest

from diffqa.analyzer.diff_parser import DiffParser
from diffqa.analyzer.flow_planner import FlowPlanner, TestFlowPlan
from diffqa.config import Settings
from diffqa.llm.client import LLMClient


@pytest.fixture
def mock_llm():
    return LLMClient(settings=Settings(provider="mock"))


@pytest.fixture
def parser():
    return DiffParser()


CART_DIFF = """\
diff --git a/src/Cart.tsx b/src/Cart.tsx
--- a/src/Cart.tsx
+++ b/src/Cart.tsx
@@ -10,3 +10,3 @@
- const qty = Math.max(0, count);
+ const qty = count;
"""


class TestFlowPlanner:

    @pytest.mark.asyncio
    async def test_generates_flows(self, parser, mock_llm):
        parsed = parser.parse_string(CART_DIFF)
        planner = FlowPlanner(llm_client=mock_llm)
        plan = await planner.plan_flows(parsed, preview_url="http://localhost:3000")

        assert isinstance(plan, TestFlowPlan)
        assert len(plan.flows) >= 1
        assert plan.summary != ""
        assert plan.risk_assessment != ""

    @pytest.mark.asyncio
    async def test_prompt_contains_diff_and_truncates(self, parser):
        class RecordingLLM:
            async def complete_json(self, messages, model=None):
                self.prompt = messages[-1]["content"]
                return {"summary": "s", "risk_assessment": "r", "flows": []}

        llm = RecordingLLM()
        huge = CART_DIFF + "+" + "x" * (FlowPlanner.MAX_DIFF_CHARS * 2) + "\n"
        await FlowPlanner(llm_client=llm).plan_flows(parser.parse_string(huge))
        assert "src/Cart.tsx" in llm.prompt
        assert "(truncated)" in llm.prompt

    @pytest.mark.asyncio
    async def test_flows_have_invariants(self, parser, mock_llm):
        parsed = parser.parse_string(CART_DIFF)
        planner = FlowPlanner(llm_client=mock_llm)
        plan = await planner.plan_flows(parsed)

        for flow in plan.flows:
            assert len(flow.invariants) >= 1, f"Flow '{flow.flow_id}' has no invariants"

    @pytest.mark.asyncio
    async def test_affected_routes_populated(self, parser, mock_llm):
        parsed = parser.parse_string(CART_DIFF)
        planner = FlowPlanner(llm_client=mock_llm)
        plan = await planner.plan_flows(parsed)

        assert len(plan.affected_routes) >= 1

    @pytest.mark.asyncio
    async def test_flows_have_steps(self, parser, mock_llm):
        parsed = parser.parse_string(CART_DIFF)
        planner = FlowPlanner(llm_client=mock_llm)
        plan = await planner.plan_flows(parsed)

        for flow in plan.flows:
            assert len(flow.steps) >= 1, f"Flow '{flow.flow_id}' has no steps"

    @pytest.mark.asyncio
    async def test_flow_ids_are_unique(self, parser, mock_llm):
        parsed = parser.parse_string(CART_DIFF)
        planner = FlowPlanner(llm_client=mock_llm)
        plan = await planner.plan_flows(parsed)

        ids = [f.flow_id for f in plan.flows]
        assert len(ids) == len(set(ids)), "Flow IDs must be unique"
