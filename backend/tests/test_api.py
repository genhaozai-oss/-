def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_initial_state_contains_simulated_devices(client):
    state = client.get("/api/state").get_json()
    assert state["environment"]["temperature"] == 27.0
    assert len(state["devices"]) == 4
    assert all(device["is_virtual"] for device in state["devices"])


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
