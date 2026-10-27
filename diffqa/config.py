"""Configuration management for DiffQA."""

from pathlib import Path
from typing import Optional, Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Provider & Model Settings
    provider: Literal["meta", "fireworks", "baseten", "modal", "ollama", "mock"] = "meta"
    
    meta_api_key: Optional[str] = Field(default=None, alias="META_API_KEY")
    meta_api_base: str = Field(default="https://api.meta.com/v1", alias="META_API_BASE")
    
    fireworks_api_key: Optional[str] = Field(default=None, alias="FIREWORKS_API_KEY")
    baseten_api_key: Optional[str] = Field(default=None, alias="BASETEN_API_KEY")
    modal_api_key: Optional[str] = Field(default=None, alias="MODAL_API_KEY")
    
    # Model identifiers
    code_model: str = Field(default="meta/muse-code", alias="DIFFQA_CODE_MODEL")
    agent_model: str = Field(default="meta/muse-spark", alias="DIFFQA_AGENT_MODEL")
    judge_model: str = Field(default="meta/muse-wonder", alias="DIFFQA_JUDGE_MODEL")

    # Playwright Browser Settings
    headless: bool = True
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    viewport_width: int = 1280
    viewport_height: int = 800
    action_timeout_ms: int = 10000
    navigation_timeout_ms: int = 30000
    max_steps_per_flow: int = 20

    # Output / Artifact directories
    output_dir: Path = Path("./diffqa_output")


def get_settings() -> Settings:
    """Return an instantiated Settings object."""
    return Settings()

