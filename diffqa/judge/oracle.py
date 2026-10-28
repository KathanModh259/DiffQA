"""Comprehensive regression judge evaluating functional, network, console, and visual signals."""

from enum import Enum
import logging
from typing import List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field, field_validator

from diffqa.agent.runner import FlowRunResult
from diffqa.config import Settings, get_settings
from diffqa.llm.client import LLMClient, get_llm_client

logger = logging.getLogger("diffqa.judge")


class BugType(str, Enum):
    FUNCTIONAL_REGRESSION = "functional_regression"
    VISUAL_REGRESSION = "visual_regression"
    NETWORK_ERROR = "network_error"
    RUNTIME_CRASH = "runtime_crash"
    INVARIANT_VIOLATION = "invariant_violation"


class BugSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class JudgeVerdict(BaseModel):
    flow_id: str
    passed: bool
    verdict: str = "PASS"  # PASS, FAIL, WARNING
    bug_type: Optional[BugType] = None
    title: Optional[str] = None
    root_cause_hypothesis: Optional[str] = None
    severity: Optional[BugSeverity] = None
    repro_steps: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    evidence_logs: List[str] = Field(default_factory=list)
    screenshot_evidence: Optional[str] = None
    # Filled by the pipeline after re-running a failed flow: True = failed again, False = passed on retry.
    reproduced: Optional[bool] = None
    # Filled when a base (pre-change) URL is given: True = the flow fails there too, so this PR didn't cause it.
    pre_existing: Optional[bool] = None

    # Model output is loose ("high", "95%", "Runtime Crash"); coerce rather than discard the verdict.
    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, v: object) -> Optional[str]:
        v = (v.value if isinstance(v, BugSeverity) else str(v or "")).strip().upper()
        return v if v in BugSeverity.__members__ else (BugSeverity.HIGH.value if v else None)

    @field_validator("bug_type", mode="before")
    @classmethod
    def _bug_type(cls, v: object) -> Optional[str]:
        v = (v.value if isinstance(v, BugType) else str(v or "")).strip().lower().replace(" ", "_").replace("-", "_")
        return v if v in {b.value for b in BugType} else (BugType.FUNCTIONAL_REGRESSION.value if v else None)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: object) -> float:
        try:
            c = float(str(v).strip().rstrip("%"))
        except ValueError:
            return 0.5
        return max(0.0, min(1.0, c / 100 if c > 1 else c))

    @field_validator("repro_steps", mode="before")
    @classmethod
    def _repro(cls, v: object) -> List[str]:
        if not v:
            return []
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]


SYSTEM_JUDGE_PROMPT = """You are DiffQA Regression Judge, an expert QA oracle powered by Meta's Muse Wonder.
You analyze the recorded execution trace of an autonomous browser run on a web application following a code change.

You are provided with:
1. The intended User Flow, Goal, and Invariants
2. A step-by-step trace: each action, any dialog that appeared, and what the page showed afterwards
3. The final page text and the interactive controls present at the end
4. Uncaught JavaScript errors, network failures, and deterministic rendering checks

Judge like a meticulous human tester:
- Verify each invariant against the page text after the relevant step (do the arithmetic: prices × quantities,
  totals, counts). A number that does not add up, or a value that should never be negative, is a FAIL.
- Verify the goal's expected outcome happened: a confirmation that never appeared, a control that should be
  present but is missing, or a silent no-op after a submit is a FAIL even with no errors in the logs.
- Uncaught JS errors, 5xx responses, and rendering defects ([object Object], NaN) are always a FAIL.
- Do not fail a flow for the agent's own mistakes (clicking a wrong element) or for cosmetic text differences.
- PASS only when the invariants held and the goal's outcome was observed; use WARNING when something looks
  off but you cannot confirm it from the trace.
- Give a precise root cause hypothesis and numbered reproduction steps a developer can follow.

Respond strictly in valid JSON:
{
  "verdict": "PASS" | "FAIL" | "WARNING",
  "bug_type": "functional_regression" | "visual_regression" | "network_error" | "runtime_crash" | "invariant_violation" | null,
  "title": "Concise summary of the bug",
  "root_cause_hypothesis": "Detailed explanation of what failed and why",
  "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | null,
  "repro_steps": [
    "1. Navigate to ...",
    "2. Click ...",
    "3. Observe ..."
  ],
  "confidence": 0.95
}
"""


