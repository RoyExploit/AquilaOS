"""Base interface every LLM provider must implement.

Keeping this interface tiny (one method) is what makes the platform
provider-agnostic: the Planner, Coding Agent, Repair Engine, etc. never
know or care whether they're talking to Claude, GPT, Gemini, OpenRouter,
or a local Ollama/LM Studio model. They just call `.generate(...)`.
"""

from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


def sanitize_api_key(key: str | None) -> str | None:
    """Strip whitespace and control characters (NUL included) from an API key.

    Keys get pasted into the UI or hand-edited into config.yaml, so a stray
    control character (e.g. a trailing ``\\0`` written as a YAML escape)
    silently corrupts every request and turns it into a 401. Legitimate API
    keys never contain spaces or control characters, so removing them is
    always safe.
    """
    if not key:
        return key
    return re.sub(r"[\x00-\x20\x7f]+", "", key) or None


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    raw: dict | None = None


class LLMProvider(ABC):
    """A single chat-completion call. Implementations should be stateless."""

    name: str = "base"
    # Last-resort model, used only when live model detection is unavailable.
    default_model: str = ""

    @classmethod
    def preferred_chat_model(cls, ids, preferred: str | None = None) -> str:
        """Pick the best model out of a provider's live model list.

        Cloud providers call this with the list their API key can actually
        reach: a saved/pinned model wins only if it is still available,
        otherwise the provider's default (or first available) is used.
        """
        ids = list(ids)
        if preferred and preferred in ids:
            return preferred
        if cls.default_model and cls.default_model in ids:
            return cls.default_model
        return ids[0] if ids else cls.default_model

    def __init__(self, model: str, **kwargs):
        self.model = model
        self.extra = kwargs

    def generate(self, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.2) -> LLMResponse:
        """Send a single-turn request -- with automatic model recovery.

        Providers rename/retire models over time (Gemini, OpenAI, Anthropic,
        OpenRouter all do). If a call fails because the model name is stale,
        re-detect a model this credential can actually reach and retry once,
        instead of surfacing an error the user cannot fix. A pinned model in
        config.yaml therefore keeps working until the provider drops it,
        then silently falls back to the key's current default.
        """
        try:
            return self._generate(system, prompt, max_tokens=max_tokens,
                                  temperature=temperature)
        except Exception as exc:
            recovered = self._redetect_model() if self._is_model_unavailable(exc) else None
            if not recovered:
                raise
        self.model = recovered
        return self._generate(system, prompt, max_tokens=max_tokens,
                              temperature=temperature)

    def _is_model_unavailable(self, exc: Exception) -> bool:
        """True when the error says 'this model name no longer exists'
        (vs. a bad key, network problem, quota, or invalid request)."""
        resp = getattr(exc, "response", None)
        if resp is None or getattr(resp, "status_code", 0) not in (400, 404, 410):
            return False
        try:
            body = (resp.text or "").lower()
        except Exception:
            return False
        stale_markers = ("not found", "not_found", "does not exist",
                         "no longer available", "unknown model", "deprecated",
                         "retired", "unavailable")
        return "model" in body and any(m in body for m in stale_markers)

    def _redetect_model(self) -> str | None:
        """Ask this provider (with this credential/endpoint) for its current
        model list and pick a working one, excluding the broken name."""
        lister = getattr(type(self), "list_models", None)
        if lister is None:
            return None
        kwargs = {}
        api_key = getattr(self, "api_key", None)
        base_url = getattr(self, "base_url", None)
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        try:
            ids = [m for m in lister(**kwargs) if m and m != self.model]
            if not ids:
                return None
            return type(self).preferred_chat_model(ids) or None
        except Exception:
            return None

    @abstractmethod
    def _generate(self, system: str, prompt: str, max_tokens: int = 2000,
                  temperature: float = 0.2) -> LLMResponse:
        """Send a single-turn request and return the text response.

        Implementations only need the happy path -- generate() above adds
        the stale-model recovery on top of it.
        """
        raise NotImplementedError

    def generate_json(self, system: str, prompt: str, max_tokens: int = 2000) -> dict:
        """Convenience wrapper: instructs the model to return ONLY JSON and parses it."""
        import json
        strict_system = (
            system
            + "\n\nCRITICAL: Respond with ONLY valid JSON. No markdown fences, "
              "no preamble, no explanation text before or after the JSON."
        )
        resp = self.generate(strict_system, prompt, max_tokens=max_tokens, temperature=0.0)
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"[{self.name}] Model did not return valid JSON: {e}\nRaw: {text[:500]}")
