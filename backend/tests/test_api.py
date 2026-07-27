import io


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


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


def test_city_name_is_automatically_geocoded(client, monkeypatch):
    payload = io.BytesIO(
        (
            '{"results":[{"name":"广州","latitude":23.11667,'
            '"longitude":113.25,"country":"中国","admin1":"广东"}]}'
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
