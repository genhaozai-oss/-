from dotenv import dotenv_values

import configure_ai
import configure_doubao_tts
import configure_weather


def test_ai_configuration_is_saved_for_future_startups(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(configure_ai, "ENV_PATH", env_path)
    answers = iter(["", "ws-missing-prefix", "sk-ws-test-key-1234567890"])
    monkeypatch.setattr(configure_ai, "read_secret", lambda _prompt: next(answers))

    configure_ai.main()

    values = dotenv_values(env_path)
    assert values["SMARTHOME_LLM_BASE_URL"] == configure_ai.BASE_URL
    assert values["SMARTHOME_LLM_MODEL"] == "qwen-plus"
    assert values["SMARTHOME_LLM_API_KEY"] == "sk-ws-test-key-1234567890"


def test_token_plan_key_is_rejected():
    message = configure_ai.validate_key("sk-sp-12345678901234567890")
    assert "Token Plan" in message


def test_doubao_tts_configuration_is_saved(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(configure_doubao_tts, "ENV_PATH", env_path)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "doubao-api-key-1234567890",
    )

    assert configure_doubao_tts.main() == 0

    values = dotenv_values(env_path)
    assert values["SMARTHOME_DOUBAO_TTS_API_KEY"] == (
        "doubao-api-key-1234567890"
    )
    assert values["SMARTHOME_DOUBAO_TTS_RESOURCE_ID"] == "seed-tts-2.0"
    assert values["SMARTHOME_DOUBAO_TTS_VOICE"] == (
        "zh_female_vv_uranus_bigtts"
    )


def test_weather_configuration_is_saved_after_connection_check(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(configure_weather, "ENV_PATH", env_path)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "abc123.qweatherapi.com",
    )
    monkeypatch.setattr(
        configure_weather,
        "read_secret",
        lambda _prompt: "weather-key-123456",
    )
    monkeypatch.setattr(
        configure_weather,
        "test_credentials",
        lambda _host, _key: (True, "连接成功"),
    )

    configure_weather.main()

    values = dotenv_values(env_path)
    assert values["SMARTHOME_WEATHER_API_HOST"] == "abc123.qweatherapi.com"
    assert values["SMARTHOME_WEATHER_API_KEY"] == "weather-key-123456"


def test_weather_host_rejects_full_api_path():
    host = configure_weather.normalize_host(
        "https://abc123.qweatherapi.com/v7/weather/now"
    )
    assert configure_weather.validate_host(host) is not None
