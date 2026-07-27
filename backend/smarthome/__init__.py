import os
from pathlib import Path

from flask import Flask

from . import database
from .mqtt_bridge import MqttBridge
from .routes import api


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
        TESTING=False,
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database.init_app(app)
    app.register_blueprint(api)
    mqtt_bridge = MqttBridge(app)
    app.extensions["mqtt_bridge"] = mqtt_bridge

    with app.app_context():
        database.init_db()

    if app.config["MQTT_ENABLED"] and not app.config["TESTING"]:
        mqtt_bridge.start()

    return app
