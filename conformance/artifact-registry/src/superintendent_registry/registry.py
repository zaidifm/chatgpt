from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .canonical import canonical_json_bytes, canonical_sha256, content_id, sha256_bytes
from .contracts import SCHEMA_VERSIONS, validate_contract
from .database import transaction


@dataclass(frozen=True)
class FileObservation:
    sha256: str
    size_bytes: int


def observe_file(path: str | Path) -> FileObservation:
    file_path = Path(path)
    digest = hashlib.sha256()
    size = 0
    with file_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return FileObservation(digest.hexdigest(), size)


class RegistryError(RuntimeError):
    pass


class Registry:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _event(self, *, occurred_at: str, event_type: str, subject_type: str, subject_id: str, payload: dict[str, Any]) -> str:
        body = {"occurred_at": occurred_at, "event_type": event_type, "subject_type": subject_type, "subject_id": subject_id, "payload": payload}
        event_id = content_id("evt", body)
        self.connection.execute(
            "INSERT OR IGNORE INTO events(event_id, occurred_at, event_type, subject_type, subject_id, payload_json, payload_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, occurred_at, event_type, subject_type, subject_id, canonical_json_bytes(payload).decode("utf-8"), canonical_sha256(payload)),
        )
        return event_id

    def register_contract(self, kind: str, payload: dict[str, Any], created_at: str) -> str:
        errors = validate_contract(kind, payload)
        if errors:
            raise RegistryError("contract_invalid:" + ",".join(errors))
        record_id = content_id(kind.replace("_", "-"), payload)
        with transaction(self.connection):
            self.connection.execute(
                "INSERT OR IGNORE INTO contract_records(record_id, kind, schema_version, payload_json, payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (record_id, kind, payload["schema_version"], canonical_json_bytes(payload).decode("utf-8"), canonical_sha256(payload), created_at),
            )
            self._event(occurred_at=created_at, event_type="contract_registered", subject_type=kind, subject_id=record_id, payload={"record_id": record_id, "kind": kind})
        return record_id

    def register_artifact_bytes(self, *, logical_name: str, data: bytes, media_type: str, authority_class: str, source: dict[str, Any], created_at: str, active: bool = True) -> dict[str, Any]:
        digest = sha256_bytes(data)
        artifact_id = f"sha256:{digest}"
        payload = {
            "schema_version": SCHEMA_VERSIONS["artifact"],
            "artifact_id": artifact_id,
            "logical_name": logical_name,
            "sha256": digest,
            "size_bytes": len(data),
            "media_type": media_type,
            "authority_class": authority_class,
            "created_at": created_at,
            "source": source,
        }
        errors = validate_contract("artifact", payload)
        if errors:
            raise RegistryError("artifact_invalid:" + ",".join(errors))
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO artifacts(artifact_id, sha256, size_bytes, media_type, authority_class, source_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET media_type=excluded.media_type,
                    authority_class=excluded.authority_class, source_json=excluded.source_json
                """,
                (artifact_id, digest, len(data), media_type, authority_class, canonical_json_bytes(source).decode("utf-8"), created_at),
            )
            name_id = content_id("artifact-name", {"logical_name": logical_name, "artifact_id": artifact_id})
            self.connection.execute(
                """
                INSERT INTO artifact_names(name_id, logical_name, artifact_id, named_at, active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(logical_name, artifact_id) DO UPDATE SET active=excluded.active
                """,
                (name_id, logical_name, artifact_id, created_at, 1 if active else 0),
            )
            self._event(occurred_at=created_at, event_type="artifact_registered", subject_type="artifact", subject_id=artifact_id, payload=payload)
        return payload

    def register_artifact_file(self, *, logical_name: str, path: str | Path, authority_class: str, source: dict[str, Any], created_at: str, media_type: str | None = None, active: bool = True) -> dict[str, Any]:
        file_path = Path(path)
        guessed = media_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return self.register_artifact_bytes(logical_name=logical_name, data=file_path.read_bytes(), media_type=guessed, authority_class=authority_class, source=source, created_at=created_at, active=active)

    def set_artifact_active(self, artifact_id: str, active: bool, observed_at: str) -> None:
        with transaction(self.connection):
            cursor = self.connection.execute("UPDATE artifact_names SET active=? WHERE artifact_id=?", (1 if active else 0, artifact_id))
            if cursor.rowcount < 1:
                raise RegistryError(f"artifact_not_found:{artifact_id}")
            self._event(occurred_at=observed_at, event_type="artifact_activation_changed", subject_type="artifact", subject_id=artifact_id, payload={"active": active})

    def register_replica(self, *, artifact_id: str, plane: str, locator: dict[str, Any], persistence_state: str, observed_at: str, observed_sha256: str | None = None, observed_size_bytes: int | None = None, index_state: str | None = None, required: bool = True, last_error: str | None = None) -> str:
        locator_hash = canonical_sha256(locator)
        identity = {"artifact_id": artifact_id, "plane": plane, "locator_sha256": locator_hash}
        replica_id = content_id("replica", identity)
        with transaction(self.connection):
            if self.connection.execute("SELECT 1 FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone() is None:
                raise RegistryError(f"artifact_not_found:{artifact_id}")
            self.connection.execute(
                """
                INSERT INTO replicas(replica_id, artifact_id, plane, locator_json, locator_sha256, required, persistence_state, index_state,
                    observed_sha256, observed_size_bytes, observed_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(replica_id) DO UPDATE SET required=excluded.required, persistence_state=excluded.persistence_state,
                    index_state=excluded.index_state, observed_sha256=excluded.observed_sha256,
                    observed_size_bytes=excluded.observed_size_bytes, observed_at=excluded.observed_at, last_error=excluded.last_error
                """,
                (replica_id, artifact_id, plane, canonical_json_bytes(locator).decode("utf-8"), locator_hash, 1 if required else 0,
                 persistence_state, index_state, observed_sha256, observed_size_bytes, observed_at, last_error),
            )
            self._event(occurred_at=observed_at, event_type="replica_observed", subject_type="replica", subject_id=replica_id, payload={**identity, "persistence_state": persistence_state, "index_state": index_state, "observed_sha256": observed_sha256, "observed_size_bytes": observed_size_bytes, "required": required, "last_error": last_error})
        return replica_id

    def register_pointer(self, *, pointer_name: str, plane: str, locator: dict[str, Any], target_artifact_id: str | None, declared_sha256: str | None, observed_sha256: str | None, observed_at: str) -> str:
        identity = {"pointer_name": pointer_name, "plane": plane, "locator": locator}
        pointer_id = content_id("pointer", identity)
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO pointers(pointer_id, pointer_name, plane, locator_json, target_artifact_id, declared_sha256, observed_sha256, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pointer_id) DO UPDATE SET target_artifact_id=excluded.target_artifact_id,
                    declared_sha256=excluded.declared_sha256, observed_sha256=excluded.observed_sha256, observed_at=excluded.observed_at
                """,
                (pointer_id, pointer_name, plane, canonical_json_bytes(locator).decode("utf-8"), target_artifact_id, declared_sha256, observed_sha256, observed_at),
            )
            self._event(occurred_at=observed_at, event_type="pointer_observed", subject_type="pointer", subject_id=pointer_id, payload={**identity, "target_artifact_id": target_artifact_id, "declared_sha256": declared_sha256, "observed_sha256": observed_sha256})
        return pointer_id

    def register_terminal_receipt(self, payload: dict[str, Any], created_at: str) -> str:
        errors = validate_contract("terminal_receipt", payload)
        if errors:
            raise RegistryError("receipt_invalid:" + ",".join(errors))
        receipt_id = payload["receipt_id"]
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT OR REPLACE INTO receipts(receipt_id, task_id, idempotency_key, authority_epoch, executor, terminal_state,
                    output_artifact_id, output_sha256, source_commit, payload_json, payload_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (receipt_id, payload["task_id"], payload["idempotency_key"], payload["authority_epoch"], payload["executor"], payload["state"],
                 payload.get("output_artifact_id"), payload.get("output_sha256"), payload.get("source_commit"),
                 canonical_json_bytes(payload).decode("utf-8"), canonical_sha256(payload), created_at),
            )
            self._event(occurred_at=created_at, event_type="terminal_receipt_registered", subject_type="receipt", subject_id=receipt_id, payload=payload)
        return receipt_id

    def _active_artifacts(self, logical_name: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """
            SELECT a.*, n.logical_name, n.active, n.named_at, n.name_id
            FROM artifacts a
            JOIN artifact_names n ON n.artifact_id=a.artifact_id
            WHERE n.logical_name=? AND n.active=1
            ORDER BY n.named_at, a.artifact_id
            """,
            (logical_name,),
        ))

    def reconcile(self, *, logical_name: str, required_planes: Iterable[str], observed_at: str) -> dict[str, Any]:
        required = sorted(set(required_planes))
        states: list[str] = []
        evidence: dict[str, Any] = {"required_planes": required, "artifacts": [], "replicas": [], "pointers": [], "receipt_groups": []}
        actions: list[dict[str, Any]] = []
        artifacts = self._active_artifacts(logical_name)
        evidence["artifacts"] = [dict(row) for row in artifacts]
        accepted_artifact_id: str | None = None
        artifact: sqlite3.Row | None = None
        if not artifacts:
            states.append("missing_artifact")
            actions.append({"action": "register_authoritative_artifact"})
        elif len(artifacts) > 1:
            states.append("duplicate_logical_name")
            actions.append({"action": "owner_select_active_artifact", "artifact_ids": [row["artifact_id"] for row in artifacts]})
        else:
            artifact = artifacts[0]
            accepted_artifact_id = artifact["artifact_id"]

        if artifact is not None:
            replicas = list(self.connection.execute("SELECT * FROM replicas WHERE artifact_id=? ORDER BY plane, replica_id", (artifact["artifact_id"],)))
            evidence["replicas"] = [dict(row) for row in replicas]
            by_plane: dict[str, list[sqlite3.Row]] = {}
            for row in replicas:
                by_plane.setdefault(row["plane"], []).append(row)
            for plane in required:
                candidates = by_plane.get(plane, [])
                matching = [row for row in candidates if row["persistence_state"] == "present" and row["observed_sha256"] == artifact["sha256"] and row["observed_size_bytes"] == artifact["size_bytes"]]
                mismatched = [row for row in candidates if row["persistence_state"] == "present" and (row["observed_sha256"] != artifact["sha256"] or row["observed_size_bytes"] != artifact["size_bytes"])]
                if mismatched:
                    states.append("changed_bytes")
                    actions.append({"action": "quarantine_replica", "plane": plane, "replica_ids": [row["replica_id"] for row in mismatched]})
                elif not matching:
                    states.append("missing_replica")
                    actions.append({"action": "reconstruct_replica", "plane": plane, "artifact_id": artifact["artifact_id"], "source": "git" if plane == "library" else "authoritative_artifact"})
                else:
                    states.append(f"verified:{plane}")
                    if plane == "library" and any(row["index_state"] not in (None, "indexed") for row in matching):
                        states.append("persisted_not_indexed")
                        actions.append({"action": "wait_for_index", "replica_ids": [row["replica_id"] for row in matching if row["index_state"] not in (None, "indexed")]})

            pointers = list(self.connection.execute("SELECT * FROM pointers WHERE pointer_name=? ORDER BY plane, pointer_id", (logical_name,)))
            evidence["pointers"] = [dict(row) for row in pointers]
            for pointer in pointers:
                if pointer["target_artifact_id"] != artifact["artifact_id"] or pointer["declared_sha256"] != artifact["sha256"] or (pointer["observed_sha256"] is not None and pointer["observed_sha256"] != artifact["sha256"]):
                    states.append("stale_pointer")
                    actions.append({"action": "update_pointer", "pointer_id": pointer["pointer_id"], "target_artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]})

            receipt_rows = list(self.connection.execute(
                """
                SELECT * FROM receipts
                WHERE output_artifact_id IN (SELECT artifact_id FROM artifact_names WHERE logical_name=? )
                   OR output_sha256 IN (SELECT a.sha256 FROM artifacts a JOIN artifact_names n ON n.artifact_id=a.artifact_id WHERE n.logical_name=? )
                ORDER BY task_id, idempotency_key, receipt_id
                """, (logical_name, logical_name)))
            groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for row in receipt_rows:
                groups.setdefault((row["task_id"], row["idempotency_key"]), []).append(row)
            for (task_id, key), group in groups.items():
                outputs = sorted({row["output_sha256"] for row in group if row["terminal_state"] == "completed" and row["output_sha256"]})
                group_evidence = {"task_id": task_id, "idempotency_key": key, "receipt_ids": [row["receipt_id"] for row in group], "completed_output_sha256": outputs}
                evidence["receipt_groups"].append(group_evidence)
                if len(outputs) > 1:
                    states.append("conflicting_receipts")
                    actions.append({"action": "quarantine_receipt_group", "task_id": task_id, "idempotency_key": key, "receipt_ids": group_evidence["receipt_ids"]})

        states = sorted(set(states))
        if any(state in states for state in ("changed_bytes", "conflicting_receipts", "duplicate_logical_name")):
            decision = "quarantined"
            accepted_artifact_id = None
        elif "missing_artifact" in states:
            decision = "missing_authority"
        elif "missing_replica" in states:
            decision = "repair_required"
        elif "stale_pointer" in states:
            decision = "pointer_update_required"
        elif "persisted_not_indexed" in states:
            decision = "accepted_pending_index"
        else:
            decision = "accepted"

        body = {"schema_version": SCHEMA_VERSIONS["reconciliation_result"], "logical_name": logical_name, "decision": decision,
                "accepted_artifact_id": accepted_artifact_id, "states": states, "observed_at": observed_at, "evidence": evidence, "actions": actions}
        reconciliation_id = content_id("reconciliation", body)
        receipt = {**body, "reconciliation_id": reconciliation_id}
        errors = validate_contract("reconciliation_result", receipt)
        if errors:
            raise RegistryError("reconciliation_invalid:" + ",".join(errors))
        receipt_sha = canonical_sha256(receipt)
        with transaction(self.connection):
            self.connection.execute(
                "INSERT OR REPLACE INTO reconciliations(reconciliation_id, logical_name, decision, accepted_artifact_id, states_json, receipt_json, receipt_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (reconciliation_id, logical_name, decision, accepted_artifact_id, canonical_json_bytes(states).decode("utf-8"), canonical_json_bytes(receipt).decode("utf-8"), receipt_sha, observed_at),
            )
            self._event(occurred_at=observed_at, event_type="artifact_reconciled", subject_type="logical_artifact", subject_id=logical_name, payload={"reconciliation_id": reconciliation_id, "decision": decision, "receipt_sha256": receipt_sha})
        return {**receipt, "receipt_sha256": receipt_sha}

    def export_state(self) -> dict[str, Any]:
        def rows(table: str) -> list[dict[str, Any]]:
            return [dict(row) for row in self.connection.execute(f"SELECT * FROM {table}")]
        return {"schema_version": "superintendent-registry-export-v1", "artifacts": rows("artifacts"), "artifact_names": rows("artifact_names"),
                "replicas": rows("replicas"), "pointers": rows("pointers"), "receipts": rows("receipts"),
                "reconciliations": rows("reconciliations"), "events": rows("events"), "contract_records": rows("contract_records")}
