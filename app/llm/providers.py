from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx


class ProviderConnectionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LLMProvider(ABC):
    provider_name: str

    @abstractmethod
    async def list_models(self) -> list[str]:
        pass

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        pass

    async def json_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self.chat(messages, model=model, temperature=0.0)
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return json.loads(cleaned)

    @abstractmethod
    async def test_connection(self) -> bool:
        pass


class MockProvider(LLMProvider):
    provider_name = "mock"

    async def list_models(self) -> list[str]:
        return ["mock-fast", "mock-smart"]

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        last_message = messages[-1]["content"] if messages else ""
        return f"[mock:{model}] {last_message[:500]}"

    async def json_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": "mock",
            "model": model,
            "ok": True,
            "schema_keys": list(schema.keys()) if schema else [],
        }

    async def test_connection(self) -> bool:
        return True


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str | None,
        configured_models: list[str],
        provider_config: dict[str, Any] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.configured_models = configured_models
        self.provider_config = provider_config or {}
        self.auth_header = str(self.provider_config.get("auth_header") or "Authorization")
        self.auth_scheme = str(self.provider_config.get("auth_scheme", "Bearer"))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[self.auth_header] = f"{self.auth_scheme} {self.api_key}" if self.auth_scheme else self.api_key
        return headers

    def _raise_for_response(self, response: httpx.Response) -> None:
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            if "uuap.baidu.com/login" in location:
                raise ProviderConnectionError(
                    "uuap_redirect",
                    "请求被 UUAP 登录页拦截，请确认当前模型供应商的 API Key、鉴权 Header、Base URL 和内网访问方式。",
                    response.status_code,
                )
            raise ProviderConnectionError("redirect", f"请求被重定向到 {location or '未知地址'}", response.status_code)
        if response.status_code in {401, 403}:
            raise ProviderConnectionError("auth_failed", "模型接口认证失败，请检查 API Key 或权限。", response.status_code)
        if response.status_code == 404:
            raise ProviderConnectionError("endpoint_not_found", "模型接口地址不存在，请检查 Base URL 是否需要包含 /v1。", 404)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderConnectionError("http_error", f"模型接口 HTTP 错误：{response.status_code}", response.status_code) from exc

    async def list_models(self) -> list[str]:
        if not self.api_key or not self.base_url:
            return self.configured_models
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            self._raise_for_response(response)
        payload = response.json()
        remote_models = [item["id"] for item in payload.get("data", []) if "id" in item]
        return remote_models or self.configured_models

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        if not self.api_key:
            raise ProviderConnectionError("api_key_missing", f"{self.provider_name} API key is not configured")
        if not self.base_url:
            raise ProviderConnectionError("base_url_missing", f"{self.provider_name} Base URL 未配置")
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={"model": model, "messages": messages, "temperature": temperature},
            )
            self._raise_for_response(response)
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    async def test_connection(self) -> bool:
        if not self.api_key or not self.base_url:
            return False
        try:
            await self.list_models()
            return True
        except Exception:
            return False


class AnthropicProvider(LLMProvider):
    def __init__(self, provider_name: str, base_url: str, api_key: str | None, configured_models: list[str]) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.configured_models = configured_models

    async def list_models(self) -> list[str]:
        return self.configured_models

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        if not self.api_key:
            raise ValueError("Anthropic API key is not configured")
        system = "\n".join(message["content"] for message in messages if message.get("role") == "system")
        user_messages = [message for message in messages if message.get("role") != "system"]
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "system": system,
                    "messages": user_messages,
                    "temperature": temperature,
                    "max_tokens": 1024,
                },
            )
            response.raise_for_status()
        payload = response.json()
        return "".join(block.get("text", "") for block in payload.get("content", []))

    async def test_connection(self) -> bool:
        return bool(self.api_key and self.configured_models)


class GeminiProvider(LLMProvider):
    def __init__(self, provider_name: str, base_url: str, api_key: str | None, configured_models: list[str]) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.configured_models = configured_models

    async def list_models(self) -> list[str]:
        return self.configured_models

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        if not self.api_key:
            raise ValueError("Gemini API key is not configured")
        text = "\n".join(f"{message['role']}: {message['content']}" for message in messages)
        url = f"{self.base_url}/v1beta/models/{model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                json={
                    "contents": [{"parts": [{"text": text}]}],
                    "generationConfig": {"temperature": temperature},
                },
            )
            response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)

    async def test_connection(self) -> bool:
        return bool(self.api_key and self.configured_models)

