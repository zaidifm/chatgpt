from __future__ import annotations

import argparse
import base64
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from superintendent_registry.canonical import canonical_json_bytes, sha256_bytes
from superintendent_registry.service import ResidentRuntime, ServiceConfig

TOKEN = "synthetic-resident-scenario-token-0001"
SOURCE_COMMIT = "RESIDENT-SERVICE-SCENARIO-COMMIT"
T0 = "2026-07-27T10:00:00Z"
T1 = "2026-07-27T10:01:00Z"
T2 = "2026-07-27T10:02:00Z"
T3 = "2026-07-27T10:03:00Z"
T4 = "2026-07-27T10:04:00Z"
T5 = "2026-07-27T10:05:00Z"
T6 = "2026-07-27T10:06:00Z"
T7 = "2026-07-27T10:07:00Z"
T8 = "2026-07-27T10:08:00Z"
T9 = "2026-07-27T10:09:00Z"
FUTURE = "2030-07-27T10:30:00Z"


def request(base: str, method: str, path: str, body=None):
    data = None if body is None else canonical_json_bytes(body)
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def task_payload(task_id: str, key: str):
    return {
        "schema_version": "superintendent-task-v1",
        "task_id": task_id,
        "idempotency_key": key,
        "authority_epoch": 1,
        "capability": "canonical-json-sha256-v1",
        "operation": "canonicalize-and-hash",
        "input": {"message": task_id, "scenario": "resident-service-v1"},
    }


