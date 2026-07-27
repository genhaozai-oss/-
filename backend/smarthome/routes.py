import sqlite3

from flask import Blueprint, jsonify, request, send_from_directory

from . import database
from .devices import set_device_state
from .home import run_comfort_rules
from .intent import handle_message
from .weather import get_weather


api = Blueprint("api", __name__)


def error(message, status=400):
    return jsonify({"error": message}), status


@api.get("/")
def index():
    return send_from_directory("static", "index.html")


@api.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@api.get("/api/state")
def state():
    return jsonify(
        {
            "environment": database.get_environment(),
            "devices": database.list_devices(),
            "alarms": database.list_alarms(),
            "due_alarms": database.due_alarms(),
            "settings": database.get_settings(),
            "events": database.list_events(8),
        }
    )


@api.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    result = handle_message(message, payload.get("selected_device_id"))

    if result["intent"] == "home_arrival":
        weather = get_weather(database.get_settings())
        result["weather"] = weather
        if weather.get("configured"):
            result["reply"] += weather["summary"]
    elif "天气" in message or "下雨" in message or "带伞" in message:
        weather = get_weather(database.get_settings())
        result = {
            "intent": "weather_query",
            "reply": weather["summary"],
            "actions": [],
            "weather": weather,
        }

    return jsonify(result)


@api.post("/api/environment")
def update_environment():
    payload = request.get_json(silent=True) or {}
    try:
        temperature = float(payload["temperature"])
        humidity = float(payload["humidity"])
    except (KeyError, TypeError, ValueError):
        return error("温度和湿度必须是数字。")

    if not -20 <= temperature <= 80 or not 0 <= humidity <= 100:
        return error("温湿度超出合理范围。")

    environment = database.update_environment(temperature, humidity)
    _, actions = run_comfort_rules()
    database.log_event(
        "sensor",
        f"温度 {temperature:.1f}℃，湿度 {humidity:.0f}%",
        {"environment": environment, "actions": actions},
    )
    return jsonify({"environment": environment, "actions": actions})


@api.patch("/api/devices/<device_id>")
def update_device(device_id):
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    room = payload.get("room")
    state = payload.get("state")
    if state is not None and state not in {"on", "off"}:
        return error("设备状态只能是 on 或 off。")
    if name is not None and not str(name).strip():
        return error("设备名称不能为空。")

    try:
        device = database.update_device(
            device_id,
            name=str(name).strip() if name is not None else None,
            room=str(room).strip() if room is not None else None,
        )
        if device and state is not None:
            device = set_device_state(device_id, state)
    except sqlite3.IntegrityError:
        return error("设备名称已经存在。", 409)
    if not device:
        return error("设备不存在。", 404)
    database.log_event("device", f"更新设备：{device['name']}", device)
    return jsonify({"device": device})


@api.get("/api/weather")
def weather():
    return jsonify(get_weather(database.get_settings()))


@api.put("/api/settings/location")
def location():
    payload = request.get_json(silent=True) or {}
    try:
        latitude = float(payload["latitude"])
        longitude = float(payload["longitude"])
    except (KeyError, TypeError, ValueError):
        return error("经纬度必须是数字。")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return error("经纬度超出有效范围。")

    settings = database.set_settings(
        {
            "latitude": latitude,
            "longitude": longitude,
            "location_name": str(payload.get("location_name", "当前位置")).strip()
            or "当前位置",
        }
    )
    return jsonify({"settings": settings})


@api.delete("/api/alarms/<int:alarm_id>")
def delete_alarm(alarm_id):
    if not database.delete_alarm(alarm_id):
        return error("闹钟不存在。", 404)
    return "", 204


@api.get("/api/events")
def events():
    return jsonify({"events": database.list_events(30)})
