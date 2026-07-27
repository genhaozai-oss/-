import importlib.util
import threading


class SpeechRecognizer:
    def __init__(self, app):
        self.model_name = app.config["SPEECH_MODEL"]
        self.device = app.config["SPEECH_DEVICE"]
        self.compute_type = app.config["SPEECH_COMPUTE_TYPE"]
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self):
        return importlib.util.find_spec("faster_whisper") is not None

    def _get_model(self):
        if self._model is not None:
            return self._model
        if not self.available:
            raise RuntimeError("尚未安装本地语音识别依赖")

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

    def transcribe(self, audio_path):
        model = self._get_model()
        segments, info = model.transcribe(
            str(audio_path),
            language="zh",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            hotwords=(
                "客厅风扇 加湿器 抽湿器 客厅灯 打开 关闭 "
                "准备回家 温度 湿度 天气 闹钟"
            ),
        )
        text = "".join(segment.text for segment in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
        }

