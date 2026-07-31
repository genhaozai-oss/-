from smarthome import database


class FakeBridge:
    def __init__(self):
        self.commands = []

    def publish_device_command(self, device_id, state):
        self.commands.append((device_id, state))
        return True

    def publish_device_capability(self, device_id, capability, value):
        self.commands.append((device_id, capability, value))
        return True


class FailingBridge(FakeBridge):
    def publish_device_command(self, device_id, state):
        self.commands.append((device_id, state))
        return False


def device(client, device_id):
    return next(
        item
        for item in client.get("/api/state").get_json()["devices"]
        if item["id"] == device_id
    )


def test_auto_flow_is_enabled_by_default(client):
    assert client.get("/api/state").get_json()["auto_flow"]["enabled"] is True

    result = client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    ).get_json()

    assert result["auto_flow"]["status"] == "executed"
    assert any(
        action["device_id"] == "fan-1" and action["state"] == "on"
        for action in result["actions"]
    )


def test_paused_flow_only_saves_environment(client):
    client.post(
        "/api/automations",
        json={
            "sensor": "temperature",
            "operator": "above",
            "threshold": 29,
            "device_name": "客厅风扇",
            "action": "on",
        },
    )
    client.patch("/api/auto-flow", json={"enabled": False})

    result = client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 90},
    ).get_json()

    assert result["environment"]["temperature"] == 31
    assert result["environment"]["humidity"] == 90
    assert result["actions"] == []
    assert result["auto_flow"]["status"] == "paused"
    assert device(client, "fan-1")["state"] == "off"
    assert device(client, "dehumidifier-1")["state"] == "off"


def test_run_now_bypasses_pause_without_enabling_it(client):
    client.patch("/api/auto-flow", json={"enabled": False})
    client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 55},
    )

    result = client.post("/api/auto-flow/run").get_json()["auto_flow"]

    assert result["forced"] is True
    assert result["status"] == "executed"
    assert device(client, "fan-1")["state"] == "on"
    assert client.get("/api/state").get_json()["auto_flow"]["enabled"] is False


def test_stale_sensor_data_blocks_manual_run(app, client):
    with app.app_context():
        database.get_db().execute(
            """
            UPDATE environment
            SET temperature = 35, humidity = 55,
                updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = 1
            """
        )
        database.get_db().commit()

    result = client.post("/api/auto-flow/run").get_json()["auto_flow"]

    assert result["status"] == "stale"
    assert result["actions"] == []
    assert device(client, "fan-1")["state"] == "off"


def test_manual_control_holds_device_for_30_minutes(app, client):
    client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    )
    client.patch("/api/devices/fan-1", json={"state": "off"})

    held = client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 55},
    ).get_json()
    assert not any(
        action["device_id"] == "fan-1" for action in held["actions"]
    )
    assert device(client, "fan-1")["state"] == "off"

    with app.app_context():
        database.get_db().execute(
            """
            UPDATE device_manual_overrides
            SET until_at = '2000-01-01T00:00:00+00:00'
            WHERE device_id = 'fan-1'
            """
        )
        database.get_db().commit()

    resumed = client.post(
        "/api/environment",
        json={"temperature": 32, "humidity": 55},
    ).get_json()
    assert any(
        action["device_id"] == "fan-1" and action["state"] == "on"
        for action in resumed["actions"]
    )


def test_physical_water_devices_are_never_automatically_started(app, client):
    bridge = FakeBridge()
    app.extensions["mqtt_bridge"] = bridge
    with app.app_context():
        database.update_device(
            "humidifier-1",
            is_virtual=False,
            online=True,
        )
        database.update_device(
            "dehumidifier-1",
            is_virtual=False,
            online=True,
        )

    client.post(
        "/api/automations",
        json={
            "sensor": "humidity",
            "operator": "above",
            "threshold": 80,
            "device_name": "抽湿器演示",
            "action": "on",
        },
    )
    wet = client.post(
        "/api/environment",
        json={"temperature": 26, "humidity": 90},
    ).get_json()
    dry = client.post(
        "/api/environment",
        json={"temperature": 26, "humidity": 30},
    ).get_json()

    assert not any(
        action.get("state") == "on"
        and action["device_id"] in {"humidifier-1", "dehumidifier-1"}
        for action in wet["actions"] + dry["actions"]
    )
    assert device(client, "humidifier-1")["state"] == "off"
    assert device(client, "dehumidifier-1")["state"] == "off"
    assert bridge.commands == []


