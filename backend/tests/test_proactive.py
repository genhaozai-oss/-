import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from smarthome import create_app, database
from smarthome.proactive import collect_weather_notifications, set_enabled
from smarthome.weather import clear_weather_cache, get_weather, get_weather_warnings


def create_due_alarm(app, label="测试提醒"):
    with app.app_context():
        scheduled_at = (
            datetime.now().astimezone() - timedelta(minutes=1)
        ).isoformat(timespec="seconds")
        return database.create_alarm(label, scheduled_at)


def test_state_read_does_not_consume_due_alarm(app, client):
    alarm = create_due_alarm(app)

    state = client.get("/api/state").get_json()

    assert any(item["id"] == alarm["id"] for item in state["alarms"])
    assert state["due_alarms"] == []
    assert state["notifications"] == []
    with app.app_context():
        assert database.get_alarm(alarm["id"])["triggered"] == 0


def test_background_check_persists_due_alarm_once(app):
    alarm = create_due_alarm(app, "喝水")
    monitor = app.extensions["proactive_monitor"]

    first = monitor.run_once(force=True)
    second = monitor.run_once(force=True)

    alarm_notifications = [
        item for item in first["created"] if item["kind"] == "alarm"
    ]
    assert len(alarm_notifications) == 1
    assert "喝水" in alarm_notifications[0]["message"]
    assert not any(item["kind"] == "alarm" for item in second["created"])
    with app.app_context():
        assert database.get_alarm(alarm["id"])["triggered"] == 1
        assert len(database.list_notifications()) == 1


def test_claim_requires_delivery_acknowledgement(app, client):
    create_due_alarm(app)
    app.extensions["proactive_monitor"].run_once(force=True)

    first = client.post("/api/notifications/claim").get_json()["notification"]
    second = client.post("/api/notifications/claim").get_json()["notification"]

    assert first["kind"] == "alarm"
    assert first["claimed_at"] is not None
    assert first["delivered_at"] is None
    assert second is None

    acknowledged = client.post(
        f"/api/notifications/{first['id']}/ack",
        json={"claim_token": first["claim_token"]},
    ).get_json()["notification"]

    assert acknowledged["delivered_at"] is not None


def test_unacknowledged_claim_can_be_retried_after_lease(app):
    create_due_alarm(app)
    app.extensions["proactive_monitor"].run_once(force=True)
    first = app.test_client().post(
        "/api/notifications/claim"
    ).get_json()["notification"]
    with app.app_context():
        database.get_db().execute(
            "UPDATE notifications SET claimed_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", first["id"]),
        )
        database.get_db().commit()

    restarted_app = create_app(
        {
            "TESTING": True,
            "DATABASE": app.config["DATABASE"],
            "LLM_API_KEY": "",
            "WEATHER_API_KEY": "",
        }
    )
    retried = restarted_app.test_client().post(
        "/api/notifications/claim"
    ).get_json()["notification"]

    assert retried["id"] == first["id"]
    assert retried["claim_token"] != first["claim_token"]
    stale_ack = restarted_app.test_client().post(
        f"/api/notifications/{first['id']}/ack",
        json={"claim_token": first["claim_token"]},
    )
    current_ack = restarted_app.test_client().post(
        f"/api/notifications/{retried['id']}/ack",
        json={"claim_token": retried["claim_token"]},
    )
    assert stale_ack.status_code == 409
    assert current_ack.status_code == 200


def test_concurrent_claim_has_only_one_winner(app):
    create_due_alarm(app)
    app.extensions["proactive_monitor"].run_once(force=True)

    def claim():
        with app.app_context():
            notification = database.claim_notification()
            return notification["id"] if notification else None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: claim(), range(16)))

    assert len([item for item in results if item is not None]) == 1


def test_notification_can_be_marked_read(app, client):
    create_due_alarm(app)
    app.extensions["proactive_monitor"].run_once(force=True)
    notification = client.get("/api/state").get_json()["notifications"][0]

    response = client.patch(
        f"/api/notifications/{notification['id']}",
        json={"read": True},
    )

    assert response.status_code == 200
    assert response.get_json()["notification"]["read_at"] is not None
    assert client.get("/api/state").get_json()["unread_notifications"] == 0


def test_read_notification_is_not_claimed_afterward(app, client):
    create_due_alarm(app)
    app.extensions["proactive_monitor"].run_once(force=True)
    client.post("/api/notifications/read-all")

    claimed = client.post("/api/notifications/claim").get_json()["notification"]

    assert claimed is None


