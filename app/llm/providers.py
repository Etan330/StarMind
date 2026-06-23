from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx


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
    def __init__(self, provider_name: str, base_url: str, api_key: str | None, configured_models: list[str]) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.configured_models = configured_models

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        if not self.api_key or not self.base_url:
            return self.configured_models
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            response.raise_for_status()
        payload = response.json()
        remote_models = [item["id"] for item in payload.get("data", []) if "id" in item]
        return remote_models or self.configured_models

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
        if not self.api_key:
            raise ValueError(f"{self.provider_name} API key is not configured")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={"model": model, "messages": messages, "temperature": temperature},
            )
            response.raise_for_status()
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

