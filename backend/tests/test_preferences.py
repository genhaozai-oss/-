from smarthome import database


def test_saved_preferences_are_exposed_as_readable_memories(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")
        database.set_user_preference("temperature", "26")

    memories = client.get("/api/memories").get_json()["memories"]

    assert memories == [
        {
            "name": "fan_speed",
            "label": "常用风速",
            "value": 60.0,
            "unit": "%",
            "display_value": "60%",
        },
        {
            "name": "temperature",
            "label": "舒适温度",
            "value": 26.0,
            "unit": "℃",
            "display_value": "26℃",
        },
    ]
    assert client.get("/api/state").get_json()["memories"] == memories


def test_single_memory_can_be_deleted_without_affecting_others(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")
        database.set_user_preference("humidity", "55")

    response = client.delete("/api/memories/fan_speed")

    assert response.status_code == 204
    memories = client.get("/api/memories").get_json()["memories"]
    assert [memory["name"] for memory in memories] == ["humidity"]


def test_deleting_missing_memory_returns_clear_error(client):
    response = client.delete("/api/memories/fan_speed")

    assert response.status_code == 404
    assert "还没有记住" in response.get_json()["error"]
