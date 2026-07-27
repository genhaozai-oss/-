import json
import sqlite3
from datetime import datetime

from . import database
from .devices import set_device_state
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
                },
                "required": ["new_name"],
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
        history = database.list_conversation_messages(session_id)
        messages = [
            {
                "role": "system",
                "content": self._system_prompt(devices, selected),
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
        if not reply or not self._reply_matches_capabilities(reply):
            reply = self._fallback_reply(outputs)
        result = self._build_result(outputs, reply)
        self._remember(session_id, message, result["reply"])
        return result

    @staticmethod
    def _system_prompt(devices, selected):
        device_text = "；".join(
            f"{device['name']}（{device['type']}，{device['state']}）"
            for device in devices
        )
        selected_text = selected["name"] if selected else "无"
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %A")
        return (
            "你是“栖居”，一个温暖、简洁、可靠的中文家庭智能管理助手。"
            "需要查询实时状态或执行操作时必须调用工具，绝不能假装已经操作。"
            "只能操作工具列出的已登记设备；名称不明确时应请用户说清楚。"
            "风扇属于送风降温设备；用户说热、想凉快时应开启已登记的风扇，"
            "加湿器和抽湿器只调节湿度，灯光也不能降温。"
            "当前设备都只有打开和关闭能力，不支持风速、温度或亮度档位调节，"
            "不要声称或建议执行未登记的能力。"
            "禁止建议裸线接水或直接接触220V市电，高风险设备只做安全模拟。"
            "结合最近对话理解“它”“刚才那个”等指代，回复通常不超过三句话。"
            f"\n当前时间：{now}"
            f"\n已登记设备：{device_text or '无'}"
            f"\n网页当前选中设备：{selected_text}"
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
            "run_home_arrival": self._run_home_arrival,
            "get_weather": self._get_weather,
            "create_alarm": self._create_alarm,
            "rename_device": lambda args: self._rename_device(
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
        try:
            device = database.update_device(matches[0]["id"], name=new_name)
        except sqlite3.IntegrityError:
            return SmartHomeAgent._failure(
                "rename_device", f"已经有设备叫“{new_name}”。"
            )
        action = {"device_id": device["id"], "name": new_name}
        database.log_event("device", f"{old_name}已命名为{new_name}", action)
        return {
            "ok": True,
            "intent": "rename_device",
            "message": f"已记住，“{old_name}”现在叫“{new_name}”。",
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

    @staticmethod
    def _reply_matches_capabilities(reply):
        if any(term in reply for term in ("风速", "亮度", "温度档位")):
            return False
        if "降温" in reply and any(
            device in reply for device in ("加湿器", "抽湿器", "灯光")
        ):
            return False
        return True

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
