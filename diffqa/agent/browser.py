"""Browser controller managing Playwright instance, diagnostics listeners, and actions."""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from diffqa.agent.actions import AgentAction, ActionType
from diffqa.config import Settings, get_settings

logger = logging.getLogger("diffqa.browser")


class BrowserController:
    """Controls a Playwright browser session with diagnostic event listening."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        self.console_logs: List[Dict[str, str]] = []
        self.network_errors: List[Dict[str, Any]] = []
        self.page_errors: List[str] = []

    async def start(self, storage_state_path: Optional[Path | str] = None) -> None:
        """Initialize the Playwright engine and launch browser, optionally loading cached session cookies."""
        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, self.settings.browser_type)
        self._browser = await browser_type.launch(
            headless=self.settings.headless,
        )
        context_kwargs: Dict[str, Any] = {
            "viewport": {
                "width": self.settings.viewport_width,
                "height": self.settings.viewport_height,
            }
        }
        if storage_state_path and Path(storage_state_path).exists():
            context_kwargs["storage_state"] = str(storage_state_path)
            logger.info(f"Loaded cached browser authentication state from {storage_state_path}")

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()
        self._attach_listeners()

    async def save_storage_state(self, path: Path | str) -> None:
        """Save browser session cookies and localStorage to disk."""
        if not self._context:
            raise RuntimeError("Browser context not started.")
        save_path = Path(path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(save_path))

    def _attach_listeners(self) -> None:
        """Attach listeners for console errors, page errors, and network issues."""
        if not self._page:
            return

        def on_console(msg):
            entry = {"type": msg.type, "text": msg.text}
            self.console_logs.append(entry)
            if msg.type == "error":
                logger.warning(f"Browser console error: {msg.text}")

        def on_page_error(error):
            self.page_errors.append(str(error))
            logger.error(f"Browser page uncaught exception: {error}")

        def on_request_failed(request):
            failure = request.failure
            self.network_errors.append({
                "url": request.url,
                "method": request.method,
                "error": failure if failure else "Failed",
            })

        def on_response(response):
            if response.status >= 400:
                self.network_errors.append({
                    "url": response.url,
                    "method": response.request.method,
                    "status": response.status,
                    "status_text": response.status_text,
                })

        self._page.on("console", on_console)
        self._page.on("pageerror", on_page_error)
        self._page.on("requestfailed", on_request_failed)
        self._page.on("response", on_response)

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser page not started. Call start() first.")
        return self._page

    async def navigate(self, url: str) -> None:
        """Navigate to a target URL."""
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.settings.navigation_timeout_ms)
        await self.page.wait_for_timeout(500)

    async def take_screenshot(self, output_path: Path | str) -> str:
        """Capture screenshot and save to disk."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path), full_page=False)
        return str(path)

    async def execute_action(self, action: AgentAction, selector_fallback: Optional[str] = None) -> None:
        """Execute the requested action against the page."""
        target_selector = action.selector
        if not target_selector and action.target_id:
            target_selector = f"[data-diffqa-id='{action.target_id}']"
        if not target_selector and selector_fallback:
            target_selector = selector_fallback

        timeout = self.settings.action_timeout_ms

        if action.action == ActionType.NAVIGATE:
            if action.value:
                await self.navigate(action.value)
        elif action.action == ActionType.CLICK:
            if not target_selector:
                raise ValueError("Target selector or target_id required for click action.")
            await self.page.click(target_selector, timeout=timeout)
            await self.page.wait_for_timeout(500)
        elif action.action == ActionType.FILL:
            if not target_selector:
                raise ValueError("Target selector or target_id required for fill action.")
            await self.page.fill(target_selector, action.value or "", timeout=timeout)
        elif action.action == ActionType.SCROLL:
            pixels = int(action.value) if action.value and action.value.isdigit() else 400
            await self.page.evaluate(f"window.scrollBy(0, {pixels});")
            await self.page.wait_for_timeout(200)
        elif action.action == ActionType.PRESS:
            key = action.value or "Enter"
            if target_selector:
                await self.page.press(target_selector, key, timeout=timeout)
            else:
                await self.page.keyboard.press(key)
        elif action.action == ActionType.WAIT:
            ms = int(action.value) if action.value and action.value.isdigit() else 1000
            await self.page.wait_for_timeout(ms)
        elif action.action == ActionType.ASSERT:
            # Assertion action handled in execution step
            pass

    def drain_diagnostics(self) -> Dict[str, Any]:
        """Collect and clear pending logs and errors since last step."""
        res = {
            "console": list(self.console_logs),
            "network": list(self.network_errors),
            "page_errors": list(self.page_errors),
        }
        self.console_logs.clear()
        self.network_errors.clear()
        self.page_errors.clear()
        return res

    async def close(self) -> None:
        """Gracefully close browser and playwright instances."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

