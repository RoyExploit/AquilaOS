import os
import requests
from .base import LLMProvider, LLMResponse, sanitize_api_key


class OpenRouterProvider(LLMProvider):
    """OpenRouter exposes an OpenAI-compatible API in front of many hosted
    models (Claude, GPT, Gemini, Llama, Mistral, etc).
    """

    name = "openrouter"
    default_model = "meta-llama/llama-3.1-70b-instruct"

    def __init__(self, model: str = "meta-llama/llama-3.1-70b-instruct",
                 api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("OPENROUTER_API_KEY"))
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set (env var or config)")

    @staticmethod
    def list_models(api_key: str | None = None, **kwargs) -> list[str]:
        # OpenRouter's model catalog is public, but we still send the key
        # when present since it's the same key that will be used to call
        # the model, and some accounts see extra/gated models this way.
        api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("OPENROUTER_API_KEY"))
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        ids = {m["id"] for m in data.get("data", []) if m.get("id")}
        return sorted(ids)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return LLMResponse(text=text, provider=self.name, model=self.model, raw=data)
