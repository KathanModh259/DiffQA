"""Tests for AuthManager and SiteExplorer."""

import pytest
from pathlib import Path
from diffqa.agent.auth import AuthManager
from diffqa.config import Settings
from diffqa.llm.client import LLMClient
from diffqa.agent.explorer import SiteExplorer


class TestAuthManager:

    def test_has_cached_session_false_when_missing(self, tmp_path):
        auth = AuthManager(auth_state_path=tmp_path / "nonexistent.json")
        assert auth.has_cached_session() is False

    def test_has_cached_session_true_when_valid(self, tmp_path):
        state_file = tmp_path / "auth.json"
        state_file.write_text('{"cookies": [{"name": "session", "value": "12345"}]}')
        auth = AuthManager(auth_state_path=state_file)
        assert auth.has_cached_session() is True


class TestSiteExplorerPlanning:

    @pytest.mark.asyncio
    async def test_plan_site_flows(self):
        settings = Settings(provider="mock")
        llm = LLMClient(settings=settings)
        explorer = SiteExplorer(settings=settings, llm_client=llm)

        plan = await explorer._plan_site_flows(
            url="https://example.com",
            title="Example Store",
            elements_summary="[btn-1] <button> 'Add to Cart'",
            custom_goal="Test shopping cart",
        )

        assert plan.summary != ""
        assert len(plan.flows) >= 1
        assert any(f.flow_type in ("happy_path", "adversarial") for f in plan.flows)

