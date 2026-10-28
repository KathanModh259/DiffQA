"""Autonomous browser agent loop executing user flows with perception and action."""

import logging
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.agent.browser import BrowserController
from diffqa.agent.dom_observer import DOMObserver
from diffqa.analyzer.flow_planner import UserFlow
from diffqa.config import Settings, get_settings
from diffqa.agent.sanity import check_page_text
from diffqa.llm.client import LLMClient, get_llm_client

logger = logging.getLogger("diffqa.agent")


class FlowRunResult(BaseModel):
    flow: UserFlow
    steps: List[ExecutionStep] = Field(default_factory=list)
    success: bool = True
    terminal_reached: bool = False
    duration_seconds: float = 0.0
    all_screenshots: List[str] = Field(default_factory=list)
    uncaught_errors: List[str] = Field(default_factory=list)
    network_failures: List[Dict[str, Any]] = Field(default_factory=list)
    final_page_text: str = ""
    # Deterministic sanity findings from rendered text, split by how certain they are (see judge.sanity).
    hard_findings: List[str] = Field(default_factory=list)
    soft_findings: List[str] = Field(default_factory=list)
    # Set when DiffQA itself couldn't test (site unreachable, model down): not evidence of an app bug.
    infra_error: Optional[str] = None


MAX_PAGE_TEXT_CHARS = 4000
STEP_PAGE_TEXT_CHARS = 1500
MAX_PROMPT_ELEMENTS = 80
MAX_LLM_FAILURES = 3


def flow_url(base_url: str, route: str) -> str:
    """Join a planned route onto the base URL; absolute routes are honoured only on the same host."""
    if urlparse(route).scheme:
        return route if urlparse(route).netloc == urlparse(base_url).netloc else base_url
    return f"{base_url.rstrip('/')}/{route.lstrip('/')}"


SYSTEM_AGENT_PROMPT = """You are DiffQA Browser Agent, an expert QA automation agent powered by Meta's Muse Spark.
You drive a headless browser to test a specific user flow on a web application.

At each step, you receive:
1. Target Flow Goal & Invariants
2. Current URL
3. Previous Actions taken
4. Currently visible interactive DOM elements with IDs

Your job is to select the next single best action to advance towards the test goal or test the adversarial edge case.
If the flow goal has been accomplished, or an unrecoverable failure occurs, set `is_terminal: true`.

Supported actions:
- `click`: Click on an element by target_id or selector.
- `fill`: Enter text into an input by target_id or selector, with value.
- `navigate`: Go to a page the app links to (or one already visited). Guessed URLs are refused.
- `scroll`: Scroll down or up (value in pixels, e.g. "300").
- `press`: Press a keyboard key (e.g. "Enter", "Tab", "Escape").
- `wait`: Pause for milliseconds (value e.g. "1000").
- `assert`: Check an expectation. Put the text you expect to see on the page in `value`, or a target_id
  that must be visible. A failed assert is recorded as evidence, so assert what the goal and invariants promise.

Test like a careful human: after each action, read what changed (the "Page now shows" line and any dialog
text). Before finishing, assert the key expected outcome. Do not repeat an action that already failed.

You MUST respond strictly with a valid JSON object:
{
  "thought": "Reasoning about why you are performing this step",
  "action": "click" | "fill" | "navigate" | "scroll" | "press" | "wait" | "assert",
  "target_id": "elem-id-from-list",
  "selector": null,
  "value": "text or key or null",
  "is_terminal": false
}
"""


