"""Authentication manager for detecting, executing, and persisting web application logins."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from rich.console import Console
from playwright.async_api import Page

from diffqa.agent.browser import BrowserController

logger = logging.getLogger("diffqa.auth")
console = Console()

LOGIN_DETECTION_SCRIPT = r"""
() => {
    const hasPasswordField = !!document.querySelector('input[type="password"]');
    const hasLoginKeywordInUrl = /login|signin|auth|session|sign-in/i.test(window.location.href);
    const hasLoginForm = !!document.querySelector('form input[type="password"]') || 
                         !!document.querySelector('form[action*="login"], form[action*="auth"], form[id*="login"]');
    
    // Locate candidate username/email and password fields
    const userField = document.querySelector('input[type="email"], input[type="text"][name*="user"], input[type="text"][name*="email"], input[name="username"], input[id*="user"], input[id*="email"]');
    const passField = document.querySelector('input[type="password"]');
    const submitBtn = document.querySelector('button[type="submit"], input[type="submit"]') ||
        Array.from(document.querySelectorAll('button')).find(b => /^\s*(sign in|log in|login)\s*$/i.test(b.innerText));
    const cssFor = (el, fallback) => el.id ? `#${CSS.escape(el.id)}` : (el.name ? `[name="${CSS.escape(el.name)}"]` : fallback);

    return {
        is_login_page: hasPasswordField || (hasLoginKeywordInUrl && hasLoginForm),
        has_password_field: hasPasswordField,
        has_user_field: !!userField,
        user_selector: userField ? cssFor(userField, 'input[type="email"]') : null,
        pass_selector: passField ? cssFor(passField, 'input[type="password"]') : null,
        submit_selector: submitBtn ? cssFor(submitBtn, 'button[type="submit"]') : null,
        current_url: window.location.href
    };
}
"""


class AuthManager:
    """Detects login barriers, orchestrates interactive or automated authentication, and caches storage state."""

    def __init__(self, auth_state_path: Optional[Path | str] = None):
        self.auth_state_path = Path(auth_state_path) if auth_state_path else Path(".diffqa_auth.json")

    def has_cached_session(self) -> bool:
        """Check if a previously saved session file exists."""
        return self.auth_state_path.exists() and self.auth_state_path.stat().st_size > 50

    async def check_login_required(self, page: Page) -> Dict[str, Any]:
        """Detect whether the current page is a login / authentication barrier."""
        try:
            res = await page.evaluate(LOGIN_DETECTION_SCRIPT)
            return res
        except Exception as e:
            logger.debug(f"Login detection script error: {e}")
            return {"is_login_page": False}

    async def handle_login(
        self,
        controller: BrowserController,
        username: Optional[str] = None,
        password: Optional[str] = None,
        wait_interactive: bool = True,
        max_wait_seconds: int = 120,
    ) -> bool:
        """Log in automatically or wait for a human; return True only if a login actually completed."""
        page = controller.page
        status = await self.check_login_required(page)

        if not status.get("is_login_page"):
            logger.info("No login barrier detected on the page. Proceeding.")
            return False

        console.print(f"\n[bold yellow][Auth] Login Page Detected:[/bold yellow] [cyan]{page.url}[/cyan]")

        # 1. Automated login if username & password provided
        if username and password and status.get("user_selector") and status.get("pass_selector"):
            console.print(f"[Agent] Attempting automated login for user: [green]{username}[/green]...")
            try:
                await page.fill(status["user_selector"], username)
                await page.fill(status["pass_selector"], password)
                
                submit_selector = status.get("submit_selector") or 'button[type="submit"]'
                try:
                    await page.click(submit_selector, timeout=5000)
                except Exception:
                    await page.keyboard.press("Enter")

                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                # Verify login succeeded
                new_status = await self.check_login_required(page)
                if not new_status.get("has_password_field"):
                    console.print("[bold green][Auth] Automated login succeeded![/bold green]")
                    await self.save_storage_state(controller)
                    return True
                else:
                    console.print("[bold red]Automated login form did not clear. Falling back to interactive login...[/bold red]")
            except Exception as err:
                console.print(f"[bold red]Automated login error: {err}. Falling back to interactive login...[/bold red]")

        # 2. Interactive login: wait for human to complete login in the browser
        if wait_interactive:
            console.print(
                "\n"
                "┌────────────────────────────────────────────────────────────────────────┐\n"
                "│ [bold cyan]>> INTERACTIVE LOGIN REQUIRED[/bold cyan]                                            │\n"
                "│ Please log in to the web application in the open browser window.       │\n"
                "│ (SSO, Google, MFA, CAPTCHA, or custom credentials are all supported).   │\n"
                f"│ DiffQA detects completion automatically (timeout: {max_wait_seconds:>3}s).             │\n"
                "└────────────────────────────────────────────────────────────────────────┘\n"
            )

            # Polling loop: detect when URL leaves login page or password field disappears
            start_url = page.url
            for _ in range(max_wait_seconds):
                await asyncio.sleep(1)
                try:
                    cur_status = await self.check_login_required(page)
                    # If URL changed from login and no password input visible, user logged in!
                    if not cur_status.get("has_password_field") and page.url != start_url:
                        console.print(f"\n[bold green][Auth] Login completed! Navigated to:[/bold green] [cyan]{page.url}[/cyan]")
                        await asyncio.sleep(1)
                        await self.save_storage_state(controller)
                        return True
                except Exception:
                    pass

            # Don't cache a logged-out session: it would be silently reused on every later run.
            console.print("[bold red][Auth] Login not detected before timeout; continuing unauthenticated.[/bold red]")

        return False

    async def save_storage_state(self, controller: BrowserController) -> None:
        """Persist session cookies and localStorage to disk so user doesn't need to re-login."""
        try:
            self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
            await controller.save_storage_state(self.auth_state_path)
            console.print(f"💾 [dim]Saved authentication session to {self.auth_state_path}[/dim]")
        except Exception as e:
            logger.warning(f"Could not save storage state: {e}")
