"""Verify provider resilience: transient 503/429 -> backoff -> model fallback,
stale model -> re-detect, and user-friendly error text (no raw requests dump)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from agentos.llm.base import LLMProvider, LLMResponse


def _http_error(status: int, body: str = "") -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode()
    return requests.exceptions.HTTPError(f"{status} Server Error", response=resp)


class FakeProvider(LLMProvider):
    name = "fake"
    default_model = "m1"
    max_retries = 1
    backoff_base = 0.01
    backoff_cap = 0.02

    def __init__(self, models, fail_for, status=503, key=None, base_url=None):
        super().__init__(models[0])
        self._models = models
        self._fail_for = set(fail_for)
        self._status = status
        self.api_key = key
        self.base_url = base_url
        self.calls = []

    @classmethod
    def list_models(cls, api_key=None, **kw):
        return FakeProvider._catalog

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2):
        self.calls.append(self.model)
        if self.model in self._fail_for:
            raise _http_error(self._status, '{"error": {"status": "UNAVAILABLE", '
                                            '"message": "This model is currently experiencing high demand."}}')
        return LLMResponse(text=f"ok:{self.model}", provider=self.name, model=self.model)


# 1. one overloaded model -> falls back to the next available one
FakeProvider._catalog = ["m1", "m2"]
p = FakeProvider(["m1", "m2"], fail_for=["m1"])
r = p.generate("s", "p")
assert r.text == "ok:m2", r.text
assert p.model == "m2", p.model
assert p.calls.count("m1") == 2, p.calls   # max_retries(1) -> 2 attempts before switching
print(f"PASS 503 on '{p.calls[0]}' -> fell back to '{r.model}' (calls={p.calls})")

# 2. FIRST model ok -> no fallback, single call
FakeProvider._catalog = ["m1", "m2"]
p2 = FakeProvider(["m1", "m2"], fail_for=[])
assert p2.generate("s", "p").text == "ok:m1"
assert p2.calls == ["m1"], p2.calls
print("PASS healthy model used directly (no wasted fallback)")

# 3. quota error (429) also falls back -- and switches instantly, because a
#    quota never clears in the couple of seconds a backoff would wait for
FakeProvider._catalog = ["m1", "m2"]
p3 = FakeProvider(["m1", "m2"], fail_for=["m1"], status=429)
assert p3.generate("s", "p").text == "ok:m2"
assert p3.calls == ["m1", "m2"], f"429 should switch immediately, got {p3.calls}"
print("PASS 429 quota -> instant switch to another model (no wasted backoff)")

# 4. EVERYTHING failing -> friendly message, never the raw requests string
FakeProvider._catalog = ["m1", "m2", "m3"]
p4 = FakeProvider(["m1", "m2", "m3"], fail_for=["m1", "m2", "m3"])
try:
    p4.generate("s", "p")
    raise AssertionError("should have raised")
except RuntimeError as e:
    msg = str(e)
    assert "503 Server Error" not in msg, msg
    assert "overloaded" in msg and "wait a few seconds" in msg, msg
    print(f"PASS all models busy -> friendly error: {msg[:96]}...")

# 5. stale model name -> re-detect and retry immediately (no backoff sleep)
FakeProvider._catalog = ["m1", "m2"]
p5 = FakeProvider(["m1"], fail_for=[])
p5._fail_for = {"m1"}          # m1 404s as retired, catalog now offers m2
def _stale(system, prompt, max_tokens=2000, temperature=0.2):
    p5.calls.append(p5.model)
    if p5.model == "m1":
        raise _http_error(404, '{"error": {"message": "models/m1 is not found for API version v1beta"}}')
    return LLMResponse(text="ok:m2", provider="fake", model=p5.model)
p5._generate = _stale
assert p5.generate("s", "p").text == "ok:m2"
print("PASS retired model (404 'not found') -> re-detected and retried")

# 6. unreachable host -> network wording, not 'overloaded'
FakeProvider._catalog = ["m1"]
p6 = FakeProvider(["m1"], fail_for=["m1"])
def _net(system, prompt, max_tokens=2000, temperature=0.2):
    raise requests.exceptions.ConnectionError("Connection refused")
p6._generate = _net
try:
    p6.generate("s", "p")
    raise AssertionError("should have raised")
except RuntimeError as e:
    assert "Could not reach the provider" in str(e), str(e)
    print("PASS unreachable provider -> actionable network message")

# 7. Gemini auto-pick hands versioning to Google: the hot-swapped '-latest'
#    alias beats every frozen version string in the catalog
from agentos.llm.gemini_provider import GeminiProvider as G
catalog = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-flash-latest",
           "gemini-flash-lite-latest", "gemini-pro-latest", "gemini-3.8-flash-tts"]
assert G.preferred_chat_model(catalog) == "gemini-flash-latest", G.preferred_chat_model(catalog)
print("PASS gemini auto -> 'gemini-flash-latest' (Google rotates it; we never edit code)")

# 7b. no alias in the catalog -> newest stable chat model wins
assert G.preferred_chat_model(["gemini-3.7-flash", "gemini-3.8-flash"]) == "gemini-3.8-flash"
assert G.preferred_chat_model(["gemini-3.8-flash-tts", "gemini-flash-lite-latest"]) == "gemini-flash-lite-latest"
assert G.preferred_chat_model(["gemini-pro-latest", "gemini-flash-latest"]) == "gemini-flash-latest"
print("PASS gemini ranking: alias > full tier > flash > stable > newest; tts excluded")

# 7c. a pin is honoured only if the key can actually call it; a retired or
#     typo'd name is silently ignored instead of erroring (this is the bug
#     where a stale pin used to break every request)
assert G.preferred_chat_model(catalog, preferred="gemini-pro-latest") == "gemini-pro-latest"
assert G.preferred_chat_model(catalog, preferred="gemini-3.8-flash") == "gemini-3.8-flash"
assert G.preferred_chat_model(catalog, preferred="gemini-9.9-flash") == "gemini-flash-latest"
print("PASS gemini pin: live name honoured, retired/typo'd name ignored")


from agentos.config import AgentOSConfig
from agentos.llm.factory import build_provider
cfg = AgentOSConfig.load()
if cfg.planner.get("provider") in ("gemini", "openai", "anthropic", "openrouter"):
    try:
        prov = build_provider(dict(cfg.planner))
        print(f"live planner model -> {prov.model}")
        out = prov.generate("You are terse.", "Reply with exactly: PONG")
        print(f"PASS live {cfg.planner['provider']} reply from '{out.model}': "
              f"{out.text.strip()[:60]!r}")
    except Exception as e:
        print(f"SKIP live {cfg.planner.get('provider')}: {type(e).__name__}: {str(e)[:140]}")
else:
    print(f"SKIP live check (planner provider = {cfg.planner.get('provider')})")

# 8. LOCAL providers (ollama/lmstudio) must NOT quietly swap models on overload
FakeProvider._catalog = ["m1", "m2"]
class LocalFake(FakeProvider):
    switch_model_on_overload = False


local = LocalFake(["m1"], fail_for=["m1"])
local.api_key = None
try:
    local.generate("s", "p")
    raise AssertionError("should have raised for local provider")
except RuntimeError as e:
    assert "overloaded" in str(e), str(e)
    assert local.model == "m1", f"local model was swapped to {local.model}"
    print("PASS local provider keeps its loaded model (retries, no swap)")

print("RESILIENCE_OK")
