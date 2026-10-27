"""Synthesizes human-readable, deterministic Playwright test specs from confirmed bug traces."""

import re
from pathlib import Path
from typing import List, Optional
from diffqa.agent.actions import ActionType, ExecutionStep
from diffqa.judge.oracle import JudgeVerdict


TYPESCRIPT_TEMPLATE = """import { test, expect } from '@playwright/test';

/**
 * 🤖 DiffQA Auto-Generated Regression Test
 * Flow: {{ flow_title }}
 * Bug: {{ bug_title }}
 * Severity: {{ severity }}
 * Root Cause Hypothesis: {{ root_cause }}
 */
test.describe('DiffQA Regressions: {{ flow_id }}', () => {
  test('reproduce: {{ bug_title_escaped }}', async ({ page }) => {
    // 1. Initial Navigation
    await page.goto('{{ initial_url }}');

    // 2. Playwright Step Replay
{% for step in steps %}
    // Step {{ step.step_number }}: {{ step.thought }}
{% if step.action == 'click' %}
    await page.locator(`{{ step.selector }}`).click();
{% elif step.action == 'fill' %}
    await page.locator(`{{ step.selector }}`).fill(`{{ step.value }}`);
{% elif step.action == 'navigate' %}
    await page.goto(`{{ step.value }}`);
{% elif step.action == 'scroll' %}
    await page.evaluate(() => window.scrollBy(0, {{ step.value or 400 }}));
{% elif step.action == 'press' %}
    await page.keyboard.press(`{{ step.value or "Enter" }}`);
{% elif step.action == 'wait' %}
    await page.waitForTimeout({{ step.value or 1000 }});
{% endif %}
    await page.waitForTimeout(300);
{% endfor %}

    // 3. Regression Assertion (Expected behavior that was violated)
{% for invariant in invariants %}
    // Invariant: {{ invariant }}
{% endfor %}
    // Assert no console errors or regressions were observed
    await expect(page.locator('body')).not.toContainText('Internal Server Error');
  });
});
"""

PYTHON_TEMPLATE = '''"""
🤖 DiffQA Auto-Generated Regression Test
Flow: {{ flow_title }}
Bug: {{ bug_title }}
Severity: {{ severity }}
Root Cause Hypothesis: {{ root_cause }}
"""

import pytest
from playwright.async_api import Page, expect

@pytest.mark.asyncio
async def test_repro_{{ flow_id_slug }}(page: Page):
    # 1. Initial Navigation
    await page.goto("{{ initial_url }}")

    # 2. Replay steps
{% for step in steps %}
    # Step {{ step.step_number }}: {{ step.thought }}
{% if step.action == 'click' %}
    await page.locator("""{{ step.selector }}""").click()
{% elif step.action == 'fill' %}
    await page.locator("""{{ step.selector }}""").fill("""{{ step.value }}""")
{% elif step.action == 'navigate' %}
    await page.goto("""{{ step.value }}""")
{% elif step.action == 'scroll' %}
    await page.evaluate("window.scrollBy(0, {{ step.value or 400 }})")
{% elif step.action == 'press' %}
    await page.keyboard.press("""{{ step.value or 'Enter' }}""")
{% elif step.action == 'wait' %}
    await page.wait_for_timeout({{ step.value or 1000 }})
{% endif %}
    await page.wait_for_timeout(300)
{% endfor %}

    # 3. Assertions
    await expect(page.locator("body")).not_to_contain_text("Internal Server Error")
'''


class PlaywrightTestSynthesizer:
    """Generates Playwright test files (.spec.ts / .spec.py) for confirmed bugs."""

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
        formatted_steps = []
        for s in steps:
            selector = s.action.selector
            if not selector and s.action.target_id:
                selector = f"[data-diffqa-id='{s.action.target_id}']"
            if not selector:
                selector = "body"

            formatted_steps.append({
                "step_number": s.step_number,
                "action": s.action.action.value,
                "selector": selector,
                "value": (s.action.value or "").replace("'", "\\'"),
                "thought": (s.action.thought or "").replace("\n", " "),
            })

        from jinja2 import Template

        template = Template(TYPESCRIPT_TEMPLATE)
        rendered = template.render(
            flow_title=flow_title,
            flow_id=verdict.flow_id,
            bug_title=verdict.title or "Regression caught by DiffQA",
            bug_title_escaped=(verdict.title or "Regression").replace("'", "\\'"),
            severity=verdict.severity.value if verdict.severity else "HIGH",
            root_cause=(verdict.root_cause_hypothesis or "State or visual regression").replace("\n", " "),
            initial_url=initial_url,
            steps=formatted_steps,
            invariants=invariants or [],
        )

        if output_file:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")

        return rendered

    def synthesize_python(
        self,
        verdict: JudgeVerdict,
        steps: List[ExecutionStep],
        initial_url: str,
        flow_title: str,
        invariants: Optional[List[str]] = None,
        output_file: Optional[Path | str] = None,
    ) -> str:
        """Generate a Python Playwright test script."""
        formatted_steps = []
        for s in steps:
            selector = s.action.selector
            if not selector and s.action.target_id:
                selector = f"[data-diffqa-id='{s.action.target_id}']"
            if not selector:
                selector = "body"

            formatted_steps.append({
                "step_number": s.step_number,
                "action": s.action.action.value,
                "selector": selector,
                "value": (s.action.value or "").replace('"', '\\"'),
                "thought": (s.action.thought or "").replace("\n", " "),
            })

        from jinja2 import Template

        template = Template(PYTHON_TEMPLATE)
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", verdict.flow_id)
        rendered = template.render(
            flow_title=flow_title,
            flow_id_slug=slug,
            bug_title=verdict.title or "Regression caught by DiffQA",
            severity=verdict.severity.value if verdict.severity else "HIGH",
            root_cause=(verdict.root_cause_hypothesis or "State or visual regression").replace("\n", " "),
            initial_url=initial_url,
            steps=formatted_steps,
            invariants=invariants or [],
        )

        if output_file:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")

        return rendered

