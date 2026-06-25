from __future__ import annotations

import re
from typing import Any

from app.config import (
    DEFAULT_MODEL_CONFIG,
    MODEL_CONFIG_PATH,
    PROVIDERS_PATH,
    SECRETS_PATH,
    ensure_config_files,
    read_json,
    write_json,
)
from app.llm.providers import AnthropicProvider, GeminiProvider, LLMProvider, MockProvider, OpenAICompatibleProvider, ProviderConnectionError


def get_providers() -> dict[str, Any]:
    ensure_config_files()
    return read_json(PROVIDERS_PATH, {})


def get_model_settings() -> dict[str, Any]:
    ensure_config_files()
    settings = DEFAULT_MODEL_CONFIG.copy()
    settings.update(read_json(MODEL_CONFIG_PATH, DEFAULT_MODEL_CONFIG))
    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    provider = settings.get("default_provider", "mock")
    settings["has_api_key"] = bool(secrets.get("api_keys", {}).get(provider)) or provider == "mock"
    return settings


def save_provider_base_url(provider: str, base_url: str | None) -> None:
    ensure_config_files()
    if base_url is None:
        return
    providers = get_providers()
    if provider not in providers:
        return
    providers[provider]["base_url"] = base_url.strip()
    write_json(PROVIDERS_PATH, providers)


