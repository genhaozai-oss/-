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
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()
