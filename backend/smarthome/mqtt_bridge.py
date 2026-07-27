import json
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

from . import database
from .home import run_comfort_rules


ENVIRONMENT_TOPIC = "smarthome/sensor/environment"
DEVICE_STATE_PREFIX = "smarthome/device/"
CONTROLLER_STATUS_TOPIC = "smarthome/controller/esp32-s3/status"


class MqttBridge:
    def __init__(self, app):
        self.app = app
        self.connected = False
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="smarthome-edge",
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        username = app.config["MQTT_USERNAME"]
        if username:
            self.client.username_pw_set(username, app.config["MQTT_PASSWORD"])

    def start(self):
        broker = urlparse(self.app.config["MQTT_BROKER_URI"])
        if broker.scheme not in {"mqtt", "mqtts"} or not broker.hostname:
            raise ValueError("MQTT 地址应为 mqtt://主机:端口 或 mqtts://主机:端口")
        if broker.scheme == "mqtts":
            self.client.tls_set()
        self.client.connect_async(
            broker.hostname,
            broker.port or (8883 if broker.scheme == "mqtts" else 1883),
            keepalive=60,
        )
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def publish_device_command(self, device_id, state):
        if not self.connected:
            return False
        info = self.client.publish(
            f"smarthome/device/{device_id}/set",
            payload=state,
            qos=1,
            retain=False,
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_device_capability(self, device_id, capability, value):
        if not self.connected:
            return False
        info = self.client.publish(
            f"smarthome/device/{device_id}/capability/{capability}/set",
            payload=str(value),
            qos=1,
            retain=False,
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            return
        self.connected = True
        client.subscribe(
            [
                (ENVIRONMENT_TOPIC, 1),
                ("smarthome/device/+/state", 1),
                ("smarthome/device/+/capability/+/state", 1),
                (CONTROLLER_STATUS_TOPIC, 1),
            ]
        )

    def _on_disconnect(
        self,
        _client,
        _userdata,
        _disconnect_flags,
        _reason_code,
        _properties,
    ):
        self.connected = False

    def _on_message(self, _client, _userdata, message):
        try:
            payload = message.payload.decode("utf-8")
            with self.app.app_context():
                if message.topic == ENVIRONMENT_TOPIC:
                    self._handle_environment(payload)
                elif message.topic == CONTROLLER_STATUS_TOPIC:
                    self._handle_controller_status(payload)
                elif message.topic.startswith(DEVICE_STATE_PREFIX):
                    self._handle_device_state(message.topic, payload)
        except (UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            self.app.logger.warning("忽略格式错误的 MQTT 消息：%s", message.topic)

    def _handle_environment(self, payload):
        data = json.loads(payload)
        temperature = float(data["temperature"])
        humidity = float(data["humidity"])
        if not -20 <= temperature <= 80 or not 0 <= humidity <= 100:
            raise ValueError("温湿度超出合理范围")
        database.update_environment(temperature, humidity)
        _, actions = run_comfort_rules()
        database.log_event(
            "sensor",
            f"实体传感器：{temperature:.1f}℃，{humidity:.0f}%",
            {"actions": actions},
        )

    def _handle_controller_status(self, payload):
        is_online = payload.strip().lower() == "online"
        database.update_device(
            "fan-1",
            online=is_online,
            is_virtual=False,
        )
        database.log_event(
            "controller",
            "ESP32-S3 已连接" if is_online else "ESP32-S3 已离线",
        )

    def _handle_device_state(self, topic, payload):
        parts = topic.split("/")
        if (
            len(parts) == 6
            and parts[3] == "capability"
            and parts[5] == "state"
        ):
            capability = database.update_device_capability(
                parts[2],
                parts[4],
                float(payload),
            )
            if not capability:
                raise ValueError("设备能力尚未注册")
            return
        if len(parts) != 4 or parts[3] != "state":
            return
        state = payload.strip().lower()
        if state not in {"on", "off"}:
            raise ValueError("设备状态无效")
        database.update_device(
            parts[2],
            state=state,
            online=True,
            is_virtual=False,
        )
