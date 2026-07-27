from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from superintendent_registry import Registry, connect
from superintendent_registry.canonical import canonical_json_bytes, sha256_bytes

NOW = "2026-07-27T09:00:00Z"
DATA = b"Superintendent deterministic artifact registry fixture v1\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp:
        connection = connect(Path(temp) / "registry.sqlite3")
        registry = Registry(connection)
        artifact = registry.register_artifact_bytes(
            logical_name="conformance/deterministic-current",
            data=DATA,
            media_type="text/plain",
            authority_class="git-authoritative",
            source={"repository": "zaidifm/chatgpt", "commit": "DETERMINISTIC-COMMIT", "path": "conformance/artifact-registry/fixtures/deterministic.txt"},
            created_at=NOW,
        )
        for plane, locator, index_state in (
            ("git", {"repository": "zaidifm/chatgpt", "commit": "DETERMINISTIC-COMMIT", "path": "conformance/artifact-registry/fixtures/deterministic.txt"}, None),
            ("library", {"library_path": "/Project Orientation/Superintendent Architecture/Conformance/deterministic.txt", "library_file_id": "libfile-deterministic"}, "indexed"),
        ):
            registry.register_replica(
                artifact_id=artifact["artifact_id"],
                plane=plane,
                locator=locator,
                persistence_state="present",
                observed_at=NOW,
                observed_sha256=artifact["sha256"],
                observed_size_bytes=artifact["size_bytes"],
                index_state=index_state,
            )
        registry.register_pointer(
            pointer_name="conformance/deterministic-current",
            plane="library",
            locator={"library_path": "/Project Orientation/Superintendent Architecture/CURRENT_ARTIFACT_REGISTRY.json"},
            target_artifact_id=artifact["artifact_id"],
            declared_sha256=artifact["sha256"],
            observed_sha256=artifact["sha256"],
            observed_at=NOW,
        )
        registry.register_terminal_receipt({
            "schema_version": "superintendent-terminal-receipt-v1",
            "receipt_id": "deterministic-terminal-receipt-v1",
            "task_id": "DETERMINISTIC-TASK-1",
            "idempotency_key": "deterministic-task-1",
            "authority_epoch": 1,
            "executor": "portable-python",
            "state": "completed",
            "created_at": NOW,
            "output_artifact_id": artifact["artifact_id"],
            "output_sha256": artifact["sha256"],
            "source_commit": "DETERMINISTIC-COMMIT",
        }, NOW)
        reconciliation = registry.reconcile(logical_name="conformance/deterministic-current", required_planes=["git", "library"], observed_at=NOW)
        state = registry.export_state()
        connection.close()

    receipt_bytes = canonical_json_bytes(reconciliation) + b"\n"
    state_bytes = canonical_json_bytes(state) + b"\n"
    (output / "reconciliation.json").write_bytes(receipt_bytes)
    (output / "registry-export.json").write_bytes(state_bytes)
    manifest = {
        "schema_version": "superintendent-deterministic-scenario-manifest-v1",
        "reconciliation_sha256": sha256_bytes(receipt_bytes),
        "registry_export_sha256": sha256_bytes(state_bytes),
        "decision": reconciliation["decision"],
        "receipt_sha256": reconciliation["receipt_sha256"],
    }
    (output / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
