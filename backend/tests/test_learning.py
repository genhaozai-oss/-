from smarthome import database
from smarthome.learning import learn_from_result


def adjust(client, device_id, capability, value):
    return client.patch(
        f"/api/devices/{device_id}/capabilities/{capability}",
        json={"value": value},
    ).get_json()


def test_three_similar_fan_adjustments_are_learned(app, client):
    adjust(client, "fan-1", "speed", 60)
    adjust(client, "fan-1", "speed", 70)
    result = adjust(client, "fan-1", "speed", 60)

    assert result["learning"]["learned"] is True
    assert result["learning"]["memory"]["display_value"] == "60%"
    memories = client.get("/api/memories").get_json()["memories"]
    assert memories[0]["name"] == "fan_speed"
    assert memories[0]["value"] == 60
    assert memories[0]["source"] == "automatic"
    assert memories[0]["source_label"] == "自动学习"


def test_two_adjustments_only_report_learning_progress(client):
    first = adjust(client, "light-1", "brightness", 70)
    second = adjust(client, "light-1", "brightness", 70)

    assert first["learning"]["progress"] == 1
    assert second["learning"]["progress"] == 2
    assert second["learning"]["learned"] is False
    assert client.get("/api/memories").get_json()["memories"] == []


def test_unstable_adjustments_restart_learning(client):
    adjust(client, "fan-1", "speed", 20)
    adjust(client, "fan-1", "speed", 80)
    result = adjust(client, "fan-1", "speed", 40)

    assert result["learning"]["learned"] is False
    assert result["learning"]["progress"] == 1


def test_automatic_learning_never_overwrites_explicit_preference(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "80")

    for value in (40, 40, 40):
        result = adjust(client, "fan-1", "speed", value)

    assert result["learning"] is None
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "80"


def test_automatic_preference_relearns_changed_habit(app, client):
    for value in (60, 60, 60):
        adjust(client, "fan-1", "speed", value)

    for value in (80, 80, 80):
        result = adjust(client, "fan-1", "speed", value)

    assert result["learning"]["learned"] is True
    assert result["learning"]["updated"] is True
    assert "从60%更新为80%" in result["learning"]["message"]
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "80"
        assert database.get_user_preference_source("fan_speed") == "automatic"
        database.update_device_capability("fan-1", "speed", 20)

    client.post("/api/chat", json={"message": "打开客厅风扇"})
    fan = next(
        item
        for item in client.get("/api/state").get_json()["devices"]
        if item["id"] == "fan-1"
    )
    speed = next(
        item for item in fan["capabilities"]
        if item["capability"] == "speed"
    )
    assert speed["value"] == 80


def test_automatic_preference_learns_adjacent_steps(app, client):
    for value in (60, 60, 60):
        adjust(client, "fan-1", "speed", value)
    for value in (70, 70, 70):
        changed_up = adjust(client, "fan-1", "speed", value)

    assert changed_up["learning"]["learned"] is True
    assert "从60%更新为70%" in changed_up["learning"]["message"]

    for value in (60, 60, 60):
        changed_down = adjust(client, "fan-1", "speed", value)

    assert changed_down["learning"]["learned"] is True
    assert "从70%更新为60%" in changed_down["learning"]["message"]
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"


def test_similar_observations_confirm_automatic_preference_without_event(
    app,
    client,
):
    for value in (60, 60, 60):
        adjust(client, "fan-1", "speed", value)
    with app.app_context():
        first_event = database.latest_event("learning")["id"]

    for value in (60, 60, 60):
        result = adjust(client, "fan-1", "speed", value)

    assert result["learning"]["learned"] is False
    assert result["learning"]["confirmed"] is True
    assert "无需更新" in result["learning"]["message"]
    with app.app_context():
        assert database.latest_event("learning")["id"] == first_event


def test_chat_reports_when_automatic_preference_is_confirmed(client):
    for _ in range(3):
        client.post(
            "/api/chat",
            json={"message": "把风速调到60%", "session_id": "learn-chat"},
        )

    for _ in range(3):
        result = client.post(
            "/api/chat",
            json={"message": "把风速调到60%", "session_id": "learn-chat"},
        ).get_json()

    assert result["learning"][0]["confirmed"] is True
    assert "常用风速仍是60%，无需更新" in result["reply"]


def test_legacy_preference_without_source_is_not_overwritten(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")
        database.get_db().execute(
            "DELETE FROM settings WHERE key = 'preference_meta.fan_speed'"
        )
        database.get_db().commit()

    for value in (80, 80, 80):
        result = adjust(client, "fan-1", "speed", value)

    assert result["learning"] is None
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"
        assert database.get_user_preference_source("fan_speed") == "explicit"


def test_stale_automatic_write_cannot_overwrite_explicit_preference(app):
    with app.app_context():
        database.set_user_preference(
            "fan_speed",
            "60",
            source="automatic",
        )
        observed_value = database.get_user_preferences()["fan_speed"]
        database.set_user_preference("fan_speed", "90", source="explicit")

        saved = database.set_automatic_user_preference(
            "fan_speed",
            "80",
            expected_value=observed_value,
        )

        assert saved is False
        assert database.get_user_preferences()["fan_speed"] == "90"
        assert database.get_user_preference_source("fan_speed") == "explicit"


def test_deleting_memory_resets_previous_learning_samples(app, client):
    adjust(client, "fan-1", "speed", 60)
    adjust(client, "fan-1", "speed", 60)
    with app.app_context():
        database.set_user_preference("fan_speed", "80")

    client.delete("/api/memories/fan_speed")
    result = adjust(client, "fan-1", "speed", 60)

    assert result["learning"]["progress"] == 1
    assert result["learning"]["learned"] is False
    with app.app_context():
        settings = database.get_settings()
        assert "preference_meta.fan_speed" not in settings


def test_forgetting_pending_learning_samples_restarts_progress(client):
    adjust(client, "fan-1", "speed", 60)
    adjust(client, "fan-1", "speed", 60)

    response = client.delete("/api/memories/fan_speed")
    result = adjust(client, "fan-1", "speed", 60)

    assert response.status_code == 404
    assert result["learning"]["progress"] == 1
    assert result["learning"]["learned"] is False


def test_chat_result_capability_learning_is_supported(app):
    result = {
        "intent": "set_device_level",
        "actions": [
            {
                "device_id": "light-1",
                "capability": "brightness",
                "value": 80,
            }
        ],
    }
    with app.app_context():
        for _ in range(3):
            learning = learn_from_result(result)[0]

    assert learning["learned"] is True
    assert learning["memory"]["name"] == "light_brightness"


def test_page_explains_local_automatic_learning(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert "连续 3 次相近调节会自动学习" in html
    assert "正在学习" in script
    assert "memory.source_label" in script
    assert 'memory.source === "automatic"' in script
    assert 'learning: "学习"' in script
