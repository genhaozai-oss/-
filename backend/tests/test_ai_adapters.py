import io
import json

from smarthome import database
from smarthome.agent import SmartHomeAgent
from smarthome.voice import SpeechRecognitionError, SpeechRecognizer


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

    def transcribe(self, audio_path, mime_type=None, context_terms=None):
        assert audio_path.exists()
        assert mime_type == "audio/webm"
        assert "客厅风扇" in context_terms
        return {
            "text": "打开客厅风扇。",
            "language": "zh",
            "language_probability": 0.99,
            "provider": "aliyun",
            "provider_label": "阿里云 Qwen3-ASR",
            "model": "qwen3-asr-flash",
            "latency_ms": 320,
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


class FakeDynamicToolLlm:
    enabled = True
    model = "test-model"

    def __init__(self, name, arguments, reply):
        self.name = name
        self.arguments = arguments
        self.reply = reply
        self.calls = 0

    def chat(self, _messages, **options):
        self.calls += 1
        if options.get("tools"):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "dynamic-call",
                        "type": "function",
                        "function": {
                            "name": self.name,
                            "arguments": self.arguments,
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": self.reply}


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
    transcription_response = client.post(
        "/api/voice/transcribe",
        data={
            "audio": (
                io.BytesIO(b"fake-audio"),
                "voice.webm",
                "audio/webm",
            ),
            "execute": "0",
        },
        content_type="multipart/form-data",
    )
    assert transcription_response.status_code == 200
    transcription = transcription_response.get_json()["transcription"]
    assert transcription["text"] == "打开客厅风扇。"
    assert transcription["provider"] == "aliyun"

    result = client.post(
        "/api/chat",
        json={"message": transcription["text"]},
    ).get_json()
    assert result["actions"][0]["state"] == "on"


def test_voice_endpoint_keeps_execute_compatibility(app, client):
    app.extensions["speech_recognizer"] = FakeSpeechRecognizer()
    result = client.post(
        "/api/voice/transcribe",
        data={
            "audio": (
                io.BytesIO(b"fake-audio"),
                "voice.webm",
                "audio/webm",
            )
        },
        content_type="multipart/form-data",
    ).get_json()

    assert result["transcription"]["text"] == "打开客厅风扇。"
    assert result["result"]["actions"][0]["state"] == "on"


def test_cloud_speech_uses_context_and_returns_diagnostics(
    app,
    tmp_path,
    monkeypatch,
):
    app.config.update(
        {
            "SPEECH_CLOUD_BASE_URL": "https://example.test/v1",
            "SPEECH_CLOUD_API_KEY": "test-key",
            "SPEECH_CLOUD_MODEL": "qwen3-asr-flash",
        }
    )
    audio_path = tmp_path / "voice.webm"
    audio_path.write_bytes(b"fake-webm")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        response = {
            "choices": [
                {
                    "message": {
                        "content": "打开卧室灯",
                        "annotations": [
                            {
                                "type": "audio_info",
                                "language": "zh",
                                "emotion": "neutral",
                            }
                        ],
                    }
                }
            ],
            "usage": {"seconds": 2},
        }
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr("smarthome.voice.urlopen", fake_urlopen)
    recognizer = SpeechRecognizer(app)
    result = recognizer.transcribe(
        audio_path,
        mime_type="audio/webm;codecs=opus",
        context_terms=["卧室灯", "卧室"],
    )

    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert captured["request"].full_url.endswith("/chat/completions")
    assert captured["request"].headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "qwen3-asr-flash"
    assert "卧室灯" in payload["messages"][0]["content"][0]["text"]
    assert payload["messages"][1]["content"][0]["input_audio"]["data"].startswith(
        "data:audio/webm;base64,"
    )
    assert payload["asr_options"] == {
        "language": "zh",
        "enable_itn": True,
    }
    assert result["text"] == "打开卧室灯"
    assert result["provider"] == "aliyun"
    assert result["emotion"] == "neutral"
    assert result["audio_seconds"] == 2
    assert result["latency_ms"] >= 0


def test_cloud_speech_failure_falls_back_to_local(app, tmp_path, monkeypatch):
    app.config.update(
        {
            "SPEECH_CLOUD_BASE_URL": "https://example.test/v1",
            "SPEECH_CLOUD_API_KEY": "test-key",
            "SPEECH_CLOUD_MODEL": "qwen3-asr-flash",
        }
    )
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wav")
    recognizer = SpeechRecognizer(app)

    def fail_cloud(*_args):
        raise SpeechRecognitionError("模拟云端超时")

    def use_local(*_args):
        return {
            "text": "打开书房风扇",
            "language": "zh",
            "provider": "local",
            "provider_label": "本地 Whisper",
            "model": "small",
        }

    monkeypatch.setattr(
        SpeechRecognizer,
        "local_available",
        property(lambda _self: True),
    )
    monkeypatch.setattr(recognizer, "_transcribe_cloud", fail_cloud)
    monkeypatch.setattr(recognizer, "_transcribe_local", use_local)

    result = recognizer.transcribe(audio_path, mime_type="audio/wav")

    assert result["text"] == "打开书房风扇"
    assert result["provider"] == "local"
    assert result["fallback_from"] == "aliyun"
    assert recognizer.last_cloud_error == "模拟云端超时"


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


def test_explicit_weather_query_bypasses_cloud_conversation(app, client):
    fake = FakeConversationLlm()
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={"message": "今天天气怎么样", "session_id": "weather-routing"},
    ).get_json()

    assert result["intent"] == "weather_query"
    assert result["weather"]["provider"] == "qweather"
    assert fake.requests == []


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
    assert "调高风速" in result["reply"]


def test_agent_adjusts_registered_device_capability(app, client):
    fake = FakeDynamicToolLlm(
        "set_device_level",
        '{"device_name":"客厅风扇","capability":"speed","value":70}',
        "已将客厅风扇风速调到70%。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={"message": "把风速调到70%", "session_id": "level-session"},
    ).get_json()

    assert result["intent"] == "set_device_level"
    assert result["actions"][0]["capability"] == "speed"
    assert result["actions"][0]["value"] == 70


def test_agent_remembers_user_preference(app, client):
    fake = FakeDynamicToolLlm(
        "remember_preference",
        '{"preference":"fan_speed","value":60}',
        "记住了，你常用60%风速。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={"message": "记住我的常用风速是60%", "session_id": "preference-session"},
    ).get_json()

    assert result["intent"] == "remember_preference"
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"


def test_agent_forgets_only_requested_preference(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")
        database.set_user_preference("humidity", "55")
    fake = FakeDynamicToolLlm(
        "forget_preference",
        '{"preference":"fan_speed"}',
        "已忘记你的常用风速。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "忘掉我的常用风速",
            "session_id": "forget-preference",
        },
    ).get_json()

    assert result["intent"] == "forget_preference"
    with app.app_context():
        assert database.get_user_preferences() == {"humidity": "55"}


def test_agent_creates_persistent_environment_automation(app, client):
    fake = FakeDynamicToolLlm(
        "create_automation",
        (
            '{"sensor":"humidity","operator":"above","threshold":70,'
            '"device_name":"抽湿器演示","action":"on"}'
        ),
        "已记住，湿度高于70%时自动打开抽湿器演示。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "以后湿度超过70%就自动打开抽湿器",
            "session_id": "automation-session",
        },
    ).get_json()

    assert result["intent"] == "create_automation"
    assert result["automation"]["threshold"] == 70
    with app.app_context():
        assert database.list_automation_rules()[0]["device_id"] == "dehumidifier-1"


