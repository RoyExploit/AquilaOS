import requests
from .base import LLMProvider, LLMResponse


class LMStudioProvider(LLMProvider):
    """Fully offline / local. LM Studio exposes an OpenAI-compatible local
    server (default http://localhost:1234/v1) once a model is loaded in its UI.
    """

    name = "lmstudio"
    # You deliberately loaded this model -- never silently swap it out on a
    # transient error (retries only).
    switch_model_on_overload = False

    def __init__(self, model: str = "local-model", base_url: str = "http://localhost:1234/v1", **kwargs):
        super().__init__(model, **kwargs)
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def list_models(base_url: str = "http://localhost:1234/v1", **kwargs) -> list[str]:
        """Lists whatever model is currently loaded in LM Studio's local
        server -- real availability, not a guessed name."""
        resp = requests.get(f"{base_url.rstrip('/')}/models", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        ids = [m["id"] for m in data.get("data", []) if m.get("id")]
        if not ids:
            raise ValueError("LM Studio server reachable but no model is loaded yet.")
        return sorted(ids)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return LLMResponse(text=text, provider=self.name, model=self.model, raw=data)
