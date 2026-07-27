from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes, canonical_sha256, content_id
from .database import event_chain_hash, transaction
from .registry import Registry as BaseRegistry
from .registry import RegistryError


class AuthorityRegistry(BaseRegistry):
    """Registry v2: v1 artifact reconciliation plus authority and chained events."""

    def __init__(self, connection):
        super().__init__(connection)
        from .authority import AuthorityCore

        self.authority = AuthorityCore(connection, self._event)

    def _event(
        self,
        *,
        occurred_at: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any],
    ) -> str:
        body = {
            "occurred_at": occurred_at,
            "event_type": event_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "payload": payload,
        }
        event_id = content_id("evt", body)
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        payload_sha = canonical_sha256(payload)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events(
                event_id, occurred_at, event_type, subject_type, subject_id,
                payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                occurred_at,
                event_type,
                subject_type,
                subject_id,
                payload_json,
                payload_sha,
            ),
        )
        if cursor.rowcount == 1:
            sequence = cursor.lastrowid
            previous_row = self.connection.execute(
                "SELECT event_hash FROM event_chain ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = previous_row["event_hash"] if previous_row else "0" * 64
            event_row = {
                "sequence": sequence,
                "event_id": event_id,
                "occurred_at": occurred_at,
                "event_type": event_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "payload_sha256": payload_sha,
            }
            chain_hash = event_chain_hash(event_row, previous)
            self.connection.execute(
                """
                INSERT INTO event_chain(
                    sequence, event_id, previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?)
                """,
                (sequence, event_id, previous, chain_hash),
            )
        return event_id

    def verify_event_chain(self) -> dict[str, Any]:
        previous = "0" * 64
        count = 0
        for row in self.connection.execute(
            """
            SELECT e.*, c.previous_event_hash, c.event_hash
            FROM events e JOIN event_chain c ON c.sequence=e.sequence
            ORDER BY e.sequence
            """
        ):
            expected = event_chain_hash(row, previous)
            if row["previous_event_hash"] != previous or row["event_hash"] != expected:
                return {
                    "valid": False,
                    "sequence": row["sequence"],
                    "expected": expected,
                    "observed": row["event_hash"],
                }
            previous = expected
            count += 1
        event_count = self.connection.execute(
            "SELECT COUNT(*) AS n FROM events"
        ).fetchone()["n"]
        if event_count != count:
            return {
                "valid": False,
                "error": "chain_length_mismatch",
                "events": event_count,
                "chain": count,
            }
        return {"valid": True, "events": count, "head": previous}

    def register_terminal_receipt(self, payload: dict[str, Any], created_at: str) -> str:
        from .contracts import validate_contract

        errors = validate_contract("terminal_receipt", payload)
        if errors:
            raise RegistryError("receipt_invalid:" + ",".join(errors))
        receipt_id = payload["receipt_id"]
        payload_sha = canonical_sha256(payload)
        existing = self.connection.execute(
            "SELECT payload_sha256 FROM receipts WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] == payload_sha:
                return receipt_id
            raise RegistryError("receipt_id_conflict")
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO receipts(
                    receipt_id, task_id, idempotency_key, authority_epoch,
                    executor, terminal_state, output_artifact_id,
                    output_sha256, source_commit, payload_json,
                    payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    payload["task_id"],
                    payload["idempotency_key"],
                    payload["authority_epoch"],
                    payload["executor"],
                    payload["state"],
                    payload.get("output_artifact_id"),
                    payload.get("output_sha256"),
                    payload.get("source_commit"),
                    canonical_json_bytes(payload).decode("utf-8"),
                    payload_sha,
                    created_at,
                ),
            )
            self._event(
                occurred_at=created_at,
                event_type="terminal_receipt_registered",
                subject_type="receipt",
                subject_id=receipt_id,
                payload=payload,
            )
        return receipt_id

    def export_state(self) -> dict[str, Any]:
        result = super().export_state()
        result["schema_version"] = "superintendent-registry-export-v2"
        for table in (
            "event_chain",
            "authority_epochs",
            "capability_declarations",
            "tasks",
            "work_grants",
            "leases",
            "task_transitions",
        ):
            result[table] = [
                dict(row) for row in self.connection.execute(f"SELECT * FROM {table}")
            ]
        return result
