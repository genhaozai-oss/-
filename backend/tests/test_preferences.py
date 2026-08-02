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
            "source": "explicit",
            "source_label": "用户设定",
        },
        {
            "name": "temperature",
            "label": "舒适温度",
            "value": 26.0,
            "unit": "℃",
            "display_value": "26℃",
            "source": "explicit",
            "source_label": "用户设定",
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


def test_legacy_learning_event_restores_automatic_source(app):
    with app.app_context():
        database.set_user_preference(
            "fan_speed",
            "60",
            source="automatic",
        )
        database.log_event(
            "learning",
            "已自动记住常用风速",
            {
                "memory": {
                    "name": "fan_speed",
                    "value": 60,
                    "source": "automatic",
                }
            },
        )
        db = database.get_db()
        db.execute(
            "DELETE FROM settings WHERE key = 'preference_meta.fan_speed'"
        )
        db.execute(
            "DELETE FROM settings WHERE key = 'preference_source_migration_v1'"
        )
        db.commit()

        database.init_db()

        assert database.get_user_preference_source("fan_speed") == "automatic"
        assert (
            database.get_settings()["preference_source_migration_v1"]
            == "done"
        )


def test_legacy_preference_without_matching_event_stays_explicit(app):
    with app.app_context():
        database.set_user_preference("fan_speed", "70")
        database.log_event(
            "learning",
            "旧的自动学习记录",
            {"memory": {"name": "fan_speed", "value": 60}},
        )
        db = database.get_db()
        db.execute(
            """
            INSERT INTO events (kind, message, payload, created_at)
            VALUES ('learning', '旧的异常记录', '[]', ?)
            """,
            (database.now_iso(),),
        )
        db.execute(
            "DELETE FROM settings WHERE key = 'preference_meta.fan_speed'"
        )
        db.execute(
            "DELETE FROM settings WHERE key = 'preference_source_migration_v1'"
        )
        db.commit()

        database.init_db()

        assert database.get_user_preference_source("fan_speed") == "explicit"
