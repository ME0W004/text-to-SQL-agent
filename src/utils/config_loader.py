# src/utils/config_loader.py

import os
from dataclasses import dataclass
from dotenv import load_dotenv

class ConfigurationError(Exception):
    pass

def load_config():
    load_dotenv()

    # مپینگ نام متغیرهای .env به نام پارامترهای کلاس
    mapping = {
        "DB_HOST": "host",
        "DB_PORT": "port",
        "DB_USER": "user",
        "DB_PASSWORD": "password",
        "DB_NAME": "database",
    }

    required_keys = list(mapping.keys())
    config = {}
    missing = []

    for env_key, param_key in mapping.items():
        value = os.getenv(env_key)
        if value is None:
            missing.append(env_key)
        else:
            if env_key == "DB_PORT":
                try:
                    value = int(value)
                except ValueError as exc:
                    raise ConfigurationError("DB_PORT must be an integer between 1 and 65535.") from exc
                if not 1 <= value <= 65535:
                    raise ConfigurationError("DB_PORT must be an integer between 1 and 65535.")
            config[param_key] = value

    if missing:
        raise ConfigurationError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return config


# ---------------------------------------------------------------------------
# LLM routing configuration (Sprint 3A, ADR-011)
# ---------------------------------------------------------------------------

# The default fallback chain preserves the de-facto provider policy of the
# repository (OpenAI wired first; Gemini deliberately excluded — see
# docs/architecture/llm-architecture.md).
DEFAULT_PROVIDER_ORDER = ("openai", "groq", "ollama")

# Env var read by each provider to decide whether it is configured.
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    # Local provider: no API key required.
    "ollama": None,
}


@dataclass(frozen=True)
class LLMRoutingConfig:
    """Deterministic LLM routing configuration for the LLMRouter."""

    provider_order: tuple = DEFAULT_PROVIDER_ORDER
    #: Providers whose required environment configuration is present.
    configured_providers: frozenset = frozenset()


def load_llm_routing_config() -> LLMRoutingConfig:
    """Load the LLM routing policy from the environment.

    ``LLM_PROVIDER_ORDER`` (comma-separated) overrides the default chain.
    Unknown provider names raise :class:`ConfigurationError` — fail fast
    on typos instead of silently skipping part of the chain.
    """
    load_dotenv()

    order = DEFAULT_PROVIDER_ORDER
    raw_order = os.getenv("LLM_PROVIDER_ORDER")
    if raw_order:
        names = tuple(name.strip().lower() for name in raw_order.split(",") if name.strip())
        unknown = [name for name in names if name not in _PROVIDER_KEY_ENV]
        if unknown:
            raise ConfigurationError(
                f"LLM_PROVIDER_ORDER contains unknown providers: {', '.join(unknown)}. "
                f"Known providers: {', '.join(sorted(_PROVIDER_KEY_ENV))}."
            )
        if names:
            order = names

    configured = frozenset(
        name
        for name, env_key in _PROVIDER_KEY_ENV.items()
        if env_key is None or os.getenv(env_key)
    )
    return LLMRoutingConfig(provider_order=order, configured_providers=configured)
