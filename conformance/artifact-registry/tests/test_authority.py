from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from superintendent_registry import Registry, RegistryError, connect
from superintendent_registry.authority import AuthorityError

T0 = "2026-07-27T08:00:00Z"
T1 = "2026-07-27T08:01:00Z"
T2 = "2026-07-27T08:02:00Z"
T3 = "2026-07-27T08:03:00Z"
T4 = "2026-07-27T08:04:00Z"
T5 = "2026-07-27T08:05:00Z"
T6 = "2026-07-27T08:06:00Z"
T9 = "2026-07-27T08:09:00Z"


class AuthorityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "registry.sqlite3"
        self.connection = connect(self.path)
        self.registry = Registry(self.connection)
        self.core = self.registry.authority
        self.core.issue_epoch({
            "schema_version": "superintendent-authority-epoch-v1",
            "epoch": 1,
            "issued_at": T0,
            "issued_by": "Ali-Zaidi",
            "reason": "initial authority",
        })
        self.core.declare_capability({
            "schema_version": "superintendent-capability-declaration-v1",
            "capability_id": "canonical-json-sha256-v1",
            "executor": "github-actions",
            "operations": ["canonicalize-and-hash"],
            "limits": {"max_input_bytes": 4096},
            "health": {"healthy": True},
            "declared_at": T0,
        })

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def task_payload(self, task_id="TASK-1", key="idem-1", epoch=1):
        return {
            "schema_version": "superintendent-task-v1",
            "task_id": task_id,
            "idempotency_key": key,
            "authority_epoch": epoch,
            "capability": "canonical-json-sha256-v1",
            "operation": "canonicalize-and-hash",
            "input": {"message": "bounded authority test"},
        }

    def create_approved(self, task_id="TASK-1", key="idem-1"):
        self.core.create_task(self.task_payload(task_id, key), T1)
        self.core.approve_task(task_id, T2)

    def grant_payload(self, task_id="TASK-1", grant_id="GRANT-1", epoch=1, expires=T9):
        return {
            "schema_version": "superintendent-work-grant-v1",
            "grant_id": grant_id,
            "task_id": task_id,
            "authority_epoch": epoch,
            "executor": "github-actions",
            "lease_id": f"LEASE-{grant_id}",
            "issued_at": T2,
            "expires_at": expires,
            "permitted_actions": ["canonicalize-and-hash"],
            "forbidden_actions": ["network-write", "credential-read"],
        }

    def test_happy_path_and_append_only_chain(self):
        self.create_approved()
        self.core.issue_grant(self.grant_payload(), T2)
        lease_id = self.core.accept_grant("GRANT-1", T3)
        self.assertEqual(lease_id, "LEASE-GRANT-1")
        self.core.start_task("GRANT-1", T4)
        self.core.heartbeat(lease_id, T5)
        artifact = self.registry.register_artifact_bytes(
            logical_name="authority/output",
            data=b'{"message":"bounded authority test"}\n',
            media_type="application/json",
            authority_class="task-output",
            source={"task_id": "TASK-1"},
            created_at=T5,
        )
        receipt = {
            "schema_version": "superintendent-terminal-receipt-v1",
            "receipt_id": "RECEIPT-1",
            "task_id": "TASK-1",
            "idempotency_key": "idem-1",
            "authority_epoch": 1,
            "executor": "github-actions",
            "state": "completed",
            "created_at": T6,
            "output_artifact_id": artifact["artifact_id"],
            "output_sha256": artifact["sha256"],
            "source_commit": "TEST-COMMIT",
        }
        self.registry.register_terminal_receipt(receipt, T6)
        self.core.complete_task("TASK-1", "RECEIPT-1", T6, "github-actions")
        task = self.connection.execute("SELECT * FROM tasks WHERE task_id='TASK-1'").fetchone()
        grant = self.connection.execute("SELECT * FROM work_grants WHERE grant_id='GRANT-1'").fetchone()
        lease = self.connection.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["terminal_receipt_id"], "RECEIPT-1")
        self.assertEqual(grant["state"], "completed")
        self.assertEqual(lease["state"], "released")
        self.assertTrue(self.registry.verify_event_chain()["valid"])
        self.assertTrue(self.core.verify_materialized_state()["valid"])
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute("UPDATE events SET event_type='tampered' WHERE sequence=1")
        self.connection.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute("DELETE FROM task_transitions WHERE sequence=1")
        self.connection.rollback()

    def test_lost_lease_moves_running_task_to_unknown(self):
        self.create_approved()
        self.core.issue_grant(self.grant_payload(expires=T5), T2)
        self.core.accept_grant("GRANT-1", T3)
        self.core.start_task("GRANT-1", T4)
        self.assertEqual(self.core.expire_leases(T6), ["LEASE-GRANT-1"])
        self.assertEqual(self.connection.execute("SELECT state FROM tasks WHERE task_id='TASK-1'").fetchone()["state"], "unknown")
        self.assertTrue(self.core.verify_materialized_state()["valid"])

    def test_stale_epoch_fences_old_task_and_grant(self):
        self.create_approved()
        self.core.issue_epoch({
            "schema_version": "superintendent-authority-epoch-v1",
            "epoch": 2,
            "previous_epoch": 1,
            "issued_at": T3,
            "issued_by": "Ali-Zaidi",
            "reason": "standby promotion",
        })
        with self.assertRaisesRegex(AuthorityError, "stale_authority_epoch"):
            self.core.transition_task(task_id="TASK-1", to_state="cancelled", occurred_at=T4, actor="old-primary", evidence={"epoch": 1})
        with self.assertRaisesRegex(AuthorityError, "stale_authority_epoch"):
            self.core.issue_grant(self.grant_payload(epoch=1), T4)

    def test_expired_grant_cannot_be_accepted(self):
        self.create_approved()
        self.core.issue_grant(self.grant_payload(expires=T3), T2)
        with self.assertRaisesRegex(AuthorityError, "grant_expired"):
            self.core.accept_grant("GRANT-1", T3)

    def test_unhealthy_or_undeclared_capability_fails_closed(self):
        self.create_approved()
        bad = self.grant_payload()
        bad["executor"] = "unknown-node"
        with self.assertRaisesRegex(AuthorityError, "capability_not_declared"):
            self.core.issue_grant(bad, T2)
        self.core.declare_capability({
            "schema_version": "superintendent-capability-declaration-v1",
            "capability_id": "canonical-json-sha256-v1",
            "executor": "github-actions",
            "operations": ["canonicalize-and-hash"],
            "health": {"healthy": False},
            "declared_at": T3,
        })
        with self.assertRaisesRegex(AuthorityError, "capability_unhealthy"):
            self.core.issue_grant(self.grant_payload(), T3)

    def test_idempotency_key_replay_and_conflict(self):
        payload = self.task_payload()
        self.assertEqual(self.core.create_task(payload, T1), "TASK-1")
        self.assertEqual(self.core.create_task(payload, T1), "TASK-1")
        with self.assertRaisesRegex(AuthorityError, "idempotency_key_conflict"):
            self.core.create_task(self.task_payload(task_id="TASK-2", key="idem-1"), T2)

    def test_invalid_transition_is_rejected(self):
        self.core.create_task(self.task_payload(), T1)
        with self.assertRaisesRegex(AuthorityError, "transition_not_allowed"):
            self.core.transition_task(task_id="TASK-1", to_state="completed", occurred_at=T2, actor="executor", evidence={})

    def test_lease_revocation_moves_task_to_unknown(self):
        self.create_approved()
        self.core.issue_grant(self.grant_payload(), T2)
        lease = self.core.accept_grant("GRANT-1", T3)
        self.core.start_task("GRANT-1", T4)
        self.core.revoke_lease(lease, T5, "credential_revoked")
        self.assertEqual(self.connection.execute("SELECT state FROM tasks WHERE task_id='TASK-1'").fetchone()["state"], "unknown")
        self.assertEqual(self.connection.execute("SELECT state FROM leases WHERE lease_id=?", (lease,)).fetchone()["state"], "revoked")

    def test_event_chain_survives_reopen_and_backfill(self):
        self.create_approved()
        before = self.registry.verify_event_chain()
        self.assertTrue(before["valid"])
        self.connection.close()
        self.connection = connect(self.path)
        self.registry = Registry(self.connection)
        self.core = self.registry.authority
        self.assertEqual(before, self.registry.verify_event_chain())

    def test_capability_and_grant_replays_are_idempotent(self):
        declaration = {
            "schema_version": "superintendent-capability-declaration-v1",
            "capability_id": "canonical-json-sha256-v1",
            "executor": "github-actions",
            "operations": ["canonicalize-and-hash"],
            "limits": {"max_input_bytes": 4096},
            "health": {"healthy": True},
            "declared_at": T0,
        }
        first = self.core.declare_capability(declaration)
        self.assertEqual(first, self.core.declare_capability(declaration))
        self.assertEqual(self.connection.execute("SELECT active FROM capability_declarations WHERE declaration_id=?", (first,)).fetchone()["active"], 1)
        self.create_approved()
        grant = self.grant_payload()
        self.assertEqual(self.core.issue_grant(grant, T2), "GRANT-1")
        self.assertEqual(self.core.issue_grant(grant, T2), "GRANT-1")
        changed = dict(grant)
        changed["expires_at"] = "2026-07-27T08:10:00Z"
        with self.assertRaisesRegex(AuthorityError, "grant_id_conflict"):
            self.core.issue_grant(changed, T2)

    def test_epoch_replay_is_idempotent_but_conflict_fails(self):
        same = {
            "schema_version": "superintendent-authority-epoch-v1",
            "epoch": 1,
            "issued_at": T0,
            "issued_by": "Ali-Zaidi",
            "reason": "initial authority",
        }
        self.assertEqual(self.core.issue_epoch(same), 1)
        with self.assertRaisesRegex(AuthorityError, "authority_epoch_conflict"):
            self.core.issue_epoch({**same, "reason": "rewritten history"})

    def test_grant_accept_and_start_recheck_task_state(self):
        self.create_approved()
        self.core.issue_grant(self.grant_payload(), T2)
        self.core.transition_task(task_id="TASK-1", to_state="cancelled", occurred_at=T3, actor="owner", evidence={"reason": "withdrawn before acceptance"})
        with self.assertRaisesRegex(AuthorityError, "task_not_approved_at_accept"):
            self.core.accept_grant("GRANT-1", T4)
        self.core.create_task(self.task_payload("TASK-2", "idem-2"), T1)
        self.core.approve_task("TASK-2", T2)
        self.core.issue_grant(self.grant_payload("TASK-2", "GRANT-2"), T2)
        self.core.accept_grant("GRANT-2", T3)
        self.core.transition_task(task_id="TASK-2", to_state="cancelled", occurred_at=T4, actor="owner", evidence={"reason": "withdrawn after dispatch"})
        with self.assertRaisesRegex(AuthorityError, "task_not_dispatched_at_start"):
            self.core.start_task("GRANT-2", T5)

    def test_v1_event_rows_are_backfilled_into_chain(self):
        old_path = Path(self.temp.name) / "old-v1.sqlite3"
        old = sqlite3.connect(old_path)
        old.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,occurred_at TEXT NOT NULL,event_type TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL);
            INSERT INTO metadata(key,value) VALUES ('schema_version','superintendent-registry-sqlite-v1');
            INSERT INTO events(event_id,occurred_at,event_type,subject_type,subject_id,payload_json,payload_sha256) VALUES ('evt-old','2026-07-27T00:00:00Z','old_event','fixture','1','{}','44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a');
        """)
        old.commit()
        old.close()
        migrated = connect(old_path)
        migrated_registry = Registry(migrated)
        self.assertTrue(migrated_registry.verify_event_chain()["valid"])
        self.assertEqual(migrated.execute("SELECT COUNT(*) FROM event_chain").fetchone()[0], 1)
        self.assertEqual(migrated.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0], "superintendent-registry-sqlite-v2")
        migrated.close()

    def test_terminal_receipt_ids_are_immutable(self):
        artifact = self.registry.register_artifact_bytes(logical_name="authority/immutable-receipt", data=b"immutable receipt output\n", media_type="text/plain", authority_class="task-output", source={"test": True}, created_at=T1)
        payload = {
            "schema_version": "superintendent-terminal-receipt-v1",
            "receipt_id": "IMMUTABLE-RECEIPT-1",
            "task_id": "IMMUTABLE-TASK-1",
            "idempotency_key": "immutable-task-1",
            "authority_epoch": 1,
            "executor": "node-a",
            "state": "completed",
            "created_at": T2,
            "output_artifact_id": artifact["artifact_id"],
            "output_sha256": artifact["sha256"],
        }
        self.assertEqual(self.registry.register_terminal_receipt(payload, T2), "IMMUTABLE-RECEIPT-1")
        self.assertEqual(self.registry.register_terminal_receipt(payload, T2), "IMMUTABLE-RECEIPT-1")
        with self.assertRaisesRegex(RegistryError, "receipt_id_conflict"):
            self.registry.register_terminal_receipt({**payload, "executor": "node-b"}, T2)
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute("DELETE FROM receipts WHERE receipt_id='IMMUTABLE-RECEIPT-1'")
        self.connection.rollback()


if __name__ == "__main__":
    unittest.main()