def save_model_settings(provider: str, model: str, api_key: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    ensure_config_files()
    if base_url is not None:
        save_provider_base_url(provider, base_url)

    settings = read_json(MODEL_CONFIG_PATH, DEFAULT_MODEL_CONFIG)
    settings["default_provider"] = provider
    settings["default_model"] = model
    for task_name in ["classifier_model", "ingest_model", "query_model", "lint_model", "repair_model"]:
        settings.setdefault("task_models", {}).setdefault(task_name, {})
        settings["task_models"][task_name]["provider"] = provider
        settings["task_models"][task_name]["model"] = model
    write_json(MODEL_CONFIG_PATH, settings)

    if api_key:
        secrets = read_json(SECRETS_PATH, {"api_keys": {}})
        secrets.setdefault("api_keys", {})[provider] = api_key
        write_json(SECRETS_PATH, secrets)
    return get_model_settings()


def save_provider_api_key(provider: str, api_key: str) -> None:
    ensure_config_files()
    if not api_key:
        return
    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    secrets.setdefault("api_keys", {})[provider] = api_key
    write_json(SECRETS_PATH, secrets)


def clear_api_key(provider: str | None = None) -> dict[str, Any]:
    ensure_config_files()
    settings = get_model_settings()
    target_provider = provider or settings.get("default_provider", "mock")
    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    secrets.setdefault("api_keys", {}).pop(target_provider, None)
    write_json(SECRETS_PATH, secrets)
    return get_model_settings()


def add_custom_provider(display_name: str, base_url: str, model: str, api_key_label: str = "API Key") -> dict[str, Any]:
    ensure_config_files()
    providers = get_providers()
    slug_base = re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_") or "custom"
    provider_id = f"custom_{slug_base}"
    counter = 2
    while provider_id in providers:
        provider_id = f"custom_{slug_base}_{counter}"
        counter += 1
    providers[provider_id] = {
        "display_name": display_name,
        "api_style": "openai_compatible",
        "base_url": base_url,
        "models": [model],
        "api_key_label": api_key_label,
        "adapter_status": "custom",
    }
    write_json(PROVIDERS_PATH, providers)
    return {"provider_id": provider_id, "provider": providers[provider_id]}


def get_active_provider() -> LLMProvider:
    ensure_config_files()
    providers = get_providers()
    settings = get_model_settings()
    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    provider_id = settings.get("default_provider", "mock")
    provider_config = providers.get(provider_id, providers["mock"])
    api_key = secrets.get("api_keys", {}).get(provider_id)
    api_style = provider_config.get("api_style", "mock")
    models = provider_config.get("models", [])
    base_url = provider_config.get("base_url", "")

    if api_style == "mock":
        return MockProvider()
    if api_style == "openai_compatible":
        return OpenAICompatibleProvider(provider_id, base_url, api_key, models, provider_config=provider_config)
    if api_style == "anthropic":
        return AnthropicProvider(provider_id, base_url, api_key, models)
    if api_style == "gemini":
        return GeminiProvider(provider_id, base_url, api_key, models)
    return MockProvider()


def get_provider_runtime(provider_id: str | None = None, model: str | None = None) -> tuple[LLMProvider, str, dict[str, Any]]:
    ensure_config_files()
    providers = get_providers()
    settings = get_model_settings()
    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    resolved_provider_id = provider_id or settings.get("default_provider", "deepseek")
    provider_config = providers.get(resolved_provider_id, providers.get("deepseek", providers["mock"]))
    resolved_model = model or settings.get("default_model") or (provider_config.get("models") or [""])[0]
    api_key = secrets.get("api_keys", {}).get(resolved_provider_id)
    api_style = provider_config.get("api_style", "mock")
    models = provider_config.get("models", [])
    base_url = provider_config.get("base_url", "")

    if api_style == "mock":
        provider = MockProvider()
    elif api_style == "openai_compatible":
        provider = OpenAICompatibleProvider(resolved_provider_id, base_url, api_key, models, provider_config=provider_config)
    elif api_style == "anthropic":
        provider = AnthropicProvider(resolved_provider_id, base_url, api_key, models)
    elif api_style == "gemini":
        provider = GeminiProvider(resolved_provider_id, base_url, api_key, models)
    else:
        provider = MockProvider()
    return provider, resolved_model, provider_config


def _connection_error_payload(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ProviderConnectionError):
        return {"error": exc.code, "message": str(exc)}
    message = str(exc)
    if "uuap.baidu.com/login" in message or "uuap_redirect" in message:
        return {"error": "uuap_redirect", "message": "请求被 UUAP 登录页拦截，请检查内部 API 鉴权 Header、Base URL 或内网访问方式。"}
    return {"error": type(exc).__name__, "message": message}


async def test_active_connection() -> dict[str, Any]:
    settings = get_model_settings()
    provider = get_active_provider()
    try:
        ok = await provider.test_connection()
        return {
            "ok": ok,
            "provider": settings.get("default_provider"),
            "model": settings.get("default_model"),
            "error": None if ok else "connection_failed",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": settings.get("default_provider"),
            "model": settings.get("default_model"),
            **_connection_error_payload(exc),
        }


async def test_model_connection(
    provider_id: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    ensure_config_files()
    providers = get_providers()
    provider_config = providers.get(provider_id)
    if provider_config is None:
        return {"ok": False, "provider": provider_id, "model": model, "error": "provider_not_found"}

    secrets = read_json(SECRETS_PATH, {"api_keys": {}})
    resolved_key = api_key or secrets.get("api_keys", {}).get(provider_id)
    models = [model] if model else provider_config.get("models", [])
    api_style = provider_config.get("api_style", "mock")
    base_url = base_url if base_url is not None else provider_config.get("base_url", "")

    if api_style == "mock":
        provider = MockProvider()
    elif api_style == "openai_compatible":
        provider = OpenAICompatibleProvider(provider_id, base_url, resolved_key, models, provider_config=provider_config)
    elif api_style == "anthropic":
        provider = AnthropicProvider(provider_id, base_url, resolved_key, models)
    elif api_style == "gemini":
        provider = GeminiProvider(provider_id, base_url, resolved_key, models)
    else:
        provider = MockProvider()
    resolved_model = model or (models[0] if models else None)
    try:
        ok = await provider.test_connection()
        return {"ok": ok, "provider": provider_id, "model": resolved_model, "error": None if ok else "connection_failed"}
    except Exception as exc:
        return {"ok": False, "provider": provider_id, "model": resolved_model, **_connection_error_payload(exc)}
