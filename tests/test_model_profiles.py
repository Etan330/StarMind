import json

from fastapi.testclient import TestClient

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
