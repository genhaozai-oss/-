import sqlite3
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from . import database
from .devices import set_device_state
from .home import run_comfort_rules, run_home_arrival
from .intent import handle_message
from .weather import get_weather


api = Blueprint("api", __name__)


def error(message, status=400):
    return jsonify({"error": message}), status


def apply_llm_plan(plan):
    if not isinstance(plan, dict):
        return None
    intent = plan.get("intent")
    if intent == "device_control":
        device_name = str(plan.get("device_name", "")).strip()
        state = plan.get("state")
        matches = database.find_devices(device_name)
        if len(matches) != 1 or state not in {"on", "off"}:
            return None
        device = set_device_state(matches[0]["id"], state)
        verb = "打开" if state == "on" else "关闭"
        return {
            "intent": "device_control",
            "reply": f"好的，已{verb}{device['name']}。",
            "actions": [
                {
                    "device_id": device["id"],
                    "device_name": device["name"],
                    "state": state,
                    "is_virtual": bool(device["is_virtual"]),
                }
            ],
        }
    if intent == "home_arrival":
        return run_home_arrival()
    if intent == "environment_query":
        environment = database.get_environment()
        return {
            "intent": intent,
            "reply": (
                f"室内温度 {environment['temperature']:.1f}℃，"
                f"湿度 {environment['humidity']:.0f}%。"
            ),
            "actions": [],
            "environment": environment,
        }
    if intent == "weather_query":
        weather = get_weather(database.get_settings())
        return {
            "intent": intent,
            "reply": weather["summary"],
            "actions": [],
            "weather": weather,
        }
    if intent == "conversation":
        reply = str(plan.get("reply", "")).strip()
        if reply:
            return {"intent": intent, "reply": reply[:300], "actions": []}
    return None


def process_message(message, selected_device_id=None):
    result = handle_message(message, selected_device_id)
    if result["intent"] == "unknown":
        interpreter = current_app.extensions["llm_interpreter"]
        plan = interpreter.classify(message, database.list_devices())
        llm_result = apply_llm_plan(plan)
        if llm_result:
            result = llm_result

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
    return result


@api.get("/")
def index():
    return send_from_directory("static", "index.html")


@api.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@api.get("/api/ai/status")
def ai_status():
    interpreter = current_app.extensions["llm_interpreter"]
    return jsonify(interpreter.status())


@api.post("/api/ai/test")
def test_ai_connection():
    interpreter = current_app.extensions["llm_interpreter"]
    if not interpreter.enabled:
        return (
            jsonify(
                {
                    "ok": False,
                    "status": interpreter.status(),
                    "error": "尚未配置云端 AI 的 Base URL 和模型名称。",
                }
            ),
            503,
        )

    result = interpreter.classify(
        "你好，请用一句简短的话介绍自己，不要控制任何设备。",
        database.list_devices(),
    )
    response = {
        "ok": result is not None,
        "status": interpreter.status(),
    }
    if result is not None:
        response["sample"] = result
        return jsonify(response)
    return jsonify(response), 502


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
    return jsonify(process_message(message, payload.get("selected_device_id")))


@api.post("/api/voice/transcribe")
def transcribe_voice():
    recognizer = current_app.extensions["speech_recognizer"]
    if not recognizer.available:
        return error(
            "尚未安装本地语音模型，请安装 requirements-voice.txt。",
            503,
        )

    audio = request.files.get("audio")
    if not audio or not audio.filename:
        return error("没有收到录音文件。")

    suffix = Path(audio.filename).suffix.lower()
    if suffix not in {".webm", ".wav", ".mp3", ".m4a", ".ogg"}:
        suffix = ".webm"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            temporary_path = Path(temporary.name)
            audio.save(temporary)
        transcription = recognizer.transcribe(temporary_path)
    except Exception:
        current_app.logger.exception("本地语音识别失败")
        return error(
            "本地语音模型加载或识别失败，请检查模型缓存和运行配置。",
            503,
        )
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    text = transcription["text"].strip()
    if not text:
        return error("没有识别到清晰语音，请靠近麦克风再试一次。", 422)
    result = process_message(text, request.form.get("selected_device_id") or None)
    return jsonify({"transcription": transcription, "result": result})


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