class BrowserAgentRunner:
    """Executes a UserFlow autonomously inside the browser."""

    def __init__(
        self,
        controller: BrowserController,
        dom_observer: Optional[DOMObserver] = None,
        llm_client: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
    ):
        self.controller = controller
        self.dom = dom_observer or DOMObserver()
        self.llm = llm_client or get_llm_client()
        self.settings = settings or get_settings()

    async def run_flow(
        self,
        flow: UserFlow,
        base_url: str,
        artifacts_dir: Optional[Path] = None,
    ) -> FlowRunResult:
        """Run an entire user flow to completion."""
        artifacts_dir = artifacts_dir or self.settings.output_dir / flow.flow_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        initial_url = flow_url(base_url, flow.route)
        logger.info(f"Starting flow '{flow.title}' at {initial_url}")

        steps: List[ExecutionStep] = []
        all_screenshots: List[str] = []
        uncaught_errors: List[str] = []
        network_failures: List[Dict[str, Any]] = []
        hard_findings: List[str] = []
        soft_findings: List[str] = []

        self.controller.drain_diagnostics()  # don't inherit errors from the previous flow
        try:
            await self.controller.navigate(initial_url)
        except Exception as e:
            logger.error(f"Failed initial navigation to {initial_url}: {e}")
            return FlowRunResult(
                flow=flow,
                success=False,
                duration_seconds=time.time() - start_time,
                infra_error=f"Could not load {initial_url}: {e}",
            )

        step_num = 0
        terminal_reached = False
        llm_failures = 0
        failed_actions: Dict[tuple, str] = {}
        site_host = urlparse(initial_url).netloc
        # Pages a person could reach: where they started, where they've been, and what the app links to.
        known_paths = {_path(initial_url)}

        while step_num < self.settings.max_steps_per_flow:
            step_num += 1
            step_start = time.time()
            url_before = self.controller.page.url
            known_paths.add(_path(url_before))
            known_paths.update(_path(h) for h in await self._links())

            # 1. Observe current page interactive DOM (capped: real pages can have thousands of controls)
            elements = await self.dom.extract_interactive_elements(self.controller.page)
            elements_text = self.dom.format_elements_for_prompt(elements[:MAX_PROMPT_ELEMENTS])

            # Map diffqa_id -> selector for fast lookup
            elem_map = {el.diffqa_id: el.selector for el in elements}

            # 2. Capture screenshot
            screenshot_path = artifacts_dir / f"step_{step_num}.png"
            try:
                saved_screenshot = await self.controller.take_screenshot(screenshot_path)
                all_screenshots.append(saved_screenshot)
            except Exception:
                saved_screenshot = None

            # 3. Build prompt for Next Action
            history_text = "\n".join(self._describe_step(s) for s in steps) or "None (just landed on page)"
            if time.time() - start_time > self.settings.flow_timeout_seconds:
                logger.warning(f"Flow '{flow.flow_id}' hit its {self.settings.flow_timeout_seconds}s budget.")
                break

            user_prompt = f"""Target Flow: {flow.title}
Flow Type: {flow.flow_type}
Goal: {flow.goal}
Key Invariants to maintain: {flow.invariants}
Current URL: {url_before}

Previous Actions Taken:
{history_text}

Available Interactive Elements on Page:
{elements_text}

Choose the next action to advance this test."""

            messages = [
                {"role": "system", "content": SYSTEM_AGENT_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            # 4. Prompt LLM for Action
            try:
                action_data = await self.llm.complete_json(messages, model=self.settings.agent_model)
                action = AgentAction(**action_data)
                llm_failures = 0
            except Exception as e:
                # One bad reply shouldn't end the flow; a persistently failing model should.
                llm_failures += 1
                logger.error(f"Failed to generate next action ({llm_failures}/{MAX_LLM_FAILURES}): {e}")
                if llm_failures >= MAX_LLM_FAILURES:
                    return FlowRunResult(
                        flow=flow,
                        steps=steps,
                        success=False,
                        duration_seconds=time.time() - start_time,
                        all_screenshots=all_screenshots,
                        uncaught_errors=uncaught_errors,
                        network_failures=network_failures,
                        infra_error=f"Agent model failed {llm_failures} times in a row: {e}",
                    )
                step_num -= 1
                continue

            # 5. Execute Action
            exec_success = True
            error_msg = None
            try:
                # A hallucinated target_id fails loudly; the error shows up in the next prompt's history.
                if not action.selector and action.target_id:
                    action.selector = elem_map.get(action.target_id) or f"[data-diffqa-id='{action.target_id}']"
                # Retrying an identical failed click just burns another full timeout; fail it instantly.
                key = (action.action, action.selector, action.value)
                if key in failed_actions and action.action != ActionType.ASSERT:
                    raise RuntimeError(f"Skipped: identical action already failed ({failed_actions[key][:120]})")
                # A guessed URL (e.g. /products on a one-page app) tests the model's imagination, not the app.
                if action.action == ActionType.NAVIGATE and action.value:
                    target = urljoin(url_before, action.value)
                    if urlparse(target).scheme in ("http", "https") and _path(target) not in known_paths:
                        raise ValueError(f"Not navigating to {action.value[:80]}: the app does not link there")
                await self.controller.execute_action(action)
            except Exception as e:
                exec_success = False
                error_msg = str(e).split("\n")[0][:300]
                failed_actions.setdefault((action.action, action.selector, action.value), error_msg)
                logger.warning(f"Action execution failed on step {step_num}: {error_msg}")

            # Keep the agent on the site under test (external links, OAuth popups in the main tab, ads).
            if site_host and urlparse(self.controller.page.url).netloc not in (site_host, ""):
                left_to = self.controller.page.url
                try:
                    await self.controller.navigate(url_before)
                    action.thought += f" [DiffQA returned from external page {left_to}]"
                except Exception as e:
                    logger.warning(f"Could not return from external page {left_to}: {e}")

            # 6. Observe the outcome the way a tester would, then drain diagnostics
            diag = self.controller.drain_diagnostics()
            uncaught_errors.extend(diag["page_errors"])
            network_failures.extend(diag["network"])
            page_text_after = await self.controller.page_text(STEP_PAGE_TEXT_CHARS)
            elements_after = await self.dom.extract_interactive_elements(self.controller.page)
            observations = []
            for finding in check_page_text(page_text_after):
                observations.append(str(finding))
                (hard_findings if finding.hard else soft_findings).append(f"Step {step_num}: {finding}")

            step_record = ExecutionStep(
                step_number=step_num,
                action=action,
                url_before=url_before,
                url_after=self.controller.page.url,
                screenshot_path=saved_screenshot,
                console_logs=diag["console"],
                network_errors=diag["network"],
                success=exec_success,
                error_message=error_msg,
                duration_ms=(time.time() - step_start) * 1000,
                dialogs=diag["dialogs"],
                page_text_after=page_text_after,
                elements_after=[_element_label(el) for el in elements_after[:MAX_PROMPT_ELEMENTS]],
                observations=observations,
            )
            steps.append(step_record)

            if action.is_terminal:
                terminal_reached = True
                break

        final_page_text = await self.controller.page_text(MAX_PAGE_TEXT_CHARS)

        duration = time.time() - start_time
        overall_success = (
            not uncaught_errors
            and not hard_findings
            and not any(n.get("status", 0) >= 500 for n in network_failures)
            and all(s.success for s in steps)
        )

        return FlowRunResult(
            flow=flow,
            steps=steps,
            success=overall_success,
            terminal_reached=terminal_reached,
            duration_seconds=duration,
            all_screenshots=all_screenshots,
            uncaught_errors=uncaught_errors,
            network_failures=network_failures,
            final_page_text=final_page_text,
            hard_findings=_dedupe(hard_findings),
            soft_findings=_dedupe(soft_findings),
        )

    @staticmethod
    def _describe_step(s: ExecutionStep) -> str:
        line = (f"Step {s.step_number}: [{s.action.action.value}] target={s.action.target_id} value='{s.action.value}' "
                f"(success={s.success}{', error=' + s.error_message[:150] if s.error_message else ''})")
        for d in s.dialogs:
            line += f"\n    Dialog appeared: {d[:200]}"
        for o in s.observations:
            line += f"\n    Looks wrong: {o}"
        if s.page_text_after:
            line += f"\n    Page now shows: {s.page_text_after[:400]}"
        return line

    async def _links(self) -> List[str]:
        try:
            return await self.controller.page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        except Exception:
            return []


def _path(url: str) -> str:
    """Path without query/fragment, so /cart?x=1 and /cart#top count as the same page."""
    return urlparse(url).path.rstrip("/") or "/"


def _element_label(el) -> str:
    text = el.text or el.aria_label or el.placeholder or el.value or ""
    return f"[{el.diffqa_id}] <{el.tag}>" + (f" {text[:40]!r}" if text else "") + (" (DISABLED)" if el.is_disabled else "")


def _dedupe(items: List[str]) -> List[str]:
    """Keep the first occurrence of each finding (the 'Step N: ' prefix is ignored for comparison)."""
    seen, out = set(), []
    for item in items:
        key = item.split(": ", 1)[-1]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

