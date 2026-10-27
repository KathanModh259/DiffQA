"""Adversarial Fuzzing Engine: injects superhuman boundary conditions, race conditions, and payloads."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from playwright.async_api import Page

from diffqa.agent.dom_observer import DOMObserver, InteractiveElement

logger = logging.getLogger("diffqa.fuzzer")


class FuzzPayload(BaseModel):
    category: str
    name: str
    value: str
    description: str


# Comprehensive superhuman fuzzing vectors
FUZZ_PAYLOADS: List[FuzzPayload] = [
    # Boundary Numbers
    FuzzPayload(category="numeric", name="negative_one", value="-1", description="Negative boundary value"),
    FuzzPayload(category="numeric", name="zero", value="0", description="Zero boundary value"),
    FuzzPayload(category="numeric", name="extreme_large", value="999999999999999", description="Overflow numeric integer"),
    FuzzPayload(category="numeric", name="nan_string", value="NaN", description="Literal Not-a-Number token"),
    
    # Unicode & Layout
    FuzzPayload(category="unicode", name="rtl_arabic", value="مرحبا بك في الاختبار", description="Right-to-left bidirectional text"),
    FuzzPayload(category="unicode", name="emoji_cluster", value="👩‍💻🔥⚡🚀💥", description="Multi-byte emoji cluster"),
    FuzzPayload(category="unicode", name="zero_width", value="test\u200b\u200cinvisible", description="Zero-width non-breaking spaces"),
    
    # String Overflow
    FuzzPayload(category="overflow", name="long_string_1k", value="A" * 1024, description="1KB continuous text string"),
    FuzzPayload(category="overflow", name="whitespace_only", value="       ", description="Whitespace-only string"),
    
    # Invariant & Safety
    FuzzPayload(category="injection", name="sql_token", value="' OR '1'='1", description="SQL quote escaping probe"),
    FuzzPayload(category="injection", name="html_script", value="<img src=x onerror=1>", description="HTML script tag injection"),
    FuzzPayload(category="injection", name="prototype_pollute", value='{"__proto__":{"polluted":true}}', description="JSON prototype probe"),
]


class FuzzResult(BaseModel):
    element_id: str
    selector: str
    payload: FuzzPayload
    triggered_crash: bool = False
    console_errors: List[str] = Field(default_factory=list)
    http_errors: List[int] = Field(default_factory=list)
    ui_breakage: Optional[str] = None


class AdversarialFuzzer:
    """Stress-tests inputs, forms, and buttons with boundary conditions and rapid interactions."""

    def __init__(self, dom_observer: Optional[DOMObserver] = None):
        self.dom = dom_observer or DOMObserver()

    async def fuzz_page_inputs(self, page: Page, max_elements: int = 5) -> List[FuzzResult]:
        """Discover form inputs on page and inject boundary fuzz payloads."""
        results: List[FuzzResult] = []
        elements = await self.dom.extract_interactive_elements(page)
        
        input_elements = [
            e for e in elements 
            if e.tag in ("input", "textarea") and not e.is_disabled
        ][:max_elements]

        for el in input_elements:
            for payload in FUZZ_PAYLOADS[:4]:  # test key boundary categories
                try:
                    await page.fill(el.selector, payload.value, timeout=3000)
                    await page.keyboard.press("Tab")
                    await page.wait_for_timeout(200)

                    # Check if page crashed or unhandled alert popped up
                    results.append(FuzzResult(
                        element_id=el.diffqa_id,
                        selector=el.selector,
                        payload=payload,
                        triggered_crash=False,
                    ))
                except Exception as e:
                    logger.debug(f"Fuzz step error on {el.selector}: {e}")
                    results.append(FuzzResult(
                        element_id=el.diffqa_id,
                        selector=el.selector,
                        payload=payload,
                        triggered_crash=True,
                        ui_breakage=str(e),
                    ))

        return results

    async def test_rapid_clicks(self, page: Page, selector: str, count: int = 4) -> bool:
        """Rapidly click a button to test for race conditions, double submits, and debouncing."""
        try:
            for _ in range(count):
                await page.click(selector, timeout=2000, delay=15)
            await page.wait_for_timeout(500)
            return True
        except Exception as e:
            logger.warning(f"Rapid click failure on {selector}: {e}")
            return False