def grant_payload(task_id: str, expires: str):
    return {
        "schema_version": "superintendent-work-grant-v1",
        "grant_id": f"GRANT-{task_id}",
        "task_id": task_id,
        "authority_epoch": 1,
        "executor": "resident-scenario-executor",
        "lease_id": f"LEASE-{task_id}",
        "issued_at": T2,
        "expires_at": expires,
        "permitted_actions": ["canonicalize-and-hash"],
        "forbidden_actions": ["credential-read", "network-write"],
    }


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = ResidentRuntime(
            ServiceConfig(
                db_path=root / "resident.sqlite3",
                bearer_token=TOKEN,
                host="127.0.0.1",
                port=0,
                lock_path=root / "resident.lock",
                snapshot_dir=root / "snapshots",
                scheduler_interval_seconds=0,
                source_commit=SOURCE_COMMIT,
                startup_reconcile=True,
            ),
            clock=lambda: T0,
        )
        runtime.start(background=True)
        try:
            base = runtime.base_url
            request(base, "POST", "/v1/epochs", {
                "schema_version": "superintendent-authority-epoch-v1",
                "epoch": 1,
                "issued_at": T0,
                "issued_by": "Ali-Zaidi",
                "reason": "deterministic resident service scenario",
            })
            request(base, "POST", "/v1/capabilities", {
                "schema_version": "superintendent-capability-declaration-v1",
                "capability_id": "canonical-json-sha256-v1",
                "executor": "resident-scenario-executor",
                "operations": ["canonicalize-and-hash"],
                "limits": {"max_input_bytes": 4096},
                "health": {"healthy": True},
                "declared_at": T0,
            })

            completed_task = task_payload("TASK-COMPLETE", "idem-complete")
            request(base, "POST", "/v1/tasks", {"task": completed_task, "created_at": T1})
            request(base, "POST", "/v1/tasks/TASK-COMPLETE/approve", {"at": T2, "actor": "owner"})
            complete_grant = grant_payload("TASK-COMPLETE", FUTURE)
            request(base, "POST", "/v1/grants", {"grant": complete_grant, "created_at": T2})
            request(base, "POST", "/v1/grants/GRANT-TASK-COMPLETE/accept", {"at": T3})
            request(base, "POST", "/v1/grants/GRANT-TASK-COMPLETE/start", {"at": T4})
            output_bytes = canonical_json_bytes(completed_task["input"]) + b"\n"
            artifact = request(base, "POST", "/v1/artifacts", {
                "logical_name": "resident-scenario/completed-output",
                "content_base64": base64.b64encode(output_bytes).decode("ascii"),
                "media_type": "application/json",
                "authority_class": "task-output",
                "source": {"task_id": "TASK-COMPLETE", "source_commit": SOURCE_COMMIT},
                "created_at": T5,
            })
            receipt = {
                "schema_version": "superintendent-terminal-receipt-v1",
                "receipt_id": "RECEIPT-COMPLETE",
                "task_id": "TASK-COMPLETE",
                "idempotency_key": "idem-complete",
                "authority_epoch": 1,
                "executor": "resident-scenario-executor",
                "state": "completed",
                "created_at": T5,
                "output_artifact_id": artifact["artifact_id"],
                "output_sha256": artifact["sha256"],
                "source_commit": SOURCE_COMMIT,
            }
            request(base, "POST", "/v1/receipts", {"receipt": receipt, "created_at": T5})
            request(base, "POST", "/v1/tasks/TASK-COMPLETE/complete", {
                "receipt_id": "RECEIPT-COMPLETE",
                "at": T6,
                "actor": "resident-scenario-executor",
            })

            lost_task = task_payload("TASK-LOST", "idem-lost")
            request(base, "POST", "/v1/tasks", {"task": lost_task, "created_at": T1})
            request(base, "POST", "/v1/tasks/TASK-LOST/approve", {"at": T2, "actor": "owner"})
            lost_grant = grant_payload("TASK-LOST", T7)
            request(base, "POST", "/v1/grants", {"grant": lost_grant, "created_at": T2})
            request(base, "POST", "/v1/grants/GRANT-TASK-LOST/accept", {"at": T3})
            request(base, "POST", "/v1/grants/GRANT-TASK-LOST/start", {"at": T4})
            tick = request(base, "POST", "/v1/scheduler/tick", {"at": T8})
            assert tick["expired_lease_ids"] == ["LEASE-TASK-LOST"]

            fixture_bytes = b"resident connector observation fixture\n"
            fixture = request(base, "POST", "/v1/artifacts", {
                "logical_name": "resident-scenario/connector-fixture",
                "content_base64": base64.b64encode(fixture_bytes).decode("ascii"),
                "media_type": "text/plain",
                "authority_class": "git-authoritative",
                "source": {"repository": "zaidifm/chatgpt", "commit": SOURCE_COMMIT},
                "created_at": T5,
            })
            for plane in ("git", "library"):
                request(base, "POST", "/v1/observations/replicas", {
                    "artifact_id": fixture["artifact_id"],
                    "plane": plane,
                    "locator": {"plane": plane, "stable_id": f"resident-{plane}-fixture"},
                    "persistence_state": "present",
                    "index_state": "indexed" if plane == "library" else None,
                    "observed_sha256": fixture["sha256"],
                    "observed_size_bytes": fixture["size_bytes"],
                    "observed_at": T8,
                    "required": True,
                })
            reconciliation = request(base, "POST", "/v1/reconcile", {
                "logical_name": "resident-scenario/connector-fixture",
                "required_planes": ["git", "library"],
                "observed_at": T8,
            })
            assert reconciliation["decision"] == "accepted"

            snapshot_receipt = request(base, "POST", "/v1/snapshots", {"observed_at": T9})
            snapshot_path = root / "snapshots" / snapshot_receipt["snapshot_path"]
            snapshot_bytes = snapshot_path.read_bytes()
            registry_export = request(base, "GET", "/v1/export")
            registry_bytes = canonical_json_bytes(registry_export) + b"\n"
            completed_row = request(base, "GET", "/v1/tasks/TASK-COMPLETE")
            lost_row = request(base, "GET", "/v1/tasks/TASK-LOST")
            status = request(base, "GET", "/v1/status")
        finally:
            runtime.stop()

        summary = {
            "schema_version": "superintendent-resident-scenario-summary-v1",
            "source_commit": SOURCE_COMMIT,
            "snapshot_sha256": sha256_bytes(snapshot_bytes),
            "registry_export_sha256": sha256_bytes(registry_bytes),
            "journal": status["verification"]["journal"],
            "materialized": status["verification"]["materialized"],
            "tasks": {
                "TASK-COMPLETE": completed_row["state"],
                "TASK-LOST": lost_row["state"],
            },
            "reconciliation_decision": reconciliation["decision"],
            "fixture_sha256": fixture["sha256"],
        }
        summary_bytes = canonical_json_bytes(summary) + b"\n"
        files = {
            "resident-snapshot.json": snapshot_bytes,
            "registry-export.json": registry_bytes,
            "scenario-summary.json": summary_bytes,
        }
        manifest = {
            "schema_version": "superintendent-resident-evidence-manifest-v1",
            "files": {
                name: {"sha256": sha256_bytes(data), "size_bytes": len(data)}
                for name, data in sorted(files.items())
            },
        }
        files["manifest.json"] = canonical_json_bytes(manifest) + b"\n"
        for name, data in files.items():
            (output / name).write_bytes(data)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
