import asyncio
import json

from fastapi.testclient import TestClient

from app.agent.runner import AgentRunner
from app.llm.providers import OpenAICompatibleProvider
from app.llm.registry import test_model_connection as run_model_connection_test
from app.main import app


def test_model_profile_save_persists_profile_settings_and_key(tmp_path, monkeypatch):
    profiles_path = tmp_path / "model_profiles.json"
    model_config_path = tmp_path / "model_config.json"
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr("app.api.routes.MODEL_PROFILES_PATH", profiles_path)
    monkeypatch.setattr("app.llm.registry.MODEL_CONFIG_PATH", model_config_path)
    monkeypatch.setattr("app.llm.registry.SECRETS_PATH", secrets_path)

    client = TestClient(app)
    response = client.post(
        "/settings/model-profiles",
        data={
            "name": "DeepSeek 日常问答",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "use_case": "知识库问答",
        },
    )

    assert response.status_code == 200
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    settings = json.loads(model_config_path.read_text(encoding="utf-8"))
    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert profiles["profiles"][0]["name"] == "DeepSeek 日常问答"
    assert profiles["active_profile_id"] == profiles["profiles"][0]["id"]
    assert settings["default_provider"] == "deepseek"
    assert settings["default_model"] == "deepseek-v4-flash"
    assert secrets["api_keys"]["deepseek"] == "sk-test"


def test_openai_compatible_provider_uses_bearer_auth_by_default():
    provider = OpenAICompatibleProvider("deepseek", "https://api.example.com", "sk-test", ["model-a"])

    headers = provider._headers()

    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"


def test_openai_compatible_provider_supports_custom_auth_header_without_scheme():
    provider = OpenAICompatibleProvider(
        "baidu_internal",
        "https://oneapi-comate.baidu-int.com",
        "ak-test",
        ["gpt-5.5"],
        provider_config={"auth_header": "X-API-Key", "auth_scheme": ""},
    )

    headers = provider._headers()

    assert headers["X-API-Key"] == "ak-test"
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_model_connection_reports_uuap_redirect_without_leaking_key(monkeypatch):
    providers = {
        "baidu_internal": {
            "api_style": "openai_compatible",
            "base_url": "https://oneapi-comate.baidu-int.com",
            "models": ["gpt-5.5"],
        }
    }
    monkeypatch.setattr("app.llm.registry.get_providers", lambda: providers)
    monkeypatch.setattr("app.llm.registry.read_json", lambda path, default=None: {"api_keys": {"baidu_internal": "secret-key"}})

    async def fake_test_connection(self):
        raise RuntimeError("uuap_redirect: 请求被 UUAP 登录页拦截 https://uuap.baidu.com/login")

    monkeypatch.setattr(OpenAICompatibleProvider, "test_connection", fake_test_connection)

    result = asyncio.run(run_model_connection_test("baidu_internal", "gpt-5.5"))

    assert result["ok"] is False
    assert result["error"] == "uuap_redirect"
    assert "UUAP" in result["message"]
    assert "secret-key" not in result["message"]


def test_baidu_internal_profile_save_activates_provider_and_model(tmp_path, monkeypatch):
    profiles_path = tmp_path / "model_profiles.json"
    model_config_path = tmp_path / "model_config.json"
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr("app.api.routes.MODEL_PROFILES_PATH", profiles_path)
    monkeypatch.setattr("app.llm.registry.MODEL_CONFIG_PATH", model_config_path)
    monkeypatch.setattr("app.llm.registry.SECRETS_PATH", secrets_path)

    client = TestClient(app)
    response = client.post(
        "/settings/model-profiles",
        data={
            "name": "百度内部问答",
            "provider": "baidu_internal",
            "model": "gpt-5.5",
            "api_key": "ak-test",
            "use_case": "知识库问答",
        },
    )

    assert response.status_code == 200
    settings = json.loads(model_config_path.read_text(encoding="utf-8"))
    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert settings["default_provider"] == "baidu_internal"
    assert settings["default_model"] == "gpt-5.5"
    assert secrets["api_keys"]["baidu_internal"] == "ak-test"


def test_settings_page_shows_uuap_redirect_diagnostic():
    client = TestClient(app)

    response = client.get("/ui/settings?test=failed&provider=baidu_internal&error=uuap_redirect")

    assert response.status_code == 200
    assert "连接测试：失败" in response.text
    assert "百度内部请求被 UUAP 登录页拦截" in response.text


def test_agent_model_failure_message_uses_current_provider_not_deepseek(monkeypatch):
    class FakeProvider:
        provider_name = "baidu_internal"

        async def chat(self, *args, **kwargs):
            raise RuntimeError("uuap_redirect: 请求被 UUAP 登录页拦截")

    monkeypatch.setattr("app.agent.runner.get_provider_runtime", lambda provider_id=None, model=None: (
        FakeProvider(),
        "gpt-5.5",
        {"api_style": "openai_compatible", "base_url": "https://oneapi-comate.baidu-int.com", "display_name": "百度内部"},
    ))
    monkeypatch.setattr("app.agent.runner.KnowledgeSearchTool", lambda db: type("Search", (), {"run": lambda self, q: type("Result", (), {"content": "", "metadata": {"items": []}})()})())

    answer = asyncio.run(AgentRunner(db=None).answer_question("测试问题"))

    assert "百度内部" in answer.answer
    assert "DeepSeek API Key" not in answer.answer
    assert "UUAP" in answer.answer
