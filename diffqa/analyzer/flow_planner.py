"""LLM-driven test flow and edge case planner for PR diffs."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from diffqa.analyzer.diff_parser import ParsedDiff
from diffqa.llm.client import LLMClient, get_llm_client


class UserFlow(BaseModel):
    flow_id: str
    title: str
    route: str = "/"
    flow_type: Literal["happy_path", "adversarial", "visual_regression"] = "happy_path"
    goal: str
    steps: List[str] = Field(default_factory=list)
    invariants: List[str] = Field(default_factory=list)


class TestFlowPlan(BaseModel):
    summary: str
    affected_routes: List[str] = Field(default_factory=list)
    risk_assessment: str
    flows: List[UserFlow] = Field(default_factory=list)


SYSTEM_PROMPT = """You are DiffQA Flow Planner, an expert senior QA engineer and web tester powered by Meta's Muse Code.
Your goal is to inspect a Git PR code diff and map it into targeted end-to-end browser user flows to test on a live preview URL.

You must design:
1. Happy path flows: Normal intended user interactions touching the modified code.
2. Adversarial & Edge Case flows: Boundary tests, rapid interactions, invalid/empty inputs, state resets, refresh, back navigation.
3. Invariants: Clear observable invariants that must hold true (e.g., 'Subtotal equals sum of items', 'No uncaught JS error', 'Modal closes on escape').

You must reply strictly in valid JSON matching this schema:
{
  "summary": "Brief summary of what the PR changed and what needs testing",
  "affected_routes": ["/route1", "/route2"],
  "risk_assessment": "High/Medium/Low: Why regressions might slip through",
  "flows": [
    {
      "flow_id": "flow-unique-slug",
      "title": "Clear description of the test scenario",
      "route": "/initial-route",
      "flow_type": "happy_path" | "adversarial" | "visual_regression",
      "goal": "What user behavior this tests",
      "steps": [
        "Step 1: Navigate to ...",
        "Step 2: Click on ...",
        "Step 3: Enter text into ..."
      ],
      "invariants": [
        "Assertion 1: Element with text ... must be visible",
        "Assertion 2: No 500 network responses in network log"
      ]
    }
  ]
}
"""


class FlowPlanner:
    """Plans targeted and adversarial browser test flows based on PR diffs."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or get_llm_client()

    async def plan_flows(self, parsed_diff: ParsedDiff, preview_url: str = "http://localhost:3000") -> TestFlowPlan:
        """Analyze the parsed diff and generate structured test flows."""
        # Truncate raw diff if excessively large to protect context limit
        raw_diff_sample = parsed_diff.raw_diff[:12000] if len(parsed_diff.raw_diff) > 12000 else parsed_diff.raw_diff

        files_summary = "\n".join(
            f"- {f.path} (+{f.added_count}/-{f.removed_count}) [UI: {f.is_ui_component}, Routes: {f.detected_routes}]"
            for f in parsed_diff.files
        )

        user_prompt = f"""Target Preview URL: {preview_url}
Modified Files:
{files_summary}

Suspected Routes:
{parsed_diff.suspected_routes}

Git Unified Diff:
```diff
{raw_diff_sample}
```

Analyze the code modifications above. Map out 2 to 4 high-priority test flows (both happy-path and adversarial edge cases) to thoroughly verify these changes on the web application."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        data = await self.llm.complete_json(messages)
        return TestFlowPlan(**data)

