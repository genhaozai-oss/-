from smarthome import database


def test_scene_is_saved_and_runs_multiple_validated_actions(app, client):
    client.patch(
        "/api/devices/light-1",
        json={"state": "on"},
    )
    created = client.post(
        "/api/scenes",
        json={
            "name": "睡眠模式",
            "actions": [
                {"device_name": "客厅灯", "action": "off"},
                {
                    "device_name": "客厅风扇",
                    "action": "set_level",
                    "capability": "speed",
                    "value": 35,
                },
            ],
        },
    )

    assert created.status_code == 201
    scene = created.get_json()["scene"]
    assert scene["name"] == "睡眠模式"
    assert scene["actions"][1]["value"] == 40

    result = client.post(f"/api/scenes/{scene['id']}/run").get_json()
    assert result["errors"] == []
    assert len(result["actions"]) == 3

    with app.app_context():
        assert database.get_device("light-1")["state"] == "off"
        assert database.get_device("fan-1")["state"] == "on"
        assert database.get_device_capability("fan-1", "speed")["value"] == 40


def test_saving_same_scene_name_updates_instead_of_duplicating(client):
    first = client.post(
        "/api/scenes",
        json={
            "name": "离家模式",
            "actions": [{"device_name": "客厅灯", "action": "off"}],
        },
    ).get_json()["scene"]
    second = client.post(
        "/api/scenes",
        json={
            "name": "离家模式",
            "actions": [{"device_name": "客厅风扇", "action": "off"}],
        },
    ).get_json()["scene"]

    assert first["id"] == second["id"]
    state = client.get("/api/state").get_json()
    assert len(state["scenes"]) == 1
    assert state["scenes"][0]["actions"][0]["device_id"] == "fan-1"


def test_scene_rejects_unknown_device_without_partial_save(client):
    response = client.post(
        "/api/scenes",
        json={
            "name": "错误场景",
            "actions": [
                {"device_name": "客厅灯", "action": "off"},
                {"device_name": "不存在的空调", "action": "on"},
            ],
        },
    )

    assert response.status_code == 400
    assert client.get("/api/state").get_json()["scenes"] == []


def test_scene_can_be_deleted(client):
    scene = client.post(
        "/api/scenes",
        json={
            "name": "观影模式",
            "actions": [{"device_name": "客厅灯", "action": "off"}],
        },
    ).get_json()["scene"]

    response = client.delete(f"/api/scenes/{scene['id']}")

    assert response.status_code == 204
    assert client.get("/api/state").get_json()["scenes"] == []


def test_saved_scene_executes_locally_without_cloud_ai(app, client):
    client.post(
        "/api/scenes",
        json={
            "name": "睡眠模式",
            "actions": [
                {
                    "device_name": "客厅风扇",
                    "action": "set_level",
                    "capability": "speed",
                    "value": 30,
                }
            ],
        },
    )

    class FailingAgent:
        enabled = True

        def respond(self, *_args, **_kwargs):
            raise AssertionError("执行已保存场景不应调用云端 AI")

    app.extensions["assistant_agent"] = FailingAgent()
    result = client.post(
        "/api/chat",
        json={"message": "现在执行睡眠模式"},
    ).get_json()

    assert result["intent"] == "run_scene"
    assert result["scene"]["name"] == "睡眠模式"
    assert any(action.get("state") == "on" for action in result["actions"])


def test_negated_scene_command_never_executes(client):
    client.post(
        "/api/scenes",
        json={
            "name": "睡眠模式",
            "actions": [{"device_name": "客厅风扇", "action": "on"}],
        },
    )

    result = client.post(
        "/api/chat",
        json={"message": "先不要执行睡眠模式"},
    ).get_json()

    assert result["intent"] == "scene_cancelled"
    assert result["actions"] == []
    state = client.get("/api/state").get_json()
    fan = next(device for device in state["devices"] if device["id"] == "fan-1")
    assert fan["state"] == "off"