def test_agent_saves_and_runs_custom_scene(app, client):
    save_fake = FakeDynamicToolLlm(
        "save_scene",
        (
            '{"name":"睡眠模式","actions":['
            '{"device_name":"客厅灯","action":"off"},'
            '{"device_name":"客厅风扇","action":"set_level",'
            '"capability":"speed","value":30}]}'
        ),
        "睡眠模式已经记住了。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(save_fake)
    saved = client.post(
        "/api/chat",
        json={
            "message": "记住睡眠模式，关灯并把风扇调到30%",
            "session_id": "scene-save",
        },
    ).get_json()

    assert saved["intent"] == "save_scene"
    assert saved["scene"]["name"] == "睡眠模式"

    run_fake = FakeDynamicToolLlm(
        "run_scene",
        '{"name":"睡眠模式"}',
        "睡眠模式已执行。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(run_fake)
    executed = client.post(
        "/api/chat",
        json={
            "message": "执行睡眠模式",
            "session_id": "scene-run",
        },
    ).get_json()

    assert executed["intent"] == "run_scene"
    assert any(action.get("state") == "on" for action in executed["actions"])
    with app.app_context():
        assert database.get_device_capability("fan-1", "speed")["value"] == 30


def test_agent_learns_new_capability_for_selected_device(app, client):
    fake = FakeDynamicToolLlm(
        "remember_device_capability",
        '{"device_name":"这个设备","capability":"position"}',
        "已记住这个设备支持位置调节。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "记住，这个设备以后支持位置调节",
            "selected_device_id": "light-1",
            "session_id": "capability-memory-session",
        },
    ).get_json()

    assert result["intent"] == "remember_device_capability"
    with app.app_context():
        capability = database.get_device_capability("light-1", "position")
    assert capability["learned"] == 1


def test_agent_rename_synchronizes_room_from_new_name(app, client):
    fake = FakeDynamicToolLlm(
        "rename_device",
        '{"device_name":"客厅灯","new_name":"厕所灯"}',
        "记住了，客厅灯现在叫厕所灯。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={"message": "把客厅灯改名为厕所灯", "session_id": "rename-room"},
    ).get_json()

    assert result["actions"][0]["room"] == "厕所"
    with app.app_context():
        assert database.get_device("light-1")["room"] == "厕所"


def test_agent_can_update_selected_device_location(app, client):
    fake = FakeDynamicToolLlm(
        "update_device_location",
        '{"device_name":"这个设备","room":"书房"}',
        "已记住，这个设备在书房。",
    )
    app.extensions["assistant_agent"] = SmartHomeAgent(fake)

    result = client.post(
        "/api/chat",
        json={
            "message": "这个设备放在书房",
            "selected_device_id": "fan-1",
            "session_id": "device-location",
        },
    ).get_json()

    assert result["intent"] == "update_device_location"
    with app.app_context():
        assert database.get_device("fan-1")["room"] == "书房"


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