def test_offline_physical_device_that_needs_closing_is_reported(app, client):
    with app.app_context():
        database.update_device(
            "fan-1",
            state="on",
            online=False,
            is_virtual=False,
        )

    flow = client.post(
        "/api/environment",
        json={"temperature": 20, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["status"] == "blocked"
    assert "离线" in flow["summary"]
    assert device(client, "fan-1")["state"] == "on"


def test_unrelated_manual_override_does_not_block_flow(client):
    client.patch("/api/devices/light-1", json={"state": "on"})

    flow = client.post(
        "/api/environment",
        json={"temperature": 27, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["status"] == "no_change"
    assert flow["blocked"] == []


def test_failed_mqtt_command_does_not_change_local_state(app, client):
    bridge = FailingBridge()
    app.extensions["mqtt_bridge"] = bridge
    with app.app_context():
        database.update_device(
            "fan-1",
            online=True,
            is_virtual=False,
        )

    flow = client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["status"] == "blocked"
    assert "发送失败" in flow["summary"]
    assert device(client, "fan-1")["state"] == "off"
    assert bridge.commands == [("fan-1", "on")]


def test_home_arrival_reuses_stale_data_safety_gate(app, client):
    with app.app_context():
        database.get_db().execute(
            """
            UPDATE environment
            SET temperature = 35, humidity = 55,
                updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = 1
            """
        )
        database.get_db().commit()

    result = client.post(
        "/api/chat",
        json={"message": "我要下班回家了"},
    ).get_json()

    assert result["auto_flow"]["status"] == "stale"
    assert device(client, "fan-1")["state"] == "off"


def test_user_scene_holds_its_devices_from_immediate_auto_reversal(client):
    scene = client.post(
        "/api/scenes",
        json={
            "name": "强风模式",
            "actions": [{"device_name": "客厅风扇", "action": "on"}],
        },
    ).get_json()["scene"]
    client.post(f"/api/scenes/{scene['id']}/run")

    flow = client.post(
        "/api/environment",
        json={"temperature": 20, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["status"] == "blocked"
    assert "手动接管" in flow["summary"]
    assert device(client, "fan-1")["state"] == "on"


def test_newest_matching_rule_wins_when_rules_conflict(client):
    client.post(
        "/api/automations",
        json={
            "sensor": "temperature",
            "operator": "above",
            "threshold": 29,
            "device_name": "客厅风扇",
            "action": "on",
        },
    )
    newest = client.post(
        "/api/automations",
        json={
            "sensor": "humidity",
            "operator": "above",
            "threshold": 50,
            "device_name": "客厅风扇",
            "action": "off",
        },
    ).get_json()["automation"]

    flow = client.post(
        "/api/environment",
        json={"temperature": 31, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["actions"] == []
    assert device(client, "fan-1")["state"] == "off"
    state = client.get("/api/state").get_json()
    saved = next(
        rule
        for rule in state["automations"]
        if rule["id"] == newest["id"]
    )
    assert saved["last_triggered_at"] is None


def test_flow_contains_four_explanation_stages(app, client):
    flow = client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 94},
    ).get_json()["auto_flow"]

    assert [step["stage"] for step in flow["steps"]] == [
        "sense",
        "decide",
        "safety",
        "execute",
    ]
    assert all(
        step["label"] and step["status"] and step["detail"]
        for step in flow["steps"]
    )
    with app.app_context():
        saved = database.latest_event("auto_flow")["payload"]
    assert saved["steps"] == flow["steps"]


def test_auto_flow_action_can_be_undone(client):
    client.post(
        "/api/environment",
        json={"temperature": 30, "humidity": 55},
    )
    assert device(client, "fan-1")["state"] == "on"

    result = client.post(
        "/api/chat",
        json={"message": "撤销刚才的操作"},
    ).get_json()

    assert result["intent"] == "undo_last_action"
    assert device(client, "fan-1")["state"] == "off"


def test_learned_comfort_preference_changes_decision_threshold(app, client):
    with app.app_context():
        database.set_user_preference("temperature", "28")

    flow = client.post(
        "/api/environment",
        json={"temperature": 29, "humidity": 55},
    ).get_json()["auto_flow"]

    assert flow["thresholds"]["target_temperature"] == 28
    assert flow["thresholds"]["fan_on"] == 30
    assert device(client, "fan-1")["state"] == "off"


def test_auto_flow_can_be_controlled_by_conversation(client):
    paused = client.post(
        "/api/chat",
        json={"message": "暂停AI自动流"},
    ).get_json()
    assert paused["intent"] == "auto_flow_control"
    assert paused["auto_flow"]["enabled"] is False

    enabled = client.post(
        "/api/chat",
        json={"message": "开启AI自动流"},
    ).get_json()
    assert enabled["auto_flow"]["enabled"] is True


def test_page_contains_auto_flow_controls(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="autoFlowBadge"' in html
    assert 'id="autoFlowSteps"' in html
    assert 'id="autoFlowToggleButton"' in html
    assert 'id="autoFlowRunButton"' in html

    script = client.get("/app.js").get_data(as_text=True)
    assert "function renderAutoFlow" in script
    assert '"/api/auto-flow"' in script
    assert '"/api/auto-flow/run"' in script
    assert 'auto_flow: "自动流"' in script
