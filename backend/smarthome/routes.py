import re
import sqlite3
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from . import database
from .autoflow import (
    auto_flow_status,
    handle_auto_flow_message,
    hold_manual_control,
    run_auto_flow,
    set_auto_flow_enabled,
)
from .automations import create_rule, list_rules
from .context import (
    handle_context_message,
    remember_result_device,
    resolve_context_device,
)
from .devices import (
    DeviceCommandError,
    set_device_capability,
    set_device_state,
)
from .home import run_home_arrival
from .intent import handle_message
from .learning import learn_from_result, observe_capability
from .preferences import forget_preference, list_preferences
from .proactive import (
    handle_proactive_message,
    set_enabled as set_proactive_enabled,
)
from .scenes import (
    list_scenes as list_custom_scenes,
    run_scene,
    save_scene,
)
from .tts import SpeechSynthesisError
from .undo import (
    capture_device_snapshot,
    is_undo_request,
    record_undoable,
    undo_last_action,
)
from .voice import SpeechRecognitionError
from .weather import geocode_location, get_weather


api = Blueprint("api", __name__)
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def error(message, status=400):
    return jsonify({"error": message}), status


def normalize_session_id(value):
    value = str(value or "").strip()
    return value if SESSION_ID_PATTERN.fullmatch(value) else "default"


def run_saved_scene_from_message(message):
    normalized = str(message or "").strip()
    if not any(
        hint in normalized
        for hint in ("执行", "运行", "开启", "启动", "进入")
    ):
        return None
    matches = [
        scene
        for scene in database.list_scenes()
        if scene["name"] in normalized
    ]
    if len(matches) != 1:
        return None
    if any(word in normalized for word in ("不要", "别", "取消")):
        return {
            "intent": "scene_cancelled",
            "reply": f"好的，不执行“{matches[0]['name']}”场景。",
            "actions": [],
        }
    result = run_scene(scene_id=matches[0]["id"])
    reply = f"已执行“{result['scene']['name']}”场景。"
    if result["errors"]:
        reply += " ".join(result["errors"])
    elif not result["actions"]:
        reply += "设备当前已经符合场景设置。"
    return {
        "intent": "run_scene",
        "reply": reply,
        **result,
    }


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


def _process_message_core(message, selected_device_id=None, session_id="default"):
    scene_result = run_saved_scene_from_message(message)
    if scene_result:
        return scene_result
    result = handle_message(message, selected_device_id)
    automation_request = (
        any(sensor in message for sensor in ("温度", "湿度"))
        and any(
            comparison in message
            for comparison in ("超过", "高于", "低于", "少于")
        )
        and any(
            action in message
            for action in ("打开", "开启", "关闭", "关掉", "调到", "调成")
        )
    )
    if result["intent"] == "home_arrival":
        weather = get_weather(database.get_settings())
        result["weather"] = weather
        if weather.get("available"):
            result["reply"] += weather["summary"]
        return result

    if "天气" in message or "下雨" in message or "带伞" in message:
        weather = get_weather(database.get_settings())
        return {
            "intent": "weather_query",
            "reply": weather["summary"],
            "actions": [],
            "weather": weather,
        }

    if result["intent"] != "unknown" and not automation_request:
        return result

    assistant_agent = current_app.extensions["assistant_agent"]
    if assistant_agent.enabled:
        ai_result = assistant_agent.respond(
            message,
            normalize_session_id(session_id),
            selected_device_id,
        )
        if ai_result:
            return ai_result

    interpreter = current_app.extensions["llm_interpreter"]
    plan = interpreter.classify(message, database.list_devices())
    llm_result = apply_llm_plan(plan)
    if llm_result:
        result = llm_result
    return result


def _process_message(message, selected_device_id=None, session_id="default"):
    session_id = normalize_session_id(session_id)
    context_device_id = resolve_context_device(session_id, selected_device_id)
    if is_undo_request(message):
        result = undo_last_action()
        hold_manual_control(result, "用户撤销自动操作")
        if context_device_id:
            result["context_device_id"] = context_device_id
        return result

    snapshot = capture_device_snapshot()
    result = handle_proactive_message(
        message,
        current_app.extensions["proactive_monitor"],
    )
    if result is None:
        result = handle_auto_flow_message(message)
    if result is None:
        result = handle_context_message(message, context_device_id)
    if result is None:
        result = _process_message_core(
            message,
            context_device_id,
            session_id,
        )
    record_undoable(snapshot, result, message)
    if result.get("intent") in {
        "control_device",
        "device_control",
        "set_device_level",
        "assistant",
        "run_scene",
        "home_arrival",
    }:
        hold_manual_control(result)
    learnings = learn_from_result(result)
    learned = [item for item in learnings if item.get("learned")]
    learning_feedback = [
        item
        for item in learnings
        if item.get("message")
    ]
    if learnings:
        result["learning"] = learnings
    if learning_feedback:
        result["reply"] += " " + " ".join(
            item["message"] for item in learning_feedback
        )
    if learned:
        result["memories"] = list_preferences()
    remembered_device_id = remember_result_device(session_id, result)
    if remembered_device_id:
        result["context_device_id"] = remembered_device_id
    return result


