"""Base interface every LLM provider must implement.

Keeping this interface tiny (one method) is what makes the platform
provider-agnostic: the Planner, Coding Agent, Repair Engine, etc. never
know or care whether they're talking to Claude, GPT, Gemini, OpenRouter,
or a local Ollama/LM Studio model. They just call `.generate(...)`.
"""

from __future__ import annotations
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

# HTTP statuses that mean "the provider is busy right now" -- worth a short
# backoff and another go at the SAME model: 503 = overloaded, 500/502/504 =
# upstream hiccup, 408/409/425 = the request raced/timed out.
RETRYABLE_STATUS = {408, 409, 425, 500, 502, 503, 504}
# 429 = rate limit / quota. Retrying the same model is pointless (a quota
# doesn't clear in a couple of seconds), so we skip the backoff and move
# straight to a different model instead.
QUOTA_STATUS = {429}
TRANSIENT_STATUS = RETRYABLE_STATUS | QUOTA_STATUS

# urllib3/requests exception class names that mean "couldn't reach the host".
CONNECTION_ERRORS = ("ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout",
                     "SSLError", "ChunkedEncodingError", "NewConnectionError",
                     "MaxRetryError", "RemoteDisconnected")


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

    # Retry tuning for transient provider errors (see TRANSIENT_STATUS):
    # each model gets a couple of backed-off attempts, then generate() walks
    # down the live catalog to another model rather than failing the chat.
    max_retries: int = 2
    max_model_switches: int = 3
    backoff_base: float = 1.5
    backoff_cap: float = 8.0
    # Cloud providers: another model in the catalog is a valid stand-in for an
    # overloaded one. Locally-run models (Ollama / LM Studio) opt out -- you
    # deliberately loaded that model, so we retry rather than quietly swap it
    # for a different one. (A *retired* name is always re-detected.)
    switch_model_on_overload: bool = True

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
        """Send a single-turn request -- with automatic recovery.

        Two failure modes are handled here so the user never sees a raw HTTP
        error they can't act on:

        1. **Stale model name** (400/404/410 "model not found") -- providers
           retire and rename models constantly. Re-detect one this credential
           can actually reach and retry immediately.
        2. **Transient provider failure** (503 "high demand", 429 quota,
           5xx/timeouts) -- these are per-model and temporary. Back off and
           retry a couple of times, then walk down the live catalog to the
           next-best chat model, because while `gemini-3.8-flash` is having a
           demand spike, `gemini-3.6-flash` is usually fine.
        """
        tried = [self.model]
        attempt = 0
        while True:
            try:
                return self._generate(system, prompt, max_tokens=max_tokens,
                                      temperature=temperature)
            except Exception as exc:
                stale = self._is_model_unavailable(exc)
                retryable = self._is_retryable(exc)   # spike/timeout -> same model again
                quota = self._is_quota(exc)           # rate limit -> switch, don't sleep
                transient = retryable or quota
                if not (stale or transient):
                    raise

                # Give the current model backed-off retries first (a demand
                # spike usually clears in a few seconds). Quota errors skip
                # this -- sleeping never fixes a quota, only switching does.
                if retryable and attempt < self.max_retries:
                    attempt += 1
                    delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_cap)
                    time.sleep(delay * (1 + random.random() * 0.3))  # jitter
                    continue

                # Still failing: for a retired model name, or a transient
                # failure on a provider where another catalog model is a valid
                # stand-in, walk down to the next-best model.
                may_switch = stale or (transient and self.switch_model_on_overload)
                if may_switch and len(tried) <= self.max_model_switches:
                    alternative = self._redetect_model(exclude=tried)
                    if alternative:
                        self.model = alternative
                        tried.append(alternative)
                        attempt = 0
                        continue

                if transient:
                    raise RuntimeError(self._transient_message(tried, exc)) from exc
                raise

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

    @staticmethod
    def _status_of(exc: Exception) -> int | None:
        resp = getattr(exc, "response", None)
        return getattr(resp, "status_code", None)

    def _is_retryable(self, exc: Exception) -> bool:
        """True when re-sending to the SAME model is likely to help."""
        code = self._status_of(exc)
        if code is not None:
            return code in RETRYABLE_STATUS
        return type(exc).__name__ in CONNECTION_ERRORS

    def _is_quota(self, exc: Exception) -> bool:
        """True for rate-limit/quota errors -- switch models, don't sleep."""
        return self._status_of(exc) in QUOTA_STATUS

    def _is_transient(self, exc: Exception) -> bool:
        """True for errors that mean 'the provider is busy/overloaded right
        now' as opposed to a bad key or a bad request."""
        return self._is_retryable(exc) or self._is_quota(exc)

    def _transient_message(self, tried: list[str], exc: Exception) -> str:
        """A message the user can actually act on, instead of
        '503 Server Error: Service Unavailable for url: ...'."""
        names = ", ".join(tried)
        code = self._status_of(exc)
        if code is None:
            return (f"[{self.name}] Could not reach the provider for {names} "
                    f"(network error). Check the base URL / your connection.")
        if code == 429:
            return (f"[{self.name}] Rate limit or quota reached on {names}. "
                    f"Wait a moment, or add billing / switch to another model.")
        return (f"[{self.name}] {names} are temporarily overloaded "
                f"(HTTP {code} -- provider-side demand spike). Nothing is wrong "
                f"with your setup: wait a few seconds and send again.")

    def _redetect_model(self, exclude=()) -> str | None:
        """Ask this provider (with this credential/endpoint) for its current
        model list and pick the best working one, skipping any model already
        tried (or the broken name itself)."""
        lister = getattr(type(self), "list_models", None)
        if lister is None:
            return None
        skip = {m for m in exclude if m} | {self.model}
        kwargs = {}
        api_key = getattr(self, "api_key", None)
        base_url = getattr(self, "base_url", None)
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        try:
            ids = [m for m in lister(**kwargs) if m and m not in skip]
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
