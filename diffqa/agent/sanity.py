"""Deterministic checks on rendered page text that any human tester would flag on sight.

These run after every agent step, with no model involved. "Hard" findings almost never
appear in a healthy UI; "soft" findings are plausible in some apps (a refund shows negative
money) and are passed to the judge as warnings rather than failing a flow on their own.
"""

import re
from typing import List, Tuple

# (pattern, label, hard)
_CHECKS: List[Tuple[re.Pattern, str, bool]] = [
    (re.compile(r"\[object Object\]"), "unrendered object ('[object Object]')", True),
    (re.compile(r"(?<![\w.])NaN(?![\w.])"), "NaN rendered in page text", True),
    (re.compile(r"[$€£¥₹]\s?NaN|NaN\s?[$€£¥₹%]"), "NaN rendered as a price or percentage", True),
    (re.compile(r"(?<![\w/.-])undefined(?![\w/.-])"), "'undefined' rendered in page text", False),
    (re.compile(r"(?<![\w/.-])null(?![\w/.-])"), "'null' rendered in page text", False),
    (re.compile(r"(?<![\w.])Infinity(?![\w.])"), "'Infinity' rendered in page text", False),
    (re.compile(r"-\s?[$€£¥₹]\s?\d|[$€£¥₹]\s?-\s?\d"), "negative currency amount", False),
    (re.compile(r"\b(Internal Server Error|Application error|Something went wrong|Unhandled Runtime Error|"
                r"Cannot (GET|POST) /|TypeError:|ReferenceError:|Traceback \(most recent call last\))\b"),
     "error page or stack trace text visible", False),
    (re.compile(r"\{\{\s*[\w.]+\s*\}\}|\$\{[\w.]+\}"), "unrendered template placeholder", True),
    (re.compile(r"\b(lorem ipsum|TODO|FIXME)\b", re.IGNORECASE), "placeholder/debug text left in UI", False),
]


class SanityFinding:
    __slots__ = ("label", "snippet", "hard")

    def __init__(self, label: str, snippet: str, hard: bool):
        self.label, self.snippet, self.hard = label, snippet, hard

    def __str__(self) -> str:
        return f"{self.label}: '{self.snippet}'"


def check_page_text(text: str) -> List[SanityFinding]:
    """Return one finding per triggered check, with a short snippet of surrounding text."""
    findings: List[SanityFinding] = []
    for pattern, label, hard in _CHECKS:
        m = pattern.search(text)
        if m:
            start, end = max(0, m.start() - 25), min(len(text), m.end() + 25)
            snippet = " ".join(text[start:end].split())
            findings.append(SanityFinding(label, snippet, hard))
    return findings