def process_message(message, selected_device_id=None, session_id="default"):
    try:
        return _process_message(
            message,
            selected_device_id,
            session_id,
        )
    except DeviceCommandError as exc:
        return {
            "intent": "device_command_failed",
            "reply": str(exc),
            "actions": [],
        }


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
            "due_alarms": [],
            "settings": database.get_settings(),
            "events": database.list_events(8),
            "automations": list_rules(),
            "scenes": list_custom_scenes(),
            "memories": list_preferences(),
            "auto_flow": auto_flow_status(),
            "notifications": database.list_notifications(20),
            "unread_notifications": database.unread_notification_count(),
            "proactive": current_app.extensions[
                "proactive_monitor"
            ].status(),
        }
    )


@api.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    return jsonify(
        process_message(
            message,
            payload.get("selected_device_id"),
            payload.get("session_id"),
        )
    )


@api.get("/api/voice/status")
def voice_status():
    recognizer = current_app.extensions["speech_recognizer"]
    synthesizer = current_app.extensions["speech_synthesizer"]
    status = recognizer.status()
    status["tts"] = synthesizer.status()
    return jsonify(status)


@api.post("/api/voice/synthesize")
def synthesize_voice():
    synthesizer = current_app.extensions["speech_synthesizer"]
    if not synthesizer.available:
        return error("云端语音播报尚未配置。", 503)

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    voice = str(payload.get("voice", "")).strip() or None
    if not text:
        return error("播报文字不能为空。")
    if len(text) > 400:
        return error("单次播报不能超过 400 个字符。")

    try:
        result = synthesizer.synthesize(text, voice=voice)
    except SpeechSynthesisError as exc:
        current_app.logger.warning("语音合成失败：%s", exc)
        return error(str(exc), 503)
    except Exception:
        current_app.logger.exception("语音合成发生未预期错误")
        return error("语音合成失败，请稍后重试。", 503)
    return jsonify(result)


@api.post("/api/voice/transcribe")
def transcribe_voice():
    recognizer = current_app.extensions["speech_recognizer"]
    if not recognizer.available:
        return error(
            "语音识别不可用：请配置百炼 API，或安装 requirements-voice.txt。",
            503,
        )

    audio = request.files.get("audio")
    if not audio or not audio.filename:
        return error("没有收到录音文件。")

    suffix = Path(audio.filename).suffix.lower()
    if suffix not in {
        ".aac",
        ".aiff",
        ".amr",
        ".flac",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
    }:
        suffix = ".webm"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            temporary_path = Path(temporary.name)
            audio.save(temporary)
        devices = database.list_devices()
        context_terms = [
            term
            for device in devices
            for term in (device["name"], device.get("room"))
            if term
        ]
        transcription = recognizer.transcribe(
            temporary_path,
            mime_type=audio.mimetype,
            context_terms=context_terms,
        )
    except SpeechRecognitionError as exc:
        current_app.logger.warning("语音识别失败：%s", exc)
        return error(str(exc), 503)
    except Exception:
        current_app.logger.exception("语音识别发生未预期错误")
        return error("语音识别失败，请稍后重试。", 503)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    text = transcription["text"].strip()
    if not text:
        return error("没有识别到清晰语音，请靠近麦克风再试一次。", 422)
    response = {"transcription": transcription}
    if request.form.get("execute", "1") != "0":
        response["result"] = process_message(
            text,
            request.form.get("selected_device_id") or None,
            request.form.get("session_id"),
        )
    return jsonify(response)


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

    snapshot = capture_device_snapshot()
    environment = database.update_environment(temperature, humidity)
    flow = run_auto_flow(trigger="web_sensor")
    actions = flow["actions"]
    database.log_event(
        "sensor",
        f"温度 {temperature:.1f}℃，湿度 {humidity:.0f}%",
        {
            "environment": environment,
            "actions": actions,
            "auto_flow_status": flow["status"],
        },
    )
    record_undoable(
        snapshot,
        {
            "intent": "auto_flow_sensor",
            "reply": flow["summary"],
            "actions": actions,
        },
        "环境自动流",
    )
    return jsonify(
        {
            "environment": environment,
            "actions": actions,
            "auto_flow": flow,
        }
    )


