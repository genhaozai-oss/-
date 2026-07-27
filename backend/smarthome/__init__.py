from pathlib import Path

from flask import Flask

from . import database
from .routes import api


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    default_db = Path(app.instance_path) / "smarthome.db"
    app.config.from_mapping(
        DATABASE=str(default_db),
        WEATHER_TIMEOUT_SECONDS=5,
        TESTING=False,
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database.init_app(app)
    app.register_blueprint(api)

    with app.app_context():
        database.init_db()

    return app