def test_stale_sensor_alert_is_deduplicated(app):
    with app.app_context():
        database.get_db().execute(
            """
            UPDATE environment
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = 1
            """
        )
        database.get_db().commit()
    monitor = app.extensions["proactive_monitor"]

    first = monitor.run_once(force=True)
    second = monitor.run_once(force=True)

    assert len([item for item in first["created"] if item["kind"] == "sensor"]) == 1
    assert not any(item["kind"] == "sensor" for item in second["created"])


def test_weather_risks_and_official_warning_are_deduplicated(
    app,
    monkeypatch,
):
    weather = {
        "available": True,
        "daily": {
            "rain_probability": 80,
            "temperature_max": 36,
            "temperature_min": 20,
        },
    }
    warnings = {
        "available": True,
        "warnings": [
            {
                "id": "warning-1",
                "title": "暴雨黄色预警",
                "text": "预计今天有强降雨。",
                "start_time": "2026-08-01T08:00+08:00",
                "sender": "气象台",
            }
        ],
    }
    monkeypatch.setattr("smarthome.proactive.get_weather", lambda _settings: weather)
    monkeypatch.setattr(
        "smarthome.proactive.get_weather_warnings",
        lambda _settings: warnings,
    )
    with app.app_context():
        database.set_settings({"location_name": "广州"})
        now = datetime(2026, 8, 1, 9, tzinfo=timezone(timedelta(hours=8)))
        first, first_errors = collect_weather_notifications(now)
        second, second_errors = collect_weather_notifications(now)

    assert {item["title"] for item in first} == {
        "官方天气预警",
        "降雨提醒",
        "高温提醒",
    }
    assert second == []
    assert first_errors == []
    assert second_errors == []


def test_qweather_official_warning_api_is_supported(app, monkeypatch):
    app.config["WEATHER_API_HOST"] = "test.qweatherapi.com"
    app.config["WEATHER_API_KEY"] = "test-key"
    response = io.BytesIO(
        (
            '{"code":"200","warning":[{"id":"w1",'
            '"sender":"气象台","title":"暴雨黄色预警",'
            '"severity":"Moderate","typeName":"暴雨",'
            '"text":"预计有强降雨。","status":"Active"}]}'
        ).encode("utf-8")
    )
    monkeypatch.setattr("smarthome.weather.urlopen", lambda *_args, **_kwargs: response)

    with app.app_context():
        database.set_settings({"latitude": "23.1", "longitude": "113.2"})
        result = get_weather_warnings(database.get_settings())

    assert result["available"] is True
    assert result["warnings"][0]["title"] == "暴雨黄色预警"


def test_weather_failure_does_not_drop_due_alarm(app, monkeypatch):
    create_due_alarm(app)
    monkeypatch.setattr(
        "smarthome.proactive.collect_weather_notifications",
        lambda: (_ for _ in ()).throw(RuntimeError("weather down")),
    )

    result = app.extensions["proactive_monitor"].run_once(
        force=True,
        force_weather=True,
    )

    assert any(item["kind"] == "alarm" for item in result["created"])
    assert any("天气检查失败" in item for item in result["errors"])


def test_weather_failure_uses_short_retry(app, monkeypatch):
    calls = {"weather": 0, "warnings": 0}

    def failed_weather(_settings):
        calls["weather"] += 1
        return {
            "configured": True,
            "available": False,
            "error": "weather timeout",
        }

    def failed_warnings(_settings):
        calls["warnings"] += 1
        return {
            "configured": True,
            "available": False,
            "warnings": [],
            "error": "warning timeout",
        }

    ticks = iter((100.0, 161.0))
    monkeypatch.setattr("smarthome.proactive.get_weather", failed_weather)
    monkeypatch.setattr(
        "smarthome.proactive.get_weather_warnings",
        failed_warnings,
    )
    monkeypatch.setattr(
        "smarthome.proactive.time.monotonic",
        lambda: next(ticks),
    )
    monitor = app.extensions["proactive_monitor"]

    first = monitor.run_once(force=True, force_weather=True)
    second = monitor.run_once(force=True)

    assert any("weather timeout" in item for item in first["errors"])
    assert calls == {"weather": 2, "warnings": 2}
    assert second["errors"]


