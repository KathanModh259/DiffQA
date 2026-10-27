"""Generates rich, actionable Markdown summaries for GitHub PR comments and CI logs."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

from diffqa.analyzer.flow_planner import TestFlowPlan
from diffqa.judge.oracle import JudgeVerdict, BugSeverity


class FlowReportItem(BaseModel):
    flow_id: str
    title: str
    route: str
    flow_type: str
    passed: bool
    verdict: JudgeVerdict
    spec_code_ts: Optional[str] = None
    spec_code_py: Optional[str] = None
    duration_seconds: float = 0.0


class PRReportGenerator:
    """Renders structured Markdown suitable for GitHub PR comments."""

    def generate_report(
        self,
        plan: TestFlowPlan,
        items: List[FlowReportItem],
        preview_url: str = "",
        commit_sha: Optional[str] = None,
    ) -> str:
        passed_count = sum(1 for item in items if item.passed)
        failed_count = sum(1 for item in items if not item.passed)
        total_count = len(items)

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
            badge = "✅ Pass" if item.passed else "❌ Fail"
            type_badge = f"`{item.flow_type}`"
            md.append(f"| {badge} | **{item.title}** | `{item.route}` | {type_badge} | {item.duration_seconds:.1f}s |")
        md.append("\n---\n")

        # Bug Details Section if failures exist
        if failed_count > 0:
            md.append("### 🚨 Detected Regressions & Reproduction Steps\n")
            for item in items:
                if item.passed:
                    continue

                v = item.verdict
                severity_color = "red" if v.severity == BugSeverity.CRITICAL else "orange"
                severity_tag = f"**Severity: `{v.severity.value if v.severity else 'HIGH'}`**"

                md.append(f"#### ❌ {v.title or item.title}")
                md.append(f"- {severity_tag} | Flow: `{item.flow_id}` | Confidence: `{int(v.confidence * 100)}%`")
                if v.root_cause_hypothesis:
                    md.append(f"- **Root Cause Hypothesis:** {v.root_cause_hypothesis}")

                if v.evidence_logs:
                    md.append("- **Observed Diagnostic Errors:**")
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
        else:
            md.append("### 🎉 All targeted journeys & exploratory edge cases passed with no regressions!\n")

        # Footer
        md.append(
            "*Autonomous QA analysis powered by [DiffQA](https://github.com/KathanModh259/DiffQA) "
            "with **Meta Model API** (`Muse Code` · `Muse Spark` · `Muse Wonder`).*"
        )
        return "\n".join(md)

    def save_report(self, markdown_content: str, output_path: Path | str) -> Path:
        """Save report to disk."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown_content, encoding="utf-8")
        return path

