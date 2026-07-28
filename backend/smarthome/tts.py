import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


class SpeechSynthesisError(RuntimeError):
    pass


class SpeechSynthesizer:
    def __init__(self, app):
        self.base_url = app.config["TTS_BASE_URL"].rstrip("/")
        self.api_key = app.config["TTS_API_KEY"]
        self.model = app.config["TTS_MODEL"]
        self.voice = app.config["TTS_VOICE"]
        self.timeout = app.config["TTS_TIMEOUT_SECONDS"]
        self.last_error = None

    @property
    def available(self):
        return bool(self.base_url and self.api_key and self.model)

    def status(self):
        return {
            "available": self.available,
            "provider": "aliyun" if self.available else "unavailable",
            "model": self.model if self.available else None,
            "voice": self.voice if self.available else None,
            "last_error": self.last_error,
        }

    @staticmethod
    def _http_error_message(exc):
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return str(exc.reason or "云端语音合成服务返回 HTTP 错误")
        if isinstance(payload, dict):
            return str(
                payload.get("message")
                or payload.get("code")
                or payload.get("error")
                or payload
            )
        return str(payload)

    @staticmethod
    def _allowed_audio_hostname(hostname):
        return (
            hostname == "aliyuncs.com"
            or hostname.endswith(".aliyuncs.com")
            or hostname.endswith(".aliyun.com")
        )

    @classmethod
    def _normalize_audio_url(cls, value):
        parsed = urlparse(str(value or ""))
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and cls._allowed_audio_hostname(hostname):
            parsed = parsed._replace(scheme="https")
        return urlunparse(parsed)

    @classmethod
    def _valid_audio_url(cls, value):
        parsed = urlparse(str(value or ""))
        hostname = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and bool(hostname)
            and cls._allowed_audio_hostname(hostname)
        )

    def synthesize(self, text):
        text = str(text or "").strip()
        if not text:
            raise SpeechSynthesisError("播报文字不能为空。")
        if len(text) > 400:
            raise SpeechSynthesisError("单次播报不能超过 400 个字符。")
        if not self.available:
            raise SpeechSynthesisError("云端语音播报尚未配置。")

        payload = {
            "model": self.model,
            "input": {
                "text": text,
                "voice": self.voice,
                "language_type": "Chinese",
            },
        }
        request = Request(
            f"{self.base_url}/services/aigc/multimodal-generation/generation",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except HTTPError as exc:
            message = self._http_error_message(exc)
            self.last_error = f"HTTP {exc.code}: {message}"
            raise SpeechSynthesisError(
                f"云端语音播报请求失败（HTTP {exc.code}）：{message}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None) or exc
            self.last_error = str(reason)
            raise SpeechSynthesisError(f"云端语音播报连接失败：{reason}") from exc
        except json.JSONDecodeError as exc:
            self.last_error = "响应不是有效 JSON"
            raise SpeechSynthesisError("云端语音播报返回了无效数据。") from exc

        try:
            audio_url = self._normalize_audio_url(
                body["output"]["audio"]["url"]
            )
        except (KeyError, TypeError) as exc:
            self.last_error = str(body.get("message") or "响应缺少音频地址")
            raise SpeechSynthesisError("云端语音播报响应格式不正确。") from exc
        if not self._valid_audio_url(audio_url):
            self.last_error = "响应包含不可信的音频地址"
            raise SpeechSynthesisError("云端语音播报返回了不可信的音频地址。")

        self.last_error = None
        return {
            "audio_url": audio_url,
            "provider": "aliyun",
            "model": self.model,
        }
