import json
import sqlite3
from datetime import datetime

from . import database
from .devices import set_device_capability, set_device_state
from .home import run_home_arrival
from .intent import parse_alarm_time
from .weather import get_weather


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_home_state",
            "description": "查询室内温湿度和全部已登记设备的当前状态。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": "打开或关闭一个已登记的家庭设备。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "设备的完整名称，例如客厅风扇。",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["on", "off"],
                    },
                },
                "required": ["device_name", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_device_level",
            "description": "调节设备已注册的数值能力，例如风速、亮度、窗帘位置或目标温度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string"},
                    "capability": {
                        "type": "string",
                        "enum": [
                            "speed",
                            "brightness",
                            "position",
                            "target_temperature",
                        ],
                    },
                    "value": {"type": "number"},
                },
                "required": ["device_name", "capability", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_device_capability",
            "description": (
                "仅当用户明确说明某个已登记设备支持某种能力时，"
                "把该能力持久注册到设备。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "当前名称；用户说这个或它时可填写这个设备。",
                    },
                    "capability": {
                        "type": "string",
                        "enum": [
                            "speed",
                            "brightness",
                            "position",
                            "target_temperature",
                        ],
                    },
                },
                "required": ["capability"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_preference",
            "description": "仅当用户明确表达长期偏好或要求记住时，持久保存常用设置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "preference": {
                        "type": "string",
                        "enum": [
                            "fan_speed",
                            "light_brightness",
                            "temperature",
                            "humidity",
                        ],
                    },
                    "value": {"type": "number"},
                },
                "required": ["preference", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_home_arrival",
            "description": "执行用户准备回家场景，检查环境并自动联动设备。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询用户设置位置的实时天气和出行建议。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_alarm",
            "description": "根据中文时间表达创建闹钟或提醒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_expression": {
                        "type": "string",
                        "description": "例如明天早上七点、今晚九点半。",
                    },
                    "label": {
                        "type": "string",
                        "description": "提醒内容，未说明时使用起床提醒。",
                    },
                },
                "required": ["time_expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_device",
            "description": "记住设备用途，为已登记设备设置新的名称。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "当前名称；用户说这个或它时可填写这个设备。",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "用户希望记住的新名称。",
                    },
                    "room": {
                        "type": "string",
                        "description": "设备所在房间；新名称包含房间时应同步填写。",
                    },
                },
                "required": ["new_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_device_location",
            "description": "记住一个已登记设备当前所在的房间或位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {
                        "type": "string",
                        "description": "当前设备名称；用户说这个或它时可填写这个设备。",
                    },
                    "room": {
                        "type": "string",
                        "description": "设备所在房间，例如厨房、客厅、厕所。",
                    },
                },
                "required": ["room"],
            },
        },
    },
]

ACTION_REQUEST_HINTS = (
    "打开",
    "开启",
    "关掉",
    "关闭",
    "启动",
    "停止",
    "帮我凉快",
    "有点热",
    "太热",
    "设置闹钟",
    "提醒我",
    "准备回家",
    "要回家",
    "叫做",
    "命名",
    "改名",
    "搬到",
    "位置",
    "房间",
    "风速",
    "亮度",
    "调到",
    "调成",
    "记住我的",
    "以后默认",
)
OPERATION_CLAIMS = (
    "已打开",
    "已开启",
    "已关闭",
    "已关掉",
    "已经打开",
    "已经关闭",
    "已设置",
    "已为您",
    "已为你",
)


