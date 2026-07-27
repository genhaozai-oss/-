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

