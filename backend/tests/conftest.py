import pytest

from smarthome import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "WEATHER_TIMEOUT_SECONDS": 0.1,
            "LLM_BASE_URL": "",
            "LLM_API_KEY": "",
            "LLM_MODEL": "",
            "SPEECH_CLOUD_BASE_URL": "",
            "SPEECH_CLOUD_API_KEY": "",
            "TTS_BASE_URL": "",
            "TTS_API_KEY": "",
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()
