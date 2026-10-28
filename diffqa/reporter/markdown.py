"""Generates rich, actionable Markdown summaries for GitHub PR comments and CI logs."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

from diffqa.analyzer.flow_planner import TestFlowPlan
from diffqa.judge.oracle import JudgeVerdict


class FlowReportItem(BaseModel):
    flow_id: str
    title: str
    route: str
    flow_type: str
    passed: bool
    verdict: JudgeVerdict
    spec_code_ts: Optional[str] = None
    duration_seconds: float = 0.0
    steps_count: int = 0
    assertions_count: int = 0
    invariants: List[str] = Field(default_factory=list)

    @property
    def blocks_merge(self) -> bool:
        """A failure this change is responsible for (or could not rule out). Pre-existing bugs are reported, not blocking."""
        return not self.passed and not self.verdict.pre_existing


# Checks applied to every flow regardless of model, listed in reports so readers know what was covered.
AUTOMATIC_CHECKS = [
    "Uncaught JavaScript exceptions",
    "HTTP 5xx responses and failed same-origin API calls (4xx on fetch/XHR)",
    "Rendering defects in visible text (NaN, [object Object], unrendered templates)",
    "Suspicious content (negative amounts, 'undefined'/'null', error-page text)",
    "Alert / confirm dialogs captured and read",
    "Page text and visible controls recorded after every step",
]


def reproduction_note(v: JudgeVerdict) -> str:
    if v.reproduced is True:
        return "Reproduced on re-run (2/2)"
    if v.reproduced is False:
        return "Did not reproduce on re-run (1/2) — treat as intermittent"
    return "Observed once (re-run disabled)"


def baseline_note(v: JudgeVerdict) -> str:
    if v.pre_existing is True:
        return "Also fails on the base version: already broken before this change"
    if v.pre_existing is False:
        return "Passes on the base version: introduced by this change"
    return ""


class PRReportGenerator:
    """Renders structured Markdown suitable for GitHub PR comments."""

    def generate_report(
        self,
        plan: TestFlowPlan,
        items: List[FlowReportItem],
        preview_url: str = "",
        commit_sha: Optional[str] = None,
    ) -> str:
        failed_count = sum(1 for item in items if item.blocks_merge)
        flagged = [item for item in items if not item.passed]

        status_badge = (
            "![Status](https://img.shields.io/badge/DiffQA-PASSED-brightgreen)"
            if failed_count == 0
            else f"![Status](https://img.shields.io/badge/DiffQA-{failed_count}%20FAILED-critical)"
        )

        md = []
        md.append(f"## 🤖 DiffQA Autonomous PR Regression Report {status_badge}\n")
        md.append(f"> **Tested Preview URL:** `{preview_url}`  ")
        if commit_sha:
            md.append(f"> **Commit:** `{commit_sha[:7]}`  ")
        md.append(f"> **Diff Summary:** {plan.summary}\n")

        # Summary Metrics Table
        md.append("### 📊 Test Flows Overview")
        md.append("| Status | Flow | Route | Type | Duration |")
        md.append("| :---: | :--- | :--- | :---: | :---: |")
        for item in items:
            badge = ("✅ Pass" if item.passed else "⚠️ Not tested" if item.verdict.verdict == "ERROR"
                     else "🟡 Already broken" if item.verdict.pre_existing else "❌ Fail")
            type_badge = f"`{item.flow_type}`"
            md.append(f"| {badge} | **{item.title}** | `{item.route}` | {type_badge} | {item.duration_seconds:.1f}s |")
        md.append("\n---\n")

        # Bug Details Section if failures exist
        if failed_count == 0:
            md.append("### ✅ All planned flows passed with no regressions\n")
        if flagged:
            md.append("### 🚨 Detected Regressions & Reproduction Steps\n" if failed_count else
                      "### 🟡 Issues that already existed before this change\n")
            for item in flagged:
                v = item.verdict
                severity_tag = f"**Severity: `{v.severity.value if v.severity else 'HIGH'}`**"

                if v.verdict == "ERROR":
                    md.append(f"#### ⚠️ {v.title or item.title}")
                    md.append(f"- Flow `{item.flow_id}` could not be tested. {v.root_cause_hypothesis or ''}")
                    md.append("\n---\n")
                    continue

                md.append(f"#### {'🟡 Already broken: ' if v.pre_existing else '❌ '}{v.title or item.title}")
                md.append(f"- {severity_tag} | Flow: `{item.flow_id}` | Confidence: `{int(v.confidence * 100)}%`")
                md.append(f"- **Reproduction:** {reproduction_note(v)}")
                if baseline_note(v):
                    md.append(f"- **Base version:** {baseline_note(v)}")
                if v.root_cause_hypothesis:
                    md.append(f"- **Root Cause Hypothesis:** {v.root_cause_hypothesis}")
                if v.screenshot_evidence:
                    md.append(f"- **Screenshot:** `{v.screenshot_evidence}`")

                if v.evidence_logs:
                    md.append("- **Evidence:**")
                    for err in v.evidence_logs:
                        md.append(f"  - `{err}`")

                if v.repro_steps:
                    md.append("\n**Reproduction Steps:**")
                    for step in v.repro_steps:
                        md.append(f"- {step}")

                if item.spec_code_ts:
                    md.append("\n<details><summary><b>🧪 Auto-Generated Playwright Test (TypeScript)</b></summary>\n")
                    md.append("```typescript")
                    md.append(item.spec_code_ts)
                    md.append("```")
                    md.append("</details>\n")

                md.append("\n---\n")

        # What was actually covered, so a reader can judge the result rather than trust it blindly.
        md.append("<details><summary><b>🔍 Coverage: what this run checked</b></summary>\n")
        md.append("| Flow | Steps | Assertions | Invariants checked |")
        md.append("| :--- | :---: | :---: | :--- |")
        for item in items:
            inv = "; ".join(item.invariants) if item.invariants else "—"
            md.append(f"| `{item.flow_id}` | {item.steps_count} | {item.assertions_count} | {inv} |")
        md.append("\n**Automatic checks on every step:**")
        for check in AUTOMATIC_CHECKS:
            md.append(f"- {check}")
        md.append(
            "\n**Not covered by this run:** visual pixel comparison, flows that need data or "
            "accounts not reachable from the preview, and anything outside the planned flows above.\n"
        )
        md.append("</details>\n")

        # Footer
        md.append(
            "*Autonomous QA analysis powered by [DiffQA](https://github.com/KathanModh259/DiffQA) "
            "with **Meta Model API**.*"
        )
        return "\n".join(md)

    def save_report(self, markdown_content: str, output_path: Path | str) -> Path:
        """Save report to disk."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown_content, encoding="utf-8")
        return path