@api.patch("/api/auto-flow")
def update_auto_flow():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("enabled"), bool):
        return error("请提供有效的自动托管状态。")
    return jsonify(
        {"auto_flow": set_auto_flow_enabled(payload["enabled"])}
    )


@api.post("/api/auto-flow/run")
def run_auto_flow_now():
    snapshot = capture_device_snapshot()
    flow = run_auto_flow(trigger="manual_button", force=True)
    record_undoable(
        snapshot,
        {
            "intent": "run_auto_flow",
            "reply": flow["summary"],
            "actions": flow["actions"],
        },
        "立即巡检",
    )
    return jsonify({"auto_flow": flow, "actions": flow["actions"]})


@api.patch("/api/proactive")
def update_proactive():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("enabled"), bool):
        return error("请提供有效的主动提醒状态。")
    set_proactive_enabled(payload["enabled"])
    return jsonify(
        {
            "proactive": current_app.extensions[
                "proactive_monitor"
            ].status()
        }
    )


@api.post("/api/proactive/run")
def run_proactive_now():
    result = current_app.extensions["proactive_monitor"].run_once(
        force=True,
        force_weather=True,
    )
    return jsonify(
        {
            "result": result,
            "proactive": current_app.extensions[
                "proactive_monitor"
            ].status(),
        }
    )


@api.post("/api/notifications/claim")
def claim_notification():
    return jsonify({"notification": database.claim_notification()})


@api.post("/api/notifications/<int:notification_id>/ack")
def acknowledge_notification(notification_id):
    payload = request.get_json(silent=True) or {}
    claim_token = str(payload.get("claim_token") or "").strip()
    if not claim_token:
        return error("提醒确认令牌不能为空。")
    notification = database.acknowledge_notification(
        notification_id,
        claim_token,
    )
    if not notification:
        return error("提醒领取已过期，请等待重新投递。", 409)
    return jsonify({"notification": notification})


@api.patch("/api/notifications/<int:notification_id>")
def read_notification(notification_id):
    payload = request.get_json(silent=True) or {}
    if payload.get("read") is not True:
        return error("请提供有效的已读状态。")
    notification = database.mark_notification_read(notification_id)
    if not notification:
        return error("提醒不存在。", 404)
    return jsonify({"notification": notification})


@api.post("/api/notifications/read-all")
def read_all_notifications():
    return jsonify({"updated": database.mark_all_notifications_read()})


@api.post("/api/automations")
def create_automation():
    payload = request.get_json(silent=True) or {}
    try:
        rule = create_rule(payload, payload.get("selected_device_id"))
    except ValueError as exc:
        return error(str(exc))
    return jsonify({"automation": rule}), 201


@api.patch("/api/automations/<int:rule_id>")
def update_automation(rule_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("enabled"), bool):
        return error("请提供有效的启用状态。")
    rule = database.update_automation_rule(
        rule_id,
        enabled=payload["enabled"],
    )
    if not rule:
        return error("自动化规则不存在。", 404)
    return jsonify({"automation": list_rules_by_id(rule_id)})


@api.delete("/api/automations/<int:rule_id>")
def delete_automation(rule_id):
    if not database.delete_automation_rule(rule_id):
        return error("自动化规则不存在。", 404)
    return "", 204


@api.post("/api/scenes")
def create_custom_scene():
    payload = request.get_json(silent=True) or {}
    try:
        scene = save_scene(payload, payload.get("selected_device_id"))
    except ValueError as exc:
        return error(str(exc))
    return jsonify({"scene": scene}), 201


@api.post("/api/scenes/<int:scene_id>/run")
def run_custom_scene(scene_id):
    snapshot = capture_device_snapshot()
    try:
        result = run_scene(scene_id=scene_id)
    except ValueError as exc:
        return error(str(exc), 404)
    except DeviceCommandError as exc:
        return error(str(exc), 503)
    record_undoable(
        snapshot,
        {
            "intent": "run_scene",
            "reply": f"网页运行场景：{result['scene']['name']}",
            "actions": result["actions"],
        },
        "网页场景",
    )
    hold_manual_control(result, "用户运行场景")
    return jsonify(result)


