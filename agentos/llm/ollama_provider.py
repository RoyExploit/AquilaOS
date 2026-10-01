import requests
from .base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    """Fully offline / local. Requires an Ollama server running
    (default http://localhost:11434) with the model already pulled,
    e.g. `ollama pull llama3.1`.
    """

    name = "ollama"
    # You deliberately pulled this model -- never silently swap it out on a
    # transient error (retries only).
    switch_model_on_overload = False

    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434", **kwargs):
        super().__init__(model, **kwargs)
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def list_models(base_url: str = "http://localhost:11434", **kwargs) -> list[str]:
        """Lists models actually pulled into this local Ollama server --
        real availability, not a guessed name."""
        resp = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        names = [m["name"] for m in data.get("models", []) if m.get("name")]
        if not names:
            raise ValueError("Ollama server reachable but no models are pulled yet (try `ollama pull llama3.1`).")
        return sorted(names)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["message"]["content"]
        return LLMResponse(text=text, provider=self.name, model=self.model, raw=data)
