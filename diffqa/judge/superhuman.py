"""Superhuman QA Oracles: detects invisible micro-regressions, layout shifts, network contract violations, a11y failures, and runtime anomalies that human testers miss."""

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from playwright.async_api import Page

logger = logging.getLogger("diffqa.superhuman")

SUPERHUMAN_DOM_AUDIT_SCRIPT = """
() => {
    const issues = [];
    const docWidth = document.documentElement.clientWidth;
    const docHeight = document.documentElement.clientHeight;

    // 1. Horizontal Scroll & Viewport Overflow (Mobile Responsive Breakage)
    if (document.documentElement.scrollWidth > docWidth + 3) {
        issues.push({
            category: "layout_overflow",
            severity: "HIGH",
            message: `Page width (${document.documentElement.scrollWidth}px) exceeds viewport width (${docWidth}px), causing horizontal scroll breakage.`,
            details: { scrollWidth: document.documentElement.scrollWidth, clientWidth: docWidth }
        });
    }

    // 2. Broken Image Assets
    const images = Array.from(document.querySelectorAll('img'));
    images.forEach(img => {
        if (img.complete && img.naturalWidth === 0 && img.src && !img.src.startsWith('data:')) {
            issues.push({
                category: "broken_asset",
                severity: "MEDIUM",
                message: `Broken image asset detected: ${img.src.slice(0, 80)}`,
                selector: img.id ? `#${img.id}` : (img.className ? `.${img.className.split(' ')[0]}` : 'img')
            });
        }
    });

    // 3. Accessibility & Usability (WCAG)
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], a[href]'));
    buttons.forEach(btn => {
        const text = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || btn.getAttribute('title') || '').trim();
        if (!text && !btn.querySelector('svg, img')) {
            issues.push({
                category: "accessibility",
                severity: "MEDIUM",
                message: "Interactive button has no text, icon, or aria-label.",
                selector: btn.id ? `#${btn.id}` : btn.tagName.toLowerCase()
            });
        }
    });

    // 4. Form Inputs without Labels
    const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea'));
    inputs.forEach(inp => {
        const id = inp.id;
        const hasLabel = id ? !!document.querySelector(`label[for="${id}"]`) : false;
        const hasAria = !!inp.getAttribute('aria-label') || !!inp.getAttribute('aria-labelledby');
        const hasPlaceholder = !!inp.getAttribute('placeholder');
        if (!hasLabel && !hasAria && !hasPlaceholder) {
            issues.push({
                category: "accessibility",
                severity: "LOW",
                message: `Input field has no associated <label>, aria-label, or placeholder.`,
                selector: id ? `#${id}` : `input[name="${inp.name || ''}"]`
            });
        }
    });

    // 5. DOM Bloat & Performance
    const totalNodes = document.querySelectorAll('*').length;
    if (totalNodes > 1500) {
        issues.push({
            category: "performance",
            severity: "LOW",
            message: `High DOM node count (${totalNodes} nodes) may cause performance and rendering lag.`,
            details: { nodeCount: totalNodes }
        });
    }

    // 6. Text Clipping / Truncation Overflow
    const cards = Array.from(document.querySelectorAll('p, span, h1, h2, h3, h4, h5, div.card, [class*="card"]'));
    let clippedCount = 0;
    cards.slice(0, 50).forEach(el => {
        const style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' && (el.scrollHeight > el.clientHeight + 4 || el.scrollWidth > el.clientWidth + 4)) {
            clippedCount++;
        }
    });
    if (clippedCount > 0) {
        issues.push({
            category: "text_clipping",
            severity: "MEDIUM",
            message: `${clippedCount} elements detected with overflow:hidden where text or content is clipped.`,
            details: { clippedCount }
        });
    }

    return {
        issues: issues,
        nodeCount: totalNodes,
        pageTitle: document.title,
        url: window.location.href
    };
}
"""


class SuperhumanIssue(BaseModel):
    category: str
    severity: str
    message: str
    selector: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class SuperhumanAuditReport(BaseModel):
    url: str
    total_issues: int
    critical_issues: int
    high_issues: int
    medium_issues: int
    low_issues: int
    issues: List[SuperhumanIssue] = Field(default_factory=list)
    quality_score: int = 100
    network_anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    slow_requests: List[Dict[str, Any]] = Field(default_factory=list)
    unhandled_errors: List[str] = Field(default_factory=list)


class SuperhumanOracle:
    """Evaluates applications for issues invisible to human testers."""

    async def audit_page(
        self,
        page: Page,
        network_errors: Optional[List[Dict[str, Any]]] = None,
        console_errors: Optional[List[str]] = None,
    ) -> SuperhumanAuditReport:
        """Execute superhuman inspection on the active Playwright page."""
        network_errors = network_errors or []
        console_errors = console_errors or []

        raw_audit = await page.evaluate(SUPERHUMAN_DOM_AUDIT_SCRIPT)
        issues: List[SuperhumanIssue] = [
            SuperhumanIssue(**item) for item in raw_audit.get("issues", [])
        ]

        # Audit network errors
        for net in network_errors:
            status = net.get("status", 0)
            url = net.get("url", "")
            if status >= 500:
                issues.append(SuperhumanIssue(
                    category="api_server_error",
                    severity="CRITICAL",
                    message=f"Server returned HTTP {status} on API request: {url}",
                    details=net,
                ))
            elif status >= 400:
                issues.append(SuperhumanIssue(
                    category="api_client_error",
                    severity="HIGH",
                    message=f"Client API failure HTTP {status} on {url}",
                    details=net,
                ))

        # Audit console errors
        for err in console_errors:
            issues.append(SuperhumanIssue(
                category="runtime_exception",
                severity="CRITICAL",
                message=f"Uncaught JavaScript runtime exception: {err[:120]}",
                details={"full_error": err},
            ))

        # Calculate quality score (starts at 100, drops per severity)
        crit = sum(1 for i in issues if i.severity == "CRITICAL")
        high = sum(1 for i in issues if i.severity == "HIGH")
        med = sum(1 for i in issues if i.severity == "MEDIUM")
        low = sum(1 for i in issues if i.severity == "LOW")

        penalty = (crit * 30) + (high * 15) + (med * 8) + (low * 3)
        quality_score = max(0, min(100, 100 - penalty))

        return SuperhumanAuditReport(
            url=raw_audit.get("url", page.url),
            total_issues=len(issues),
            critical_issues=crit,
            high_issues=high,
            medium_issues=med,
            low_issues=low,
            issues=issues,
            quality_score=quality_score,
            network_anomalies=network_errors,
            unhandled_errors=console_errors,
        )