@api.delete("/api/scenes/<int:scene_id>")
def delete_custom_scene(scene_id):
    if not database.delete_scene(scene_id):
        return error("场景不存在。", 404)
    return "", 204


@api.get("/api/memories")
def memories():
    return jsonify({"memories": list_preferences()})


@api.delete("/api/memories/<preference>")
def delete_memory(preference):
    try:
        label = forget_preference(preference)
    except ValueError as exc:
        return error(str(exc), 404)
    database.log_event(
        "memory",
        f"删除记忆：{label}",
        {"preference": preference, "deleted": True},
    )
    return "", 204


def list_rules_by_id(rule_id):
    return next(
        rule for rule in list_rules() if rule["id"] == rule_id
    )


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

    snapshot = capture_device_snapshot()
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
    except DeviceCommandError as exc:
        return error(str(exc), 503)
    if not device:
        return error("设备不存在。", 404)
    database.log_event("device", f"更新设备：{device['name']}", device)
    record_undoable(
        snapshot,
        {
            "intent": "manual_device_update",
            "reply": f"网页更新设备：{device['name']}",
            "actions": [{"device_id": device["id"]}],
        },
        "网页设备控制",
    )
    if state is not None:
        hold_manual_control(
            {
                "actions": [
                    {
                        "device_id": device["id"],
                        "state": device["state"],
                    }
                ]
            }
        )
    response = {"device": device}
    if payload.get("session_id"):
        response["context_device_id"] = database.remember_session_device(
            normalize_session_id(payload["session_id"]),
            device["id"],
        )
    return jsonify(response)


@api.patch("/api/devices/<device_id>/capabilities/<capability>")
def update_capability(device_id, capability):
    payload = request.get_json(silent=True) or {}
    if "value" not in payload:
        return error("请提供要设置的能力值。")
    snapshot = capture_device_snapshot()
    try:
        value = float(payload["value"])
        updated = set_device_capability(device_id, capability, value)
    except (TypeError, ValueError) as exc:
        return error(str(exc) or "能力值必须是数字。")
    except DeviceCommandError as exc:
        return error(str(exc), 503)
    if not database.get_device(device_id):
        return error("设备不存在。", 404)
    if not updated:
        return error("该设备尚未注册这个能力。", 409)
    database.log_event(
        "device",
        f"调节{database.get_device(device_id)['name']}的{updated['display_name']}",
        updated,
    )
    record_undoable(
        snapshot,
        {
            "intent": "manual_capability_update",
            "reply": (
                f"网页调节{database.get_device(device_id)['name']}"
                f"{updated['display_name']}"
            ),
            "actions": [{"device_id": device_id}],
        },
        "网页能力调节",
    )
    hold_manual_control(
        {
            "actions": [
                {
                    "device_id": device_id,
                    "capability": capability,
                }
            ]
        }
    )
    learning = observe_capability(device_id, capability, updated["value"])
    response = {"capability": updated, "learning": learning}
    if payload.get("session_id"):
        response["context_device_id"] = database.remember_session_device(
            normalize_session_id(payload["session_id"]),
            device_id,
        )
    return jsonify(response)


@api.get("/api/weather")
def weather():
    return jsonify(get_weather(database.get_settings()))


@api.put("/api/settings/location")
def location():
    payload = request.get_json(silent=True) or {}
    location_name = str(payload.get("location_name", "")).strip()

    if "latitude" not in payload and "longitude" not in payload:
        location = geocode_location(location_name)
        if not location["available"]:
            return error(location["summary"], 503)
        if not location["found"]:
            return error(location["summary"])
        latitude = location["latitude"]
        longitude = location["longitude"]
        location_name = location["location_name"]
        location_id = location["location_id"]
    else:
        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError):
            return error("请直接填写城市名。")
        location_id = ""

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return error("位置坐标格式不正确。")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return error("经纬度超出有效范围。")

    settings = database.set_settings(
        {
            "latitude": latitude,
            "longitude": longitude,
            "location_name": location_name or "当前位置",
            "weather_location_id": location_id,
        }
    )
    current_app.extensions["proactive_monitor"].invalidate_weather_schedule()
    return jsonify({"settings": settings})


@api.delete("/api/alarms/<int:alarm_id>")
def delete_alarm(alarm_id):
    if not database.delete_alarm(alarm_id):
        return error("闹钟不存在。", 404)
    return "", 204


@api.get("/api/events")
def events():
    return jsonify({"events": database.list_events(30)})
