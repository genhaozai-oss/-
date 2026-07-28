from smarthome import database


def test_automation_rule_is_saved_and_runs_on_environment_update(client):
    created = client.post(
        "/api/automations",
        json={
            "sensor": "humidity",
            "operator": "above",
            "threshold": 80,
            "device_name": "抽湿器演示",
            "action": "on",
        },
    )

    assert created.status_code == 201
    rule = created.get_json()["automation"]
    assert rule["description"] == "湿度高于80%时，打开抽湿器演示"

    result = client.post(
        "/api/environment",
        json={"temperature": 27, "humidity": 90},
    ).get_json()
    assert result["actions"][0]["automation_rule_id"] == rule["id"]

    state = client.get("/api/state").get_json()
    dehumidifier = next(
        device
        for device in state["devices"]
        if device["id"] == "dehumidifier-1"
    )
    assert dehumidifier["state"] == "on"
    assert state["automations"][0]["last_triggered_at"] is not None


def test_level_automation_uses_device_step_and_does_not_repeat(client):
    rule = client.post(
        "/api/automations",
        json={
            "sensor": "temperature",
            "operator": "above",
            "threshold": 29,
            "device_name": "客厅风扇",
            "action": "set_level",
            "capability": "speed",
            "value": 75,
        },
    ).get_json()["automation"]

    assert rule["value"] == 80
    first = client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    ).get_json()
    second = client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 55},
    ).get_json()

    assert {action.get("state") for action in first["actions"]} >= {"on"}
    assert any(action.get("value") == 80 for action in first["actions"])
    assert second["actions"] == []


def test_automation_can_be_disabled_and_deleted(client):
    rule = client.post(
        "/api/automations",
        json={
            "sensor": "temperature",
            "operator": "above",
            "threshold": 30,
            "device_name": "客厅风扇",
            "action": "on",
        },
    ).get_json()["automation"]

    disabled = client.patch(
        f"/api/automations/{rule['id']}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["automation"]["enabled"] == 0

    deleted = client.delete(f"/api/automations/{rule['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/state").get_json()["automations"] == []


def test_automation_rejects_unregistered_capability(client):
    response = client.post(
        "/api/automations",
        json={
            "sensor": "humidity",
            "operator": "below",
            "threshold": 40,
            "device_name": "客厅风扇",
            "action": "set_level",
            "capability": "brightness",
            "value": 50,
        },
    )

    assert response.status_code == 400
    assert "没有注册" in response.get_json()["error"]


def test_custom_rule_takes_ownership_from_default_comfort_rule(app, client):
    client.post(
        "/api/automations",
        json={
            "sensor": "temperature",
            "operator": "above",
            "threshold": 35,
            "device_name": "客厅风扇",
            "action": "on",
        },
    )

    client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    )

    with app.app_context():
        assert database.get_device("fan-1")["state"] == "off"
