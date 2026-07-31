from datetime import datetime, timedelta

from smarthome import database


def chat(client, message, session_id="context-test", selected_device_id=None):
    return client.post(
        "/api/chat",
        json={
            "message": message,
            "session_id": session_id,
            "selected_device_id": selected_device_id,
        },
    ).get_json()


def capability(client, device_id, name):
    devices = client.get("/api/state").get_json()["devices"]
    device = next(item for item in devices if item["id"] == device_id)
    return next(
        item for item in device["capabilities"] if item["capability"] == name
    )


def test_relative_adjustment_uses_previous_device(client):
    first = chat(client, "打开客厅风扇")
    result = chat(client, "再快一点")

    assert first["context_device_id"] == "fan-1"
    assert result["intent"] == "set_device_level"
    assert result["actions"][0]["value"] == 60
    assert result["context_device_id"] == "fan-1"


def test_pronoun_can_control_previous_device(client):
    chat(client, "打开客厅灯")
    result = chat(client, "把它关掉")

    assert result["intent"] == "control_device"
    assert result["actions"][0]["device_id"] == "light-1"
    assert result["actions"][0]["state"] == "off"


def test_absolute_level_can_omit_repeated_device_name(client):
    chat(client, "打开客厅风扇")
    result = chat(client, "调到70%")

    assert result["actions"][0]["value"] == 70
    assert capability(client, "fan-1", "speed")["value"] == 70


def test_unique_capability_can_be_resolved_without_cloud_or_context(client):
    result = chat(client, "把风速调到70%", session_id="unique-capability")

    assert result["intent"] == "set_device_level"
    assert result["actions"][0]["device_id"] == "fan-1"
    assert result["context_device_id"] == "fan-1"


def test_selected_device_can_start_context(client):
    result = chat(
        client,
        "暗一点",
        session_id="selected-context",
        selected_device_id="light-1",
    )

    assert result["actions"][0]["device_id"] == "light-1"
    assert result["actions"][0]["value"] == 90


def test_absolute_level_outside_safe_range_is_rejected(client):
    chat(client, "打开客厅风扇")
    result = chat(client, "调到150%")

    assert result["actions"] == []
    assert "0～100%" in result["reply"]
    assert capability(client, "fan-1", "speed")["value"] == 50


def test_context_is_isolated_by_session(client):
    chat(client, "打开客厅风扇", session_id="session-a")
    result = chat(client, "再快一点", session_id="session-b")

    assert result["intent"] == "context_missing"
    assert result["actions"] == []


def test_expired_context_is_not_used(app, client):
    with app.app_context():
        database.remember_session_device("expired", "fan-1")
        expired_at = (
            datetime.now().astimezone() - timedelta(minutes=31)
        ).isoformat(timespec="seconds")
        database.get_db().execute(
            """
            UPDATE assistant_context
            SET updated_at = ?
            WHERE session_id = ?
            """,
            (expired_at, "expired"),
        )
        database.get_db().commit()

    result = chat(client, "再快一点", session_id="expired")
    assert result["intent"] == "context_missing"


def test_contextual_adjustment_can_be_undone(client):
    chat(client, "打开客厅风扇")
    chat(client, "再快一点")
    result = chat(client, "撤销刚才的操作")

    assert result["intent"] == "undo_last_action"
    assert capability(client, "fan-1", "speed")["value"] == 50


def test_contextual_absolute_commands_feed_automatic_learning(client):
    chat(client, "打开客厅风扇")
    chat(client, "调到60%")
    chat(client, "调到60%")
    result = chat(client, "调到60%")

    assert result["learning"][0]["learned"] is True
    memories = client.get("/api/memories").get_json()["memories"]
    assert memories[0]["name"] == "fan_speed"


def test_web_device_control_updates_conversation_context(client):
    response = client.patch(
        "/api/devices/light-1",
        json={"state": "on", "session_id": "web-control"},
    ).get_json()
    result = chat(client, "把它关掉", session_id="web-control")

    assert response["context_device_id"] == "light-1"
    assert result["actions"][0]["device_id"] == "light-1"
    assert result["actions"][0]["state"] == "off"


def test_web_slider_updates_conversation_context(client):
    client.patch(
        "/api/devices/fan-1/capabilities/speed",
        json={"value": 60, "session_id": "web-slider"},
    )
    result = chat(client, "再快一点", session_id="web-slider")

    assert result["actions"][0]["device_id"] == "fan-1"
    assert result["actions"][0]["value"] == 70


def test_frontend_displays_conversation_device_context(client):
    script = client.get("/app.js").get_data(as_text=True)

    assert "result.context_device_id" in script
    assert "contextDeviceId" in script
    assert "当前设备：" in script
