from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .canonical import canonical_json_bytes, sha256_bytes

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

CREATE TABLE IF NOT EXISTS event_chain (
    sequence INTEGER PRIMARY KEY REFERENCES events(sequence),
    event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
    previous_event_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE
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

CREATE TABLE IF NOT EXISTS authority_epochs (
    epoch INTEGER PRIMARY KEY CHECK(epoch >= 0),
    status TEXT NOT NULL CHECK(status IN ('active', 'retired')),
    previous_epoch INTEGER,
    issued_at TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    reason TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_declarations (
    declaration_id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    executor TEXT NOT NULL,
    operations_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    health_json TEXT NOT NULL,
    declared_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    UNIQUE(capability_id, executor, declaration_id)
);
CREATE INDEX IF NOT EXISTS capability_active_idx ON capability_declarations(capability_id, executor, active);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    authority_epoch INTEGER NOT NULL REFERENCES authority_epochs(epoch),
    capability TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_receipt_id TEXT REFERENCES receipts(receipt_id),
    quarantine_reason TEXT
);
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, authority_epoch);

CREATE TABLE IF NOT EXISTS work_grants (
    grant_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    authority_epoch INTEGER NOT NULL REFERENCES authority_epochs(epoch),
    executor TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    permitted_actions_json TEXT NOT NULL,
    forbidden_actions_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('issued', 'accepted', 'active', 'expired', 'revoked', 'completed')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS grants_task_idx ON work_grants(task_id, state);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL UNIQUE REFERENCES work_grants(grant_id),
    executor TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL REFERENCES authority_epochs(epoch),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('issued', 'active', 'expired', 'revoked', 'released')),
    last_heartbeat_at TEXT,
    revocation_reason TEXT
);
CREATE INDEX IF NOT EXISTS leases_state_idx ON leases(state, expires_at);

CREATE TABLE IF NOT EXISTS task_transitions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS task_transition_idx ON task_transitions(task_id, sequence);
"""

APPEND_ONLY_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN
    SELECT RAISE(ABORT, 'events_append_only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN
    SELECT RAISE(ABORT, 'events_append_only');
END;
CREATE TRIGGER IF NOT EXISTS event_chain_no_update
BEFORE UPDATE ON event_chain BEGIN
    SELECT RAISE(ABORT, 'event_chain_append_only');
END;
CREATE TRIGGER IF NOT EXISTS event_chain_no_delete
BEFORE DELETE ON event_chain BEGIN
    SELECT RAISE(ABORT, 'event_chain_append_only');
END;
CREATE TRIGGER IF NOT EXISTS task_transitions_no_update
BEFORE UPDATE ON task_transitions BEGIN
    SELECT RAISE(ABORT, 'task_transitions_append_only');
END;
CREATE TRIGGER IF NOT EXISTS task_transitions_no_delete
BEFORE DELETE ON task_transitions BEGIN
    SELECT RAISE(ABORT, 'task_transitions_append_only');
END;
CREATE TRIGGER IF NOT EXISTS receipts_no_update
BEFORE UPDATE ON receipts BEGIN
    SELECT RAISE(ABORT, 'receipts_append_only');
END;
CREATE TRIGGER IF NOT EXISTS receipts_no_delete
BEFORE DELETE ON receipts BEGIN
    SELECT RAISE(ABORT, 'receipts_append_only');
END;
"""


def event_chain_hash(row: sqlite3.Row | dict, previous_hash: str) -> str:
    material = {
        "sequence": row["sequence"],
        "event_id": row["event_id"],
        "occurred_at": row["occurred_at"],
        "event_type": row["event_type"],
        "subject_type": row["subject_type"],
        "subject_id": row["subject_id"],
        "payload_sha256": row["payload_sha256"],
        "previous_event_hash": previous_hash,
    }
    return sha256_bytes(canonical_json_bytes(material))


def _backfill_event_chain(connection: sqlite3.Connection) -> None:
    previous = "0" * 64
    chain_rows = {
        row["sequence"]: row
        for row in connection.execute(
            "SELECT sequence, event_id, previous_event_hash, event_hash FROM event_chain ORDER BY sequence"
        )
    }
    for row in connection.execute("SELECT * FROM events ORDER BY sequence"):
        expected = event_chain_hash(row, previous)
        existing = chain_rows.get(row["sequence"])
        if existing is None:
            connection.execute(
                "INSERT INTO event_chain(sequence, event_id, previous_event_hash, event_hash) VALUES (?, ?, ?, ?)",
                (row["sequence"], row["event_id"], previous, expected),
            )
        elif (
            existing["event_id"] != row["event_id"]
            or existing["previous_event_hash"] != previous
            or existing["event_hash"] != expected
        ):
            raise sqlite3.DatabaseError(f"event_chain_invalid_at:{row['sequence']}")
        previous = expected


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
        ("schema_version", "superintendent-registry-sqlite-v2"),
    )
    connection.execute(
        "UPDATE metadata SET value=? WHERE key='schema_version'",
        ("superintendent-registry-sqlite-v2",),
    )
    _backfill_event_chain(connection)
    connection.executescript(APPEND_ONLY_TRIGGERS)
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
