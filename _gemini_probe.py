"""Which chat models will actually answer right now?

Run this when a provider returns 503 "high demand" -- it shows exactly which
models in your key's catalog are answering and which are overloaded, so you
can pin a working one. Usage:  python _gemini_probe.py [planner|worker]
"""
import sys
import requests
from agentos.config import AgentOSConfig
from agentos.llm.gemini_provider import GeminiProvider as G

role = sys.argv[1] if len(sys.argv) > 1 else "planner"
cfg = AgentOSConfig.load()
spec = getattr(cfg, role)
key = spec.get("api_key")
if not key:
    raise SystemExit(f"{role} is not a cloud provider with an API key (provider={spec.get('provider')})")

ids = G.list_models(api_key=key)
chat = [m for m in ids if m.startswith("gemini-") and not any(s in m for s in G._NON_CHAT)]
print(f"{role} ({spec.get('provider')}): {len(chat)} chat-capable models")
print(f"auto-detected pick -> {G.preferred_chat_model(ids)}\n")
for m in chat:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
    try:
        r = requests.post(url, json={"contents": [{"role": "user", "parts": [{"text": "say OK"}]}]},
                          timeout=30)
        note = "OK" if r.status_code == 200 else r.text[:90].replace("\n", " ")
        print(f"  {m:34} -> {r.status_code} {note}")
    except Exception as e:
        print(f"  {m:34} -> EXC {e}")

