import os
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv

from . import database
from .agent import SmartHomeAgent
from .llm import LlmInterpreter
from .mqtt_bridge import MqttBridge
from .routes import api
from .tts import SpeechSynthesizer
from .voice import SpeechRecognizer
from .weather import clear_weather_cache


def dashscope_api_base(compatible_base_url):
    base_url = str(compatible_base_url or "").rstrip("/")
    if "dashscope.aliyuncs.com" not in base_url:
        return ""
    return base_url.replace("/compatible-mode/v1", "/api/v1")


def create_app(test_config=None):
    backend_dir = Path(__file__).resolve().parents[1]
    load_dotenv(backend_dir / ".env", override=False)

    app = Flask(__name__, static_folder="static", static_url_path="")
    default_db = Path(app.instance_path) / "smarthome.db"
    app.config.from_mapping(
        DATABASE=str(default_db),
        WEATHER_TIMEOUT_SECONDS=5,
        WEATHER_CACHE_SECONDS=600,
        WEATHER_API_HOST=os.getenv("SMARTHOME_WEATHER_API_HOST", ""),
        WEATHER_API_KEY=os.getenv("SMARTHOME_WEATHER_API_KEY", ""),
        MQTT_ENABLED=os.getenv("SMARTHOME_MQTT_ENABLED") == "1",
        MQTT_BROKER_URI=os.getenv("SMARTHOME_MQTT_BROKER", "mqtt://127.0.0.1:1883"),
        MQTT_USERNAME=os.getenv("SMARTHOME_MQTT_USERNAME", ""),
        MQTT_PASSWORD=os.getenv("SMARTHOME_MQTT_PASSWORD", ""),
        LLM_BASE_URL=os.getenv("SMARTHOME_LLM_BASE_URL", ""),
        LLM_API_KEY=os.getenv("SMARTHOME_LLM_API_KEY", ""),
        LLM_MODEL=os.getenv("SMARTHOME_LLM_MODEL", ""),
        SPEECH_MODEL=os.getenv("SMARTHOME_SPEECH_MODEL", "small"),
        SPEECH_DEVICE=os.getenv("SMARTHOME_SPEECH_DEVICE", "cpu"),
        SPEECH_COMPUTE_TYPE=os.getenv("SMARTHOME_SPEECH_COMPUTE_TYPE", "int8"),
        SPEECH_CLOUD_BASE_URL=os.getenv(
            "SMARTHOME_SPEECH_BASE_URL",
            os.getenv("SMARTHOME_LLM_BASE_URL", ""),
        ),
        SPEECH_CLOUD_API_KEY=os.getenv(
            "SMARTHOME_SPEECH_API_KEY",
            os.getenv("SMARTHOME_LLM_API_KEY", ""),
        ),
        SPEECH_CLOUD_MODEL=os.getenv(
            "SMARTHOME_SPEECH_MODEL_CLOUD",
            "qwen3-asr-flash",
        ),
        SPEECH_CLOUD_TIMEOUT_SECONDS=8,
        TTS_BASE_URL=os.getenv(
            "SMARTHOME_TTS_BASE_URL",
            dashscope_api_base(os.getenv("SMARTHOME_LLM_BASE_URL", "")),
        ),
        TTS_API_KEY=os.getenv(
            "SMARTHOME_TTS_API_KEY",
            os.getenv("SMARTHOME_LLM_API_KEY", ""),
        ),
        TTS_MODEL=os.getenv("SMARTHOME_TTS_MODEL", "qwen3-tts-flash"),
        TTS_VOICE=os.getenv("SMARTHOME_TTS_VOICE", "Cherry"),
        TTS_TIMEOUT_SECONDS=12,
        MAX_CONTENT_LENGTH=7 * 1024 * 1024,
        TESTING=False,
    )

    if test_config:
        app.config.update(test_config)
    clear_weather_cache()

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database.init_app(app)
    app.register_blueprint(api)
    mqtt_bridge = MqttBridge(app)
    app.extensions["mqtt_bridge"] = mqtt_bridge
    llm_interpreter = LlmInterpreter(app)
    app.extensions["llm_interpreter"] = llm_interpreter
    app.extensions["assistant_agent"] = SmartHomeAgent(llm_interpreter)
    app.extensions["speech_recognizer"] = SpeechRecognizer(app)
    app.extensions["speech_synthesizer"] = SpeechSynthesizer(app)

    with app.app_context():
        database.init_db()

    if app.config["MQTT_ENABLED"] and not app.config["TESTING"]:
        mqtt_bridge.start()

    return app
