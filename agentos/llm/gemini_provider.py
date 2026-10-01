import os
import re
import requests
from .base import LLMProvider, LLMResponse, sanitize_api_key


class GeminiProvider(LLMProvider):
    name = "gemini"
    # Google's *hot-swapped* alias -- it always points at the current Flash
    # model, so this constant never needs editing when Google ships 3.9/4.0.
    # (Only used if the live catalog can't be reached at all; normally the
    # model is resolved from the key's catalog via preferred_chat_model.)
    default_model = "gemini-flash-latest"

    # Catalog entries that exist but can't answer a normal chat prompt
    # (TTS/image/lyria/embedding/research/robotics variants).
    _NON_CHAT = ("tts", "image", "lyria", "embedding", "transcribe", "robotics",
                 "computer-use", "deep-research", "antigravity", "omni", "nano",
                 "banana", "gemma", "customtools")

    def __init__(self, model: str = "gemini-flash-latest", api_key: str | None = None, **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("GEMINI_API_KEY"))
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set (env var or config)")

    @classmethod
    def preferred_chat_model(cls, ids, preferred: str | None = None) -> str:
        """Auto-pick a chat-capable Gemini model from the key's live catalog.

        Google documents two naming styles: a *stable* string that names one
        exact model (``gemini-3.6-flash``) and a *latest* alias that Google
        hot-swaps on every release (``gemini-flash-latest``). We rank the
        alias first: that hands model versioning to Google, so "auto" keeps
        working when 3.8 becomes 3.9 without anyone editing our code.

        Everything else is a tiebreak, and the whole list is only ever used
        as a fallback ladder -- if the chosen model is overloaded or retired
        at call time, `LLMProvider.generate()` walks to the next entry.
        """
        ids = list(ids)
        candidates = [m for m in ids
                      if m.startswith("gemini-") and not any(s in m for s in cls._NON_CHAT)]
        if preferred and preferred in candidates:
            return preferred
        if not candidates:
            return super().preferred_chat_model(ids, preferred)

        def rank(m: str):
            version = tuple(int(n) for n in re.findall(r"\d+", m))
            return (
                1 if m.endswith("-latest") else 0,  # Google's pointer over a frozen version
                0 if "lite" in m else 1,            # full tier over lite
                1 if "flash" in m else 0,           # flash works on more plans than pro
                0 if "preview" in m else 1,         # stable over preview
                version,                            # newest as the last tiebreak
            )

        return max(candidates, key=rank)

    @staticmethod
    def list_models(api_key: str | None = None, **kwargs) -> list[str]:
        api_key = sanitize_api_key(api_key) or sanitize_api_key(os.environ.get("GEMINI_API_KEY"))
        if not api_key:
            raise ValueError("No API key provided -- enter one first, or set GEMINI_API_KEY.")
        resp = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        names = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                names.append(m.get("name", "").removeprefix("models/"))
        return sorted({n for n in names if n}, reverse=True)

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        # Retry/backoff for 503 "high demand" and model fallback are handled
        # centrally by LLMProvider.generate(), so this is a single clean call.
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        resp = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    # Gemini 2.5+/3.x thinking models spend the output-token
                    # budget on reasoning first, so a small max_tokens can
                    # leave zero tokens for the answer (finishReason
                    # MAX_TOKENS + empty content). Keep a generous floor --
                    # models stop on their own when done anyway.
                    "maxOutputTokens": max(max_tokens, 8192),
                    "temperature": temperature,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        content = (candidates[0].get("content") or {}) if candidates else {}
        text = "".join(
            p.get("text", "") for p in content.get("parts", [])
            if "text" in p and not p.get("thought")
        )
        if not text:
            reason = (candidates[0].get("finishReason", "unknown") if candidates
                      else data.get("promptFeedback", {}).get("blockReason", "no candidates"))
            raise ValueError(
                f"[gemini] Model '{self.model}' returned no text "
                f"(finishReason={reason}). Raw: {str(data)[:400]}"
            )
        return LLMResponse(text=text, provider=self.name, model=self.model, raw=data)
