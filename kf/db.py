"""Conexión a SQLite y funciones genéricas que registran historial automáticamente."""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("KF_DB", ROOT / "data" / "katapulk_fuel.db"))

# Campos que no se registran en el historial (ruido).
_SKIP_HISTORY = {"updated_at", "created_at", "id"}


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columnas agregadas después de crear la base: se añaden solas a bases existentes.
_MIGRATIONS = [
    ("invoices", "bl_no", "TEXT"),
    ("invoices", "tank_no", "TEXT"),
    ("invoices", "concept", "TEXT"),
    ("invoices", "status", "TEXT NOT NULL DEFAULT 'final'"),
    ("payment_allocations", "auto", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Crea o actualiza el esquema. Si schema.sql no cambió, NO escribe nada en la base: así una simple
    consulta en la laptop no 'modifica' el archivo y no bloquea la descarga de la versión de la nube."""
    import hashlib
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    version = hashlib.sha1((schema + repr(_MIGRATIONS)).encode()).hexdigest()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row and row[0] == version:
            return
    except sqlite3.OperationalError:
        pass                                   # base nueva o sin tabla meta
    for table, col, typ in _MIGRATIONS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # Las vistas no guardan datos: se recrean para tomar la versión actual de schema.sql
    for (view,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
        conn.execute(f"DROP VIEW {view}")
    conn.executescript(schema)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (version,))
    conn.commit()


def _norm(v):
    """Compara 3 == 3.0 == '3' sin marcar falsos cambios."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v).strip()


def log_event(conn, entity, key, action, field=None, old=None, new=None, source="manual"):
    conn.execute(
        "INSERT INTO events(entity, entity_key, action, field, old_value, new_value, source)"
        " VALUES (?,?,?,?,?,?,?)",
        (entity, str(key), action, field,
         None if old is None else str(old), None if new is None else str(new), source),
    )


def upsert(conn, table, key_col, data: dict, entity: str, source="manual") -> str:
    """Crea o actualiza una fila por su clave natural y deja rastro de cada cambio.

    Solo se actualizan los campos presentes en `data` y distintos de None,
    así un email que trae solo el cutoff nuevo no borra el resto.
    Devuelve 'created', 'updated' o 'unchanged'.
    """
    key = data[key_col]
    row = conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    data = {k: int(v) if isinstance(v, bool) else v for k, v in data.items() if v is not None}

    if row is None:
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
        log_event(conn, entity, key, "created", source=source)
        for k, v in data.items():
            if k != key_col:
                log_event(conn, entity, key, "set", k, None, v, source)
        return "created"

    changes = {k: v for k, v in data.items()
               if k != key_col and k not in _SKIP_HISTORY and _norm(row[k]) != _norm(v)}
    if not changes:
        return "unchanged"
    sets = ", ".join(f"{k} = ?" for k in changes)
    has_updated_at = "updated_at" in row.keys()
    if has_updated_at:
        sets += ", updated_at = datetime('now')"
    conn.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = ?", (*changes.values(), key))
    for k, v in changes.items():
        action = "status_change" if k in ("status", "fill_state", "location") else "updated"
        log_event(conn, entity, key, action, k, row[k], v, source)
    return "updated"


def history(conn, entity, key):
    return conn.execute(
        "SELECT ts, action, field, old_value, new_value, source FROM events"
        " WHERE entity = ? AND entity_key = ? ORDER BY id", (entity, key)).fetchall()
