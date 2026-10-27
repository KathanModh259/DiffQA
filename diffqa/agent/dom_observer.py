"""DOM perception and interactive element extraction using Playwright."""

import asyncio
from typing import List, Optional
from pydantic import BaseModel
from playwright.async_api import Page


class InteractiveElement(BaseModel):
    diffqa_id: str
    tag: str
    role: Optional[str] = None
    text: Optional[str] = None
    value: Optional[str] = None
    placeholder: Optional[str] = None
    aria_label: Optional[str] = None
    name: Optional[str] = None
    is_disabled: bool = False
    selector: str


DOM_EXTRACTION_SCRIPT = """
() => {
    const isVisible = (el) => {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const candidates = document.querySelectorAll(
        'button, a, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [role="menuitem"], [onclick], [data-testid]'
    );

    const results = [];
    let counter = 0;

    candidates.forEach((el) => {
        if (!isVisible(el)) return;

        let diffqaId = el.getAttribute('data-diffqa-id');
        if (!diffqaId) {
            diffqaId = el.id || el.getAttribute('data-testid') || `elem-${counter++}`;
            el.setAttribute('data-diffqa-id', diffqaId);
        }

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const text = (el.innerText || el.textContent || '').trim().slice(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const name = el.getAttribute('name') || '';
        const value = el.value || '';
        const isDisabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';

        // Build a resilient selector
        let selector = `[data-diffqa-id="${diffqaId}"]`;
        if (el.id) {
            selector = `#${el.id}`;
        } else if (el.getAttribute('data-testid')) {
            selector = `[data-testid="${el.getAttribute('data-testid')}"]`;
        }

        results.push({
            diffqa_id: diffqaId,
            tag: tag,
            role: role || undefined,
            text: text || undefined,
            value: value || undefined,
            placeholder: placeholder || undefined,
            aria_label: ariaLabel || undefined,
            name: name || undefined,
            is_disabled: isDisabled,
            selector: selector
        });
    });

    return results;
}
"""


class DOMObserver:
    """Observes and summarizes interactive elements on a Playwright page."""

    async def extract_interactive_elements(self, page: Page, max_retries: int = 3) -> List[InteractiveElement]:
        """Execute extraction script on the page and parse interactive elements with navigation retry."""
        for attempt in range(max_retries):
            try:
                raw_elements = await page.evaluate(DOM_EXTRACTION_SCRIPT)
                return [InteractiveElement(**elem) for elem in raw_elements]
            except Exception as e:
                err_msg = str(e).lower()
                if ("execution context was destroyed" in err_msg or "navigating" in err_msg or "navigation" in err_msg) and attempt < max_retries - 1:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)
                    continue
                # If last attempt or unexpected error, log and return empty list rather than crashing
                if attempt == max_retries - 1:
                    return []
                raise

    def format_elements_for_prompt(self, elements: List[InteractiveElement]) -> str:
        """Format elements into a readable text list for the LLM prompt."""
        if not elements:
            return "No interactive elements detected on page."

        lines = []
        for el in elements:
            parts = [f"[{el.diffqa_id}] <{el.tag}>"]
            if el.role:
                parts.append(f'role="{el.role}"')
            if el.text:
                parts.append(f'text="{el.text}"')
            if el.placeholder:
                parts.append(f'placeholder="{el.placeholder}"')
            if el.aria_label:
                parts.append(f'aria-label="{el.aria_label}"')
            if el.value:
                parts.append(f'value="{el.value}"')
            if el.is_disabled:
                parts.append("(DISABLED)")
            lines.append(" ".join(parts))
        return "\n".join(lines)

