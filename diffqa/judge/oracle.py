"""Comprehensive regression judge evaluating functional, network, console, and visual signals."""

from enum import Enum
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

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


SYSTEM_JUDGE_PROMPT = """You are DiffQA Regression Judge, an expert QA oracle powered by Meta's Muse Wonder.
You analyze the recorded execution trace of an autonomous browser run on a web application following a code change.

You are provided with:
1. The intended User Flow, Goal, and Invariants
2. The sequence of actions executed
3. Uncaught JavaScript errors and console logs
4. Network errors (HTTP 4xx/5xx responses)
5. Action execution failures

Determine whether a regression or bug occurred during the run.
- If uncaught JS errors, 500 status codes, or invariant violations occurred, mark verdict as 'FAIL'.
- Formulate a precise root cause hypothesis and step-by-step reproduction instructions.

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
        evidence: List[str] = []

        # 1. Deterministic heuristic checks
        if run_result.uncaught_errors:
            evidence.extend([f"Uncaught JS error: {err}" for err in run_result.uncaught_errors])

        http_500s = [n for n in run_result.network_failures if n.get("status", 0) >= 500]
        if http_500s:
            evidence.extend([f"HTTP Server Failure: {n['status']} on {n['url']}" for n in http_500s])

        action_failures = [s for s in run_result.steps if not s.success]
        if action_failures:
            evidence.extend([f"Action failure at step {s.step_number}: {s.error_message}" for s in action_failures])

        last_screenshot = run_result.all_screenshots[-1] if run_result.all_screenshots else None

        # If deterministically clean and no heuristics triggered, pass quickly
        if not evidence and run_result.success and run_result.terminal_reached:
            return JudgeVerdict(
                flow_id=run_result.flow.flow_id,
                passed=True,
                verdict="PASS",
                title=f"Flow '{run_result.flow.title}' passed all invariants.",
                confidence=1.0,
                screenshot_evidence=last_screenshot,
            )

        # 2. Ask LLM Judge to synthesize verdict and root-cause hypothesis
        steps_summary = "\n".join(
            f"Step {s.step_number}: {s.action.action.value} on {s.action.target_id} (value={s.action.value}) -> Success={s.success}"
            for s in run_result.steps
        )

        user_prompt = f"""User Flow: {run_result.flow.title}
Goal: {run_result.flow.goal}
Flow Invariants: {run_result.flow.invariants}

Execution Steps:
{steps_summary}

Uncaught Errors:
{run_result.uncaught_errors}

Network Failures:
{run_result.network_failures}

Detected Evidence:
{evidence}

Evaluate whether this constitutes a bug or regression."""

        messages = [
            {"role": "system", "content": SYSTEM_JUDGE_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            verdict_data = await self.llm.complete_json(messages, model=self.settings.judge_model)
            passed = verdict_data.get("verdict", "").upper() == "PASS"
            
            # Map repro steps if missing
            repro = verdict_data.get("repro_steps") or [
                f"{i+1}. {s.action.action.value} on {s.action.target_id or s.action.selector}"
                for i, s in enumerate(run_result.steps)
            ]

            return JudgeVerdict(
                flow_id=run_result.flow.flow_id,
                passed=passed,
                verdict=verdict_data.get("verdict", "FAIL" if not passed else "PASS"),
                bug_type=verdict_data.get("bug_type") or (BugType.RUNTIME_CRASH if run_result.uncaught_errors else BugType.FUNCTIONAL_REGRESSION),
                title=verdict_data.get("title") or f"Regression in {run_result.flow.title}",
                root_cause_hypothesis=verdict_data.get("root_cause_hypothesis"),
                severity=verdict_data.get("severity") or BugSeverity.HIGH,
                repro_steps=repro,
                confidence=float(verdict_data.get("confidence", 0.9)),
                evidence_logs=evidence,
                screenshot_evidence=last_screenshot,
            )
        except Exception as e:
            logger.error(f"LLM Judge call failed: {e}")
            # Fallback deterministic verdict
            return JudgeVerdict(
                flow_id=run_result.flow.flow_id,
                passed=False,
                verdict="FAIL",
                bug_type=BugType.RUNTIME_CRASH if run_result.uncaught_errors else BugType.FUNCTIONAL_REGRESSION,
                title=f"Failure detected in {run_result.flow.title}",
                root_cause_hypothesis=f"Errors encountered during execution: {evidence}",
                severity=BugSeverity.HIGH,
                repro_steps=[f"Step {s.step_number}: {s.action.action.value}" for s in run_result.steps],
                confidence=0.85,
                evidence_logs=evidence,
                screenshot_evidence=last_screenshot,
            )

