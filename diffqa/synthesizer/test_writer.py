"""Synthesizes human-readable, deterministic Playwright test specs from confirmed bug traces."""

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from jinja2 import Template

from diffqa.agent.actions import ExecutionStep
from diffqa.judge.oracle import JudgeVerdict

# Strings are pre-quoted in Python (json.dumps for TS, repr for Python) so page content
# containing quotes, backticks or `${` can never break out of a literal in the generated spec.

TYPESCRIPT_TEMPLATE = """import { test, expect } from '@playwright/test';

/**
 * DiffQA auto-generated regression test
 * Flow: {{ flow_title }}
 * Bug: {{ bug_title }}
 * Severity: {{ severity }}
 * Root cause hypothesis: {{ root_cause }}
 */
test.describe({{ describe_name }}, () => {
  test({{ test_name }}, async ({ page }) => {
    await page.goto({{ initial_url }});
{% for step in steps %}
    // Step {{ step.step_number }}: {{ step.thought }}
{% if step.action == 'click' %}
    await page.locator({{ step.selector }}).click();
{% elif step.action == 'fill' %}
    await page.locator({{ step.selector }}).fill({{ step.value }});
{% elif step.action == 'navigate' %}
    await page.goto({{ step.value }});
{% elif step.action == 'scroll' %}
    await page.evaluate(() => window.scrollBy(0, {{ step.pixels }}));
{% elif step.action == 'press' %}
    await page.keyboard.press({{ step.key }});
{% elif step.action == 'wait' %}
    await page.waitForTimeout({{ step.pixels }});
{% endif %}
    await page.waitForTimeout(300);
{% endfor %}
{% for invariant in invariants %}
    // Invariant: {{ invariant }}
{% endfor %}
    await expect(page.locator('body')).not.toContainText('Internal Server Error');
  });
});
"""

PYTHON_TEMPLATE = '''"""
DiffQA auto-generated regression test
Flow: {{ flow_title }}
Bug: {{ bug_title }}
Severity: {{ severity }}
Root cause hypothesis: {{ root_cause }}
"""

from playwright.sync_api import Page, expect


def test_repro_{{ flow_id_slug }}(page: Page):
    page.goto({{ initial_url }})
{% for step in steps %}
    # Step {{ step.step_number }}: {{ step.thought }}
{% if step.action == 'click' %}
    page.locator({{ step.selector }}).click()
{% elif step.action == 'fill' %}
    page.locator({{ step.selector }}).fill({{ step.value }})
{% elif step.action == 'navigate' %}
    page.goto({{ step.value }})
{% elif step.action == 'scroll' %}
    page.evaluate("window.scrollBy(0, {{ step.pixels }})")
{% elif step.action == 'press' %}
    page.keyboard.press({{ step.key }})
{% elif step.action == 'wait' %}
    page.wait_for_timeout({{ step.pixels }})
{% endif %}
    page.wait_for_timeout(300)
{% endfor %}
{% for invariant in invariants %}
    # Invariant: {{ invariant }}
{% endfor %}
    expect(page.locator("body")).not_to_contain_text("Internal Server Error")
'''


def _one_line(text: Optional[str]) -> str:
    """Collapse to a single line that is safe inside // # and /* */ comments."""
    return " ".join((text or "").split()).replace("*/", "* /")


def _int_or(value: Optional[str], default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class PlaywrightTestSynthesizer:
    """Generates Playwright test files (.spec.ts / .py) for confirmed bugs."""

    def synthesize_typescript(
        self,
        verdict: JudgeVerdict,
        steps: List[ExecutionStep],
        initial_url: str,
        flow_title: str,
        invariants: Optional[List[str]] = None,
        output_file: Optional[Path | str] = None,
    ) -> str:
        """Generate a TypeScript Playwright test script."""
        title = verdict.title or "Regression caught by DiffQA"
        return self._render(
            TYPESCRIPT_TEMPLATE, json.dumps, verdict, steps, initial_url, flow_title, invariants, output_file,
            describe_name=json.dumps(f"DiffQA Regressions: {verdict.flow_id}"),
            test_name=json.dumps(f"reproduce: {title}"),
        )

    def synthesize_python(
        self,
        verdict: JudgeVerdict,
        steps: List[ExecutionStep],
        initial_url: str,
        flow_title: str,
        invariants: Optional[List[str]] = None,
        output_file: Optional[Path | str] = None,
    ) -> str:
        """Generate a pytest-playwright (sync API) test script."""
        return self._render(
            PYTHON_TEMPLATE, repr, verdict, steps, initial_url, flow_title, invariants, output_file,
            flow_id_slug=re.sub(r"\W", "_", verdict.flow_id),
        )

    @staticmethod
    def _render(
        template: str,
        quote: Callable[[str], str],
        verdict: JudgeVerdict,
        steps: List[ExecutionStep],
        initial_url: str,
        flow_title: str,
        invariants: Optional[List[str]],
        output_file: Optional[Path | str],
        **extra: str,
    ) -> str:
        formatted_steps: List[Dict[str, object]] = []
        for s in steps:
            selector = s.action.selector
            if not selector and s.action.target_id:
                selector = f"[data-diffqa-id='{s.action.target_id}']"
            formatted_steps.append({
                "step_number": s.step_number,
                "action": s.action.action.value,
                "selector": quote(selector or "body"),
                "value": quote(s.action.value or ""),
                "key": quote(s.action.value or "Enter"),
                "pixels": _int_or(s.action.value, 400 if s.action.action.value == "scroll" else 1000),
                "thought": _one_line(s.action.thought),
            })

        rendered = Template(template, trim_blocks=True).render(
            flow_title=_one_line(flow_title),
            bug_title=_one_line(verdict.title or "Regression caught by DiffQA"),
            severity=verdict.severity.value if verdict.severity else "HIGH",
            root_cause=_one_line(verdict.root_cause_hypothesis or "State or visual regression"),
            initial_url=quote(initial_url),
            steps=formatted_steps,
            invariants=[_one_line(i) for i in invariants or []],
            **extra,
        )

        if output_file:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
        return rendered
