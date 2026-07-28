import io


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_index_includes_optional_chinese_voice_reply(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert 'id="voiceReplyButton"' in html
    assert "语音播报：关" in html
    assert "speechSynthesis" in script
    assert "SpeechSynthesisUtterance" in script
    assert "/api/voice/synthesize" in script
    assert 'id="ttsVoiceSelect"' in html
    assert 'id="previewVoiceButton"' in html
    assert "Extended_Pictographic" in script


def test_index_includes_persistent_automation_manager(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert 'id="automationList"' in html
    assert "我的自动化" in html
    assert "/api/automations/" in script


def test_index_includes_custom_scene_manager(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert 'id="sceneList"' in html
    assert "我的场景" in html
    assert "/api/scenes/" in script


def test_index_includes_local_memory_and_decision_center(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert 'id="memoryList"' in html
    assert 'id="eventList"' in html
    assert "AI 记忆中心" in html
    assert "/api/memories/" in script


def test_initial_state_contains_simulated_devices(client):
    state = client.get("/api/state").get_json()
    assert state["environment"]["temperature"] == 27.0
    assert len(state["devices"]) == 4
    assert all(device["is_virtual"] for device in state["devices"])
    fan = next(device for device in state["devices"] if device["id"] == "fan-1")
    light = next(device for device in state["devices"] if device["id"] == "light-1")
    assert fan["capabilities"][0]["capability"] == "speed"
    assert light["capabilities"][0]["capability"] == "brightness"


def test_text_command_controls_fan(client):
    response = client.post("/api/chat", json={"message": "打开客厅风扇"})
    result = response.get_json()
    assert result["intent"] == "control_device"
    assert result["actions"][0]["state"] == "on"

    devices = client.get("/api/state").get_json()["devices"]
    fan = next(device for device in devices if device["id"] == "fan-1")
    assert fan["state"] == "on"


def test_selected_device_can_be_renamed_and_remembered(client):
    response = client.post(
        "/api/chat",
        json={"message": "这个设备叫做厨房风扇", "selected_device_id": "fan-1"},
    )
    assert response.get_json()["intent"] == "rename_device"

    response = client.post("/api/chat", json={"message": "打开厨房风扇"})
    result = response.get_json()
    assert result["actions"][0]["device_name"] == "厨房风扇"
    fan = next(
        device
        for device in client.get("/api/state").get_json()["devices"]
        if device["id"] == "fan-1"
    )
    assert fan["room"] == "厨房"


def test_pronoun_rename_requires_selected_device(client):
    response = client.post(
        "/api/chat",
        json={"message": "这个设备叫做厨房灯"},
    )
    assert "请先" in response.get_json()["reply"]


def test_duplicate_device_name_is_rejected_without_server_error(client):
    response = client.post(
        "/api/chat",
        json={"message": "这个设备叫做客厅灯", "selected_device_id": "fan-1"},
    )
    assert response.status_code == 200
    assert "已经有设备" in response.get_json()["reply"]


def test_hot_environment_turns_fan_on(client):
    response = client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    )
    actions = response.get_json()["actions"]
    assert any(
        action["device_id"] == "fan-1" and action["state"] == "on"
        for action in actions
    )


def test_humidifier_and_dehumidifier_are_mutually_exclusive(client):
    wet = client.post(
        "/api/environment",
        json={"temperature": 26, "humidity": 80},
    ).get_json()
    assert any(
        action["device_id"] == "dehumidifier-1" and action["state"] == "on"
        for action in wet["actions"]
    )

    dry = client.post(
        "/api/environment",
        json={"temperature": 26, "humidity": 30},
    ).get_json()
    assert any(
        action["device_id"] == "dehumidifier-1" and action["state"] == "off"
        for action in dry["actions"]
    )
    assert any(
        action["device_id"] == "humidifier-1" and action["state"] == "on"
        for action in dry["actions"]
    )


def test_home_arrival_scene_returns_warm_reply(client):
    result = client.post(
        "/api/chat",
        json={"message": "我要下班回家了"},
    ).get_json()
    assert result["intent"] == "home_arrival"
    assert "辛苦啦" in result["reply"]
    assert "weather" in result


def test_home_arrival_reports_high_humidity_even_when_dehumidifier_is_already_on(
    client,
):
    client.post(
        "/api/environment",
        json={"temperature": 27, "humidity": 94},
    )
    result = client.post(
        "/api/chat",
        json={"message": "我要下班回家了"},
    ).get_json()

    assert "湿度偏高" in result["reply"]
    assert "抽湿" in result["reply"]
    assert "比较舒适" not in result["reply"]
    dehumidifier = next(
        device
        for device in client.get("/api/state").get_json()["devices"]
        if device["id"] == "dehumidifier-1"
    )
    assert dehumidifier["state"] == "on"


def test_location_can_be_saved_from_browser_coordinates(client):
    response = client.put(
        "/api/settings/location",
        json={
            "location_name": "当前位置",
            "latitude": 23.1291,
            "longitude": 113.2644,
        },
    )
    assert response.status_code == 200
    assert response.get_json()["settings"]["location_name"] == "当前位置"


def test_city_name_is_automatically_geocoded(app, client, monkeypatch):
    app.config["WEATHER_API_HOST"] = "test.qweatherapi.com"
    app.config["WEATHER_API_KEY"] = "test-weather-key"
    payload = io.BytesIO(
        (
            '{"code":"200","location":[{"name":"广州","id":"101280101",'
            '"lat":"23.11667","lon":"113.25","country":"中国","adm1":"广东"}]}'
        ).encode()
    )
    monkeypatch.setattr(
        "smarthome.weather.urlopen",
        lambda *_args, **_kwargs: payload,
    )

    response = client.put(
        "/api/settings/location",
        json={"location_name": "广州"},
    )

    assert response.status_code == 200
    settings = response.get_json()["settings"]
    assert settings["location_name"] == "广州"
    assert float(settings["latitude"]) == 23.11667
    assert float(settings["longitude"]) == 113.25
    assert settings["weather_location_id"] == "101280101"


def test_qweather_response_is_cached_for_ten_minutes(app, client, monkeypatch):
    app.config["WEATHER_API_HOST"] = "test.qweatherapi.com"
    app.config["WEATHER_API_KEY"] = "test-weather-key"
    responses = [
        (
            '{"code":"200","updateTime":"2026-07-27T15:00+08:00",'
            '"now":{"temp":"31","feelsLike":"35","text":"小雨",'
            '"humidity":"80","windDir":"东南风","windScale":"2",'
            '"precip":"0.4"}}'
        ).encode(),
        (
            '{"code":"200","daily":[{"tempMin":"25","tempMax":"32",'
            '"textDay":"小雨"}]}'
        ).encode(),
        (
            '{"code":"200","hourly":[{"pop":"30"},{"pop":"80"}]}'
        ).encode(),
    ]
    calls = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(True)
        return io.BytesIO(responses[len(calls) - 1])

    monkeypatch.setattr("smarthome.weather.urlopen", fake_urlopen)
    with app.app_context():
        from smarthome import database

        database.set_settings(
            {
                "location_name": "广州",
                "weather_location_id": "101280101",
                "latitude": 23.11667,
                "longitude": 113.25,
            }
        )

    first = client.get("/api/weather").get_json()
    second = client.get("/api/weather").get_json()

    assert first["available"] is True
    assert first["provider"] == "qweather"
    assert "体感35℃" in first["summary"]
    assert "最高降雨概率 80%" in first["summary"]
    assert second == first
    assert len(calls) == 3


def test_alarm_can_be_created_and_deleted(client):
    result = client.post(
        "/api/chat",
        json={"message": "明天早上七点设置闹钟"},
    ).get_json()
    assert result["intent"] == "create_alarm"
    alarm_id = result["alarm"]["id"]

    response = client.delete(f"/api/alarms/{alarm_id}")
    assert response.status_code == 204


def test_invalid_environment_is_rejected(client):
    response = client.post(
        "/api/environment",
        json={"temperature": 26, "humidity": 130},
    )
    assert response.status_code == 400


def test_device_capability_can_be_adjusted(client):
    response = client.patch(
        "/api/devices/fan-1/capabilities/speed",
        json={"value": 73},
    )
    assert response.status_code == 200
    capability = response.get_json()["capability"]
    assert capability["value"] == 70

    fan = next(
        device
        for device in client.get("/api/state").get_json()["devices"]
        if device["id"] == "fan-1"
    )
    assert fan["capabilities"][0]["value"] == 70


def test_unregistered_capability_is_rejected(client):
    response = client.patch(
        "/api/devices/fan-1/capabilities/brightness",
        json={"value": 50},
    )
    assert response.status_code == 409

    response = client.patch(
        "/api/devices/fan-1/capabilities/speed",
        json={"value": 130},
    )
    assert response.status_code == 400


def test_saved_preference_is_applied_when_device_turns_on(app, client):
    with app.app_context():
        from smarthome import database

        database.set_user_preference("fan_speed", "60")

    client.post("/api/chat", json={"message": "打开客厅风扇"})
    fan = next(
        device
        for device in client.get("/api/state").get_json()["devices"]
        if device["id"] == "fan-1"
    )
    speed = next(
        item for item in fan["capabilities"] if item["capability"] == "speed"
    )
    assert speed["value"] == 60


def test_voice_endpoint_returns_clear_install_message(app, client):
    class UnavailableSpeechRecognizer:
        available = False

    app.extensions["speech_recognizer"] = UnavailableSpeechRecognizer()
    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"not-audio"), "voice.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 503
    assert "requirements-voice.txt" in response.get_json()["error"]
