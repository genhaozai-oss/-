import base64
import binascii
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


class SpeechSynthesisError(RuntimeError):
    pass


TTS_VOICES = {
    "Serena": "温柔自然女声",
    "Chelsie": "柔和可爱女声",
    "Ethan": "清爽温暖男声",
    "Cherry": "活泼女声",
}

DOUBAO_TTS_VOICES = {
    "zh_female_vv_uranus_bigtts": "Vivi 2.0 · 自然女声",
    "zh_female_meilinvyou_saturn_bigtts": "魅力女友 · 活泼女声",
    "ICL_zh_female_keainvsheng_tob": "可爱女生 · 角色音色",
    "ICL_zh_female_tiaopigongzhu_tob": "调皮公主 · 角色音色",
}

DOUBAO_TTS_URL = (
    "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
)

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\uFE0F\u200D\u20E3"
    "]+"
)


class SpeechSynthesizer:
    def __init__(self, app):
        self.base_url = app.config["TTS_BASE_URL"].rstrip("/")
        self.api_key = app.config["TTS_API_KEY"]
        self.model = app.config["TTS_MODEL"]
        self.voice = app.config["TTS_VOICE"]
        self.timeout = app.config["TTS_TIMEOUT_SECONDS"]
        self.doubao_api_key = app.config["DOUBAO_TTS_API_KEY"]
        self.doubao_resource_id = app.config["DOUBAO_TTS_RESOURCE_ID"]
        self.doubao_voice = app.config["DOUBAO_TTS_VOICE"]
        self.last_error = None

    @property
    def available(self):
        return self.doubao_available or self.aliyun_available

    @property
    def doubao_available(self):
        return bool(self.doubao_api_key and self.doubao_resource_id)

    @property
    def aliyun_available(self):
        return bool(self.base_url and self.api_key and self.model)

    def status(self):
        use_doubao = self.doubao_available
        voices = DOUBAO_TTS_VOICES if use_doubao else TTS_VOICES
        return {
            "available": self.available,
            "provider": (
                "doubao"
                if use_doubao
                else "aliyun" if self.aliyun_available else "unavailable"
            ),
            "provider_label": (
                "豆包 TTS 2.0"
                if use_doubao
                else "百炼 Qwen TTS"
                if self.aliyun_available
                else "未配置"
            ),
            "model": (
                self.doubao_resource_id
                if use_doubao
                else self.model if self.aliyun_available else None
            ),
            "voice": (
                self.doubao_voice
                if use_doubao
                else self.voice if self.aliyun_available else None
            ),
            "voices": [
                {"id": voice_id, "label": label}
                for voice_id, label in voices.items()
            ],
            "fallback_available": use_doubao and self.aliyun_available,
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

    @staticmethod
    def clean_text(text):
        text = re.sub(r"https?://\S+", "链接", str(text or ""))
        text = EMOJI_PATTERN.sub("", text)
        text = re.sub(r"[*_`#>]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def synthesize(self, text, voice=None):
        text = self.clean_text(text)
        if not text:
            raise SpeechSynthesisError("播报文字不能为空。")
        if len(text) > 400:
            raise SpeechSynthesisError("单次播报不能超过 400 个字符。")
        if not self.available:
            raise SpeechSynthesisError("云端语音播报尚未配置。")
        if self.doubao_available:
            selected_voice = str(voice or self.doubao_voice).strip()
            if selected_voice not in DOUBAO_TTS_VOICES:
                selected_voice = self.doubao_voice
            try:
                return self._synthesize_doubao(text, selected_voice)
            except SpeechSynthesisError as exc:
                self.last_error = str(exc)
                if not self.aliyun_available:
                    raise
                result = self._synthesize_aliyun(text, self.voice)
                result["fallback_from"] = "doubao"
                return result

        return self._synthesize_aliyun(text, voice)

    def _synthesize_aliyun(self, text, voice=None):
        selected_voice = str(voice or self.voice).strip()
        if selected_voice not in TTS_VOICES:
            raise SpeechSynthesisError("不支持这个语音音色。")

        payload = {
            "model": self.model,
            "input": {
                "text": text,
                "voice": selected_voice,
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
            "voice": selected_voice,
        }

    def _synthesize_doubao(self, text, voice):
        additions = json.dumps(
            {
                "disable_markdown_filter": False,
                "disable_emoji_filter": False,
                "enable_latex_tn": True,
                "context_texts": ["请用温暖、活泼、自然的语气播报"],
            },
            ensure_ascii=False,
        )
        payload = {
            "req_params": {
                "text": text,
                "speaker": voice,
                "additions": additions,
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": 24000,
                    "enable_subtitle": False,
                },
            }
        }
        request = Request(
            DOUBAO_TTS_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "X-Api-Key": self.doubao_api_key,
                "X-Api-Resource-Id": self.doubao_resource_id,
                "Content-Type": "application/json",
                "Connection": "keep-alive",
            },
            method="POST",
        )
        audio_chunks = []
        try:
            with urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    event = json.loads(raw_line.decode("utf-8"))
                    code = int(event.get("code", 0))
                    if code == 0 and event.get("data"):
                        audio_chunks.append(
                            base64.b64decode(event["data"], validate=True)
                        )
                    elif code == 20000000:
                        break
                    elif code > 0:
                        raise SpeechSynthesisError(
                            str(event.get("message") or f"豆包错误码 {code}")
                        )
        except HTTPError as exc:
            message = self._http_error_message(exc)
            raise SpeechSynthesisError(
                f"豆包语音请求失败（HTTP {exc.code}）：{message}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise SpeechSynthesisError(f"豆包语音连接失败：{reason}") from exc
        except (json.JSONDecodeError, binascii.Error, ValueError) as exc:
            raise SpeechSynthesisError("豆包语音返回了无效数据。") from exc

        if not audio_chunks:
            raise SpeechSynthesisError("豆包语音没有返回音频。")

        self.last_error = None
        audio = base64.b64encode(b"".join(audio_chunks)).decode("ascii")
        return {
            "audio_url": f"data:audio/mpeg;base64,{audio}",
            "provider": "doubao",
            "model": self.doubao_resource_id,
            "voice": voice,
        }
