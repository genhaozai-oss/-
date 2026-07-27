import json
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

    @property
    def enabled(self):
        return bool(self.base_url and self.model)

    def classify(self, message, devices):
        if not self.enabled:
            return None

        device_names = [device["name"] for device in devices]
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "devices": device_names},
                        ensure_ascii=False,
                    ),
                },
            ],
        }
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
            content = body["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(result, dict):
            return None
        return result

