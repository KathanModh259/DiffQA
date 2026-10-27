"""Configuration management for DiffQA.

Loads settings from environment variables and .env files using Pydantic.
Supports Meta Model API, Fireworks, Baseten, Modal, Ollama, and a zero-cost
mock provider for offline development and testing.
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration with environment variable binding."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Provider Selection ──────────────────────────────────────────────
    provider: Literal["meta", "fireworks", "baseten", "modal", "ollama", "mock"] = Field(
        default="meta",
        alias="DIFFQA_PROVIDER",
        description="Inference provider to use for LLM completions.",
    )

    # ── API Keys ────────────────────────────────────────────────────────
    meta_api_key: Optional[str] = Field(default=None, alias="META_API_KEY")
    meta_api_base: str = Field(default="https://api.meta.com/v1", alias="META_API_BASE")
    fireworks_api_key: Optional[str] = Field(default=None, alias="FIREWORKS_API_KEY")
    baseten_api_key: Optional[str] = Field(default=None, alias="BASETEN_API_KEY")
    modal_api_key: Optional[str] = Field(default=None, alias="MODAL_API_KEY")

    # ── Model Identifiers ──────────────────────────────────────────────
    code_model: str = Field(
        default="meta/muse-code",
        alias="DIFFQA_CODE_MODEL",
        description="Model for diff analysis and flow planning.",
    )
    agent_model: str = Field(
        default="meta/muse-spark",
        alias="DIFFQA_AGENT_MODEL",
        description="Model for autonomous browser agent action selection.",
    )
    judge_model: str = Field(
        default="meta/muse-wonder",
        alias="DIFFQA_JUDGE_MODEL",
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

    # ── Output ─────────────────────────────────────────────────────────
    output_dir: Path = Path("./diffqa_output")


def get_settings() -> Settings:
    """Return a Settings instance loaded from the environment."""
    return Settings()
