"""Unified LLM client supporting Meta Model API, Fireworks, Baseten, Modal, Ollama, and Mock.

The client dispatches completions to whichever provider is selected in
``Settings.provider``.  A high-fidelity mock provider is included so that the
full DiffQA pipeline (diff analysis → flow planning → browser agent →
regression oracle → test synthesis) can be exercised entirely offline with
zero API cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

import httpx

from diffqa.config import Settings, get_settings

logger = logging.getLogger("diffqa.llm")

# ── Provider endpoint constants ────────────────────────────────────────────
_FIREWORKS_BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
_BASETEN_BASE = "https://bridge.baseten.co/v1/chat/completions"
_HTTP_TIMEOUT = 120.0  # reasoning models can take a while on large prompts
_MAX_ATTEMPTS = 4
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


async def _post_with_retry(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """POST JSON, retrying rate limits, 5xx and network errors with exponential backoff + jitter."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code not in _RETRYABLE_STATUS or attempt == _MAX_ATTEMPTS:
                    if resp.is_error:
                        # Surface the provider's message ("model not found", "invalid key"...) instead of a bare status.
                        raise httpx.HTTPStatusError(
                            f"{resp.status_code} from {url}: {resp.text[:500]}", request=resp.request, response=resp
                        )
                    return resp.json()
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt == _MAX_ATTEMPTS:
                    raise
                logger.warning("LLM request failed (%s), retrying.", e)
                delay = 2 ** attempt
            await asyncio.sleep(min(delay, 30) + random.random())
    raise AssertionError("unreachable")


class LLMClient:
    """Dispatches chat completions to Meta Model API or partner inference endpoints."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    # ── Public API ─────────────────────────────────────────────────────

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Send a chat completion request and return the assistant text."""
        provider = self.settings.provider
        target_model = model or self.settings.code_model

        if provider == "mock":
            return self._mock_completion(messages)
        if provider == "ollama":
            return await self._call_ollama(messages, target_model, temperature, json_mode)

        s = self.settings
        endpoints = {
            "meta": (f"{s.meta_api_base}/chat/completions", s.meta_api_key, "Bearer"),
            "fireworks": (_FIREWORKS_BASE, s.fireworks_api_key, "Bearer"),
            "baseten": (_BASETEN_BASE, s.baseten_api_key, "Api-Key"),
            "modal": (f"{s.modal_api_base}/chat/completions" if s.modal_api_base else None, s.modal_api_key, "Bearer"),
        }
        base_url, api_key, auth_prefix = endpoints[provider]
        if not base_url:
            raise ValueError(f"Provider '{provider}' needs an API base URL (set MODAL_API_BASE).")
        if not api_key:
            raise ValueError(f"Provider '{provider}' selected but its API key is not set.")
        return await self._call_openai_compat(
            messages, target_model, temperature, max_tokens, json_mode,
            base_url=base_url, api_key=api_key, auth_prefix=auth_prefix,
        )

    async def complete_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """Request a completion, parse and return the result as a JSON dict.

        One repair round-trip is made if the model answers with something that isn't JSON.
        """
        raw = await self.complete(messages, model=model, temperature=temperature, json_mode=True)
        try:
            return self._parse_json(raw)
        except ValueError:
            logger.warning("Model returned non-JSON output; asking it to repair once.")
        repair = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object, no prose or code fences."},
        ]
        return self._parse_json(await self.complete(repair, model=model, temperature=temperature, json_mode=True))

    # ── Private provider methods ───────────────────────────────────────

    async def _call_openai_compat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        *,
        base_url: str,
        api_key: str,
        auth_prefix: str = "Bearer",
    ) -> str:
        """Generic caller for any OpenAI-compatible chat/completions endpoint."""
        headers = {
            "Authorization": f"{auth_prefix} {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = await _post_with_retry(base_url, payload, headers)
        except httpx.HTTPStatusError as e:
            # Not every OpenAI-compatible host accepts response_format; the prompt already demands JSON.
            if e.response.status_code == 400 and "response_format" in payload:
                payload.pop("response_format")
                data = await _post_with_retry(base_url, payload, headers)
            else:
                raise
        return data["choices"][0]["message"].get("content") or ""

    async def _call_ollama(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        json_mode: bool,
    ) -> str:
        """Call a local Ollama instance (e.g. llama3.3, llama3.2-vision)."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "options": {"temperature": temperature},
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        data = await _post_with_retry(self.settings.ollama_api_base, payload, {})
        return data["message"].get("content") or ""

    # ── JSON parsing helper ────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw_text: str) -> Dict[str, Any]:
        """Robustly parse JSON from LLM output, stripping fences if present."""
        cleaned = raw_text.strip()
        # Strip markdown code fences
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Fallback: extract first JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse JSON from model output (first 300 chars): {raw_text[:300]}")

    # ── Mock provider ──────────────────────────────────────────────────

    @staticmethod
    def _mock_completion(messages: List[Dict[str, Any]]) -> str:
        """Deterministic offline stand-in for a real model.

        It never invents bugs: the planner returns a generic smoke plan, the
        agent clicks each visible enabled button once, and the judge fails a
        flow only when the run produced hard evidence (errors, 5xx, failed
        actions). Anything subtler needs a real model.
        """
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        lowered = prompt.lower()

        if "choose the next action" in lowered:
            history_part, _, elements_part = prompt.partition("Available Interactive Elements on Page:")
            clicked = set(re.findall(r"target=(\S+)", history_part))
            for elem_id, tag, rest in re.findall(r"^\[([^\]]+)\] <(\w+)>(.*)$", elements_part, re.MULTILINE):
                if elem_id not in clicked and tag == "button" and "(DISABLED)" not in rest:
                    return json.dumps({
                        "thought": f"Clicking unexplored button [{elem_id}].",
                        "action": "click",
                        "target_id": elem_id,
                        "is_terminal": False,
                    })
            return json.dumps({
                "thought": "Every visible button has been exercised.",
                "action": "wait",
                "value": "300",
                "is_terminal": True,
            })

        if "evaluate whether this constitutes a bug" in lowered:
            evidence = prompt.partition("Detected Evidence:")[2].partition("\n\n")[0].strip()
            if evidence in ("", "[]"):
                return json.dumps({"verdict": "PASS", "confidence": 0.5})
            return json.dumps({
                "verdict": "FAIL",
                "title": "Errors observed during flow execution",
                "root_cause_hypothesis": f"Mock judge: hard evidence was recorded: {evidence[:300]}",
                "confidence": 0.7,
            })

        if '"flows"' in prompt:
            return json.dumps({
                "summary": "Mock plan: generic smoke and click-through flows (no model analysis).",
                "affected_routes": ["/"],
                "risk_assessment": "UNKNOWN — mock provider does not analyse the change.",
                "flows": [
                    {
                        "flow_id": "flow-smoke-click-through",
                        "title": "Click through every visible control",
                        "route": "/",
                        "flow_type": "happy_path",
                        "goal": "Every button can be clicked without runtime errors or server failures.",
                        "steps": ["Load the page", "Click each visible enabled button once"],
                        "invariants": ["No uncaught JavaScript exceptions", "No HTTP 5xx responses"],
                    },
                ],
            })

        return json.dumps({"status": "ok", "message": "DiffQA mock — no specific handler matched."})


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Factory function returning a configured LLMClient."""
    return LLMClient(settings=settings)
