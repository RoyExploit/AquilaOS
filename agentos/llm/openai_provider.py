import os
import requests
from .base import LLMProvider, LLMResponse, sanitize_api_key


class OpenAIProvider(LLMProvider):
    name = "openai"
    default_model = "gpt-4.1"

    def __init__(self, model: str = "gpt-4.1", api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("OPENAI_API_KEY"))
        self.base_url = base_url
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set (env var or config)")

    @staticmethod
    def list_models(api_key: str | None = None, base_url: str = "https://api.openai.com/v1", **kwargs) -> list[str]:
        api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("OPENAI_API_KEY"))
        if not api_key:
            raise ValueError("No API key provided -- enter one first, or set OPENAI_API_KEY.")
        resp = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        ids = {m["id"] for m in data.get("data", []) if m.get("id")}
        # Keep the list to models that can actually answer a chat prompt --
        # drop embeddings/audio/image/moderation model families that would
        # just error out if picked here.
        excluded = ("embedding", "whisper", "tts", "dall-e", "moderation", "davinci-", "babbage-")
        chat_ids = {i for i in ids if not any(x in i for x in excluded)}
        return sorted(chat_ids or ids, reverse=True)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
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
