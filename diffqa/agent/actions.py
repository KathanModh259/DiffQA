"""Action definitions and Playwright execution primitives for the browser agent."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ActionType(str, Enum):
    CLICK = "click"
    FILL = "fill"
    NAVIGATE = "navigate"
    SCROLL = "scroll"
    PRESS = "press"
    WAIT = "wait"
    ASSERT = "assert"


class AgentAction(BaseModel):
    thought: str = Field(description="Reasoning behind choosing this action")
    action: ActionType
    target_id: Optional[str] = Field(default=None, description="DiffQA element ID (e.g. 'elem-12' or 'btn-checkout')")
    selector: Optional[str] = Field(default=None, description="CSS / XPath / Playwright selector")
    value: Optional[str] = Field(default=None, description="Text to fill, key to press, URL to navigate, or text to assert")
    is_terminal: bool = Field(default=False, description="True if the test flow goal is satisfied or aborted")


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

