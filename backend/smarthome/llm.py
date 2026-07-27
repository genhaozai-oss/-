import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SYSTEM_PROMPT = """
你是家庭智能管理系统的意图分类器。只输出一个 JSON 对象，不要输出 Markdown。
可用 intent：
- device_control：控制设备，需要 device_name 和 state（on/off）
- home_arrival：用户准备回家
- environment_query：查询室内温湿度
- weather_query：查询天气
- conversation：不执行设备，只进行简短友好回复，需要 reply
- unknown：无法判断

device_name 必须原样选择设备列表中的一个名称。不要编造设备，不要执行危险操作。
""".strip()


class LlmInterpreter:
    def __init__(self, app):
        self.base_url = app.config["LLM_BASE_URL"].rstrip("/")
        self.api_key = app.config["LLM_API_KEY"]
        self.model = app.config["LLM_MODEL"]
        self.timeout = 12
        self.max_tokens = 500
        self.logger = app.logger
        self.last_error = None
        self.last_success_at = None

    @property
    def enabled(self):
        return bool(self.base_url and self.model)

    def status(self):
        if not self.enabled:
            state = "disabled"
        elif self.last_error:
            state = "error"
        elif self.last_success_at:
            state = "connected"
        else:
            state = "ready"
        return {
            "state": state,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }

    def _record_error(self, error_type, message, status_code=None):
        self.last_error = {
            "type": error_type,
            "message": str(message).strip()[:500],
            "status_code": status_code,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.logger.warning(
            "云端 AI 调用失败：%s（HTTP %s）",
            self.last_error["message"],
            status_code or "-",
        )

    @staticmethod
    def _http_error_message(exc):
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return exc.reason or "云端服务返回 HTTP 错误"
        if not isinstance(payload, dict):
            return str(payload)
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return error.get("message") or error.get("code") or str(error)
        return str(error)

    def classify(self, message, devices):
        if not self.enabled:
            return None

        device_names = [device["name"] for device in devices]
        response = self.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "devices": device_names},
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        if not response:
            return None

        try:
            result = json.loads(response["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            self._record_error("invalid_response", "云端没有返回有效的意图 JSON")
            return None
        if not isinstance(result, dict):
            self._record_error("invalid_response", "云端没有返回 JSON 对象")
            return None
        return result

    def chat(self, messages, *, tools=None, response_format=None, temperature=0.2):
        if not self.enabled:
            return None

        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except HTTPError as exc:
            self._record_error(
                "http_error",
                self._http_error_message(exc),
                exc.code,
            )
            return None
        except (URLError, TimeoutError) as exc:
            self._record_error(
                "network_error",
                str(getattr(exc, "reason", None) or exc),
            )
            return None
        except json.JSONDecodeError:
            self._record_error("invalid_response", "云端返回内容不是有效 JSON")
            return None

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            self._record_error(
                "invalid_response",
                "云端响应格式不符合 Chat Completions 规范",
            )
            return None

        if not isinstance(message, dict) or (
            not message.get("content") and not message.get("tool_calls")
        ):
            self._record_error("invalid_response", "云端没有返回文本或工具调用")
            return None
        self.last_error = None
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        return message
