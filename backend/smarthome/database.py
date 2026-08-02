import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g


MAX_EVENT_ROWS = 2000
MAX_NOTIFICATION_ROWS = 200


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

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_session
ON conversation_messages(session_id, id);

CREATE TABLE IF NOT EXISTS assistant_context (
    session_id TEXT PRIMARY KEY,
    last_device_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_capabilities (
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    display_name TEXT NOT NULL,
    value REAL NOT NULL,
    minimum REAL NOT NULL,
    maximum REAL NOT NULL,
    step REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    learned INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_id, capability)
);

CREATE TABLE IF NOT EXISTS automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold REAL NOT NULL,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    capability TEXT NOT NULL DEFAULT '',
    value REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_triggered_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    actions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_manual_overrides (
    device_id TEXT PRIMARY KEY,
    until_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    claim_token TEXT,
    delivered_at TEXT,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS runtime_leases (
    name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


SEED_DEVICES = (
    ("fan-1", "客厅风扇", "fan", "客厅", 1),
    ("humidifier-1", "加湿器", "humidifier", "客厅", 1),
    ("dehumidifier-1", "抽湿器演示", "dehumidifier", "客厅", 1),
    ("light-1", "客厅灯", "light", "客厅", 1),
)

CAPABILITY_DEFINITIONS = {
    "speed": {
        "display_name": "风速",
        "minimum": 0,
        "maximum": 100,
        "step": 10,
        "unit": "%",
        "default": 50,
    },
    "brightness": {
        "display_name": "亮度",
        "minimum": 0,
        "maximum": 100,
        "step": 10,
        "unit": "%",
        "default": 100,
    },
    "position": {
        "display_name": "位置",
        "minimum": 0,
        "maximum": 100,
        "step": 10,
        "unit": "%",
        "default": 0,
    },
    "target_temperature": {
        "display_name": "目标温度",
        "minimum": 16,
        "maximum": 30,
        "step": 1,
        "unit": "℃",
        "default": 26,
    },
}

SEED_CAPABILITIES = (
    ("fan-1", "speed"),
    ("light-1", "brightness"),
)

KNOWN_ROOMS = (
    "客厅",
    "卧室",
    "主卧",
    "次卧",
    "书房",
    "厨房",
    "厕所",
    "卫生间",
    "浴室",
    "阳台",
    "餐厅",
    "玄关",
)
DEVICE_NAME_SUFFIXES = ("灯", "风扇", "加湿器", "抽湿器", "除湿器", "窗帘", "空调")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _absolute_time(value):
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    return timestamp.astimezone(timezone.utc)


def infer_room_from_name(name):
    normalized = str(name or "").strip()
    for room in sorted(KNOWN_ROOMS, key=len, reverse=True):
        if normalized.startswith(room) and any(
            normalized.endswith(suffix) for suffix in DEVICE_NAME_SUFFIXES
        ):
            return room
    return None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate_preference_sources(db):
    migrated = db.execute(
        """
        SELECT value FROM settings
        WHERE key = 'preference_source_migration_v1'
        """
    ).fetchone()
    if migrated:
        return

    preferences = db.execute(
        """
        SELECT key, value FROM settings
        WHERE key LIKE 'preference.%'
        """
    ).fetchall()
    events = db.execute(
        """
        SELECT kind, payload FROM events
        WHERE kind IN ('learning', 'memory')
        ORDER BY id DESC
        """
    ).fetchall()
    latest_evidence = {}
    for event in events:
        try:
            payload = json.loads(event["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        memory = payload.get("memory")
        if isinstance(memory, dict):
            name = str(memory.get("name", "")).strip()
        else:
            name = str(payload.get("preference", "")).strip()
        if name and name not in latest_evidence:
            latest_evidence[name] = (event["kind"], memory)

    for row in preferences:
        name = row["key"].removeprefix("preference.")
        existing = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (f"preference_meta.{name}",),
        ).fetchone()
        if existing:
            continue
        source = "explicit"
        evidence = latest_evidence.get(name)
        if evidence:
            kind, memory = evidence
            if isinstance(memory, dict):
                try:
                    values_match = float(memory.get("value")) == float(
                        row["value"]
                    )
                except (TypeError, ValueError):
                    values_match = (
                        str(memory.get("value", "")) == row["value"]
                    )
                if kind == "learning" and values_match:
                    source = "automatic"
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (f"preference_meta.{name}", source),
        )

    db.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('preference_source_migration_v1', 'done')
        """
    )


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    notification_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(notifications)").fetchall()
    }
    for column_name in ("claimed_at", "claim_token"):
        if column_name not in notification_columns:
            db.execute(
                f"ALTER TABLE notifications ADD COLUMN {column_name} TEXT"
            )
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
    for device_id, capability in SEED_CAPABILITIES:
        definition = CAPABILITY_DEFINITIONS[capability]
        db.execute(
            """
            INSERT OR IGNORE INTO device_capabilities
                (device_id, capability, display_name, value, minimum, maximum,
                 step, unit, learned, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                device_id,
                capability,
                definition["display_name"],
                definition["default"],
                definition["minimum"],
                definition["maximum"],
                definition["step"],
                definition["unit"],
                timestamp,
            ),
        )
    migrated = db.execute(
        "SELECT value FROM settings WHERE key = 'device_room_name_sync_v1'"
    ).fetchone()
    if not migrated:
        devices = db.execute("SELECT id, name, room FROM devices").fetchall()
        for device in devices:
            inferred_room = infer_room_from_name(device["name"])
            if inferred_room and inferred_room != device["room"]:
                db.execute(
                    "UPDATE devices SET room = ?, updated_at = ? WHERE id = ?",
                    (inferred_room, timestamp, device["id"]),
                )
        db.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('device_room_name_sync_v1', 'done')
            """
        )
    _migrate_preference_sources(db)
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def list_devices():
    rows = get_db().execute(
        "SELECT * FROM devices ORDER BY room, name"
    ).fetchall()
    devices = rows_to_dicts(rows)
    capabilities = list_device_capabilities()
    by_device = {}
    for capability in capabilities:
        by_device.setdefault(capability["device_id"], []).append(capability)
    for device in devices:
        device["capabilities"] = by_device.get(device["id"], [])
    return devices


def get_device(device_id):
    row = get_db().execute(
        "SELECT * FROM devices WHERE id = ?", (device_id,)
    ).fetchone()
    if not row:
        return None
    device = dict(row)
    device["capabilities"] = list_device_capabilities(device_id)
    return device


def list_device_capabilities(device_id=None):
    if device_id is None:
        rows = get_db().execute(
            """
            SELECT * FROM device_capabilities
            ORDER BY device_id, capability
            """
        ).fetchall()
    else:
        rows = get_db().execute(
            """
            SELECT * FROM device_capabilities
            WHERE device_id = ?
            ORDER BY capability
            """,
            (device_id,),
        ).fetchall()
    return rows_to_dicts(rows)


def get_device_capability(device_id, capability):
    row = get_db().execute(
        """
        SELECT * FROM device_capabilities
        WHERE device_id = ? AND capability = ?
        """,
        (device_id, capability),
    ).fetchone()
    return dict(row) if row else None


def register_device_capability(device_id, capability, learned=True):
    device = get_device(device_id)
    definition = CAPABILITY_DEFINITIONS.get(capability)
    if not device or not definition:
        return None
    db = get_db()
    db.execute(
        """
        INSERT INTO device_capabilities
            (device_id, capability, display_name, value, minimum, maximum,
             step, unit, learned, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, capability) DO UPDATE SET
            learned = MAX(device_capabilities.learned, excluded.learned),
            updated_at = excluded.updated_at
        """,
        (
            device_id,
            capability,
            definition["display_name"],
            definition["default"],
            definition["minimum"],
            definition["maximum"],
            definition["step"],
            definition["unit"],
            int(learned),
            now_iso(),
        ),
    )
    db.commit()
    return get_device_capability(device_id, capability)


def update_device_capability(device_id, capability, value):
    registered = get_device_capability(device_id, capability)
    if not registered:
        return None
    numeric_value = float(value)
    if not registered["minimum"] <= numeric_value <= registered["maximum"]:
        raise ValueError(
            f"{registered['display_name']}应在 "
            f"{registered['minimum']:g}～{registered['maximum']:g}"
            f"{registered['unit']} 之间。"
        )
    stepped_value = round(
        (numeric_value - registered["minimum"]) / registered["step"]
    ) * registered["step"] + registered["minimum"]
    db = get_db()
    db.execute(
        """
        UPDATE device_capabilities
        SET value = ?, updated_at = ?
        WHERE device_id = ? AND capability = ?
        """,
        (stepped_value, now_iso(), device_id, capability),
    )
    db.commit()
    return get_device_capability(device_id, capability)


def get_user_preferences():
    rows = get_db().execute(
        """
        SELECT key, value FROM settings
        WHERE key LIKE 'preference.%'
        ORDER BY key
        """
    ).fetchall()
    return {
        row["key"].removeprefix("preference."): row["value"]
        for row in rows
    }


def get_user_preference_source(name):
    source = get_settings().get(f"preference_meta.{name}")
    return source if source in {"explicit", "automatic"} else "explicit"


def set_user_preference(name, value, source="explicit"):
    if source not in {"explicit", "automatic"}:
        raise ValueError("偏好来源无效。")
    set_settings(
        {
            f"preference.{name}": value,
            f"preference_meta.{name}": source,
        }
    )
    return get_user_preferences()


def set_automatic_user_preference(name, value, expected_value):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        current_row = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (f"preference.{name}",),
        ).fetchone()
        source_row = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (f"preference_meta.{name}",),
        ).fetchone()
        current_value = current_row["value"] if current_row else None
        current_source = (
            source_row["value"]
            if source_row and source_row["value"] in {"explicit", "automatic"}
            else "explicit"
        )
        if (
            current_value != expected_value
            or (current_value is not None and current_source != "automatic")
        ):
            db.rollback()
            return False
        db.executemany(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [
                (f"preference.{name}", str(value)),
                (f"preference_meta.{name}", "automatic"),
            ],
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def delete_user_preference(name):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM settings WHERE key = ?",
        (f"preference.{name}",),
    )
    db.execute(
        "DELETE FROM settings WHERE key = ?",
        (f"preference_meta.{name}",),
    )
    db.commit()
    return cursor.rowcount > 0


def create_automation_rule(
    sensor,
    operator,
    threshold,
    device_id,
    action,
    capability="",
    value=None,
):
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO automation_rules
            (sensor, operator, threshold, device_id, action, capability,
             value, enabled, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            sensor,
            operator,
            threshold,
            device_id,
            action,
            capability,
            value,
            now_iso(),
        ),
    )
    db.commit()
    return get_automation_rule(cursor.lastrowid)


def get_automation_rule(rule_id):
    row = get_db().execute(
        """
        SELECT automation_rules.*, devices.name AS device_name
        FROM automation_rules
        LEFT JOIN devices ON devices.id = automation_rules.device_id
        WHERE automation_rules.id = ?
        """,
        (rule_id,),
    ).fetchone()
    return dict(row) if row else None


def list_automation_rules(enabled_only=False):
    where = "WHERE automation_rules.enabled = 1" if enabled_only else ""
    rows = get_db().execute(
        f"""
        SELECT automation_rules.*, devices.name AS device_name
        FROM automation_rules
        LEFT JOIN devices ON devices.id = automation_rules.device_id
        {where}
        ORDER BY automation_rules.id DESC
        """
    ).fetchall()
    return rows_to_dicts(rows)


def update_automation_rule(rule_id, *, enabled=None, triggered=False):
    rule = get_automation_rule(rule_id)
    if not rule:
        return None
    db = get_db()
    db.execute(
        """
        UPDATE automation_rules
        SET enabled = ?, last_triggered_at = ?
        WHERE id = ?
        """,
        (
            int(enabled) if enabled is not None else rule["enabled"],
            now_iso() if triggered else rule["last_triggered_at"],
            rule_id,
        ),
    )
    db.commit()
    return get_automation_rule(rule_id)


def delete_automation_rule(rule_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM automation_rules WHERE id = ?",
        (rule_id,),
    )
    db.commit()
    return cursor.rowcount > 0


def automation_managed_device_ids():
    rows = get_db().execute(
        """
        SELECT DISTINCT device_id FROM automation_rules
        WHERE enabled = 1
        """
    ).fetchall()
    return {row["device_id"] for row in rows}


def save_scene(name, actions):
    timestamp = now_iso()
    db = get_db()
    db.execute(
        """
        INSERT INTO custom_scenes (name, actions, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            actions = excluded.actions,
            updated_at = excluded.updated_at
        """,
        (
            name,
            json.dumps(actions, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    db.commit()
    return get_scene_by_name(name)


def _decode_scene(row):
    if not row:
        return None
    scene = dict(row)
    scene["actions"] = json.loads(scene["actions"])
    return scene


def get_scene(scene_id):
    row = get_db().execute(
        "SELECT * FROM custom_scenes WHERE id = ?",
        (scene_id,),
    ).fetchone()
    return _decode_scene(row)


def get_scene_by_name(name):
    row = get_db().execute(
        "SELECT * FROM custom_scenes WHERE name = ?",
        (name,),
    ).fetchone()
    return _decode_scene(row)


def list_scenes():
    rows = get_db().execute(
        "SELECT * FROM custom_scenes ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    return [_decode_scene(row) for row in rows]


def delete_scene(scene_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM custom_scenes WHERE id = ?",
        (scene_id,),
    )
    db.commit()
    return cursor.rowcount > 0


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


def update_device(
    device_id,
    *,
    name=None,
    room=None,
    state=None,
    online=None,
    is_virtual=None,
):
    device = get_device(device_id)
    if not device:
        return None

    values = {
        "name": name if name is not None else device["name"],
        "room": room if room is not None else device["room"],
        "state": state if state is not None else device["state"],
        "online": int(online) if online is not None else device["online"],
        "is_virtual": (
            int(is_virtual) if is_virtual is not None else device["is_virtual"]
        ),
    }
    db = get_db()
    db.execute(
        """
        UPDATE devices
        SET name = ?, room = ?, state = ?, online = ?, is_virtual = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            values["name"],
            values["room"],
            values["state"],
            values["online"],
            values["is_virtual"],
            now_iso(),
            device_id,
        ),
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


def set_device_manual_override(device_id, minutes=30, reason="用户手动控制"):
    if not get_device(device_id):
        return None
    created_at = now_iso()
    until_at = (
        datetime.now().astimezone() + timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")
    db = get_db()
    db.execute(
        """
        INSERT INTO device_manual_overrides
            (device_id, until_at, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            until_at = excluded.until_at,
            reason = excluded.reason,
            created_at = excluded.created_at
        """,
        (device_id, until_at, reason, created_at),
    )
    db.commit()
    return {
        "device_id": device_id,
        "until_at": until_at,
        "reason": reason,
    }


def list_active_manual_overrides():
    rows = get_db().execute(
        """
        SELECT device_manual_overrides.*, devices.name AS device_name
        FROM device_manual_overrides
        LEFT JOIN devices ON devices.id = device_manual_overrides.device_id
        ORDER BY until_at
        """
    ).fetchall()
    current = datetime.now(timezone.utc)
    active = [row for row in rows if _absolute_time(row["until_at"]) > current]
    active.sort(key=lambda row: _absolute_time(row["until_at"]))
    return rows_to_dicts(active)


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
        """
    ).fetchall()
    rows = sorted(rows, key=lambda row: _absolute_time(row["scheduled_at"]))
    return rows_to_dicts(rows)


def delete_alarm(alarm_id):
    db = get_db()
    cursor = db.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
    db.commit()
    return cursor.rowcount > 0


def due_alarms():
    current = datetime.now(timezone.utc)
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM alarms
        WHERE enabled = 1 AND triggered = 0
        """
    ).fetchall()
    rows = [row for row in rows if _absolute_time(row["scheduled_at"]) <= current]
    rows.sort(key=lambda row: _absolute_time(row["scheduled_at"]))
    if rows:
        db.executemany(
            "UPDATE alarms SET triggered = 1 WHERE id = ?",
            [(row["id"],) for row in rows],
        )
        db.commit()
    return rows_to_dicts(rows)


def _decode_notification(row):
    if not row:
        return None
    notification = dict(row)
    notification["payload"] = json.loads(notification["payload"])
    return notification


def get_notification(notification_id):
    row = get_db().execute(
        "SELECT * FROM notifications WHERE id = ?",
        (notification_id,),
    ).fetchone()
    return _decode_notification(row)


def _prune_read_notifications(db):
    db.execute(
        """
        DELETE FROM notifications
        WHERE read_at IS NOT NULL
          AND id NOT IN (
              SELECT id FROM notifications
              WHERE read_at IS NOT NULL
              ORDER BY id DESC
              LIMIT ?
          )
        """,
        (MAX_NOTIFICATION_ROWS,),
    )


def create_notification(kind, title, message, dedupe_key, payload=None):
    db = get_db()
    cursor = db.execute(
        """
        INSERT OR IGNORE INTO notifications
            (kind, title, message, dedupe_key, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            title,
            message,
            dedupe_key,
            json.dumps(payload or {}, ensure_ascii=False),
            now_iso(),
        ),
    )
    created = cursor.rowcount > 0
    _prune_read_notifications(db)
    db.commit()
    row = db.execute(
        "SELECT * FROM notifications WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    return _decode_notification(row), created


def enqueue_due_alarm_notifications():
    timestamp = now_iso()
    current = datetime.now(timezone.utc)
    db = get_db()
    created_ids = []
    try:
        db.execute("BEGIN IMMEDIATE")
        alarms = db.execute(
            """
            SELECT * FROM alarms
            WHERE enabled = 1 AND triggered = 0
            """
        ).fetchall()
        alarms = [
            alarm
            for alarm in alarms
            if _absolute_time(alarm["scheduled_at"]) <= current
        ]
        alarms.sort(key=lambda alarm: _absolute_time(alarm["scheduled_at"]))
        alarms = alarms[:100]
        for alarm in alarms:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (kind, title, message, dedupe_key, payload, created_at)
                VALUES ('alarm', '闹钟提醒', ?, ?, ?, ?)
                """,
                (
                    f"闹钟提醒：{alarm['label']}",
                    f"alarm:{alarm['id']}",
                    json.dumps(dict(alarm), ensure_ascii=False),
                    timestamp,
                ),
            )
            if cursor.rowcount > 0:
                created_ids.append(cursor.lastrowid)
            db.execute(
                "UPDATE alarms SET triggered = 1 WHERE id = ?",
                (alarm["id"],),
            )
        _prune_read_notifications(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return [get_notification(item) for item in created_ids]


def list_notifications(limit=20, unread_only=False):
    where = "WHERE read_at IS NULL" if unread_only else ""
    rows = get_db().execute(
        f"""
        SELECT * FROM notifications
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_decode_notification(row) for row in rows]


def unread_notification_count():
    row = get_db().execute(
        "SELECT COUNT(*) AS count FROM notifications WHERE read_at IS NULL"
    ).fetchone()
    return row["count"]


def claim_notification(lease_seconds=30):
    claimed_at = now_iso()
    claim_token = secrets.token_urlsafe(24)
    lease_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=lease_seconds
    )
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """
            SELECT * FROM notifications
            WHERE delivered_at IS NULL
              AND read_at IS NULL
            ORDER BY id
            """
        ).fetchall()
        row = next(
            (
                item
                for item in rows
                if item["claimed_at"] is None
                or _absolute_time(item["claimed_at"]) <= lease_cutoff
            ),
            None,
        )
        if not row:
            db.commit()
            return None
        db.execute(
            """
            UPDATE notifications
            SET claimed_at = ?, claim_token = ?
            WHERE id = ? AND delivered_at IS NULL AND read_at IS NULL
            """,
            (claimed_at, claim_token, row["id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_notification(row["id"])


def acknowledge_notification(notification_id, claim_token):
    db = get_db()
    cursor = db.execute(
        """
        UPDATE notifications
        SET delivered_at = COALESCE(delivered_at, ?)
        WHERE id = ? AND claim_token = ?
        """,
        (now_iso(), notification_id, claim_token),
    )
    db.commit()
    return get_notification(notification_id) if cursor.rowcount else None


def acquire_runtime_lease(name, owner_id, ttl_seconds):
    timestamp = datetime.now(timezone.utc)
    expires_at = (timestamp + timedelta(seconds=ttl_seconds)).isoformat(
        timespec="seconds"
    )
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM runtime_leases WHERE name = ?",
            (name,),
        ).fetchone()
        if (
            current
            and current["owner_id"] != owner_id
            and _absolute_time(current["expires_at"]) > timestamp
        ):
            db.commit()
            return False
        db.execute(
            """
            INSERT INTO runtime_leases (name, owner_id, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner_id = excluded.owner_id,
                expires_at = excluded.expires_at
            """,
            (name, owner_id, expires_at),
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def release_runtime_lease(name, owner_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM runtime_leases WHERE name = ? AND owner_id = ?",
        (name, owner_id),
    )
    db.commit()
    return cursor.rowcount > 0


def mark_notification_read(notification_id):
    db = get_db()
    cursor = db.execute(
        """
        UPDATE notifications
        SET read_at = COALESCE(read_at, ?)
        WHERE id = ?
        """,
        (now_iso(), notification_id),
    )
    db.commit()
    return get_notification(notification_id) if cursor.rowcount else None


def mark_all_notifications_read():
    db = get_db()
    cursor = db.execute(
        """
        UPDATE notifications
        SET read_at = ?
        WHERE read_at IS NULL
        """,
        (now_iso(),),
    )
    db.commit()
    return cursor.rowcount


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
    db.execute(
        """
        DELETE FROM events
        WHERE id <= (
            SELECT COALESCE(MAX(id) - ?, 0) FROM events
        )
        """,
        (MAX_EVENT_ROWS,),
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


def latest_event(kind):
    row = get_db().execute(
        """
        SELECT * FROM events
        WHERE kind = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (kind,),
    ).fetchone()
    if not row:
        return None
    event = dict(row)
    event["payload"] = json.loads(event["payload"])
    return event


def add_conversation_message(session_id, role, content, keep=12):
    db = get_db()
    db.execute(
        """
        INSERT INTO conversation_messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, role, content, now_iso()),
    )
    db.execute(
        """
        DELETE FROM conversation_messages
        WHERE session_id = ? AND id NOT IN (
            SELECT id FROM conversation_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (session_id, session_id, keep),
    )
    db.commit()


def list_conversation_messages(session_id, limit=12):
    rows = get_db().execute(
        """
        SELECT role, content
        FROM conversation_messages
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def remember_session_device(session_id, device_id):
    if not get_device(device_id):
        return None
    get_db().execute(
        """
        INSERT INTO assistant_context (session_id, last_device_id, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            last_device_id = excluded.last_device_id,
            updated_at = excluded.updated_at
        """,
        (session_id, device_id, now_iso()),
    )
    get_db().commit()
    return device_id


def get_session_device(session_id, max_age_minutes=30):
    row = get_db().execute(
        """
        SELECT last_device_id, updated_at
        FROM assistant_context
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if not row:
        return None
    updated_at = datetime.fromisoformat(row["updated_at"])
    if datetime.now().astimezone() - updated_at > timedelta(
        minutes=max_age_minutes
    ):
        return None
    return row["last_device_id"] if get_device(row["last_device_id"]) else None