class SmartHomeAgent:
    def __init__(self, interpreter):
        self.llm = interpreter

    @property
    def enabled(self):
        return self.llm.enabled

    def respond(self, message, session_id, selected_device_id=None):
        if not self.enabled or not message.strip():
            return None

        devices = database.list_devices()
        selected = database.get_device(selected_device_id) if selected_device_id else None
        preferences = database.get_user_preferences()
        history = database.list_conversation_messages(session_id)
        messages = [
            {
                "role": "system",
                "content": self._system_prompt(devices, selected, preferences),
            },
            *history,
            {"role": "user", "content": message},
        ]

        assistant = self.llm.chat(messages, tools=TOOLS)
        if not assistant:
            return None

        tool_calls = (assistant.get("tool_calls") or [])[:4]
        if not tool_calls and self._requires_tool(
            message,
            str(assistant.get("content") or ""),
        ):
            retry_messages = [
                messages[0],
                {
                    "role": "system",
                    "content": (
                        "这条用户消息包含实际查询或设备操作请求。"
                        "不要用文字假装执行，必须选择合适的工具；"
                        "如果目标无法唯一确定，也要调用工具让本地返回准确错误。"
                    ),
                },
                *messages[1:],
            ]
            assistant = self.llm.chat(
                retry_messages,
                tools=TOOLS,
                temperature=0,
            )
            if not assistant:
                return None
            tool_calls = (assistant.get("tool_calls") or [])[:4]
            if not tool_calls:
                return None

        if not tool_calls:
            reply = str(assistant.get("content") or "").strip()
            if not reply:
                return None
            result = {
                "intent": "conversation",
                "reply": reply[:500],
                "actions": [],
                "ai": {"provider": "cloud", "model": self.llm.model},
            }
            self._remember(session_id, message, result["reply"])
            return result

        messages.append(
            {
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        outputs = []
        for tool_call in tool_calls:
            output = self._execute_tool_call(tool_call, selected_device_id)
            outputs.append(output)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": json.dumps(output, ensure_ascii=False),
                }
            )

        final = self.llm.chat(messages)
        reply = (
            str(final.get("content") or "").strip()
            if final
            else self._fallback_reply(outputs)
        )
        if not reply:
            reply = self._fallback_reply(outputs)
        result = self._build_result(outputs, reply)
        self._remember(session_id, message, result["reply"])
        return result

    @staticmethod
    def _system_prompt(devices, selected, preferences):
        device_descriptions = []
        for device in devices:
            capability_text = "、".join(
                f"{item['capability']}={item['value']:g}{item['unit']}"
                for item in device["capabilities"]
            )
            suffix = f"，能力：{capability_text}" if capability_text else ""
            device_descriptions.append(
                f"{device['name']}（房间：{device['room']}，"
                f"{device['type']}，{device['state']}{suffix}）"
            )
        device_text = "；".join(device_descriptions)
        preference_text = "、".join(
            f"{name}={value}" for name, value in preferences.items()
        )
        selected_text = selected["name"] if selected else "无"
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %A")
        return (
            "你是“栖居”，一个温暖、简洁、可靠的中文家庭智能管理助手。"
            "需要查询实时状态或执行操作时必须调用工具，绝不能假装已经操作。"
            "只能操作工具列出的已登记设备；名称不明确时应请用户说清楚。"
            "风扇属于送风降温设备；用户说热、想凉快时应开启已登记的风扇，"
            "加湿器和抽湿器只调节湿度，灯光也不能降温。"
            "设备能力来自动态注册表，只能调节该设备已经注册的能力。"
            "用户明确告诉你设备支持新能力时调用记忆工具；"
            "用户表达长期偏好时调用偏好工具，后续相关操作主动采用已记住偏好。"
            "给设备改名时，如果新名称包含房间（如厕所灯、厨房风扇），"
            "必须同时更新设备房间；用户说明设备放在哪里时调用位置工具。"
            "禁止建议裸线接水或直接接触220V市电，高风险设备只做安全模拟。"
            "结合最近对话理解“它”“刚才那个”等指代，回复通常不超过三句话。"
            f"\n当前时间：{now}"
            f"\n已登记设备：{device_text or '无'}"
            f"\n网页当前选中设备：{selected_text}"
            f"\n已记住的用户偏好：{preference_text or '无'}"
        )

    @staticmethod
    def _requires_tool(message, assistant_reply=""):
        return any(hint in message for hint in ACTION_REQUEST_HINTS) or any(
            claim in assistant_reply for claim in OPERATION_CLAIMS
        )

    def _execute_tool_call(self, tool_call, selected_device_id):
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = (
                raw_arguments
                if isinstance(raw_arguments, dict)
                else json.loads(raw_arguments)
            )
        except (TypeError, json.JSONDecodeError):
            return self._failure(name, "工具参数不是有效 JSON。")
        if not isinstance(arguments, dict):
            return self._failure(name, "工具参数格式错误。")

        handlers = {
            "get_home_state": self._get_home_state,
            "control_device": self._control_device,
            "set_device_level": self._set_device_level,
            "remember_device_capability": lambda args: (
                self._remember_device_capability(args, selected_device_id)
            ),
            "remember_preference": self._remember_preference,
            "run_home_arrival": self._run_home_arrival,
            "get_weather": self._get_weather,
            "create_alarm": self._create_alarm,
            "rename_device": lambda args: self._rename_device(
                args, selected_device_id
            ),
            "update_device_location": lambda args: self._update_device_location(
                args, selected_device_id
            ),
        }
        handler = handlers.get(name)
        if not handler:
            return self._failure(name, "不允许调用这个工具。")
        return handler(arguments)

    @staticmethod
    def _get_home_state(_arguments):
        environment = database.get_environment()
        devices = [
            {
                "id": device["id"],
                "name": device["name"],
                "type": device["type"],
                "room": device["room"],
                "state": device["state"],
                "online": bool(device["online"]),
                "is_virtual": bool(device["is_virtual"]),
            }
            for device in database.list_devices()
        ]
        return {
            "ok": True,
            "intent": "home_state",
            "message": (
                f"室内 {environment['temperature']:.1f}℃，"
                f"湿度 {environment['humidity']:.0f}%。"
            ),
            "environment": environment,
            "devices": devices,
            "actions": [],
        }

    @staticmethod
    def _control_device(arguments):
        name = str(arguments.get("device_name", "")).strip()
        state = arguments.get("state")
        if not name or state not in {"on", "off"}:
            return SmartHomeAgent._failure(
                "control_device", "设备名称或开关状态无效。"
            )
        matches = database.find_devices(name)
        if not matches:
            return SmartHomeAgent._failure(
                "control_device", f"没有找到“{name}”。"
            )
        if len(matches) > 1:
            names = "、".join(device["name"] for device in matches)
            return SmartHomeAgent._failure(
                "control_device", f"设备不唯一：{names}。"
            )
        device = matches[0]
        if not device["online"] and not device["is_virtual"]:
            return SmartHomeAgent._failure(
                "control_device", f"{device['name']}当前离线。"
            )
        updated = set_device_state(device["id"], state)
        action = {
            "device_id": updated["id"],
            "device_name": updated["name"],
            "state": state,
            "is_virtual": bool(updated["is_virtual"]),
        }
        verb = "打开" if state == "on" else "关闭"
        database.log_event("device", f"{verb}{updated['name']}", action)
        return {
            "ok": True,
            "intent": "control_device",
            "message": f"已{verb}{updated['name']}。",
            "device": updated,
            "actions": [action],
        }

    @staticmethod
    def _set_device_level(arguments):
        name = str(arguments.get("device_name", "")).strip()
        capability_name = str(arguments.get("capability", "")).strip()
        try:
            value = float(arguments["value"])
        except (KeyError, TypeError, ValueError):
            return SmartHomeAgent._failure(
                "set_device_level", "能力值必须是数字。"
            )
        matches = database.find_devices(name)
        if not matches:
            return SmartHomeAgent._failure(
                "set_device_level", f"没有找到“{name}”。"
            )
        if len(matches) > 1:
            return SmartHomeAgent._failure(
                "set_device_level", "设备名称不唯一，请说出完整名称。"
            )
        device = matches[0]
        registered = database.get_device_capability(
            device["id"], capability_name
        )
        if not registered:
            return SmartHomeAgent._failure(
                "set_device_level",
                f"{device['name']}尚未注册 {capability_name} 能力。",
            )
        try:
            updated = set_device_capability(
                device["id"], capability_name, value
            )
        except ValueError as exc:
            return SmartHomeAgent._failure("set_device_level", str(exc))
        action = {
            "device_id": device["id"],
            "device_name": device["name"],
            "capability": capability_name,
            "value": updated["value"],
            "unit": updated["unit"],
            "is_virtual": bool(device["is_virtual"]),
        }
        message = (
            f"已将{device['name']}的{updated['display_name']}调到"
            f"{updated['value']:g}{updated['unit']}。"
        )
        database.log_event("device", message, action)
        return {
            "ok": True,
            "intent": "set_device_level",
            "message": message,
            "capability": updated,
            "actions": [action],
        }

    @staticmethod
    def _remember_device_capability(arguments, selected_device_id):
        current_name = str(arguments.get("device_name", "")).strip()
        capability_name = str(arguments.get("capability", "")).strip()
        selected_words = {"", "这个", "它", "这个设备", "选中的设备"}
        if current_name in selected_words and selected_device_id:
            device = database.get_device(selected_device_id)
            matches = [device] if device else []
        else:
            matches = database.find_devices(current_name) if current_name else []
        if len(matches) != 1:
            return SmartHomeAgent._failure(
                "remember_device_capability",
                "没有唯一找到设备，请先在网页中选中或说出完整名称。",
            )
        capability = database.register_device_capability(
            matches[0]["id"],
            capability_name,
            learned=True,
        )
        if not capability:
            return SmartHomeAgent._failure(
                "remember_device_capability", "这种能力暂时不在安全接口范围内。"
            )
        message = (
            f"已记住{matches[0]['name']}支持"
            f"{capability['display_name']}调节。"
        )
        database.log_event("memory", message, capability)
        return {
            "ok": True,
            "intent": "remember_device_capability",
            "message": message,
            "capability": capability,
            "actions": [],
        }

    @staticmethod
    def _remember_preference(arguments):
        preference = str(arguments.get("preference", "")).strip()
        ranges = {
            "fan_speed": (0, 100, "%"),
            "light_brightness": (0, 100, "%"),
            "temperature": (16, 30, "℃"),
            "humidity": (30, 80, "%"),
        }
        if preference not in ranges:
            return SmartHomeAgent._failure(
                "remember_preference", "这种偏好暂时不支持。"
            )
        try:
            value = float(arguments["value"])
        except (KeyError, TypeError, ValueError):
            return SmartHomeAgent._failure(
                "remember_preference", "偏好值必须是数字。"
            )
        minimum, maximum, unit = ranges[preference]
        if not minimum <= value <= maximum:
            return SmartHomeAgent._failure(
                "remember_preference",
                f"偏好值应在 {minimum}～{maximum}{unit} 之间。",
            )
        preferences = database.set_user_preference(preference, f"{value:g}")
        labels = {
            "fan_speed": "常用风速",
            "light_brightness": "常用亮度",
            "temperature": "舒适温度",
            "humidity": "舒适湿度",
        }
        message = f"已记住你的{labels[preference]}是 {value:g}{unit}。"
        database.log_event("memory", message, {"preferences": preferences})
        return {
            "ok": True,
            "intent": "remember_preference",
            "message": message,
            "preferences": preferences,
            "actions": [],
        }

    @staticmethod
    def _run_home_arrival(_arguments):
        result = run_home_arrival()
        weather = get_weather(database.get_settings())
        return {
            "ok": True,
            **result,
            "weather": weather,
            "message": result["reply"],
        }

    @staticmethod
    def _get_weather(_arguments):
        weather = get_weather(database.get_settings())
        return {
            "ok": weather.get("available", weather.get("configured", False)),
            "intent": "weather_query",
            "message": weather["summary"],
            "weather": weather,
            "actions": [],
        }

    @staticmethod
    def _create_alarm(arguments):
        time_expression = str(arguments.get("time_expression", "")).strip()
        scheduled = parse_alarm_time(time_expression)
        if not scheduled:
            return SmartHomeAgent._failure(
                "create_alarm", f"无法理解时间“{time_expression}”。"
            )
        label = str(arguments.get("label") or "起床提醒").strip()[:50]
        alarm = database.create_alarm(
            label or "起床提醒",
            scheduled.isoformat(timespec="seconds"),
        )
        message = f"闹钟已设置在 {scheduled.strftime('%m月%d日 %H:%M')}。"
        database.log_event("alarm", message, alarm)
        return {
            "ok": True,
            "intent": "create_alarm",
            "message": message,
            "alarm": alarm,
            "actions": [{"alarm_id": alarm["id"]}],
        }

    @staticmethod
    def _rename_device(arguments, selected_device_id):
        current_name = str(arguments.get("device_name", "")).strip()
        new_name = str(arguments.get("new_name", "")).strip()
        if not new_name or len(new_name) > 20:
            return SmartHomeAgent._failure(
                "rename_device", "新名称应为1至20个字符。"
            )

        selected_words = {"", "这个", "它", "这个设备", "选中的设备"}
        if current_name in selected_words and selected_device_id:
            device = database.get_device(selected_device_id)
            matches = [device] if device else []
        else:
            matches = database.find_devices(current_name) if current_name else []
        if not matches:
            return SmartHomeAgent._failure(
                "rename_device", "没有找到要命名的设备，请先在网页中选中它。"
            )
        if len(matches) > 1:
            return SmartHomeAgent._failure(
                "rename_device", "找到了多个设备，请使用更完整的名称。"
            )

        old_name = matches[0]["name"]
        room = str(arguments.get("room", "")).strip()
        room = room or database.infer_room_from_name(new_name) or matches[0]["room"]
        if len(room) > 20:
            return SmartHomeAgent._failure(
                "rename_device", "房间名称不能超过20个字符。"
            )
        try:
            device = database.update_device(
                matches[0]["id"], name=new_name, room=room
            )
        except sqlite3.IntegrityError:
            return SmartHomeAgent._failure(
                "rename_device", f"已经有设备叫“{new_name}”。"
            )
        action = {
            "device_id": device["id"],
            "name": new_name,
            "room": device["room"],
        }
        database.log_event("device", f"{old_name}已命名为{new_name}", action)
        return {
            "ok": True,
            "intent": "rename_device",
            "message": f"已记住，“{old_name}”现在叫“{new_name}”。",
            "device": device,
            "actions": [action],
        }

    @staticmethod
    def _update_device_location(arguments, selected_device_id):
        current_name = str(arguments.get("device_name", "")).strip()
        room = str(arguments.get("room", "")).strip()
        if not room or len(room) > 20:
            return SmartHomeAgent._failure(
                "update_device_location", "房间名称应为1至20个字符。"
            )

        selected_words = {"", "这个", "它", "这个设备", "选中的设备"}
        if current_name in selected_words and selected_device_id:
            device = database.get_device(selected_device_id)
            matches = [device] if device else []
        else:
            matches = database.find_devices(current_name) if current_name else []
        if not matches:
            return SmartHomeAgent._failure(
                "update_device_location", "没有找到这个设备，请先在网页中选中它。"
            )
        if len(matches) > 1:
            return SmartHomeAgent._failure(
                "update_device_location", "找到了多个设备，请使用更完整的名称。"
            )

        device = database.update_device(matches[0]["id"], room=room)
        action = {"device_id": device["id"], "room": room}
        database.log_event(
            "device", f"{device['name']}的位置已更新为{room}", action
        )
        return {
            "ok": True,
            "intent": "update_device_location",
            "message": f"已记住，{device['name']}在{room}。",
            "device": device,
            "actions": [action],
        }

    @staticmethod
    def _failure(intent, message):
        return {
            "ok": False,
            "intent": intent or "unknown",
            "message": message,
            "actions": [],
        }

    @staticmethod
    def _fallback_reply(outputs):
        messages = [output["message"] for output in outputs if output.get("message")]
        return " ".join(messages) or "操作已经处理，但云端暂时没有生成回复。"

    def _build_result(self, outputs, reply):
        intents = [output.get("intent") for output in outputs if output.get("intent")]
        result = {
            "intent": intents[0] if len(set(intents)) == 1 else "assistant",
            "reply": reply[:500],
            "actions": [
                action
                for output in outputs
                for action in output.get("actions", [])
            ],
            "ai": {"provider": "cloud", "model": self.llm.model},
        }
        for key in ("environment", "weather", "alarm"):
            value = next(
                (output[key] for output in reversed(outputs) if key in output),
                None,
            )
            if value is not None:
                result[key] = value
        return result

    @staticmethod
    def _remember(session_id, user_message, assistant_message):
        database.add_conversation_message(session_id, "user", user_message)
        database.add_conversation_message(session_id, "assistant", assistant_message)
