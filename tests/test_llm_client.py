"""Tests for LLMClient — mock provider, JSON parsing, and provider dispatch."""

import json
import pytest

from diffqa.config import Settings
from diffqa.llm.client import LLMClient


@pytest.fixture
def mock_client():
    return LLMClient(settings=Settings(provider="mock"))


class TestMockProvider:

    @pytest.mark.asyncio
    async def test_flow_planner_response(self, mock_client):
        messages = [{"role": "user", "content": "Plan user flows for the cart diff"}]
        result = await mock_client.complete(messages)
        data = json.loads(result)
        assert "summary" in data
        assert "flows" in data
        assert len(data["flows"]) >= 1

    @pytest.mark.asyncio
    async def test_agent_response(self, mock_client):
        messages = [{"role": "user", "content": "Choose the next action. Available interactive elements: ..."}]
        result = await mock_client.complete(messages)
        data = json.loads(result)
        assert "action" in data
        assert "thought" in data

    @pytest.mark.asyncio
    async def test_judge_response(self, mock_client):
        messages = [{"role": "user", "content": "Evaluate whether this is a regression"}]
        result = await mock_client.complete(messages)
        data = json.loads(result)
        assert "verdict" in data

    @pytest.mark.asyncio
    async def test_fallback_response(self, mock_client):
        messages = [{"role": "user", "content": "random unmatched prompt"}]
        result = await mock_client.complete(messages)
        data = json.loads(result)
        assert "status" in data


class TestCompleteJson:

    @pytest.mark.asyncio
    async def test_returns_dict(self, mock_client):
        messages = [{"role": "user", "content": "Plan user flows"}]
        data = await mock_client.complete_json(messages)
        assert isinstance(data, dict)
        assert "flows" in data

    @pytest.mark.asyncio
    async def test_parses_fenced_json(self):
        raw = '```json\n{"key": "value"}\n```'
        result = LLMClient._parse_json(raw)
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_parses_embedded_json(self):
        raw = 'Here is the result: {"key": 42} -- done'
        result = LLMClient._parse_json(raw)
        assert result == {"key": 42}

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            LLMClient._parse_json("not json at all")


class TestProviderDispatch:

    @pytest.mark.asyncio
    async def test_unknown_provider_falls_back_to_mock(self):
        client = LLMClient(settings=Settings(provider="mock"))
        # Manually override to test fallback logic
        client.settings.provider = "unknown_provider"
        messages = [{"role": "user", "content": "test"}]
        result = await client.complete(messages)
        data = json.loads(result)
        assert "status" in data

