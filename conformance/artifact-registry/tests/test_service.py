from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from superintendent_registry.service import ResidentRuntime, ServiceConfig, ServiceLockError, RequestError

TOKEN = "synthetic-resident-test-token-0001"
T0 = "2026-07-27T09:00:00Z"
T1 = "2026-07-27T09:01:00Z"
T2 = "2026-07-27T09:02:00Z"
T3 = "2026-07-27T09:03:00Z"
T4 = "2026-07-27T09:04:00Z"
T5 = "2026-07-27T09:05:00Z"
T9 = "2030-07-27T09:09:00Z"


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Harness:
    def __init__(self, root: Path, *, scheduler: float = 0.0, startup_reconcile: bool = True):
        self.root = root
        self.runtime = ResidentRuntime(
            ServiceConfig(
                db_path=root / "core.sqlite3",
                bearer_token=TOKEN,
                port=0,
                lock_path=root / "core.lock",
                snapshot_dir=root / "snapshots",
                scheduler_interval_seconds=scheduler,
                source_commit="TEST-COMMIT",
                startup_reconcile=startup_reconcile,
            )
        )
        self.runtime.start(background=True)

    def close(self):
        self.runtime.stop()

    def request(self, method: str, path: str, body=None, token: str | None = TOKEN):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.runtime.base_url + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def seed_authority(self):
        self.request(
            "POST",
            "/v1/epochs",
            {
                "schema_version": "superintendent-authority-epoch-v1",
                "epoch": 1,
                "issued_at": T0,
                "issued_by": "Ali-Zaidi",
                "reason": "resident service test",
            },
        )
        self.request(
            "POST",
            "/v1/capabilities",
            {
                "schema_version": "superintendent-capability-declaration-v1",
                "capability_id": "canonical-json-sha256-v1",
                "executor": "resident-test-executor",
                "operations": ["canonicalize-and-hash"],
                "limits": {"max_input_bytes": 4096},
                "health": {"healthy": True},
                "declared_at": T0,
            },
        )

    def create_running_task(self, *, task_id="TASK-1", key="idem-1", issued=T2, expires=T9):
        task = {
            "schema_version": "superintendent-task-v1",
            "task_id": task_id,
            "idempotency_key": key,
            "authority_epoch": 1,
            "capability": "canonical-json-sha256-v1",
            "operation": "canonicalize-and-hash",
            "input": {"message": task_id},
        }
        status, _ = self.request("POST", "/v1/tasks", {"task": task, "created_at": T1})
        assert status == 200
        status, _ = self.request("POST", f"/v1/tasks/{task_id}/approve", {"at": T2, "actor": "owner"})
        assert status == 200
        grant_id = f"GRANT-{task_id}"
        lease_id = f"LEASE-{task_id}"
        grant = {
            "schema_version": "superintendent-work-grant-v1",
            "grant_id": grant_id,
            "task_id": task_id,
            "authority_epoch": 1,
            "executor": "resident-test-executor",
            "lease_id": lease_id,
            "issued_at": issued,
            "expires_at": expires,
            "permitted_actions": ["canonicalize-and-hash"],
            "forbidden_actions": ["credential-read", "network-write"],
        }
        status, _ = self.request("POST", "/v1/grants", {"grant": grant, "created_at": issued})
        assert status == 200
        status, _ = self.request("POST", f"/v1/grants/{grant_id}/accept", {"at": T3})
        assert status == 200
        status, _ = self.request("POST", f"/v1/grants/{grant_id}/start", {"at": T4})
        assert status == 200
        return task, grant_id, lease_id


class ResidentServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_authentication_and_loopback_only(self):
        harness = Harness(self.root)
        try:
            status, health = harness.request("GET", "/healthz", token=None)
            self.assertEqual(status, 200)
            self.assertEqual(health["status"], "ok")
            status, body = harness.request("GET", "/v1/status", token=None)
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "missing_bearer_token")
            status, body = harness.request("GET", "/v1/status", token="wrong-token-value")
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "invalid_bearer_token")
            status, body = harness.request("GET", "/v1/status")
            self.assertEqual(status, 200)
            self.assertTrue(body["verification"]["valid"])
        finally:
            harness.close()
        with self.assertRaisesRegex(RequestError, "service_host_not_loopback"):
            ResidentRuntime(
                ServiceConfig(
                    db_path=self.root / "other.sqlite3",
                    bearer_token=TOKEN,
                    host="0.0.0.0",
                )
            )

    def test_single_writer_lock(self):
        first = Harness(self.root)
        try:
            second = ResidentRuntime(
                ServiceConfig(
                    db_path=self.root / "core.sqlite3",
                    bearer_token=TOKEN,
                    lock_path=self.root / "core.lock",
                )
            )
            with self.assertRaisesRegex(ServiceLockError, "single_writer_lock_held"):
                second.start()
        finally:
            first.close()

    def test_full_authority_lifecycle_and_deterministic_snapshot(self):
        harness = Harness(self.root)
        try:
            harness.seed_authority()
            task, _grant, lease = harness.create_running_task()
            status, heartbeat = harness.request(
                "POST", f"/v1/leases/{lease}/heartbeat", {"at": T5}
            )
            self.assertEqual(status, 200)
            self.assertEqual(heartbeat["lease_id"], lease)

            output = b'{"message":"TASK-1"}\n'
            status, artifact = harness.request(
                "POST",
                "/v1/artifacts",
                {
                    "logical_name": "resident/output/TASK-1",
                    "content_base64": base64.b64encode(output).decode("ascii"),
                    "media_type": "application/json",
                    "authority_class": "task-output",
                    "source": {"task_id": "TASK-1"},
                    "created_at": T5,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(artifact["sha256"], hashlib.sha256(output).hexdigest())
            receipt = {
                "schema_version": "superintendent-terminal-receipt-v1",
                "receipt_id": "RECEIPT-1",
                "task_id": task["task_id"],
                "idempotency_key": task["idempotency_key"],
                "authority_epoch": 1,
                "executor": "resident-test-executor",
                "state": "completed",
                "created_at": T5,
                "output_artifact_id": artifact["artifact_id"],
                "output_sha256": artifact["sha256"],
                "source_commit": "TEST-COMMIT",
            }
            status, registered = harness.request(
                "POST", "/v1/receipts", {"receipt": receipt, "created_at": T5}
            )
            self.assertEqual(status, 200)
            self.assertEqual(registered["receipt_id"], "RECEIPT-1")
            status, completed = harness.request(
                "POST",
                "/v1/tasks/TASK-1/complete",
                {"receipt_id": "RECEIPT-1", "at": T5, "actor": "resident-test-executor"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(completed["receipt_id"], "RECEIPT-1")
            status, task_row = harness.request("GET", "/v1/tasks/TASK-1")
            self.assertEqual(status, 200)
            self.assertEqual(task_row["state"], "completed")

            status, first = harness.request(
                "POST", "/v1/snapshots", {"observed_at": "2026-07-27T09:10:00Z"}
            )
            self.assertEqual(status, 200)
            first_bytes = (self.root / "snapshots" / first["snapshot_path"]).read_bytes()
            status, second = harness.request(
                "POST", "/v1/snapshots", {"observed_at": "2026-07-27T09:10:00Z"}
            )
            self.assertEqual(status, 200)
            self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
            self.assertEqual(first_bytes, (self.root / "snapshots" / second["snapshot_path"]).read_bytes())
            pointer = json.loads((self.root / "snapshots" / "CURRENT.json").read_text())
            self.assertEqual(pointer["snapshot_sha256"], first["snapshot_sha256"])
        finally:
            harness.close()

    def test_connector_observation_intake_and_reconciliation(self):
        harness = Harness(self.root)
        try:
            data = b"connector observation fixture\n"
            status, artifact = harness.request(
                "POST",
                "/v1/artifacts",
                {
                    "logical_name": "observations/current",
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "media_type": "text/plain",
                    "authority_class": "git-authoritative",
                    "source": {"repository": "zaidifm/chatgpt", "commit": "TEST-COMMIT"},
                    "created_at": T1,
                },
            )
            self.assertEqual(status, 200)
            for plane in ("git", "library"):
                status, replica = harness.request(
                    "POST",
                    "/v1/observations/replicas",
                    {
                        "artifact_id": artifact["artifact_id"],
                        "plane": plane,
                        "locator": {"plane": plane, "stable_id": f"{plane}-fixture-1"},
                        "persistence_state": "present",
                        "index_state": "indexed" if plane == "library" else None,
                        "observed_sha256": artifact["sha256"],
                        "observed_size_bytes": artifact["size_bytes"],
                        "observed_at": T2,
                        "required": True,
                    },
                )
                self.assertEqual(status, 200)
                self.assertIn("replica_id", replica)
            status, result = harness.request(
                "POST",
                "/v1/reconcile",
                {
                    "logical_name": "observations/current",
                    "required_planes": ["git", "library"],
                    "observed_at": T3,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["decision"], "accepted")
            self.assertEqual(result["accepted_artifact_id"], artifact["artifact_id"])
        finally:
            harness.close()

    def test_scheduler_expires_lease(self):
        harness = Harness(self.root, scheduler=0.05)
        try:
            harness.seed_authority()
            now = dt.datetime.now(dt.timezone.utc)
            issued = iso(now + dt.timedelta(seconds=0.05))
            expires = iso(now + dt.timedelta(seconds=0.45))
            harness.create_running_task(task_id="TASK-EXP", key="idem-exp", issued=issued, expires=expires)
            deadline = time.time() + 3
            state = None
            while time.time() < deadline:
                status, row = harness.request("GET", "/v1/tasks/TASK-EXP")
                self.assertEqual(status, 200)
                state = row["state"]
                if state == "unknown":
                    break
                time.sleep(0.05)
            self.assertEqual(state, "unknown")
            status, lease = harness.request("GET", "/v1/leases/LEASE-TASK-EXP")
            self.assertEqual(status, 200)
            self.assertEqual(lease["state"], "expired")
        finally:
            harness.close()

    def test_killed_process_restarts_and_reconciles_expired_lease(self):
        db = self.root / "crash.sqlite3"
        lock = self.root / "crash.lock"
        snapshots = self.root / "crash-snapshots"
        source_root = Path(__file__).resolve().parents[1] / "src"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["SUPERINTENDENT_BEARER_TOKEN"] = TOKEN

        def start_process(ready: Path):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "superintendent_registry.service_cli",
                    "--db", str(db),
                    "--lock-file", str(lock),
                    "--snapshot-dir", str(snapshots),
                    "--scheduler-interval", "10",
                    "--source-commit", "TEST-COMMIT",
                    "--ready-file", str(ready),
                ],
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.time() + 8
            while time.time() < deadline and not ready.exists():
                if process.poll() is not None:
                    out, err = process.communicate()
                    self.fail(f"service exited early: {out} {err}")
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "service did not become ready")
            info = json.loads(ready.read_text())
            return process, f"http://{info['host']}:{info['port']}"

        def request(base, method, path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(
                base + path,
                data=data,
                method=method,
                headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        first, base = start_process(self.root / "ready-1.json")
        try:
            request(base, "POST", "/v1/epochs", {
                "schema_version": "superintendent-authority-epoch-v1",
                "epoch": 1,
                "issued_at": T0,
                "issued_by": "Ali-Zaidi",
                "reason": "crash test",
            })
            request(base, "POST", "/v1/capabilities", {
                "schema_version": "superintendent-capability-declaration-v1",
                "capability_id": "canonical-json-sha256-v1",
                "executor": "resident-test-executor",
                "operations": ["canonicalize-and-hash"],
                "health": {"healthy": True},
                "declared_at": T0,
            })
            task = {
                "schema_version": "superintendent-task-v1",
                "task_id": "TASK-CRASH",
                "idempotency_key": "idem-crash",
                "authority_epoch": 1,
                "capability": "canonical-json-sha256-v1",
                "operation": "canonicalize-and-hash",
                "input": {"crash": True},
            }
            request(base, "POST", "/v1/tasks", {"task": task, "created_at": T1})
            request(base, "POST", "/v1/tasks/TASK-CRASH/approve", {"at": T2, "actor": "owner"})
            now = dt.datetime.now(dt.timezone.utc)
            expires = iso(now + dt.timedelta(seconds=0.8))
            grant = {
                "schema_version": "superintendent-work-grant-v1",
                "grant_id": "GRANT-CRASH",
                "task_id": "TASK-CRASH",
                "authority_epoch": 1,
                "executor": "resident-test-executor",
                "lease_id": "LEASE-CRASH",
                "issued_at": iso(now),
                "expires_at": expires,
                "permitted_actions": ["canonicalize-and-hash"],
                "forbidden_actions": ["credential-read"],
            }
            request(base, "POST", "/v1/grants", {"grant": grant, "created_at": iso(now)})
            request(base, "POST", "/v1/grants/GRANT-CRASH/accept", {"at": iso(now + dt.timedelta(seconds=0.1))})
            request(base, "POST", "/v1/grants/GRANT-CRASH/start", {"at": iso(now + dt.timedelta(seconds=0.2))})
        finally:
            first.kill()
            first.communicate(timeout=5)
        time.sleep(1.0)

        second, second_base = start_process(self.root / "ready-2.json")
        try:
            row = request(second_base, "GET", "/v1/tasks/TASK-CRASH")
            self.assertEqual(row["state"], "unknown")
            lease = request(second_base, "GET", "/v1/leases/LEASE-CRASH")
            self.assertEqual(lease["state"], "expired")
            status = request(second_base, "GET", "/v1/status")
            self.assertTrue(status["verification"]["valid"])
        finally:
            second.terminate()
            second.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
