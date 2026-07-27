import io

from smarthome.agent import SmartHomeAgent


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


class FakeToolCallingLlm:
    enabled = True
    model = "test-model"

    def __init__(self, device_name="客厅风扇"):
        self.device_name = device_name
        self.calls = 0

    def chat(self, messages, **options):
        self.calls += 1
        if options.get("tools"):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "control_device",
                            "arguments": (
                                '{"device_name":"'
                                + self.device_name
                                + '","state":"on"}'
                            ),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "好的，已经按你的要求处理。",
        }


class FakeConversationLlm:
    enabled = True
    model = "test-model"

    def __init__(self):
        self.requests = []

    def chat(self, messages, **_options):
        self.requests.append(messages)
        reply = "我记住了。" if len(self.requests) == 1 else "它指的是客厅风扇。"
        return {"role": "assistant", "content": reply}


class FakeRetryToolLlm:
    enabled = True
    model = "test-model"

    def __init__(self):
        self.calls = 0

    def chat(self, _messages, **options):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "已为您关闭风扇，目前没有降温设备。",
            }
        if options.get("tools"):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "retry-call",
                        "type": "function",
                        "function": {
                            "name": "control_device",
                            "arguments": (
                                '{"device_name":"客厅风扇","state":"on"}'
                            ),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "已打开客厅风扇，需要时还可以调高风速。",
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


def test_agent_executes_validated_device_tool(app, client):
    fake = FakeToolCallingLlm()
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "有点热，帮我凉快一点",
            "session_id": "tool-session",
        },
    ).get_json()

    assert result["intent"] == "control_device"
    assert result["actions"][0]["device_id"] == "fan-1"
    assert result["ai"]["provider"] == "cloud"
    assert fake.calls == 2
    fan = next(
        device
        for device in client.get("/api/state").get_json()["devices"]
        if device["id"] == "fan-1"
    )
    assert fan["state"] == "on"


def test_agent_keeps_short_conversation_history(app, client):
    fake = FakeConversationLlm()
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    client.post(
        "/api/chat",
        json={"message": "客厅风扇是放在书房的吗", "session_id": "memory-session"},
    )
    result = client.post(
        "/api/chat",
        json={"message": "它刚才有什么问题", "session_id": "memory-session"},
    ).get_json()

    second_messages = fake.requests[1]
    assert any(
        message["role"] == "user"
        and message["content"] == "客厅风扇是放在书房的吗"
        for message in second_messages
    )
    assert any(
        message["role"] == "assistant" and message["content"] == "我记住了。"
        for message in second_messages
    )
    assert result["reply"] == "它指的是客厅风扇。"


def test_agent_retries_with_tool_when_action_request_returns_only_text(app, client):
    fake = FakeRetryToolLlm()
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "我现在有点热，但不要开加湿器，帮我凉快一点。",
            "session_id": "retry-session",
        },
    ).get_json()

    assert fake.calls == 3
    assert result["actions"][0]["device_id"] == "fan-1"
    assert result["actions"][0]["state"] == "on"
    assert result["reply"] == "已打开客厅风扇。"


def test_agent_isolates_conversation_sessions(app, client):
    fake = FakeConversationLlm()
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    client.post(
        "/api/chat",
        json={"message": "这是甲会话", "session_id": "session-a"},
    )
    client.post(
        "/api/chat",
        json={"message": "这是乙会话", "session_id": "session-b"},
    )

    second_messages = fake.requests[1]
    assert not any(
        message.get("content") in {"这是甲会话", "我记住了。"}
        for message in second_messages
    )


def test_agent_cannot_control_unknown_device(app, client):
    fake = FakeToolCallingLlm("卧室空调")
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={"message": "打开卧室空调", "session_id": "safe-session"},
    ).get_json()

    assert result["actions"] == []
    assert all(
        device["state"] == "off"
        for device in client.get("/api/state").get_json()["devices"]
    )