class RegressionJudge:
    """Evaluates run traces to render deterministic and LLM-assisted regression verdicts."""

    def __init__(self, llm_client: Optional[LLMClient] = None, settings: Optional[Settings] = None):
        self.llm = llm_client or get_llm_client()
        self.settings = settings or get_settings()

    async def judge_flow(self, run_result: FlowRunResult) -> JudgeVerdict:
        """Analyze run result and output a JudgeVerdict."""
        flow = run_result.flow
        if run_result.infra_error:
            # DiffQA couldn't test this flow. Fail closed so CI doesn't go green, but don't call it an app bug.
            return JudgeVerdict(
                flow_id=flow.flow_id,
                passed=False,
                verdict="ERROR",
                title=f"Not tested: {run_result.infra_error[:200]}",
                root_cause_hypothesis="DiffQA could not complete this flow; this is not evidence of a regression.",
                confidence=0.0,
                evidence_logs=[run_result.infra_error],
                screenshot_evidence=run_result.all_screenshots[-1] if run_result.all_screenshots else None,
            )
        evidence: List[str] = [f"Uncaught JS error: {err}" for err in run_result.uncaught_errors]
        evidence += [
            f"HTTP Server Failure: {n['status']} on {n['url']}"
            for n in run_result.network_failures
            if n.get("status", 0) >= 500
        ]
        evidence += [
            f"Broken API call: {n['status']} on {n.get('method', 'GET')} {n['url']}"
            for n in self._broken_api_calls(run_result)
        ]
        evidence += [
            f"Action failure at step {s.step_number}: {s.error_message}"
            for s in run_result.steps
            if not s.success
        ]
        evidence += [f"Rendering defect at {f}" for f in run_result.hard_findings]
        warnings = [f"Suspicious page content at {f}" for f in run_result.soft_findings]
        warnings += [
            f"Console error at step {s.step_number}: {log['text'][:200]}"
            for s in run_result.steps for log in s.console_logs if log.get("type") == "error"
        ][:10]
        last_screenshot = run_result.all_screenshots[-1] if run_result.all_screenshots else None
        default_repro = [
            f"{i + 1}. {s.action.action.value}"
            + (f" on {s.action.target_id or s.action.selector}" if s.action.target_id or s.action.selector else "")
            for i, s in enumerate(run_result.steps)
        ]

        # The judge reads the run the way a tester would replay it: what was done, what the page
        # then showed, which controls were present, and what dialogs appeared, step by step.
        trace = "\n".join(self._describe_step(s) for s in run_result.steps) or "(no steps executed)"
        last = run_result.steps[-1] if run_result.steps else None
        controls = "\n".join(last.elements_after) if last and last.elements_after else "(none detected)"
        user_prompt = f"""User Flow: {flow.title}
Goal: {flow.goal}
Flow Invariants: {flow.invariants}
Flow finished by agent: {run_result.terminal_reached}

Step-by-step trace:
{trace}

Final Visible Page Text:
{run_result.final_page_text or "(unavailable)"}

Interactive controls present at the end:
{controls}

Uncaught Errors:
{run_result.uncaught_errors}

Network Failures:
{run_result.network_failures}

Warnings (may be legitimate; use judgement):
{warnings}

Detected Evidence:
{evidence}

Evaluate whether this constitutes a bug or regression. Check every invariant against what the page \
showed after each step, and whether the goal's expected outcome (confirmation, updated totals, \
visible controls) actually happened."""

        messages = [
            {"role": "system", "content": SYSTEM_JUDGE_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            data = await self.llm.complete_json(messages, model=self.settings.judge_model)
            verdict = str(data.get("verdict", "FAIL")).upper()
            passed = verdict == "PASS" and not self._hard_failure(run_result)
            if passed:
                return JudgeVerdict(
                    flow_id=flow.flow_id,
                    passed=True,
                    title=data.get("title") or f"Flow '{flow.title}' passed all invariants.",
                    confidence=data.get("confidence", 0.9),
                    evidence_logs=evidence,
                    screenshot_evidence=last_screenshot,
                )
            return JudgeVerdict(
                flow_id=flow.flow_id,
                passed=False,
                verdict="FAIL" if verdict == "PASS" else verdict,
                bug_type=data.get("bug_type") or self._default_bug_type(run_result),
                title=data.get("title") or f"Regression in {flow.title}",
                root_cause_hypothesis=data.get("root_cause_hypothesis"),
                severity=data.get("severity") or BugSeverity.HIGH,
                repro_steps=data.get("repro_steps") or default_repro,
                confidence=data.get("confidence", 0.9),
                evidence_logs=evidence,
                screenshot_evidence=last_screenshot,
            )
        except Exception as e:
            logger.error(f"LLM Judge call failed: {e}")
            # Without a model we can only trust hard evidence.
            failed = bool(evidence)
            return JudgeVerdict(
                flow_id=flow.flow_id,
                passed=not failed,
                verdict="FAIL" if failed else "WARNING",
                bug_type=self._default_bug_type(run_result) if failed else None,
                title=f"Failure detected in {flow.title}" if failed else f"Judge unavailable for {flow.title}: {e}",
                root_cause_hypothesis=f"Errors encountered during execution: {evidence}" if failed else None,
                severity=BugSeverity.HIGH if failed else None,
                repro_steps=default_repro if failed else [],
                confidence=0.5,
                evidence_logs=evidence,
                screenshot_evidence=last_screenshot,
            )

    @staticmethod
    def _describe_step(s) -> str:
        line = (f"Step {s.step_number}: {s.action.action.value} on {s.action.target_id or s.action.selector or '-'}"
                f" (value={s.action.value!r}) -> {'ok' if s.success else 'FAILED: ' + str(s.error_message)[:200]}")
        if s.action.thought:
            line += f"\n    Agent intent: {s.action.thought[:200]}"
        for d in s.dialogs:
            line += f"\n    Dialog: {d[:200]}"
        for o in s.observations:
            line += f"\n    Looks wrong: {o}"
        if s.url_after != s.url_before:
            line += f"\n    URL changed to: {s.url_after}"
        if s.page_text_after:
            line += f"\n    Page then showed: {s.page_text_after[:600]}"
        return line

    @staticmethod
    def _default_bug_type(run_result: FlowRunResult) -> BugType:
        return BugType.RUNTIME_CRASH if run_result.uncaught_errors else BugType.FUNCTIONAL_REGRESSION

    @staticmethod
    def _hard_failure(run_result: FlowRunResult) -> bool:
        """Evidence strong enough to fail the flow even if the model says PASS."""
        if run_result.uncaught_errors or run_result.hard_findings:
            return True
        if any(n.get("status", 0) >= 500 for n in run_result.network_failures):
            return True
        return bool(RegressionJudge._broken_api_calls(run_result))

    @staticmethod
    def _broken_api_calls(run_result: FlowRunResult) -> List[dict]:
        """Same-origin fetch/XHR calls that returned 4xx: usually a renamed endpoint or a bad payload.

        Not included: auth/rate-limit statuses (normal when browsing unauthenticated), static assets
        like a missing favicon, third-party hosts, aborted requests, and failed agent actions (those
        are more often the agent's mistake than the app's).
        """
        if not run_result.steps:
            return []
        site = urlparse(run_result.steps[0].url_before).netloc
        return [
            n for n in run_result.network_failures
            if 400 <= n.get("status", 0) < 500
            and n.get("status") not in (401, 403, 407, 429)
            and n.get("resource_type") in ("fetch", "xhr")
            and urlparse(n.get("url", "")).netloc == site
        ]
