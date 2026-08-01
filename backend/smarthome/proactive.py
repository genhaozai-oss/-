import hashlib
import threading
import time
import uuid
from datetime import datetime

from . import database
from .autoflow import MAX_SENSOR_AGE_SECONDS
from .weather import get_weather, get_weather_warnings


SETTING_KEY = "proactive_enabled"
LEASE_NAME = "proactive-monitor"


def is_enabled():
    return database.get_settings().get(SETTING_KEY) != "0"


def set_enabled(enabled):
    database.set_settings({SETTING_KEY: "1" if enabled else "0"})
    message = "主动提醒已开启" if enabled else "主动提醒已暂停"
    database.log_event(
        "notification",
        message,
        {"enabled": bool(enabled)},
    )
    return {"enabled": bool(enabled), "message": message}


def _log_notification(notification, created):
    if created and notification:
        database.log_event(
            "notification",
            notification["message"],
            {"notification_id": notification["id"]},
        )
    return notification if created else None


def collect_due_alarm_notifications():
    notifications = database.enqueue_due_alarm_notifications()
    for notification in notifications:
        database.log_event(
            "notification",
            notification["message"],
            {"notification_id": notification["id"]},
        )
    return notifications


def collect_stale_sensor_notification(now=None):
    environment = database.get_environment()
    try:
        updated_at = datetime.fromisoformat(environment["updated_at"])
        current = now or datetime.now().astimezone()
        age_seconds = (current - updated_at).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    if age_seconds <= MAX_SENSOR_AGE_SECONDS:
        return None

    minutes = max(10, round(age_seconds / 60))
    notification, created = database.create_notification(
        "sensor",
        "室内传感器提醒",
        f"温湿度已经 {minutes} 分钟没有更新，请检查传感器或 MQTT 连接。",
        f"sensor-stale:{environment['updated_at']}",
        {"environment": environment, "age_seconds": age_seconds},
    )
    return _log_notification(notification, created)


