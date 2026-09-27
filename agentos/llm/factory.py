from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider
from .gemini_provider import GeminiProvider
from .openrouter_provider import OpenRouterProvider
from .ollama_provider import OllamaProvider
from .lmstudio_provider import LMStudioProvider
from .mock_provider import MockProvider
from .base import sanitize_api_key

REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "lmstudio": LMStudioProvider,
    "mock": MockProvider,
}

# Cloud ("online") providers: you supply a provider name + API key and the
# model is resolved automatically from what that key can actually call.
# Local providers (ollama / lmstudio) and mock keep their configured model.
CLOUD_PROVIDERS = {"anthropic", "openai", "gemini", "openrouter"}

# One catalog lookup per (provider, key, base_url) per process, so building
# the planner + worker LLMs doesn't double the API round trips.
_MODEL_CACHE: dict = {}


def _auto_cloud_model(cls, spec: dict, saved_model: str | None = None) -> str:
    """Decide the model for a cloud provider purely from its API key.

    Asks the provider which models this key can actually reach and picks the
    best default (a saved model only wins if it is still in the list). If the
    catalog can't be reached (offline, no key yet), falls back to the
    provider's built-in default instead of failing the whole run.
    """
    api_key = sanitize_api_key(spec.get("api_key")) or None
    cache_id = (cls.name, api_key, spec.get("base_url"))
    if cache_id in _MODEL_CACHE:
        return _MODEL_CACHE[cache_id]

    model = getattr(cls, "default_model", "")
    try:
        kwargs = {"api_key": api_key}
        if spec.get("base_url"):
            kwargs["base_url"] = spec["base_url"]
        ids = cls.list_models(**kwargs)
        model = cls.preferred_chat_model(ids, preferred=saved_model) or model
    except Exception:
        pass  # catalog unreachable -> provider default
    _MODEL_CACHE[cache_id] = model
    return model


def build_provider(spec: dict):
    """spec looks like: {'provider': 'anthropic', 'model': 'auto', ...extra kwargs}

    For cloud providers the model in the spec is never trusted blindly: it is
    auto-detected from the API key (what that key can actually call), so a
    stale/typo'd model name in config.yaml can never break a run. Local
    providers keep using the configured model since they have no catalog to
    ask beyond what's already loaded.
    """
    spec = dict(spec)
    provider_name = spec.pop("provider")
    if provider_name not in REGISTRY:
        raise ValueError(f"Unknown provider '{provider_name}'. Available: {list(REGISTRY)}")
    cls = REGISTRY[provider_name]
    saved_model = spec.pop("model", None)
    if provider_name in CLOUD_PROVIDERS:
        spec["model"] = _auto_cloud_model(cls, spec, saved_model)
    elif saved_model:
        spec["model"] = saved_model
    return cls(**spec)


def list_models(provider_name: str, **kwargs) -> list[str]:
    """Ask a provider which models are actually callable right now -- with
    the given api_key/base_url -- instead of the caller having to guess or
    hard-code a model name. Used by the Settings dialog's 'Detect models'.
    """
    if provider_name not in REGISTRY:
        raise ValueError(f"Unknown provider '{provider_name}'. Available: {list(REGISTRY)}")
    cls = REGISTRY[provider_name]
    if not hasattr(cls, "list_models"):
        raise ValueError(f"Provider '{provider_name}' does not support model detection.")
    return cls.list_models(**kwargs)
