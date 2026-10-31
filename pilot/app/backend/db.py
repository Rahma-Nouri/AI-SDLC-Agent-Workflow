import os
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = os.getenv("APP_DB_PATH", "exam_system.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    parsed_text TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS exam_blueprints (
    id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL,
    blueprint_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS generated_exams (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_blueprint_id TEXT,
    title TEXT NOT NULL,
    is_adaptive INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (source_blueprint_id) REFERENCES exam_blueprints(id)
);
"""


def get_db_path(db_path: Optional[str] = None) -> str:
    path = db_path or DEFAULT_DB_PATH
    if path == ":memory:":
        raise ValueError("In-memory SQLite database is disallowed. Use a file-backed path.")
    return path


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    resolved_path = get_db_path(db_path)
    parent = Path(resolved_path).parent
    if parent and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    conn = get_connection(db_path)
    try:
        # "with conn" only commits (saves) the changes in sqlite3;
        # it does NOT close the connection.
        with conn:
            conn.executescript(SCHEMA_SQL)
    finally:
        # Always close, even after an error. On Windows an open
        # connection keeps the .db file locked.
        conn.close()