def _warning_key(warning):
    identity = warning.get("id") or "|".join(
        (
            warning.get("title", ""),
            warning.get("start_time", ""),
            warning.get("sender", ""),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"weather-warning:{digest}"


def collect_weather_notifications(now=None):
    settings = database.get_settings()
    weather = get_weather(settings)
    warning_result = get_weather_warnings(settings)
    current = now or datetime.now().astimezone()
    location_name = settings.get("location_name", "当前城市")
    date_key = current.date().isoformat()
    created_notifications = []
    errors = []
    if weather.get("error"):
        errors.append(str(weather["error"]))
    if warning_result.get("error"):
        errors.append(str(warning_result["error"]))

    for warning in warning_result.get("warnings", []):
        detail = str(warning.get("text") or "").strip()
        message = warning.get("title") or "收到新的官方天气预警"
        if detail:
            message += f"：{detail[:160]}"
        notification, created = database.create_notification(
            "weather_warning",
            "官方天气预警",
            message,
            _warning_key(warning),
            warning,
        )
        added = _log_notification(notification, created)
        if added:
            created_notifications.append(added)

    if not weather.get("available"):
        return created_notifications, errors

    daily = weather["daily"]
    longitude = settings.get("longitude")
    latitude = settings.get("latitude")
    coordinate_key = (
        f"{longitude},{latitude}"
        if longitude not in {None, ""} and latitude not in {None, ""}
        else ""
    )
    location_key = (
        settings.get("weather_location_id") or coordinate_key or location_name
    )
    rain_probability = int(daily["rain_probability"])
    if rain_probability >= 40:
        notification, created = database.create_notification(
            "weather",
            "降雨提醒",
            (
                f"{location_name}未来24小时最高降雨概率"
                f" {rain_probability}%，出门记得带伞。"
            ),
            f"weather-rain:{location_key}:{date_key}",
            {"weather": weather},
        )
        added = _log_notification(notification, created)
        if added:
            created_notifications.append(added)

    temperature_max = float(daily["temperature_max"])
    temperature_min = float(daily["temperature_min"])
    if temperature_max >= 35:
        notification, created = database.create_notification(
            "weather",
            "高温提醒",
            f"{location_name}今日最高 {temperature_max:g}℃，注意补水和防晒。",
            f"weather-heat:{location_key}:{date_key}",
            {"weather": weather},
        )
        added = _log_notification(notification, created)
        if added:
            created_notifications.append(added)
    if temperature_min <= 5:
        notification, created = database.create_notification(
            "weather",
            "低温提醒",
            f"{location_name}今日最低 {temperature_min:g}℃，外出注意保暖。",
            f"weather-cold:{location_key}:{date_key}",
            {"weather": weather},
        )
        added = _log_notification(notification, created)
        if added:
            created_notifications.append(added)
    return created_notifications, errors


class ProactiveMonitor:
    def __init__(self, app):
        self.app = app
        self.poll_seconds = float(app.config["PROACTIVE_POLL_SECONDS"])
        self.weather_seconds = float(app.config["PROACTIVE_WEATHER_SECONDS"])
        self.weather_retry_seconds = float(
            app.config["PROACTIVE_WEATHER_RETRY_SECONDS"]
        )
        self.last_run_at = None
        self.last_error = ""
        self._next_weather_at = 0.0
        self._weather_location_signature = None
        self._stop_event = threading.Event()
        self._thread = None
        self._owner_id = uuid.uuid4().hex
        self._leader = False
        self._run_lock = threading.Lock()
        self._start_lock = threading.Lock()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def status(self):
        return {
            "enabled": is_enabled(),
            "running": self.running,
            "leader": self._leader,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
        }

    def invalidate_weather_schedule(self):
        self._next_weather_at = 0.0
        self._weather_location_signature = None

    def run_once(self, force=False, force_weather=False, require_leader=False):
        with self._run_lock:
            return self._run_once(force, force_weather, require_leader)

    def _run_once(self, force, force_weather, require_leader):
        with self.app.app_context():
            if require_leader:
                self._leader = database.acquire_runtime_lease(
                    LEASE_NAME,
                    self._owner_id,
                    max(
                        60,
                        self.poll_seconds * 3,
                        float(
                            self.app.config["WEATHER_TIMEOUT_SECONDS"]
                        )
                        * 5,
                    ),
                )
                if not self._leader:
                    return {
                        "enabled": is_enabled(),
                        "created": [],
                        "errors": [],
                        "leader": False,
                    }

            created = []
            errors = []
            try:
                created.extend(collect_due_alarm_notifications())
            except Exception as error:
                errors.append(f"闹钟检查失败：{error}")

            enabled = is_enabled()
            if not enabled and not force:
                self.last_run_at = database.now_iso()
                self.last_error = "；".join(errors)
                return {
                    "enabled": False,
                    "created": created,
                    "errors": errors,
                    "leader": self._leader,
                    "last_run_at": self.last_run_at,
                }

            try:
                notification = collect_stale_sensor_notification()
                if notification:
                    created.append(notification)
            except Exception as error:
                errors.append(f"传感器检查失败：{error}")

            current_tick = time.monotonic()
            settings = database.get_settings()
            location_signature = (
                settings.get("weather_location_id"),
                settings.get("longitude"),
                settings.get("latitude"),
            )
            location_changed = (
                location_signature != self._weather_location_signature
            )
            check_weather = (
                force_weather
                or location_changed
                or current_tick >= self._next_weather_at
            )
            if check_weather:
                self._weather_location_signature = location_signature
                try:
                    weather_created, weather_errors = (
                        collect_weather_notifications()
                    )
                    created.extend(weather_created)
                    errors.extend(
                        f"天气检查失败：{item}" for item in weather_errors
                    )
                    delay = (
                        self.weather_retry_seconds
                        if weather_errors
                        else self.weather_seconds
                    )
                except Exception as error:
                    errors.append(f"天气检查失败：{error}")
                    delay = self.weather_retry_seconds
                self._next_weather_at = current_tick + delay

            self.last_run_at = database.now_iso()
            self.last_error = "；".join(errors)
            return {
                "enabled": is_enabled(),
                "created": created,
                "errors": errors,
                "leader": self._leader,
                "last_run_at": self.last_run_at,
            }

    def start(self):
        with self._start_lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="smarthome-proactive-monitor",
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(2, self.poll_seconds + 0.5))
        if self._leader and not self.running:
            with self.app.app_context():
                database.release_runtime_lease(LEASE_NAME, self._owner_id)
            self._leader = False

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.run_once(require_leader=True)
            except Exception:
                self.app.logger.exception("主动提醒后台检查失败")
            self._stop_event.wait(self.poll_seconds)


def handle_proactive_message(message, monitor):
    normalized = str(message or "").strip()
    if any(
        phrase in normalized
        for phrase in ("查看提醒", "有什么提醒", "未读提醒", "最近提醒")
    ):
        notifications = database.list_notifications(5, unread_only=True)
        if not notifications:
            reply = "目前没有未读提醒。"
        else:
            reply = "；".join(item["message"] for item in reversed(notifications))
        return {
            "intent": "notification_query",
            "reply": reply,
            "actions": [],
            "notifications": notifications,
        }

    if any(
        phrase in normalized
        for phrase in ("提醒全部已读", "全部提醒已读", "清空未读提醒")
    ):
        count = database.mark_all_notifications_read()
        return {
            "intent": "notification_read_all",
            "reply": f"已将 {count} 条提醒标记为已读。",
            "actions": [],
        }

    if "主动提醒" in normalized and any(
        word in normalized for word in ("暂停", "关闭", "停止")
    ):
        status = set_enabled(False)
        return {
            "intent": "proactive_control",
            "reply": status["message"],
            "actions": [],
            "proactive": monitor.status(),
        }

    if "主动提醒" in normalized and any(
        word in normalized for word in ("开启", "打开", "恢复", "启用")
    ):
        status = set_enabled(True)
        return {
            "intent": "proactive_control",
            "reply": status["message"],
            "actions": [],
            "proactive": monitor.status(),
        }

    if any(
        phrase in normalized
        for phrase in ("立即检查提醒", "检查主动提醒", "运行提醒检查")
    ):
        result = monitor.run_once(force=True, force_weather=True)
        count = len(result["created"])
        reply = (
            f"主动检查完成，新生成 {count} 条提醒。"
            if count
            else "主动检查完成，目前没有新的风险提醒。"
        )
        return {
            "intent": "proactive_run",
            "reply": reply,
            "actions": [],
            "proactive_result": result,
        }
    return None
