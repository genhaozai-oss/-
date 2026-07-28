import json

import pytest

from smarthome.tts import SpeechSynthesisError, SpeechSynthesizer


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return json.dumps(self.payload).encode("utf-8")


def configured_synthesizer(app):
    app.config.update(
        {
            "TTS_BASE_URL": "https://dashscope.aliyuncs.com/api/v1",
            "TTS_API_KEY": "sk-test",
            "TTS_MODEL": "qwen3-tts-flash",
            "TTS_VOICE": "Cherry",
        }
    )
    return SpeechSynthesizer(app)


def test_cloud_tts_uses_chinese_voice_and_returns_audio_url(
    app, monkeypatch
):
    synthesizer = configured_synthesizer(app)

    def fake_urlopen(request, timeout):
        assert timeout == 12
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["model"] == "qwen3-tts-flash"
        assert payload["input"] == {
            "text": "欢迎回家。",
            "voice": "Cherry",
            "language_type": "Chinese",
        }
        return FakeResponse(
            {
                "output": {
                    "audio": {
                        "url": "https://example.oss-cn-beijing.aliyuncs.com/test.wav"
                    }
                }
            }
        )

    monkeypatch.setattr("smarthome.tts.urlopen", fake_urlopen)
    result = synthesizer.synthesize("欢迎回家。")

    assert result["provider"] == "aliyun"
    assert result["audio_url"].endswith("/test.wav")


def test_cloud_tts_rejects_untrusted_audio_url(app, monkeypatch):
    synthesizer = configured_synthesizer(app)
    monkeypatch.setattr(
        "smarthome.tts.urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {"output": {"audio": {"url": "https://example.com/audio.wav"}}}
        ),
    )

    with pytest.raises(SpeechSynthesisError, match="不可信"):
        synthesizer.synthesize("测试")


def test_cloud_tts_upgrades_trusted_audio_url_to_https(app, monkeypatch):
    synthesizer = configured_synthesizer(app)
    monkeypatch.setattr(
        "smarthome.tts.urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "output": {
                    "audio": {
                        "url": (
                            "http://dashscope-result-bj."
                            "oss-cn-beijing.aliyuncs.com/test.wav"
                        )
                    }
                }
            }
        ),
    )

    result = synthesizer.synthesize("测试")

    assert result["audio_url"].startswith("https://")


def test_tts_endpoint_returns_synthesis_result(app, client):
    class FakeSynthesizer:
        available = True

        def synthesize(self, text):
            assert text == "欢迎回家。"
            return {
                "audio_url": (
                    "https://example.oss-cn-beijing.aliyuncs.com/test.wav"
                ),
                "provider": "aliyun",
                "model": "qwen3-tts-flash",
            }

    app.extensions["speech_synthesizer"] = FakeSynthesizer()
    response = client.post(
        "/api/voice/synthesize",
        json={"text": "欢迎回家。"},
    )

    assert response.status_code == 200
    assert response.get_json()["provider"] == "aliyun"


def test_tts_endpoint_requires_configuration(client):
    response = client.post(
        "/api/voice/synthesize",
        json={"text": "欢迎回家。"},
    )

    assert response.status_code == 503
    assert "尚未配置" in response.get_json()["error"]
