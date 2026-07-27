from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_records (
    record_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    media_type TEXT NOT NULL,
    authority_class TEXT NOT NULL,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_names (
    name_id TEXT PRIMARY KEY,
    logical_name TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    named_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    UNIQUE(logical_name, artifact_id)
);
CREATE INDEX IF NOT EXISTS artifact_names_lookup_idx ON artifact_names(logical_name, active);

CREATE TABLE IF NOT EXISTS replicas (
    replica_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    plane TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    locator_sha256 TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0, 1)),
    persistence_state TEXT NOT NULL,
    index_state TEXT,
    observed_sha256 TEXT,
    observed_size_bytes INTEGER,
    observed_at TEXT NOT NULL,
    last_error TEXT,
    UNIQUE(plane, locator_sha256)
);
CREATE INDEX IF NOT EXISTS replicas_artifact_idx ON replicas(artifact_id, plane);

CREATE TABLE IF NOT EXISTS pointers (
    pointer_id TEXT PRIMARY KEY,
    pointer_name TEXT NOT NULL,
    plane TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    target_artifact_id TEXT REFERENCES artifacts(artifact_id),
    declared_sha256 TEXT,
    observed_sha256 TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pointers_name_idx ON pointers(pointer_name, plane);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL,
    executor TEXT NOT NULL,
    terminal_state TEXT NOT NULL,
    output_artifact_id TEXT REFERENCES artifacts(artifact_id),
    output_sha256 TEXT,
    source_commit TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_task_idx ON receipts(task_id, idempotency_key);

CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    logical_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    accepted_artifact_id TEXT REFERENCES artifacts(artifact_id),
    states_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
        ("schema_version", "superintendent-registry-sqlite-v1"),
    )
    connection.commit()
    return connection


def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    class _Transaction:
        def __enter__(self) -> sqlite3.Connection:
            connection.execute("BEGIN IMMEDIATE")
            return connection

        def __exit__(self, exc_type, exc, tb) -> None:
            if exc_type is None:
                connection.commit()
            else:
                connection.rollback()

    return _Transaction()  # type: ignore[return-value]


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def json_load(value: str | None):
    return None if value is None else json.loads(value)
