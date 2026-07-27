import json
import sqlite3
from datetime import datetime

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'off',
    online INTEGER NOT NULL DEFAULT 1,
    is_virtual INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environment (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    temperature REAL NOT NULL,
    humidity REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    triggered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


SEED_DEVICES = (
    ("fan-1", "客厅风扇", "fan", "客厅", 1),
    ("humidifier-1", "加湿器", "humidifier", "客厅", 1),
    ("dehumidifier-1", "抽湿器演示", "dehumidifier", "客厅", 1),
    ("light-1", "客厅灯", "light", "客厅", 1),
)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    timestamp = now_iso()
    db.executemany(
        """
        INSERT OR IGNORE INTO devices
            (id, name, type, room, state, online, is_virtual, updated_at)
        VALUES (?, ?, ?, ?, 'off', 1, ?, ?)
        """,
        [(*device, timestamp) for device in SEED_DEVICES],
    )
    db.execute(
        """
        INSERT OR IGNORE INTO environment
            (id, temperature, humidity, updated_at)
        VALUES (1, 27.0, 55.0, ?)
        """,
        (timestamp,),
    )
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def list_devices():
    rows = get_db().execute(
        "SELECT * FROM devices ORDER BY room, name"
    ).fetchall()
    return rows_to_dicts(rows)


def get_device(device_id):
    row = get_db().execute(
        "SELECT * FROM devices WHERE id = ?", (device_id,)
    ).fetchone()
    return dict(row) if row else None


def find_devices(query):
    query = query.strip().replace("一下", "")
    devices = list_devices()
    exact = [device for device in devices if device["name"] == query]
    if exact:
        return exact
    return [
        device
        for device in devices
        if query in device["name"]
        or device["name"] in query
        or device["type"] == query
    ]


def update_device(device_id, *, name=None, room=None, state=None):
    device = get_device(device_id)
    if not device:
        return None

    values = {
        "name": name if name is not None else device["name"],
        "room": room if room is not None else device["room"],
        "state": state if state is not None else device["state"],
    }
    db = get_db()
    db.execute(
        """
        UPDATE devices
        SET name = ?, room = ?, state = ?, updated_at = ?
        WHERE id = ?
        """,
        (values["name"], values["room"], values["state"], now_iso(), device_id),
    )
    db.commit()
    return get_device(device_id)


def get_environment():
    row = get_db().execute(
        "SELECT temperature, humidity, updated_at FROM environment WHERE id = 1"
    ).fetchone()
    return dict(row)


def update_environment(temperature, humidity):
    db = get_db()
    db.execute(
        """
        UPDATE environment
        SET temperature = ?, humidity = ?, updated_at = ?
        WHERE id = 1
        """,
        (temperature, humidity, now_iso()),
    )
    db.commit()
    return get_environment()


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_settings(values):
    db = get_db()
    db.executemany(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [(key, str(value)) for key, value in values.items()],
    )
    db.commit()
    return get_settings()


def create_alarm(label, scheduled_at):
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO alarms
            (label, scheduled_at, enabled, triggered, created_at)
        VALUES (?, ?, 1, 0, ?)
        """,
        (label, scheduled_at, now_iso()),
    )
    db.commit()
    return get_alarm(cursor.lastrowid)


def get_alarm(alarm_id):
    row = get_db().execute(
        "SELECT * FROM alarms WHERE id = ?", (alarm_id,)
    ).fetchone()
    return dict(row) if row else None


def list_alarms():
    rows = get_db().execute(
        """
        SELECT * FROM alarms
        WHERE enabled = 1 AND triggered = 0
        ORDER BY scheduled_at
        """
    ).fetchall()
    return rows_to_dicts(rows)


def delete_alarm(alarm_id):
    db = get_db()
    cursor = db.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
    db.commit()
    return cursor.rowcount > 0


def due_alarms():
    timestamp = now_iso()
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM alarms
        WHERE enabled = 1 AND triggered = 0 AND scheduled_at <= ?
        ORDER BY scheduled_at
        """,
        (timestamp,),
    ).fetchall()
    if rows:
        db.executemany(
            "UPDATE alarms SET triggered = 1 WHERE id = ?",
            [(row["id"],) for row in rows],
        )
        db.commit()
    return rows_to_dicts(rows)


def log_event(kind, message, payload=None):
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO events (kind, message, payload, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            kind,
            message,
            json.dumps(payload or {}, ensure_ascii=False),
            now_iso(),
        ),
    )
    db.commit()
    return cursor.lastrowid


def list_events(limit=20):
    rows = get_db().execute(
        """
        SELECT * FROM events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    events = rows_to_dicts(rows)
    for event in events:
        event["payload"] = json.loads(event["payload"])
    return events
