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


def test_deleting_memory_resets_previous_learning_samples(app, client):
    adjust(client, "fan-1", "speed", 60)
    adjust(client, "fan-1", "speed", 60)
    with app.app_context():
        database.set_user_preference("fan_speed", "80")

    client.delete("/api/memories/fan_speed")
    result = adjust(client, "fan-1", "speed", 60)

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
    assert 'learning: "学习"' in script
