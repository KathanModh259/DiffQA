"""Action definitions and Playwright execution primitives for the browser agent."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionType(str, Enum):
    CLICK = "click"
    FILL = "fill"
    NAVIGATE = "navigate"
    SCROLL = "scroll"
    PRESS = "press"
    WAIT = "wait"
    ASSERT = "assert"


# Synonyms models commonly use for the supported actions.
_ACTION_ALIASES = {"type": "fill", "input": "fill", "enter_text": "fill", "goto": "navigate", "go_to": "navigate",
                   "open": "navigate", "tap": "click", "keypress": "press", "key": "press", "sleep": "wait",
                   "verify": "assert", "check": "assert", "done": "wait", "finish": "wait"}


class AgentAction(BaseModel):
    # Numbers for value ("300") and target ids (12) are common in model output.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    thought: str = Field(default="", description="Reasoning behind choosing this action")
    action: ActionType
    target_id: Optional[str] = Field(default=None, description="DiffQA element ID (e.g. 'elem-12' or 'btn-checkout')")
    selector: Optional[str] = Field(default=None, description="CSS / XPath / Playwright selector")
    value: Optional[str] = Field(default=None, description="Text to fill, key to press, URL to navigate, or text to assert")
    is_terminal: bool = Field(default=False, description="True if the test flow goal is satisfied or aborted")

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v: object) -> str:
        if isinstance(v, ActionType):
            return v.value
        v = str(v or "").strip().lower().replace(" ", "_")
        return _ACTION_ALIASES.get(v, v)

    @field_validator("thought", mode="before")
    @classmethod
    def _thought(cls, v: object) -> str:
        return "" if v is None else str(v)


class ExecutionStep(BaseModel):
    step_number: int
    action: AgentAction
    url_before: str
    url_after: str = ""
    screenshot_path: Optional[str] = None
    console_logs: List[Dict[str, str]] = Field(default_factory=list)
    network_errors: List[Dict[str, Any]] = Field(default_factory=list)
    success: bool = True
    error_message: Optional[str] = None
    duration_ms: float = 0.0
