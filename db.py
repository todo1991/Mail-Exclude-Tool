import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "mail_exclude.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    listmonk_id      INTEGER NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    subscriber_count INTEGER NOT NULL DEFAULT 0,
    last_synced_at   TEXT,
    selected         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS emails (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id                INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    email                  TEXT NOT NULL,
    listmonk_subscriber_id INTEGER,
    UNIQUE (list_id, email)
);

CREATE INDEX IF NOT EXISTS idx_emails_list ON emails(list_id);
CREATE INDEX IF NOT EXISTS idx_emails_subscriber_id ON emails(listmonk_subscriber_id);

CREATE TABLE IF NOT EXISTS permanent_excludes (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL,
    type  TEXT NOT NULL CHECK (type IN ('email', 'domain')),
    UNIQUE (value, type)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table: str, column: str, ddl: str, on_add=None) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        if on_add:
            on_add(conn)


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Migration: existing DBs predate `selected`. Auto-select already-synced
        # lists so the Filter tab keeps showing them after upgrade.
        _ensure_column(
            conn, "lists", "selected", "INTEGER NOT NULL DEFAULT 0",
            on_add=lambda c: c.execute(
                "UPDATE lists SET selected = 1 WHERE last_synced_at IS NOT NULL"
            ),
        )
