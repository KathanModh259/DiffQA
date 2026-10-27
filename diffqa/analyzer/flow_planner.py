"""LLM-driven test flow and edge-case planner for PR diffs.

Given a ``ParsedDiff``, the planner prompts Meta's Muse Code (or any
configured model) to synthesize:
  1. Happy-path user journeys touching the modified code.
  2. Adversarial / boundary-condition flows designed to trigger regressions.
  3. Observable invariants that must hold after each flow.
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

from diffqa.analyzer.diff_parser import ParsedDiff
from diffqa.llm.client import LLMClient, get_llm_client


# ── Data Models ─────────────────────────────────────────────────────────────


FlowType = Literal["happy_path", "adversarial", "visual_regression"]


class UserFlow(BaseModel):
    """A single test scenario to execute in the browser."""

    flow_id: str = "flow"
    title: str = "Untitled flow"
    route: str = "/"
    flow_type: FlowType = "happy_path"
    goal: str = ""
    steps: List[str] = Field(default_factory=list)
    invariants: List[str] = Field(default_factory=list)

    @field_validator("flow_id", mode="before")
    @classmethod
    def _safe_flow_id(cls, v: object) -> str:
        # flow_id becomes a directory and file name, so keep it to a safe slug.
        slug = re.sub(r"[^a-z0-9_-]+", "-", str(v or "flow").lower()).strip("-")
        return slug[:60] or "flow"

    @field_validator("flow_type", mode="before")
    @classmethod
    def _known_flow_type(cls, v: object) -> str:
        v = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        return v if v in get_args(FlowType) else "adversarial" if "advers" in v or "edge" in v else "happy_path"

    @field_validator("route", mode="before")
    @classmethod
    def _route(cls, v: object) -> str:
        return str(v or "/").strip() or "/"

    @field_validator("steps", "invariants", mode="before")
    @classmethod
    def _str_list(cls, v: object) -> List[str]:
        if v is None:
            return []
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]


class TestFlowPlan(BaseModel):
    """Complete test plan produced from a PR diff."""

    __test__ = False

    summary: str = ""
    affected_routes: List[str] = Field(default_factory=list)
    risk_assessment: str = ""
    flows: List[UserFlow] = Field(default_factory=list)

    @field_validator("flows")
    @classmethod
    def _unique_ids(cls, flows: List[UserFlow]) -> List[UserFlow]:
        seen: set[str] = set()
        for f in flows:
            base, n = f.flow_id, 2
            while f.flow_id in seen:
                f.flow_id, n = f"{base}-{n}", n + 1
            seen.add(f.flow_id)
        return flows


# ── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are DiffQA Flow Planner — an expert QA engineer powered by Meta's Muse Code.

Inspect the Git PR diff and synthesize targeted end-to-end browser test flows
for the modified web application.

Design:
1. **Happy-path flows** — normal user interactions touching the changed code.
2. **Adversarial flows** — boundary tests, rapid clicks, invalid inputs,
   state resets, navigation edge cases.
3. **Invariants** — clear, observable assertions that must hold
   (e.g. "Subtotal == sum(items)", "No uncaught JS error").

Reply with **valid JSON only** matching this schema:
{
  "summary": "...",
  "affected_routes": ["/route1"],
  "risk_assessment": "HIGH / MEDIUM / LOW — explanation",
  "flows": [
    {
      "flow_id": "flow-unique-slug",
      "title": "Clear scenario description",
      "route": "/start-route",
      "flow_type": "happy_path" | "adversarial" | "visual_regression",
      "goal": "What user behavior this tests",
      "steps": ["Step 1: ...", "Step 2: ..."],
      "invariants": ["Assertion 1", "Assertion 2"]
    }
  ]
}
"""


# ── Planner ─────────────────────────────────────────────────────────────────


class FlowPlanner:
    """Plans targeted browser test flows from parsed PR diffs."""

    MAX_DIFF_CHARS = 12_000  # truncate raw diff to protect context window

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm = llm_client or get_llm_client()

    async def plan_flows(
        self,
        parsed_diff: ParsedDiff,
        preview_url: str = "http://localhost:3000",
    ) -> TestFlowPlan:
        """Analyze the parsed diff and return a structured ``TestFlowPlan``."""
        raw_diff = parsed_diff.raw_diff
        if len(raw_diff) > self.MAX_DIFF_CHARS:
            raw_diff = raw_diff[: self.MAX_DIFF_CHARS] + "\n... (truncated)"

        files_summary = "\n".join(
            f"- {f.path} (+{f.added_count}/-{f.removed_count})"
            f" [UI={f.is_ui_component}, API={f.is_api_route}, Routes={f.detected_routes}]"
            for f in parsed_diff.files
        )

        user_prompt = (
            f"Target Preview URL: {preview_url}\n\n"
            f"Modified Files:\n{files_summary}\n\n"
            f"Suspected Routes: {parsed_diff.suspected_routes}\n\n"
            f"Git Diff:\n```diff\n{raw_diff}\n```\n\n"
            "Analyze the modifications and map out 2–4 high-priority test flows "
            "(both happy-path and adversarial edge cases)."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        return normalize_plan(await self.llm.complete_json(messages))


MAX_FLOWS = 6

SMOKE_FLOW = UserFlow(
    flow_id="flow-smoke",
    title="Smoke test: load page and exercise visible controls",
    goal="The page loads and its main controls respond without runtime errors or server failures.",
    steps=["Load the page", "Interact with the main visible controls"],
    invariants=["No uncaught JavaScript exceptions", "No HTTP 5xx responses"],
)


def normalize_plan(data: dict) -> TestFlowPlan:
    """Validate model output into a plan that is never empty and never unbounded.

    An empty plan would yield an "all passed" report for a run that tested nothing.
    """
    plan = TestFlowPlan(**data)
    plan.flows = plan.flows[:MAX_FLOWS] or [SMOKE_FLOW.model_copy()]
    return plan
