import io


class FakeLlm:
    enabled = True

    def classify(self, message, devices):
        assert message == "能不能让客厅凉快一点"
        return {
            "intent": "device_control",
            "device_name": "客厅风扇",
            "state": "on",
        }


class FakeSpeechRecognizer:
    available = True

    def transcribe(self, audio_path):
        assert audio_path.exists()
        return {
            "text": "打开客厅风扇",
            "language": "zh",
            "language_probability": 0.99,
        }


class FakeConnectionLlm:
    enabled = True

    def __init__(self):
        self.connected = False

    def classify(self, message, devices):
        assert "介绍自己" in message
        assert devices
        self.connected = True
        return {
            "intent": "conversation",
            "reply": "你好，我是家庭智能管理助手。",
        }

    def status(self):
        return {
            "state": "connected" if self.connected else "ready",
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key_configured": True,
            "last_success_at": "2026-07-27T00:00:00+00:00"
            if self.connected
            else None,
            "last_error": None,
        }


def test_unknown_text_can_use_validated_llm_plan(app, client):
    app.extensions["llm_interpreter"] = FakeLlm()
    result = client.post(
        "/api/chat",
        json={"message": "能不能让客厅凉快一点"},
    ).get_json()
    assert result["intent"] == "device_control"
    assert result["actions"][0]["device_id"] == "fan-1"
    assert result["actions"][0]["state"] == "on"


def test_voice_transcription_runs_through_same_intent_pipeline(app, client):
    app.extensions["speech_recognizer"] = FakeSpeechRecognizer()
    result = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"fake-audio"), "voice.webm")},
        content_type="multipart/form-data",
    ).get_json()
    assert result["transcription"]["text"] == "打开客厅风扇"
    assert result["result"]["actions"][0]["state"] == "on"


def test_ai_status_and_connection_probe(app, client):
    app.extensions["llm_interpreter"] = FakeConnectionLlm()

    status = client.get("/api/ai/status").get_json()
    assert status["state"] == "ready"
    assert status["api_key_configured"] is True

    response = client.post("/api/ai/test")
    assert response.status_code == 200
    result = response.get_json()
    assert result["ok"] is True
    assert result["status"]["state"] == "connected"
