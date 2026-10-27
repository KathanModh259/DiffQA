import pytest
from diffqa.analyzer.diff_parser import DiffParser
from diffqa.analyzer.flow_planner import FlowPlanner
from diffqa.llm.client import LLMClient
from diffqa.config import Settings


@pytest.mark.asyncio
async def test_flow_planner_generates_flows():
    diff_text = """diff --git a/src/Cart.tsx b/src/Cart.tsx
--- a/src/Cart.tsx
+++ b/src/Cart.tsx
@@ -10,3 +10,3 @@
- const qty = Math.max(0, count);
+ const qty = count;
"""
    parser = DiffParser()
    parsed = parser.parse_string(diff_text)

    llm = LLMClient(settings=Settings(provider="mock"))
    planner = FlowPlanner(llm_client=llm)

    plan = await planner.plan_flows(parsed, preview_url="http://localhost:3000")

    assert len(plan.flows) >= 1
    assert any(f.flow_type == "adversarial" for f in plan.flows)
    assert any("/cart" in f.route for f in plan.flows)
    assert len(plan.flows[0].invariants) >= 1