def test_paused_proactive_monitor_still_delivers_alarm(app):
    alarm = create_due_alarm(app)
    with app.app_context():
        set_enabled(False)

    result = app.extensions["proactive_monitor"].run_once()

    assert result["enabled"] is False
    assert any(item["kind"] == "alarm" for item in result["created"])
    with app.app_context():
        assert database.get_alarm(alarm["id"])["triggered"] == 1


def test_future_utc_alarm_is_not_triggered_early_when_paused(app):
    scheduled_at = (
        datetime.now(timezone.utc) + timedelta(minutes=5)
    ).isoformat(timespec="seconds")
    with app.app_context():
        alarm = database.create_alarm("UTC未来闹钟", scheduled_at)
        set_enabled(False)

    result = app.extensions["proactive_monitor"].run_once()

    assert not any(item["kind"] == "alarm" for item in result["created"])
    with app.app_context():
        assert database.get_alarm(alarm["id"])["triggered"] == 0


def test_alarm_list_is_sorted_by_absolute_time(app):
    local_first = (
        datetime.now().astimezone() + timedelta(minutes=5)
    ).isoformat(timespec="seconds")
    utc_later = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    ).isoformat(timespec="seconds")
    with app.app_context():
        first = database.create_alarm("先响", local_first)
        later = database.create_alarm("后响", utc_later)
        alarms = database.list_alarms()

    assert [item["id"] for item in alarms] == [first["id"], later["id"]]


def test_current_utc_claim_is_not_treated_as_expired(app):
    with app.app_context():
        notification, _created = database.create_notification(
            "alarm",
            "测试",
            "等待确认",
            "utc-claim",
        )
        database.get_db().execute(
            "UPDATE notifications SET claimed_at = ?, claim_token = ? WHERE id = ?",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "current-token",
                notification["id"],
            ),
        )
        database.get_db().commit()

        assert database.claim_notification() is None


