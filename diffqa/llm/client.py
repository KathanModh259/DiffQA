"""Unified LLM client supporting Meta Model API, Fireworks, Baseten, Modal, Ollama, and Mock."""

import json
import logging
from typing import Any, Dict, List, Optional
import httpx
from diffqa.config import Settings, get_settings

logger = logging.getLogger("diffqa.llm")


class LLMClient:
    """Dispatches completions to Meta Model API or partner inference endpoints."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        """Call the configured model provider and return text completion."""
        provider = self.settings.provider
        target_model = model or self.settings.code_model

        if provider == "mock":
            return self._mock_completion(messages, target_model)

        if provider == "meta":
            return await self._call_meta_api(messages, target_model, temperature, max_tokens, json_mode)
        elif provider == "fireworks":
            return await self._call_fireworks(messages, target_model, temperature, max_tokens, json_mode)
        elif provider == "baseten":
            return await self._call_baseten(messages, target_model, temperature, max_tokens, json_mode)
        elif provider == "ollama":
            return await self._call_ollama(messages, target_model, temperature, max_tokens, json_mode)
        else:
            logger.warning(f"Unknown or unsupported provider '{provider}', falling back to mock provider.")
            return self._mock_completion(messages, target_model)

    async def complete_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """Convenience method to request and parse JSON from the model."""
        raw_text = await self.complete(messages, model=model, temperature=temperature, json_mode=True)
        # Clean potential markdown fences ```json ... ```
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as err:
            logger.error(f"Failed to parse LLM JSON response: {err}. Raw response: {raw_text[:200]}")
            # Try finding first { and last }
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except Exception:
                    pass
            raise ValueError(f"Model output could not be parsed as JSON: {raw_text}") from err

    async def _call_meta_api(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Call Meta Model API endpoint."""
        api_key = self.settings.meta_api_key
        if not api_key:
            logger.warning("META_API_KEY is not set. Falling back to mock completion.")
            return self._mock_completion(messages, model)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.settings.meta_api_base}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _call_fireworks(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Call Fireworks AI endpoint."""
        api_key = self.settings.fireworks_api_key
        if not api_key:
            logger.warning("FIREWORKS_API_KEY not set. Falling back to mock completion.")
            return self._mock_completion(messages, model)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.fireworks.ai/inference/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _call_baseten(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Call Baseten deployed endpoint."""
        api_key = self.settings.baseten_api_key
        if not api_key:
            return self._mock_completion(messages, model)
        # Baseten OpenAI-compatible proxy or model endpoint
        headers = {"Authorization": f"Api-Key {api_key}"}
        payload = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"https://bridge.baseten.co/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _call_ollama(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Call local Ollama instance for Meta open-weights models (e.g. llama3.2)."""
        payload = {
            "model": model,
            "messages": messages,
            "options": {"temperature": temperature},
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("http://localhost:11434/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    def _mock_completion(self, messages: List[Dict[str, Any]], model: str) -> str:
        """High-fidelity mock completion for development, benchmark runs, and offline tests."""
        prompt_text = " ".join(str(m.get("content", "")) for m in messages).lower()

        # Check if flow planner request
        if "flow planner" in prompt_text or "test flows" in prompt_text or "affected routes" in prompt_text or "plan user flows" in prompt_text:
            return json.dumps({
                "summary": "Diff changes Cart component and order calculations.",
                "affected_routes": ["/", "/cart", "/checkout"],
                "risk_assessment": "High risk of state corruption when changing item quantities or removing cart items.",
                "flows": [
                    {
                        "flow_id": "flow-cart-quantity-update",
                        "title": "Update item quantity in cart",
                        "route": "/cart",
                        "flow_type": "happy_path",
                        "goal": "Verify updating item quantity accurately recalibrates subtotal and tax.",
                        "steps": [
                            "Navigate to '/' and add a product to cart",
                            "Navigate to '/cart'",
                            "Click increment quantity button",
                            "Verify quantity is 2 and total matches unit price * 2"
                        ],
                        "invariants": [
                            "Subtotal equals unit price * quantity",
                            "No NaN or negative numbers displayed in pricing",
                            "No uncaught console JavaScript exceptions"
                        ]
                    },
                    {
                        "flow_id": "flow-cart-negative-boundary",
                        "title": "Decrement item quantity below zero",
                        "route": "/cart",
                        "flow_type": "adversarial",
                        "goal": "Verify decrementing quantity at 1 either removes item or disables button, preventing negative counts.",
                        "steps": [
                            "Navigate to '/cart' with 1 item",
                            "Click decrement button multiple times",
                            "Verify quantity does not become 0 or negative without removing item properly"
                        ],
                        "invariants": [
                            "Item quantity cannot be negative",
                            "Cart total is non-negative",
                            "Checkout button is disabled if cart is empty"
                        ]
                    }
                ]
            })

        # Check if browser agent next action request
        if "select next browser action" in prompt_text or "available interactive elements" in prompt_text:
            # Check what state we are in from prompt
            if "cart" in prompt_text and "decrement" in prompt_text:
                return json.dumps({
                    "thought": "Found decrement button on cart page. Clicking decrement to test boundary condition.",
                    "action": "click",
                    "target_id": "btn-qty-decrement",
                    "value": None,
                    "is_terminal": False
                })
            return json.dumps({
                "thought": "Navigating to entry page and locating primary CTA.",
                "action": "click",
                "target_id": "btn-add-to-cart-1",
                "value": None,
                "is_terminal": False
            })

        # Check if regression judge request
        if "regression judge" in prompt_text or "judge regression" in prompt_text:
            return json.dumps({
                "verdict": "FAIL",
                "bug_type": "functional_regression",
                "title": "Quantity decrements to negative count and subtotal displays negative balance",
                "root_cause_hypothesis": "The updated decrement logic in Cart.tsx does not check Math.max(0, qty - 1).",
                "severity": "CRITICAL",
                "repro_steps": [
                    "1. Added item to cart",
                    "2. Navigated to /cart",
                    "3. Clicked decrement button twice",
                    "4. Quantity showed -1 and Total was -$29.99"
                ],
                "confidence": 0.95
            })

        # Fallback default JSON or text
        return json.dumps({"status": "ok", "message": "DiffQA mock completion executed successfully."})


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Factory function for LLMClient."""
    return LLMClient(settings=settings)

