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
    async def test_planner_response(self, mock_client):
        messages = [{"role": "user", "content": 'Reply with JSON: {"flows": [...]}'}]
        data = json.loads(await mock_client.complete(messages))
        assert data["flows"]

    @pytest.mark.asyncio
    async def test_agent_clicks_each_enabled_button_once(self, mock_client):
        def prompt(history: str) -> list:
            return [{"role": "user", "content": (
                f"Previous Actions Taken:\n{history}\n\nAvailable Interactive Elements on Page:\n"
                "[btn-a] <button> text=\"A\"\n[btn-off] <button> (DISABLED)\n[in-1] <input>\n[btn-b] <button>\n\n"
                "Choose the next action to advance this test."
            )}]

        first = json.loads(await mock_client.complete(prompt("None")))
        assert first["target_id"] == "btn-a"
        second = json.loads(await mock_client.complete(prompt("Step 1: [click] target=btn-a value='None'")))
        assert second["target_id"] == "btn-b"
        done = json.loads(await mock_client.complete(prompt("target=btn-a\ntarget=btn-b")))
        assert done["is_terminal"] is True

    @pytest.mark.asyncio
    async def test_judge_passes_without_evidence(self, mock_client):
        messages = [{"role": "user", "content": "Detected Evidence:\n[]\n\nEvaluate whether this constitutes a bug or regression."}]
        assert json.loads(await mock_client.complete(messages))["verdict"] == "PASS"

    @pytest.mark.asyncio
    async def test_judge_fails_with_evidence(self, mock_client):
        messages = [{"role": "user", "content": "Detected Evidence:\n['Uncaught JS error: x']\n\nEvaluate whether this constitutes a bug or regression."}]
        assert json.loads(await mock_client.complete(messages))["verdict"] == "FAIL"

    @pytest.mark.asyncio
    async def test_fallback_response(self, mock_client):
        messages = [{"role": "user", "content": "random unmatched prompt"}]
        result = await mock_client.complete(messages)
        data = json.loads(result)
        assert "status" in data


class TestCompleteJson:

    @pytest.mark.asyncio
    async def test_returns_dict(self, mock_client):
        messages = [{"role": "user", "content": '{"flows": []}'}]
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

    def test_explicit_settings_beat_dotenv(self, monkeypatch):
        monkeypatch.setenv("DIFFQA_PROVIDER", "mock")
        assert Settings(provider="fireworks").provider == "fireworks"

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_instead_of_faking_results(self):
        client = LLMClient(settings=Settings(provider="meta", meta_api_key=None))
        with pytest.raises(ValueError, match="API key"):
            await client.complete([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_modal_requires_base_url(self):
        client = LLMClient(settings=Settings(provider="modal", MODAL_API_KEY="k", MODAL_API_BASE=None))
        with pytest.raises(ValueError, match="MODAL_API_BASE"):
            await client.complete([{"role": "user", "content": "hi"}])
