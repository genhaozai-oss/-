import base64
import importlib.util
import json
import threading
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AUDIO_MIME_TYPES = {
    ".aac": "audio/aac",
    ".aiff": "audio/aiff",
    ".amr": "audio/amr",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

DEFAULT_CONTEXT_TERMS = (
    "打开",
    "关闭",
    "风速",
    "亮度",
    "准备回家",
    "温度",
    "湿度",
    "天气",
    "闹钟",
    "加湿",
    "抽湿",
)


class SpeechRecognitionError(RuntimeError):
    pass


class SpeechRecognizer:
    def __init__(self, app):
        self.model_name = app.config["SPEECH_MODEL"]
        self.device = app.config["SPEECH_DEVICE"]
        self.compute_type = app.config["SPEECH_COMPUTE_TYPE"]
        self.cloud_base_url = app.config["SPEECH_CLOUD_BASE_URL"].rstrip("/")
        self.cloud_api_key = app.config["SPEECH_CLOUD_API_KEY"]
        self.cloud_model = app.config["SPEECH_CLOUD_MODEL"]
        self.cloud_timeout = app.config["SPEECH_CLOUD_TIMEOUT_SECONDS"]
        self.logger = app.logger
        self._model = None
        self._lock = threading.Lock()
        self.last_cloud_error = None

    @property
    def cloud_available(self):
        return bool(
            self.cloud_base_url
            and self.cloud_api_key
            and self.cloud_model
        )

    @property
    def local_available(self):
        return importlib.util.find_spec("faster_whisper") is not None

    @property
    def available(self):
        return self.cloud_available or self.local_available

    def status(self):
        if self.cloud_available:
            provider = "aliyun"
            model = self.cloud_model
        elif self.local_available:
            provider = "local"
            model = self.model_name
        else:
            provider = "unavailable"
            model = None
        return {
            "available": self.available,
            "provider": provider,
            "model": model,
            "cloud_configured": self.cloud_available,
            "local_available": self.local_available,
            "last_cloud_error": self.last_cloud_error,
        }

    def _get_model(self):
        if self._model is not None:
            return self._model
        if not self.local_available:
            raise SpeechRecognitionError("尚未安装本地语音识别依赖")

        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=8,
                )
        return self._model

    @staticmethod
    def _mime_type(audio_path, supplied_mime_type):
        mime_type = str(supplied_mime_type or "").split(";", 1)[0].strip()
        if mime_type.startswith(("audio/", "video/")):
            return mime_type
        return AUDIO_MIME_TYPES.get(
            Path(audio_path).suffix.lower(),
            "audio/webm",
        )

    @staticmethod
    def _context_text(context_terms):
        terms = []
        for term in (*DEFAULT_CONTEXT_TERMS, *(context_terms or ())):
            value = str(term or "").strip()
            if value and value not in terms:
                terms.append(value)
        return (
            "家庭智能管理场景中的中文口语录音。"
            f"可能出现的设备、房间和表达有：{'、'.join(terms)}。"
        )

    @staticmethod
    def _http_error_message(exc):
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return str(exc.reason or "云端语音服务返回 HTTP 错误")
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        return str(error)

    def _transcribe_cloud(self, audio_path, mime_type, context_terms):
        audio_bytes = Path(audio_path).read_bytes()
        if not audio_bytes:
            raise SpeechRecognitionError("录音文件为空")

        data_uri = (
            f"data:{self._mime_type(audio_path, mime_type)};base64,"
            f"{base64.b64encode(audio_bytes).decode('ascii')}"
        )
        payload = {
            "model": self.cloud_model,
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": self._context_text(context_terms),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                },
            ],
            "stream": False,
            "asr_options": {
                "language": "zh",
                "enable_itn": True,
            },
        }
        request = Request(
            f"{self.cloud_base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.cloud_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.cloud_timeout) as response:
                body = json.load(response)
        except HTTPError as exc:
            raise SpeechRecognitionError(
                f"云端语音服务请求失败（HTTP {exc.code}）："
                f"{self._http_error_message(exc)}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise SpeechRecognitionError(f"云端语音服务连接失败：{reason}") from exc
        except json.JSONDecodeError as exc:
            raise SpeechRecognitionError("云端语音服务返回了无效数据") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SpeechRecognitionError("云端语音服务响应格式不正确") from exc

        text = message.get("content", "")
        if isinstance(text, list):
            text = "".join(
                str(item.get("text", ""))
                for item in text
                if isinstance(item, dict)
            )
        annotations = message.get("annotations") or []
        audio_info = next(
            (
                item
                for item in annotations
                if isinstance(item, dict) and item.get("type") == "audio_info"
            ),
            {},
        )
        return {
            "text": str(text).strip(),
            "language": audio_info.get("language", "zh"),
            "emotion": audio_info.get("emotion"),
            "audio_seconds": (body.get("usage") or {}).get("seconds"),
            "provider": "aliyun",
            "provider_label": "阿里云 Qwen3-ASR",
            "model": self.cloud_model,
        }

    def _transcribe_local(self, audio_path, context_terms):
        model = self._get_model()
        hotwords = " ".join(
            dict.fromkeys((*DEFAULT_CONTEXT_TERMS, *(context_terms or ())))
        )
        segments, info = model.transcribe(
            str(audio_path),
            language="zh",
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            hotwords=hotwords,
        )
        text = "".join(segment.text for segment in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "provider": "local",
            "provider_label": "本地 Whisper",
            "model": self.model_name,
        }

    def transcribe(self, audio_path, mime_type=None, context_terms=None):
        started_at = perf_counter()
        if self.cloud_available:
            try:
                result = self._transcribe_cloud(
                    audio_path,
                    mime_type,
                    context_terms,
                )
                self.last_cloud_error = None
            except SpeechRecognitionError as exc:
                self.last_cloud_error = str(exc)[:500]
                self.logger.warning("云端语音识别失败：%s", exc)
                if not self.local_available:
                    raise
                result = self._transcribe_local(audio_path, context_terms)
                result["fallback_from"] = "aliyun"
        elif self.local_available:
            result = self._transcribe_local(audio_path, context_terms)
        else:
            raise SpeechRecognitionError("云端语音未配置，且本地模型未安装")

        result["latency_ms"] = round((perf_counter() - started_at) * 1000)
        return result
