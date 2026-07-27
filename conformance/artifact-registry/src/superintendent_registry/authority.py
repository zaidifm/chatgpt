from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Callable

from .canonical import canonical_json_bytes, canonical_sha256, content_id
from .contracts import validate_contract
from .database import transaction

EventWriter = Callable[..., str]

TERMINAL_STATES = {"completed", "rejected", "cancelled", "quarantined"}
ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"proposed"},
    "proposed": {"approved", "rejected", "cancelled", "quarantined"},
    "approved": {"dispatched", "cancelled", "quarantined"},
    "dispatched": {"running", "unknown", "cancelled", "quarantined"},
    "running": {"unknown", "completed", "rejected", "cancelled", "quarantined"},
    "unknown": {"running", "completed", "cancelled", "quarantined"},
    "completed": set(),
    "rejected": set(),
    "cancelled": set(),
    "quarantined": set(),
}


class AuthorityError(RuntimeError):
    pass


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AuthorityError("timestamp_missing_timezone")
    return parsed.astimezone(dt.timezone.utc)


class AuthorityCore:
    def __init__(self, connection: sqlite3.Connection, event_writer: EventWriter):
        self.connection = connection
        self._event = event_writer

    def current_epoch(self) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='current_authority_epoch'"
        ).fetchone()
        return None if row is None else int(row["value"])

    def _require_current_epoch(self, epoch: int) -> None:
        current = self.current_epoch()
        if current is None:
            raise AuthorityError("authority_epoch_not_initialized")
        if epoch != current:
            raise AuthorityError(f"stale_authority_epoch:{epoch}:current:{current}")

    def issue_epoch(self, payload: dict[str, Any]) -> int:
        errors = validate_contract("authority_epoch", payload)
        if errors:
            raise AuthorityError("authority_epoch_invalid:" + ",".join(errors))
        epoch = payload["epoch"]
        existing = self.connection.execute(
            "SELECT payload_sha256 FROM authority_epochs WHERE epoch=?",
            (epoch,),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] == canonical_sha256(payload):
                return epoch
            raise AuthorityError("authority_epoch_conflict")
        current = self.current_epoch()
        expected = 1 if current is None else current + 1
        if epoch != expected:
            raise AuthorityError(f"authority_epoch_not_next:{epoch}:expected:{expected}")
        if payload.get("previous_epoch") not in (None, current):
            raise AuthorityError("authority_epoch_previous_mismatch")
        with transaction(self.connection):
            if current is not None:
                self.connection.execute(
                    "UPDATE authority_epochs SET status='retired' WHERE epoch=? AND status='active'",
                    (current,),
                )
            self.connection.execute(
                """
                INSERT INTO authority_epochs(
                    epoch, status, previous_epoch, issued_at, issued_by, reason,
                    payload_json, payload_sha256
                ) VALUES (?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    epoch,
                    current,
                    payload["issued_at"],
                    payload["issued_by"],
                    payload.get("reason"),
                    canonical_json_bytes(payload).decode("utf-8"),
                    canonical_sha256(payload),
                ),
            )
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('current_authority_epoch', ?)",
                (str(epoch),),
            )
            self._event(
                occurred_at=payload["issued_at"],
                event_type="authority_epoch_issued",
                subject_type="authority_epoch",
                subject_id=str(epoch),
                payload=payload,
            )
        return epoch

    def declare_capability(self, payload: dict[str, Any]) -> str:
        errors = validate_contract("capability_declaration", payload)
        if errors:
            raise AuthorityError("capability_invalid:" + ",".join(errors))
        declaration_id = content_id("capability-declaration", payload)
        with transaction(self.connection):
            self.connection.execute(
                "UPDATE capability_declarations SET active=0 WHERE capability_id=? AND executor=?",
                (payload["capability_id"], payload["executor"]),
            )
            self.connection.execute(
                """
                INSERT INTO capability_declarations(
                    declaration_id, capability_id, executor, operations_json,
                    limits_json, health_json, declared_at, payload_json,
                    payload_sha256, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(declaration_id) DO UPDATE SET
                    operations_json=excluded.operations_json,
                    limits_json=excluded.limits_json,
                    health_json=excluded.health_json,
                    declared_at=excluded.declared_at,
                    payload_json=excluded.payload_json,
                    payload_sha256=excluded.payload_sha256,
                    active=1
                """,
                (
                    declaration_id,
                    payload["capability_id"],
                    payload["executor"],
                    canonical_json_bytes(payload["operations"]).decode("utf-8"),
                    canonical_json_bytes(payload.get("limits", {})).decode("utf-8"),
                    canonical_json_bytes(payload.get("health", {})).decode("utf-8"),
                    payload["declared_at"],
                    canonical_json_bytes(payload).decode("utf-8"),
                    canonical_sha256(payload),
                ),
            )
            self._event(
                occurred_at=payload["declared_at"],
                event_type="capability_declared",
                subject_type="capability",
                subject_id=declaration_id,
                payload=payload,
            )
        return declaration_id

    def _task(self, task_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise AuthorityError(f"task_not_found:{task_id}")
        return row

    def create_task(self, payload: dict[str, Any], created_at: str) -> str:
        errors = validate_contract("task", payload)
        if errors:
            raise AuthorityError("task_invalid:" + ",".join(errors))
        self._require_current_epoch(payload["authority_epoch"])
        task_id = payload["task_id"]
        input_bytes = canonical_json_bytes(payload["input"])
        input_sha = canonical_sha256(payload["input"])
        with transaction(self.connection):
            existing_key = self.connection.execute(
                "SELECT * FROM tasks WHERE idempotency_key=?",
                (payload["idempotency_key"],),
            ).fetchone()
            if existing_key is not None:
                same = (
                    existing_key["task_id"] == task_id
                    and existing_key["authority_epoch"] == payload["authority_epoch"]
                    and existing_key["capability"] == payload["capability"]
                    and existing_key["operation"] == payload["operation"]
                    and existing_key["input_sha256"] == input_sha
                )
                if same:
                    return existing_key["task_id"]
                raise AuthorityError("idempotency_key_conflict")
            self.connection.execute(
                """
                INSERT INTO tasks(
                    task_id, idempotency_key, authority_epoch, capability,
                    operation, input_json, input_sha256, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                """,
                (
                    task_id,
                    payload["idempotency_key"],
                    payload["authority_epoch"],
                    payload["capability"],
                    payload["operation"],
                    input_bytes.decode("utf-8"),
                    input_sha,
                    created_at,
                    created_at,
                ),
            )
            self._append_transition_in_tx(
                task_id=task_id,
                from_state=None,
                to_state="proposed",
                occurred_at=created_at,
                actor="task-intake",
                evidence={"task_payload_sha256": canonical_sha256(payload)},
            )
        return task_id

    def _transition_identity(
        self,
        *,
        task_id: str,
        to_state: str,
        occurred_at: str,
        actor: str,
        evidence: dict[str, Any],
    ) -> str:
        return content_id(
            "transition",
            {
                "task_id": task_id,
                "to_state": to_state,
                "occurred_at": occurred_at,
                "actor": actor,
                "evidence": evidence,
            },
        )

    def _append_transition_in_tx(
        self,
        *,
        task_id: str,
        from_state: str | None,
        to_state: str,
        occurred_at: str,
        actor: str,
        evidence: dict[str, Any],
    ) -> str:
        transition_id = self._transition_identity(
            task_id=task_id,
            to_state=to_state,
            occurred_at=occurred_at,
            actor=actor,
            evidence=evidence,
        )
        existing = self.connection.execute(
            "SELECT transition_id FROM task_transitions WHERE transition_id=?",
            (transition_id,),
        ).fetchone()
        if existing is not None:
            return transition_id
        event_id = self._event(
            occurred_at=occurred_at,
            event_type="task_transition",
            subject_type="task",
            subject_id=task_id,
            payload={
                "transition_id": transition_id,
                "from_state": from_state,
                "to_state": to_state,
                "actor": actor,
                "evidence": evidence,
            },
        )
        self.connection.execute(
            """
            INSERT INTO task_transitions(
                transition_id, task_id, from_state, to_state, occurred_at,
                actor, evidence_json, evidence_sha256, event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                task_id,
                from_state,
                to_state,
                occurred_at,
                actor,
                canonical_json_bytes(evidence).decode("utf-8"),
                canonical_sha256(evidence),
                event_id,
            ),
        )
        self.connection.execute(
            "UPDATE tasks SET state=?, updated_at=? WHERE task_id=?",
            (to_state, occurred_at, task_id),
        )
        return transition_id

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        occurred_at: str,
        actor: str,
        evidence: dict[str, Any] | None = None,
        expected_from: str | None = None,
    ) -> str:
        evidence = evidence or {}
        transition_id = self._transition_identity(
            task_id=task_id,
            to_state=to_state,
            occurred_at=occurred_at,
            actor=actor,
            evidence=evidence,
        )
        if self.connection.execute(
            "SELECT 1 FROM task_transitions WHERE transition_id=?",
            (transition_id,),
        ).fetchone() is not None:
            return transition_id
        with transaction(self.connection):
            task = self._task(task_id)
            self._require_current_epoch(task["authority_epoch"])
            current = task["state"]
            if expected_from is not None and current != expected_from:
                raise AuthorityError(f"task_state_mismatch:{current}:expected:{expected_from}")
            if to_state not in ALLOWED_TRANSITIONS.get(current, set()):
                raise AuthorityError(f"transition_not_allowed:{current}->{to_state}")
            return self._append_transition_in_tx(
                task_id=task_id,
                from_state=current,
                to_state=to_state,
                occurred_at=occurred_at,
                actor=actor,
                evidence=evidence,
            )

    def approve_task(self, task_id: str, occurred_at: str, actor: str = "owner") -> str:
        return self.transition_task(
            task_id=task_id,
            to_state="approved",
            occurred_at=occurred_at,
            actor=actor,
            evidence={"approval": "explicit"},
            expected_from="proposed",
        )

    def _capability_for(self, capability_id: str, executor: str, operation: str) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT * FROM capability_declarations
            WHERE capability_id=? AND executor=? AND active=1
            ORDER BY declared_at DESC, declaration_id DESC LIMIT 1
            """,
            (capability_id, executor),
        ).fetchone()
        if row is None:
            raise AuthorityError("capability_not_declared")
        operations = json.loads(row["operations_json"])
        health = json.loads(row["health_json"])
        if operation not in operations:
            raise AuthorityError("operation_not_declared")
        if health.get("healthy") is not True:
            raise AuthorityError("capability_unhealthy")
        return row

    def issue_grant(self, payload: dict[str, Any], created_at: str) -> str:
        errors = validate_contract("work_grant", payload)
        if errors:
            raise AuthorityError("grant_invalid:" + ",".join(errors))
        task = self._task(payload["task_id"])
        self._require_current_epoch(payload["authority_epoch"])
        if task["authority_epoch"] != payload["authority_epoch"]:
            raise AuthorityError("grant_task_epoch_mismatch")
        if task["state"] != "approved":
            raise AuthorityError(f"grant_task_not_approved:{task['state']}")
        if parse_time(payload["expires_at"]) <= parse_time(payload["issued_at"]):
            raise AuthorityError("grant_expiry_not_after_issue")
        self._capability_for(task["capability"], payload["executor"], task["operation"])
        lease_id = payload.get("lease_id") or content_id(
            "lease",
            {"grant_id": payload["grant_id"], "executor": payload["executor"], "epoch": payload["authority_epoch"]},
        )
        existing = self.connection.execute(
            "SELECT * FROM work_grants WHERE grant_id=?",
            (payload["grant_id"],),
        ).fetchone()
        if existing is not None:
            same = (
                existing["task_id"] == payload["task_id"]
                and existing["authority_epoch"] == payload["authority_epoch"]
                and existing["executor"] == payload["executor"]
                and existing["lease_id"] == lease_id
                and existing["issued_at"] == payload["issued_at"]
                and existing["expires_at"] == payload["expires_at"]
                and json.loads(existing["permitted_actions_json"]) == payload["permitted_actions"]
                and json.loads(existing["forbidden_actions_json"]) == payload["forbidden_actions"]
            )
            if same:
                return payload["grant_id"]
            raise AuthorityError("grant_id_conflict")
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO work_grants(
                    grant_id, task_id, authority_epoch, executor, lease_id,
                    issued_at, expires_at, permitted_actions_json,
                    forbidden_actions_json, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'issued', ?)
                """,
                (
                    payload["grant_id"], payload["task_id"], payload["authority_epoch"],
                    payload["executor"], lease_id, payload["issued_at"], payload["expires_at"],
                    canonical_json_bytes(payload["permitted_actions"]).decode("utf-8"),
                    canonical_json_bytes(payload["forbidden_actions"]).decode("utf-8"), created_at,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO leases(lease_id, grant_id, executor, authority_epoch, issued_at, expires_at, state)
                VALUES (?, ?, ?, ?, ?, ?, 'issued')
                """,
                (lease_id, payload["grant_id"], payload["executor"], payload["authority_epoch"], payload["issued_at"], payload["expires_at"]),
            )
            self._event(
                occurred_at=created_at,
                event_type="work_grant_issued",
                subject_type="work_grant",
                subject_id=payload["grant_id"],
                payload={**payload, "lease_id": lease_id},
            )
        return payload["grant_id"]

    def _grant(self, grant_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM work_grants WHERE grant_id=?", (grant_id,)).fetchone()
        if row is None:
            raise AuthorityError(f"grant_not_found:{grant_id}")
        return row

    def _ensure_grant_live(self, grant: sqlite3.Row, at: str) -> None:
        self._require_current_epoch(grant["authority_epoch"])
        if parse_time(at) >= parse_time(grant["expires_at"]):
            raise AuthorityError("grant_expired")

    def accept_grant(self, grant_id: str, accepted_at: str) -> str:
        with transaction(self.connection):
            grant = self._grant(grant_id)
            self._ensure_grant_live(grant, accepted_at)
            if grant["state"] == "accepted":
                return grant["lease_id"]
            if grant["state"] != "issued":
                raise AuthorityError(f"grant_not_issuable:{grant['state']}")
            self.connection.execute("UPDATE work_grants SET state='accepted' WHERE grant_id=?", (grant_id,))
            self.connection.execute("UPDATE leases SET state='active', last_heartbeat_at=? WHERE lease_id=?", (accepted_at, grant["lease_id"]))
            task = self._task(grant["task_id"])
            if task["state"] != "approved":
                raise AuthorityError(f"task_not_approved_at_accept:{task['state']}")
            self._append_transition_in_tx(task_id=task["task_id"], from_state=task["state"], to_state="dispatched", occurred_at=accepted_at, actor=grant["executor"], evidence={"grant_id": grant_id, "lease_id": grant["lease_id"]})
            self._event(occurred_at=accepted_at, event_type="work_grant_accepted", subject_type="work_grant", subject_id=grant_id, payload={"lease_id": grant["lease_id"], "executor": grant["executor"]})
            return grant["lease_id"]

    def start_task(self, grant_id: str, started_at: str) -> str:
        with transaction(self.connection):
            grant = self._grant(grant_id)
            self._ensure_grant_live(grant, started_at)
            if grant["state"] == "active":
                return grant["task_id"]
            if grant["state"] != "accepted":
                raise AuthorityError(f"grant_not_accepted:{grant['state']}")
            lease = self.connection.execute("SELECT * FROM leases WHERE lease_id=?", (grant["lease_id"],)).fetchone()
            if lease is None or lease["state"] != "active":
                raise AuthorityError("lease_not_active")
            self.connection.execute("UPDATE work_grants SET state='active' WHERE grant_id=?", (grant_id,))
            task = self._task(grant["task_id"])
            if task["state"] != "dispatched":
                raise AuthorityError(f"task_not_dispatched_at_start:{task['state']}")
            self._append_transition_in_tx(task_id=task["task_id"], from_state=task["state"], to_state="running", occurred_at=started_at, actor=grant["executor"], evidence={"grant_id": grant_id, "lease_id": grant["lease_id"]})
            self._event(occurred_at=started_at, event_type="task_started_under_grant", subject_type="task", subject_id=task["task_id"], payload={"grant_id": grant_id, "lease_id": grant["lease_id"], "executor": grant["executor"]})
            return task["task_id"]

    def heartbeat(self, lease_id: str, heartbeat_at: str) -> None:
        with transaction(self.connection):
            lease = self.connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lease is None:
                raise AuthorityError("lease_not_found")
            self._require_current_epoch(lease["authority_epoch"])
            if lease["state"] != "active":
                raise AuthorityError(f"lease_not_active:{lease['state']}")
            if parse_time(heartbeat_at) >= parse_time(lease["expires_at"]):
                raise AuthorityError("lease_expired")
            self.connection.execute("UPDATE leases SET last_heartbeat_at=? WHERE lease_id=?", (heartbeat_at, lease_id))
            self._event(occurred_at=heartbeat_at, event_type="lease_heartbeat", subject_type="lease", subject_id=lease_id, payload={"executor": lease["executor"]})

    def expire_leases(self, now: str) -> list[str]:
        expired: list[str] = []
        with transaction(self.connection):
            rows = list(self.connection.execute("SELECT * FROM leases WHERE state IN ('issued','active') ORDER BY expires_at, lease_id"))
            for lease in rows:
                if parse_time(lease["expires_at"]) > parse_time(now):
                    continue
                self.connection.execute("UPDATE leases SET state='expired' WHERE lease_id=?", (lease["lease_id"],))
                self.connection.execute("UPDATE work_grants SET state='expired' WHERE grant_id=? AND state NOT IN ('completed','revoked')", (lease["grant_id"],))
                grant = self._grant(lease["grant_id"])
                task = self._task(grant["task_id"])
                if task["state"] in {"dispatched", "running"}:
                    self._append_transition_in_tx(task_id=task["task_id"], from_state=task["state"], to_state="unknown", occurred_at=now, actor="lease-reconciler", evidence={"lease_id": lease["lease_id"], "reason": "expired"})
                self._event(occurred_at=now, event_type="lease_expired", subject_type="lease", subject_id=lease["lease_id"], payload={"grant_id": lease["grant_id"], "task_id": grant["task_id"]})
                expired.append(lease["lease_id"])
        return expired

    def revoke_lease(self, lease_id: str, revoked_at: str, reason: str, actor: str = "owner") -> None:
        with transaction(self.connection):
            lease = self.connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if lease is None:
                raise AuthorityError("lease_not_found")
            if lease["state"] in {"revoked", "expired", "released"}:
                return
            self.connection.execute("UPDATE leases SET state='revoked', revocation_reason=? WHERE lease_id=?", (reason, lease_id))
            self.connection.execute("UPDATE work_grants SET state='revoked' WHERE grant_id=?", (lease["grant_id"],))
            grant = self._grant(lease["grant_id"])
            task = self._task(grant["task_id"])
            if task["state"] in {"dispatched", "running"}:
                self._append_transition_in_tx(task_id=task["task_id"], from_state=task["state"], to_state="unknown", occurred_at=revoked_at, actor=actor, evidence={"lease_id": lease_id, "reason": reason})
            self._event(occurred_at=revoked_at, event_type="lease_revoked", subject_type="lease", subject_id=lease_id, payload={"reason": reason, "actor": actor})

    def complete_task(self, task_id: str, receipt_id: str, completed_at: str, actor: str) -> str:
        with transaction(self.connection):
            task = self._task(task_id)
            self._require_current_epoch(task["authority_epoch"])
            if task["state"] == "completed" and task["terminal_receipt_id"] == receipt_id:
                return receipt_id
            if task["state"] not in {"running", "unknown"}:
                raise AuthorityError(f"task_not_completable:{task['state']}")
            receipt = self.connection.execute("SELECT * FROM receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            if receipt is None:
                raise AuthorityError("terminal_receipt_not_found")
            if receipt["task_id"] != task_id or receipt["idempotency_key"] != task["idempotency_key"]:
                raise AuthorityError("terminal_receipt_task_mismatch")
            if receipt["authority_epoch"] != task["authority_epoch"]:
                raise AuthorityError("terminal_receipt_epoch_mismatch")
            if receipt["terminal_state"] != "completed":
                raise AuthorityError("terminal_receipt_not_completed")
            self.connection.execute("UPDATE tasks SET terminal_receipt_id=? WHERE task_id=?", (receipt_id, task_id))
            self._append_transition_in_tx(task_id=task_id, from_state=task["state"], to_state="completed", occurred_at=completed_at, actor=actor, evidence={"receipt_id": receipt_id, "output_sha256": receipt["output_sha256"]})
            grants = list(self.connection.execute("SELECT * FROM work_grants WHERE task_id=? AND state IN ('accepted','active')", (task_id,)))
            for grant in grants:
                self.connection.execute("UPDATE work_grants SET state='completed' WHERE grant_id=?", (grant["grant_id"],))
                self.connection.execute("UPDATE leases SET state='released' WHERE lease_id=?", (grant["lease_id"],))
            self._event(occurred_at=completed_at, event_type="task_completed", subject_type="task", subject_id=task_id, payload={"receipt_id": receipt_id, "executor": actor})
            return receipt_id

    def verify_materialized_state(self) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        for task in self.connection.execute("SELECT * FROM tasks ORDER BY task_id"):
            transitions = list(self.connection.execute("SELECT * FROM task_transitions WHERE task_id=? ORDER BY sequence", (task["task_id"],)))
            previous: str | None = None
            for transition in transitions:
                if transition["from_state"] != previous:
                    errors.append({"task_id": task["task_id"], "transition_id": transition["transition_id"], "error": "from_state_mismatch", "expected": previous, "observed": transition["from_state"]})
                if canonical_sha256(json.loads(transition["evidence_json"])) != transition["evidence_sha256"]:
                    errors.append({"task_id": task["task_id"], "transition_id": transition["transition_id"], "error": "evidence_hash_mismatch"})
                previous = transition["to_state"]
            if previous != task["state"]:
                errors.append({"task_id": task["task_id"], "error": "materialized_state_mismatch", "expected": previous, "observed": task["state"]})
        active_epochs = list(self.connection.execute("SELECT epoch FROM authority_epochs WHERE status='active'"))
        current = self.current_epoch()
        if len(active_epochs) > 1 or (current is not None and [row["epoch"] for row in active_epochs] != [current]):
            errors.append({"error": "authority_epoch_materialization_mismatch", "current": current, "active": [row["epoch"] for row in active_epochs]})
        return {"valid": not errors, "errors": errors, "tasks": self.connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]}
