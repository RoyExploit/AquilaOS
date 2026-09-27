import os
import requests
from .base import LLMProvider, LLMResponse, sanitize_api_key


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-6"

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("ANTHROPIC_API_KEY"))
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set (env var or config)")

    @staticmethod
    def list_models(api_key: str | None = None, **kwargs) -> list[str]:
        """Ask Anthropic which models this key can actually call, instead of
        guessing a model name up front.
        """
        api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("ANTHROPIC_API_KEY"))
        if not api_key:
            raise ValueError("No API key provided -- enter one first, or set ANTHROPIC_API_KEY.")
        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return sorted({m["id"] for m in data.get("data", []) if m.get("id")}, reverse=True)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return LLMResponse(text=text, provider=self.name, model=self.model, raw=data)
