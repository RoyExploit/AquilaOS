import os
import re
import time
import requests
from .base import LLMProvider, LLMResponse, sanitize_api_key


class GeminiProvider(LLMProvider):
    name = "gemini"
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

        The Gemini catalog mixes chat, TTS, image, robotics... models and
        silently rotates/deprecates versions, so this ranks the chat-capable
        'gemini-*' entries: non-lite first, stable over preview, flash tier
        (broadly available on API-key plans), then newest version number.
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
                0 if "lite" in m else 1,      # full tier over lite
                0 if "preview" in m else 1,   # stable over preview
                1 if "flash" in m else 0,     # flash tier answers on more plans
                version,                      # newest first
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
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        resp = None
        for attempt in range(3):
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
            # 503 = transient "high demand" -- brief backoff, then give up.
            if resp.status_code == 503 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
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
