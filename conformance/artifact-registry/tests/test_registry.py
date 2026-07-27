from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from superintendent_registry import Registry, connect
from superintendent_registry.canonical import canonical_sha256
from superintendent_registry.registry import observe_file

NOW = "2026-07-27T08:00:00Z"


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = connect(self.root / "registry.sqlite3")
        self.registry = Registry(self.connection)
        self.git = self.root / "git.txt"
        self.git.write_text("immutable artifact v1\n")
        self.artifact = self.registry.register_artifact_file(
            logical_name="demo/current",
            path=self.git,
            authority_class="git-authoritative",
            source={"repository": "zaidifm/chatgpt", "commit": "abc123", "path": "demo/artifact.txt"},
            created_at=NOW,
        )
        self.artifact_id = self.artifact["artifact_id"]

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def replica(self, plane: str, path: Path, index_state="indexed"):
        observation = observe_file(path)
        return self.registry.register_replica(
            artifact_id=self.artifact_id,
            plane=plane,
            locator={"path": str(path)},
            persistence_state="present",
            observed_at=NOW,
            observed_sha256=observation.sha256,
            observed_size_bytes=observation.size_bytes,
            index_state=index_state,
        )

    def library_copy(self, content: bytes | None = None) -> Path:
        path = self.root / "library.txt"
        path.write_bytes(self.git.read_bytes() if content is None else content)
        return path

    def reconcile(self):
        return self.registry.reconcile(logical_name="demo/current", required_planes=["git", "library"], observed_at=NOW)

    def test_accepted_and_deterministic(self):
        self.replica("git", self.git, None)
        self.replica("library", self.library_copy())
        first = self.reconcile()
        second = self.reconcile()
        self.assertEqual(first["decision"], "accepted")
        self.assertEqual(first["reconciliation_id"], second["reconciliation_id"])
        self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
        for row in self.connection.execute("SELECT payload_json, payload_sha256 FROM events"):
            self.assertEqual(row["payload_sha256"], canonical_sha256(json.loads(row["payload_json"])))

    def test_persisted_not_indexed(self):
        self.replica("git", self.git, None)
        self.replica("library", self.library_copy(), "pending")
        result = self.reconcile()
        self.assertEqual(result["decision"], "accepted_pending_index")
        self.assertIn("persisted_not_indexed", result["states"])

    def test_stale_pointer(self):
        self.replica("git", self.git, None)
        self.replica("library", self.library_copy())
        self.registry.register_pointer(pointer_name="demo/current", plane="library", locator={"path": "/CURRENT.json"}, target_artifact_id=self.artifact_id, declared_sha256="0" * 64, observed_sha256="0" * 64, observed_at=NOW)
        self.assertEqual(self.reconcile()["decision"], "pointer_update_required")

    def test_missing_replica_then_reconstruct(self):
        self.replica("git", self.git, None)
        self.assertEqual(self.reconcile()["decision"], "repair_required")
        self.replica("library", self.library_copy())
        self.assertEqual(self.reconcile()["decision"], "accepted")

    def test_changed_bytes_quarantine(self):
        self.replica("git", self.git, None)
        self.replica("library", self.library_copy(b"mutated\n"))
        result = self.reconcile()
        self.assertEqual(result["decision"], "quarantined")
        self.assertIn("changed_bytes", result["states"])

    def test_duplicate_logical_name_quarantine(self):
        second = self.root / "v2.txt"
        second.write_text("immutable artifact v2\n")
        self.registry.register_artifact_file(logical_name="demo/current", path=second, authority_class="git-authoritative", source={"repository": "zaidifm/chatgpt", "commit": "def456", "path": "demo/v2.txt"}, created_at="2026-07-27T08:01:00Z")
        result = self.reconcile()
        self.assertEqual(result["decision"], "quarantined")
        self.assertIn("duplicate_logical_name", result["states"])

    def test_conflicting_receipts_quarantine(self):
        self.replica("git", self.git, None)
        self.replica("library", self.library_copy())
        base = {"schema_version": "superintendent-terminal-receipt-v1", "task_id": "TASK-1", "idempotency_key": "idem-1", "authority_epoch": 1, "executor": "node-a", "state": "completed", "created_at": NOW, "output_artifact_id": self.artifact_id, "source_commit": "abc123"}
        self.registry.register_terminal_receipt({**base, "receipt_id": "receipt-a", "output_sha256": self.artifact["sha256"]}, NOW)
        self.registry.register_terminal_receipt({**base, "receipt_id": "receipt-b", "executor": "node-b", "output_sha256": "f" * 64}, NOW)
        result = self.reconcile()
        self.assertEqual(result["decision"], "quarantined")
        self.assertIn("conflicting_receipts", result["states"])

    def test_core_contract_records(self):
        contracts = {
            "task": {"schema_version": "superintendent-task-v1", "task_id": "TASK-2", "idempotency_key": "idem-2", "authority_epoch": 1, "capability": "hash-v1", "operation": "hash", "input": {"x": 1}},
            "work_grant": {"schema_version": "superintendent-work-grant-v1", "grant_id": "GRANT-2", "task_id": "TASK-2", "authority_epoch": 1, "executor": "github-actions", "issued_at": NOW, "expires_at": "2026-07-28T08:00:00Z", "permitted_actions": ["hash"], "forbidden_actions": ["network-write"]},
            "execution_receipt": {"schema_version": "superintendent-execution-receipt-v1", "receipt_id": "EXEC-2", "task_id": "TASK-2", "idempotency_key": "idem-2", "authority_epoch": 1, "executor": "github-actions", "state": "started", "created_at": NOW},
            "authority_epoch": {"schema_version": "superintendent-authority-epoch-v1", "epoch": 1, "issued_at": NOW, "issued_by": "Ali-Zaidi"},
            "capability_declaration": {"schema_version": "superintendent-capability-declaration-v1", "capability_id": "hash-v1", "executor": "github-actions", "operations": ["hash"], "declared_at": NOW},
            "continuation_capsule": {"schema_version": "superintendent-continuation-capsule-v1", "capsule_id": "CAPSULE-2", "project": "Superintendent", "created_at": NOW, "authority_cutoff": {"commit": "abc123"}, "current_state": {"phase": "prototype"}, "next_actions": ["reconcile"]},
        }
        for kind, payload in contracts.items():
            self.assertTrue(self.registry.register_contract(kind, payload, NOW))


if __name__ == "__main__":
    unittest.main()
