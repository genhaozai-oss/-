from dotenv import dotenv_values

import configure_ai


def test_ai_configuration_is_saved_for_future_startups(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(configure_ai, "ENV_PATH", env_path)
    monkeypatch.setattr(configure_ai, "getpass", lambda _prompt: "sk-ws-test-key")

    configure_ai.main()

    values = dotenv_values(env_path)
    assert values["SMARTHOME_LLM_BASE_URL"] == configure_ai.BASE_URL
    assert values["SMARTHOME_LLM_MODEL"] == "qwen-plus"
    assert values["SMARTHOME_LLM_API_KEY"] == "sk-ws-test-key"
