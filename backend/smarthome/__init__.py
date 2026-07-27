import os
from pathlib import Path

from flask import Flask

from . import database
from .agent import SmartHomeAgent
from .llm import LlmInterpreter
from .mqtt_bridge import MqttBridge
from .routes import api
from .voice import SpeechRecognizer


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    default_db = Path(app.instance_path) / "smarthome.db"
    app.config.from_mapping(
        DATABASE=str(default_db),
        WEATHER_TIMEOUT_SECONDS=5,
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
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        TESTING=False,
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database.init_app(app)
    app.register_blueprint(api)
    mqtt_bridge = MqttBridge(app)
    app.extensions["mqtt_bridge"] = mqtt_bridge
    llm_interpreter = LlmInterpreter(app)
    app.extensions["llm_interpreter"] = llm_interpreter
    app.extensions["assistant_agent"] = SmartHomeAgent(llm_interpreter)
    app.extensions["speech_recognizer"] = SpeechRecognizer(app)

    with app.app_context():
        database.init_db()

    if app.config["MQTT_ENABLED"] and not app.config["TESTING"]:
        mqtt_bridge.start()

    return app
