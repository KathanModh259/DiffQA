"""Unified LLM client supporting Meta Model API, Fireworks, Baseten, Modal, Ollama, and Mock.

The client dispatches completions to whichever provider is selected in
``Settings.provider``.  A high-fidelity mock provider is included so that the
full DiffQA pipeline (diff analysis → flow planning → browser agent →
regression oracle → test synthesis) can be exercised entirely offline with
zero API cost.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from diffqa.config import Settings, get_settings

logger = logging.getLogger("diffqa.llm")

# ── Provider endpoint constants ────────────────────────────────────────────
_FIREWORKS_BASE = "https://api.fireworks.ai/inference/v1/chat/completions"
_BASETEN_BASE = "https://bridge.baseten.co/v1/chat/completions"
_OLLAMA_BASE = "http://localhost:11434/api/chat"
_HTTP_TIMEOUT = 90.0  # generous timeout for large model responses


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

        dispatch = {
            "mock": lambda: self._mock_completion(messages, target_model),
            "meta": lambda: self._call_openai_compat(
                messages, target_model, temperature, max_tokens, json_mode,
                base_url=f"{self.settings.meta_api_base}/chat/completions",
                api_key=self.settings.meta_api_key,
                auth_prefix="Bearer",
            ),
            "fireworks": lambda: self._call_openai_compat(
                messages, target_model, temperature, max_tokens, json_mode,
                base_url=_FIREWORKS_BASE,
                api_key=self.settings.fireworks_api_key,
                auth_prefix="Bearer",
            ),
            "baseten": lambda: self._call_openai_compat(
                messages, target_model, temperature, max_tokens, json_mode,
                base_url=_BASETEN_BASE,
                api_key=self.settings.baseten_api_key,
                auth_prefix="Api-Key",
            ),
            "modal": lambda: self._call_openai_compat(
                messages, target_model, temperature, max_tokens, json_mode,
                base_url=f"{self.settings.meta_api_base}/chat/completions",
                api_key=self.settings.modal_api_key,
                auth_prefix="Bearer",
            ),
            "ollama": lambda: self._call_ollama(messages, target_model, temperature, max_tokens, json_mode),
        }

        handler = dispatch.get(provider)
        if handler is None:
            logger.warning("Unknown provider '%s', falling back to mock.", provider)
            return self._mock_completion(messages, target_model)
        result = handler()
        # handle both sync (mock) and async returns
        if hasattr(result, "__await__"):
            return await result
        return result

    async def complete_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """Request a completion, parse and return the result as a JSON dict."""
        raw = await self.complete(messages, model=model, temperature=temperature, json_mode=True)
        return self._parse_json(raw)

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
        api_key: Optional[str],
        auth_prefix: str = "Bearer",
    ) -> str:
        """Generic caller for any OpenAI-compatible chat/completions endpoint."""
        if not api_key:
            logger.warning("API key missing for %s — falling back to mock.", base_url)
            return self._mock_completion(messages, model)

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

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(base_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _call_ollama(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(_OLLAMA_BASE, json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]

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
        # Last resort: try extracting JSON array
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse JSON from model output (first 300 chars): {raw_text[:300]}")

    # ── Mock provider ──────────────────────────────────────────────────

    def _mock_completion(self, messages: List[Dict[str, Any]], model: str) -> str:
        """Return context-aware mock responses for the full DiffQA pipeline.

        The mock is designed to exercise every module (planner, agent, judge)
        end-to-end so that ``diffqa demo`` and ``diffqa eval`` work
        deterministically without any API keys.
        """
        prompt_text = " ".join(str(m.get("content", "")) for m in messages).lower()

        # ── Flow Planner mock ──────────────────────────────────────────
        if any(kw in prompt_text for kw in ("flow planner", "test flows", "affected routes", "plan user flows", "map out")):
            return json.dumps({
                "summary": "PR modifies cart state management — quantity updates, item removal, and subtotal calculation logic.",
                "affected_routes": ["/", "/cart", "/checkout"],
                "risk_assessment": "HIGH — removing the qty <= 0 guard allows negative quantities and corrupted subtotals.",
                "flows": [
                    {
                        "flow_id": "flow-cart-quantity-update",
                        "title": "Update item quantity in cart",
                        "route": "/cart",
                        "flow_type": "happy_path",
                        "goal": "Verify updating item quantity recalculates subtotal correctly.",
                        "steps": [
                            "Navigate to '/' and add a product to cart",
                            "Navigate to '/cart'",
                            "Click increment quantity button",
                            "Verify quantity is 2 and subtotal matches unit_price * 2",
                        ],
                        "invariants": [
                            "Subtotal == unit_price * quantity for each item",
                            "No NaN or negative values in pricing display",
                            "No uncaught JavaScript exceptions in console",
                        ],
                    },
                    {
                        "flow_id": "flow-cart-negative-boundary",
                        "title": "Decrement item quantity below zero",
                        "route": "/cart",
                        "flow_type": "adversarial",
                        "goal": "Verify decrementing quantity at 1 removes item or clamps to zero — never goes negative.",
                        "steps": [
                            "Navigate to '/cart' with 1 item (qty=1)",
                            "Click decrement button repeatedly",
                            "Verify quantity never becomes 0 or negative without item removal",
                        ],
                        "invariants": [
                            "Item quantity >= 0 at all times",
                            "Cart total is always non-negative",
                            "Checkout button disables when cart is empty",
                        ],
                    },
                ],
            })

        # ── Browser Agent mock ─────────────────────────────────────────
        if any(kw in prompt_text for kw in ("choose the next action", "available interactive elements", "select next browser action")):
            if "btn-qty-decrement" in prompt_text:
                return json.dumps({
                    "thought": "Decrement button is visible. Clicking to test qty boundary behavior.",
                    "action": "click",
                    "target_id": "btn-qty-decrement",
                    "value": None,
                    "is_terminal": True,
                })
            return json.dumps({
                "thought": "Adding a product to cart to set up the test scenario.",
                "action": "click",
                "target_id": "btn-add-to-cart-1",
                "value": None,
                "is_terminal": False,
            })

        # ── Regression Judge mock ──────────────────────────────────────
        if any(kw in prompt_text for kw in ("regression judge", "judge regression", "evaluate whether", "regression")):
            return json.dumps({
                "verdict": "FAIL",
                "bug_type": "functional_regression",
                "title": "Cart quantity decrements to negative value; subtotal displays negative balance",
                "root_cause_hypothesis": "The refactored changeQuantity() removed the `if (item.qty <= 0)` guard that previously filtered the item from the cart array.",
                "severity": "CRITICAL",
                "repro_steps": [
                    "1. Add 'Meta Ray-Ban Smart Glasses' to cart",
                    "2. Navigate to /cart",
                    "3. Click the '-' (decrement) button twice",
                    "4. Observe: quantity shows -1, subtotal shows -$299.00",
                ],
                "confidence": 0.96,
            })

        # ── Fallback ───────────────────────────────────────────────────
        return json.dumps({"status": "ok", "message": "DiffQA mock — no specific handler matched."})


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Factory function returning a configured LLMClient."""
    return LLMClient(settings=settings)
