from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from superintendent_registry import Registry, connect
from superintendent_registry.authority import AuthorityError
from superintendent_registry.canonical import canonical_json_bytes, sha256_bytes

TIMES = {
    "epoch": "2026-07-27T10:00:00Z",
    "task": "2026-07-27T10:01:00Z",
    "approve": "2026-07-27T10:02:00Z",
    "grant": "2026-07-27T10:03:00Z",
    "accept": "2026-07-27T10:04:00Z",
    "start": "2026-07-27T10:05:00Z",
    "heartbeat": "2026-07-27T10:06:00Z",
    "complete": "2026-07-27T10:07:00Z",
    "task2": "2026-07-27T10:08:00Z",
    "approve2": "2026-07-27T10:09:00Z",
    "grant2": "2026-07-27T10:10:00Z",
    "accept2": "2026-07-27T10:11:00Z",
    "start2": "2026-07-27T10:12:00Z",
    "expire2": "2026-07-27T10:16:00Z",
    "epoch2": "2026-07-27T10:17:00Z",
}


def task(task_id: str, key: str, epoch: int, message: str) -> dict:
    return {
        "schema_version": "superintendent-task-v1",
        "task_id": task_id,
        "idempotency_key": key,
        "authority_epoch": epoch,
        "capability": "canonical-json-sha256-v1",
        "operation": "canonicalize-and-hash",
        "input": {"message": message},
    }


def grant(grant_id: str, task_id: str, lease_id: str, issued: str, expires: str, epoch: int = 1) -> dict:
    return {
        "schema_version": "superintendent-work-grant-v1",
        "grant_id": grant_id,
        "task_id": task_id,
        "authority_epoch": epoch,
        "executor": "github-actions",
        "lease_id": lease_id,
        "issued_at": issued,
        "expires_at": expires,
        "permitted_actions": ["canonicalize-and-hash"],
        "forbidden_actions": ["network-write", "credential-read"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp:
        connection = connect(Path(temp) / "authority.sqlite3")
        registry = Registry(connection)
        core = registry.authority
        core.issue_epoch({
            "schema_version": "superintendent-authority-epoch-v1",
            "epoch": 1,
            "issued_at": TIMES["epoch"],
            "issued_by": "Ali-Zaidi",
            "reason": "deterministic authority scenario",
        })
        core.declare_capability({
            "schema_version": "superintendent-capability-declaration-v1",
            "capability_id": "canonical-json-sha256-v1",
            "executor": "github-actions",
            "operations": ["canonicalize-and-hash"],
            "limits": {"max_input_bytes": 4096},
            "health": {"healthy": True},
            "declared_at": TIMES["epoch"],
        })

        core.create_task(task("AUTH-TASK-1", "auth-task-1", 1, "complete me"), TIMES["task"])
        core.approve_task("AUTH-TASK-1", TIMES["approve"])
        core.issue_grant(grant("AUTH-GRANT-1", "AUTH-TASK-1", "AUTH-LEASE-1", TIMES["grant"], "2026-07-27T10:15:00Z"), TIMES["grant"])
        core.accept_grant("AUTH-GRANT-1", TIMES["accept"])
        core.start_task("AUTH-GRANT-1", TIMES["start"])
        core.heartbeat("AUTH-LEASE-1", TIMES["heartbeat"])
        artifact = registry.register_artifact_bytes(logical_name="authority-scenario/output", data=b'{"message":"complete me"}\n', media_type="application/json", authority_class="task-output", source={"task_id": "AUTH-TASK-1"}, created_at=TIMES["complete"])
        registry.register_terminal_receipt({
            "schema_version": "superintendent-terminal-receipt-v1",
            "receipt_id": "AUTH-RECEIPT-1",
            "task_id": "AUTH-TASK-1",
            "idempotency_key": "auth-task-1",
            "authority_epoch": 1,
            "executor": "github-actions",
            "state": "completed",
            "created_at": TIMES["complete"],
            "output_artifact_id": artifact["artifact_id"],
            "output_sha256": artifact["sha256"],
            "source_commit": "AUTHORITY-SCENARIO",
        }, TIMES["complete"])
        core.complete_task("AUTH-TASK-1", "AUTH-RECEIPT-1", TIMES["complete"], "github-actions")

        core.create_task(task("AUTH-TASK-2", "auth-task-2", 1, "lose lease"), TIMES["task2"])
        core.approve_task("AUTH-TASK-2", TIMES["approve2"])
        core.issue_grant(grant("AUTH-GRANT-2", "AUTH-TASK-2", "AUTH-LEASE-2", TIMES["grant2"], "2026-07-27T10:15:00Z"), TIMES["grant2"])
        core.accept_grant("AUTH-GRANT-2", TIMES["accept2"])
        core.start_task("AUTH-GRANT-2", TIMES["start2"])
        expired = core.expire_leases(TIMES["expire2"])

        core.issue_epoch({
            "schema_version": "superintendent-authority-epoch-v1",
            "epoch": 2,
            "previous_epoch": 1,
            "issued_at": TIMES["epoch2"],
            "issued_by": "Ali-Zaidi",
            "reason": "deterministic promotion",
        })
        stale_rejected = False
        try:
            core.transition_task(task_id="AUTH-TASK-2", to_state="cancelled", occurred_at="2026-07-27T10:18:00Z", actor="old-primary", evidence={"epoch": 1})
        except AuthorityError as exc:
            stale_rejected = str(exc).startswith("stale_authority_epoch")
        if not stale_rejected:
            raise AssertionError("old epoch write was not rejected")

        chain = registry.verify_event_chain()
        materialized = core.verify_materialized_state()
        if not chain["valid"] or not materialized["valid"]:
            raise AssertionError({"chain": chain, "materialized": materialized})
        export = registry.export_state()
        tasks = {row["task_id"]: row["state"] for row in connection.execute("SELECT task_id, state FROM tasks ORDER BY task_id")}
        summary = {
            "schema_version": "superintendent-authority-scenario-v1",
            "current_epoch": core.current_epoch(),
            "task_states": tasks,
            "expired_lease_ids": expired,
            "stale_epoch_write_rejected": stale_rejected,
            "event_chain": chain,
            "materialized_state": materialized,
        }
        connection.close()

    summary_bytes = canonical_json_bytes(summary) + b"\n"
    export_bytes = canonical_json_bytes(export) + b"\n"
    (output / "authority-summary.json").write_bytes(summary_bytes)
    (output / "registry-export.json").write_bytes(export_bytes)
    manifest = {
        "schema_version": "superintendent-authority-scenario-manifest-v1",
        "authority_summary_sha256": sha256_bytes(summary_bytes),
        "registry_export_sha256": sha256_bytes(export_bytes),
        "event_chain_head": chain["head"],
        "event_count": chain["events"],
        "current_epoch": 2,
        "task_states": tasks,
        "stale_epoch_write_rejected": stale_rejected,
    }
    (output / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
