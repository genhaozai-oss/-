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


def test_explicit_preference_can_be_saved_and_forgotten_without_cloud(app, client):
    saved = client.post(
        "/api/chat",
        json={"message": "记住我的常用风速是60%"},
    ).get_json()

    assert saved["intent"] == "remember_preference"
    assert "本地记住" in saved["reply"]
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"

    forgotten = client.post(
        "/api/chat",
        json={"message": "忘掉我的常用风速"},
    ).get_json()

    assert forgotten["intent"] == "forget_preference"
    with app.app_context():
        assert "fan_speed" not in database.get_user_preferences()


def test_invalid_local_preference_is_not_saved(app, client):
    result = client.post(
        "/api/chat",
        json={"message": "记住我的舒适湿度是94%"},
    ).get_json()

    assert result["intent"] == "remember_preference"
    assert "30～80" in result["reply"]
    with app.app_context():
        assert "humidity" not in database.get_user_preferences()


def test_scene_wording_is_not_mistaken_for_explicit_preference(app, client):
    result = client.post(
        "/api/chat",
        json={"message": "记住睡眠模式，客厅风扇风速30%"},
    ).get_json()

    assert result["intent"] == "unknown"
    with app.app_context():
        assert "fan_speed" not in database.get_user_preferences()


def test_preference_uses_number_after_label(app, client):
    result = client.post(
        "/api/chat",
        json={"message": "晚上10点时，我喜欢风速60%"},
    ).get_json()

    assert result["intent"] == "remember_preference"
    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"


def test_deleting_humidity_automation_does_not_delete_preference(app, client):
    with app.app_context():
        database.set_user_preference("humidity", "55")

    client.post(
        "/api/chat",
        json={"message": "删除湿度高于70%的自动化"},
    )

    with app.app_context():
        assert database.get_user_preferences()["humidity"] == "55"


def test_negative_preference_wording_is_not_learned(app, client):
    for message in (
        "我不喜欢风速60%",
        "我不习惯风速60%",
        "我不想让你记住常用风速60%",
        "不用帮我记住常用风速60%",
        "我没让你记住常用风速60%",
    ):
        client.post("/api/chat", json={"message": message})

    with app.app_context():
        assert "fan_speed" not in database.get_user_preferences()


def test_unrelated_earlier_number_is_not_learned_as_brightness(app, client):
    client.post(
        "/api/chat",
        json={"message": "晚上10点我喜欢亮度"},
    )

    with app.app_context():
        assert "light_brightness" not in database.get_user_preferences()


def test_compound_preference_and_control_is_not_partially_consumed(app, client):
    for message in (
        "记住常用风速60%，然后打开客厅风扇",
        "记住常用风速60%，并打开客厅风扇",
        "记住常用风速60%，接着打开客厅风扇",
        "记住常用风速60%，以及打开客厅风扇",
    ):
        client.post("/api/chat", json={"message": message})

    with app.app_context():
        assert "fan_speed" not in database.get_user_preferences()


def test_preference_question_does_not_overwrite_memory(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")

    client.post(
        "/api/chat",
        json={"message": "你还记得我的常用风速是70%吗"},
    )

    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"


def test_negated_forget_wording_does_not_delete_memory(app, client):
    with app.app_context():
        database.set_user_preference("fan_speed", "60")

    for message in (
        "别忘记我的常用风速",
        "不要删除我的常用风速",
    ):
        client.post("/api/chat", json={"message": message})

    with app.app_context():
        assert database.get_user_preferences()["fan_speed"] == "60"


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
