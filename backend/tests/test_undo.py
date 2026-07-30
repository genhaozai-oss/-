from smarthome.undo import is_undo_request


def get_device(client, device_id):
    devices = client.get("/api/state").get_json()["devices"]
    return next(device for device in devices if device["id"] == device_id)


def get_capability(device, capability):
    return next(
        item["value"]
        for item in device["capabilities"]
        if item["capability"] == capability
    )


def test_chat_device_control_can_be_undone(client):
    result = client.post(
        "/api/chat",
        json={"message": "打开客厅风扇"},
    ).get_json()
    assert result["intent"] == "control_device"
    assert get_device(client, "fan-1")["state"] == "on"

    undone = client.post(
        "/api/chat",
        json={"message": "撤销刚才的操作"},
    ).get_json()

    assert undone["ok"] is True
    assert undone["intent"] == "undo_last_action"
    assert get_device(client, "fan-1")["state"] == "off"


def test_web_capability_change_can_be_undone(client):
    before = get_capability(get_device(client, "fan-1"), "speed")
    response = client.patch(
        "/api/devices/fan-1/capabilities/speed",
        json={"value": 80},
    )
    assert response.status_code == 200
    assert get_capability(get_device(client, "fan-1"), "speed") == 80

    undone = client.post(
        "/api/chat",
        json={"message": "撤销上一步"},
    ).get_json()

    assert undone["ok"] is True
    assert get_capability(get_device(client, "fan-1"), "speed") == before


def test_multiple_actions_can_be_undone_in_order(client):
    client.patch("/api/devices/fan-1", json={"state": "on"})
    client.patch(
        "/api/devices/fan-1/capabilities/speed",
        json={"value": 75},
    )

    client.post("/api/chat", json={"message": "撤销刚才的操作"})
    fan = get_device(client, "fan-1")
    assert fan["state"] == "on"
    assert get_capability(fan, "speed") != 75

    client.post("/api/chat", json={"message": "再撤销一次"})
    assert get_device(client, "fan-1")["state"] == "off"


def test_undo_without_previous_action_has_clear_reply(client):
    result = client.post(
        "/api/chat",
        json={"message": "撤销刚才的操作"},
    ).get_json()

    assert result["ok"] is False
    assert result["intent"] == "undo_last_action"
    assert "没有可以撤销" in result["reply"]


def test_negative_phrase_is_not_treated_as_undo():
    assert is_undo_request("不要撤销，保持现在这样") is False
    assert is_undo_request("怎么撤销自动化规则") is False
    assert is_undo_request("撤销刚才的操作") is True