def test_future_utc_runtime_lease_cannot_be_stolen(app):
    with app.app_context():
        database.get_db().execute(
            """
            INSERT INTO runtime_leases (name, owner_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (
                "utc-lease",
                "first-worker",
                (
                    datetime.now(timezone.utc) + timedelta(seconds=15)
                ).isoformat(timespec="seconds"),
            ),
        )
        database.get_db().commit()

        acquired = database.acquire_runtime_lease(
            "utc-lease",
            "second-worker",
            60,
        )

    assert acquired is False


def test_weather_cache_is_single_flight(app, monkeypatch):
    app.config.update(
        WEATHER_API_HOST="single-flight.qweather.test",
        WEATHER_API_KEY="test-key",
    )
    clear_weather_cache()
    request_count = 0
    counter_lock = threading.Lock()

    def fake_request(path, _parameters):
        nonlocal request_count
        with counter_lock:
            request_count += 1
        time.sleep(0.02)
        if path.endswith("/now"):
            return {
                "now": {
                    "text": "晴",
                    "temp": "28",
                    "feelsLike": "29",
                    "humidity": "50",
                    "windDir": "东风",
                    "windScale": "2",
                    "precip": "0",
                }
            }
        if path.endswith("/3d"):
            return {"daily": [{"tempMin": "20", "tempMax": "30"}]}
        return {"hourly": [{"pop": "10"}]}

    monkeypatch.setattr("smarthome.weather._request_json", fake_request)
    settings = {
        "weather_location_id": "101010100",
        "location_name": "北京",
    }

    def query_weather():
        with app.app_context():
            return get_weather(settings)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: query_weather(), range(2)))

    assert all(item["available"] for item in results)
    assert request_count == 3


def test_weather_failure_is_shared_between_concurrent_requests(
    app,
    monkeypatch,
):
    app.config.update(
        WEATHER_API_HOST="failure-cache.qweather.test",
        WEATHER_API_KEY="test-key",
    )
    clear_weather_cache()
    request_count = 0
    counter_lock = threading.Lock()

    def failed_request(_path, _parameters):
        nonlocal request_count
        with counter_lock:
            request_count += 1
        time.sleep(0.02)
        raise TimeoutError("timeout")

    monkeypatch.setattr("smarthome.weather._request_json", failed_request)
    settings = {
        "weather_location_id": "101010100",
        "location_name": "北京",
    }

    def query_weather():
        with app.app_context():
            return get_weather(settings)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: query_weather(), range(4)))

    assert all(item["available"] is False for item in results)
    assert request_count == 1


def test_malformed_weather_response_is_cached_as_failure(app, monkeypatch):
    app.config.update(
        WEATHER_API_HOST="malformed-cache.qweather.test",
        WEATHER_API_KEY="test-key",
    )
    clear_weather_cache()
    request_count = 0
    counter_lock = threading.Lock()

    def malformed_request(path, _parameters):
        nonlocal request_count
        with counter_lock:
            request_count += 1
        time.sleep(0.01)
        if path.endswith("/now"):
            return {"unexpected": {}}
        if path.endswith("/3d"):
            return {"daily": [{"tempMin": "20", "tempMax": "30"}]}
        return {"hourly": [{"pop": "10"}]}

    monkeypatch.setattr("smarthome.weather._request_json", malformed_request)
    settings = {
        "weather_location_id": "101010100",
        "location_name": "北京",
    }

    def query_weather():
        with app.app_context():
            return get_weather(settings)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: query_weather(), range(4)))

    assert all(item["available"] is False for item in results)
    assert request_count == 3


def test_location_change_invalidates_proactive_weather_schedule(app, client):
    monitor = app.extensions["proactive_monitor"]
    monitor._next_weather_at = 9999
    monitor._weather_location_signature = ("old", "1", "2")

    response = client.put(
        "/api/settings/location",
        json={"location_name": "北京", "latitude": 39.9, "longitude": 116.4},
    )

    assert response.status_code == 200
    assert monitor._next_weather_at == 0
    assert monitor._weather_location_signature is None


def test_database_lease_allows_only_one_background_monitor(app, monkeypatch):
    second_app = create_app(
        {
            "TESTING": True,
            "DATABASE": app.config["DATABASE"],
            "LLM_API_KEY": "",
            "WEATHER_API_KEY": "",
        }
    )
    calls = 0

    def collect_weather():
        nonlocal calls
        calls += 1
        return [], []

    monkeypatch.setattr(
        "smarthome.proactive.collect_weather_notifications",
        collect_weather,
    )
    first = app.extensions["proactive_monitor"].run_once(
        force=True,
        force_weather=True,
        require_leader=True,
    )
    second = second_app.extensions["proactive_monitor"].run_once(
        force=True,
        force_weather=True,
        require_leader=True,
    )

    assert first["leader"] is True
    assert second["leader"] is False
    assert calls == 1
    with app.app_context():
        database.get_db().execute(
            "UPDATE runtime_leases SET expires_at = ? WHERE name = ?",
            ("2000-01-01T00:00:00+00:00", "proactive-monitor"),
        )
        database.get_db().commit()
    takeover = second_app.extensions["proactive_monitor"].run_once(
        force=True,
        force_weather=True,
        require_leader=True,
    )
    assert takeover["leader"] is True
    assert calls == 2


def test_notification_pruning_keeps_unread_items(app):
    with app.app_context():
        important, _created = database.create_notification(
            "alarm",
            "重要闹钟",
            "不能被裁剪",
            "keep-unread",
        )
        for index in range(205):
            item, _created = database.create_notification(
                "weather",
                "历史提醒",
                str(index),
                f"read-{index}",
            )
            database.mark_notification_read(item["id"])
        database.create_notification(
            "sensor",
            "新提醒",
            "触发裁剪",
            "prune-trigger",
        )
        read_count = database.get_db().execute(
            "SELECT COUNT(*) AS count FROM notifications WHERE read_at IS NOT NULL"
        ).fetchone()["count"]

        assert database.get_notification(important["id"])["read_at"] is None
        assert read_count == database.MAX_NOTIFICATION_ROWS


def test_testing_app_does_not_start_background_thread(app):
    assert app.extensions["proactive_monitor"].running is False


def test_proactive_commands_work_without_cloud(client):
    paused = client.post(
        "/api/chat",
        json={"message": "暂停主动提醒"},
    ).get_json()
    enabled = client.post(
        "/api/chat",
        json={"message": "开启主动提醒"},
    ).get_json()

    assert paused["intent"] == "proactive_control"
    assert paused["proactive"]["enabled"] is False
    assert enabled["proactive"]["enabled"] is True


def test_page_contains_proactive_notification_center(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/app.js").get_data(as_text=True)

    assert 'id="notificationList"' in html
    assert 'id="proactiveToggleButton"' in html
    assert 'id="desktopNotificationButton"' in html
    assert "function renderNotifications" in script
    assert '"/api/notifications/claim"' in script
    assert "/ack" in script
    assert "notification.claim_token" in script
    assert 'addEventListener("error"' in script
    assert "systemSpeechWatchdog" in script
    assert "window.Notification.requestPermission" in script
