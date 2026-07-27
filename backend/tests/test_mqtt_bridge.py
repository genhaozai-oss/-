from smarthome import database
from smarthome.devices import set_device_state


class FakeBridge:
    def __init__(self):
        self.commands = []

    def publish_device_command(self, device_id, state):
        self.commands.append((device_id, state))
        return True


def test_physical_device_command_is_forwarded_to_mqtt(app):
    bridge = FakeBridge()
    app.extensions["mqtt_bridge"] = bridge
    with app.app_context():
        database.update_device("fan-1", is_virtual=False)
        set_device_state("fan-1", "on")

    assert bridge.commands == [("fan-1", "on")]


def test_mqtt_environment_updates_database_and_runs_rules(app):
    bridge = app.extensions["mqtt_bridge"]
    with app.app_context():
        bridge._handle_environment('{"temperature": 30, "humidity": 55}')
        environment = database.get_environment()
        fan = database.get_device("fan-1")

    assert environment["temperature"] == 30
    assert environment["humidity"] == 55
    assert fan["state"] == "on"


def test_controller_status_marks_fan_as_physical(app):
    bridge = app.extensions["mqtt_bridge"]
    with app.app_context():
        bridge._handle_controller_status("online")
        fan = database.get_device("fan-1")

    assert fan["is_virtual"] == 0
    assert fan["online"] == 1

