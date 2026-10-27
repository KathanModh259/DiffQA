"""Configuration management for DiffQA.

Loads settings from environment variables and .env files using Pydantic.
Supports Meta Model API, Fireworks, Baseten, Modal, Ollama, and a zero-cost
mock provider for offline development and testing.
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration with environment variable binding."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Fields list their own name first in AliasChoices so Settings(provider=...) beats .env;
    # with a plain alias the .env value silently won and every CLI --provider flag was ignored.

    # ── Provider Selection ──────────────────────────────────────────────
    provider: Literal["meta", "fireworks", "baseten", "modal", "ollama", "mock"] = Field(
        default="meta",
        validation_alias=AliasChoices("provider", "DIFFQA_PROVIDER"),
        description="Inference provider to use for LLM completions.",
    )

    # ── API Keys ────────────────────────────────────────────────────────
    # Meta's docs call the key MODEL_API_KEY; META_API_KEY is accepted too.
    meta_api_key: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("meta_api_key", "META_API_KEY", "MODEL_API_KEY")
    )
    meta_api_base: str = Field(default="https://api.meta.ai/v1", validation_alias=AliasChoices("meta_api_base", "META_API_BASE"))
    fireworks_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("fireworks_api_key", "FIREWORKS_API_KEY"))
    baseten_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("baseten_api_key", "BASETEN_API_KEY"))
    modal_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("modal_api_key", "MODAL_API_KEY"))
    modal_api_base: Optional[str] = Field(default=None, validation_alias=AliasChoices("modal_api_base", "MODAL_API_BASE"))
    ollama_api_base: str = Field(
        default="http://localhost:11434/api/chat", validation_alias=AliasChoices("ollama_api_base", "OLLAMA_API_BASE")
    )

    # ── Model Identifiers ──────────────────────────────────────────────
    code_model: str = Field(
        default="muse-spark-1.3",
        validation_alias=AliasChoices("code_model", "DIFFQA_CODE_MODEL"),
        description="Model for diff analysis and flow planning.",
    )
    agent_model: str = Field(
        default="muse-spark-1.3",
        validation_alias=AliasChoices("agent_model", "DIFFQA_AGENT_MODEL"),
        description="Model for autonomous browser agent action selection.",
    )
    judge_model: str = Field(
        default="muse-spark-1.3",
        validation_alias=AliasChoices("judge_model", "DIFFQA_JUDGE_MODEL"),
        description="Model for regression oracle verdicts.",
    )

    # ── Playwright Browser Settings ────────────────────────────────────
    headless: bool = Field(default=True, description="Run Playwright browser in headless mode.")
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    viewport_width: int = 1280
    viewport_height: int = 800
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 30_000
    max_steps_per_flow: int = 25
    flow_timeout_seconds: int = 300
    # Failed flows are re-run once so the report can say whether the bug reproduced.
    rerun_failures: bool = True

    # ── Output ─────────────────────────────────────────────────────────
    output_dir: Path = Path("./diffqa_output")


def get_settings() -> Settings:
    """Return a Settings instance loaded from the environment."""
    return Settings()
