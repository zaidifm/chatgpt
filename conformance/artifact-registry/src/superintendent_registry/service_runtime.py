from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from http.server import HTTPServer
from typing import Any, Callable

from .authority_registry import AuthorityRegistry
from .canonical import canonical_json_bytes, sha256_bytes
from .database import connect
from .service_common import (
    MAX_ARTIFACT_BYTES,
    SERVICE_VERSION,
    AuthenticationError,
    ProcessLock,
    RequestError,
    ServiceConfig,
    ServiceError,
    atomic_write,
    utc_now,
)


class ResidentRuntime:
    """Single-process authority runtime with serialized SQLite access."""

    def __init__(self, config: ServiceConfig, clock: Callable[[], str] = utc_now):
        self.config = config.normalized()
        self.clock = clock
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.scheduler_thread: threading.Thread | None = None
        self.server_thread: threading.Thread | None = None
        self.server: HTTPServer | None = None
        self.started_at = self.clock()
        self.last_scheduler_tick: dict[str, Any] | None = None
        self.token_digest = hashlib.sha256(self.config.bearer_token.encode("utf-8")).digest()
        self.process_lock = ProcessLock(
            self.config.lock_path,
            {
                "pid": os.getpid(),
                "db_path": str(self.config.db_path),
                "service_version": SERVICE_VERSION,
                "started_at": self.started_at,
            },
        )
        self._started = False

    @property
    def address(self) -> tuple[str, int]:
        if self.server is None:
            raise ServiceError("service_not_started")
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def authenticate(self, header: str | None) -> None:
        if not header or not header.startswith("Bearer "):
            raise AuthenticationError("missing_bearer_token")
        candidate_digest = hashlib.sha256(header[7:].encode("utf-8")).digest()
        if not hmac.compare_digest(candidate_digest, self.token_digest):
            raise AuthenticationError("invalid_bearer_token")

    def _with_registry(self, function: Callable[[AuthorityRegistry, sqlite3.Connection], Any]) -> Any:
        with self.db_lock:
            connection = connect(self.config.db_path)
            try:
                return function(AuthorityRegistry(connection), connection)
            finally:
                connection.close()

    def verify(self) -> dict[str, Any]:
        def operation(registry: AuthorityRegistry, _connection: sqlite3.Connection) -> dict[str, Any]:
            journal = registry.verify_event_chain()
            materialized = registry.authority.verify_materialized_state()
            return {
                "valid": bool(journal.get("valid")) and bool(materialized.get("valid")),
                "journal": journal,
                "materialized": materialized,
                "current_epoch": registry.authority.current_epoch(),
            }

        return self._with_registry(operation)

    def tick(self, at: str | None = None) -> dict[str, Any]:
        observed_at = at or self.clock()

        def operation(registry: AuthorityRegistry, _connection: sqlite3.Connection) -> dict[str, Any]:
            expired = registry.authority.expire_leases(observed_at)
            return {
                "observed_at": observed_at,
                "expired_lease_ids": expired,
                "journal": registry.verify_event_chain(),
                "materialized": registry.authority.verify_materialized_state(),
            }

        result = self._with_registry(operation)
        self.last_scheduler_tick = result
        return result

    def export_state(self) -> dict[str, Any]:
        return self._with_registry(lambda registry, _connection: registry.export_state())

    def write_snapshot(self, observed_at: str) -> dict[str, Any]:
        def operation(registry: AuthorityRegistry, _connection: sqlite3.Connection) -> dict[str, Any]:
            return {
                "schema_version": "superintendent-resident-snapshot-v1",
                "observed_at": observed_at,
                "source_commit": self.config.source_commit,
                "registry": registry.export_state(),
                "journal": registry.verify_event_chain(),
                "materialized": registry.authority.verify_materialized_state(),
            }

        snapshot = self._with_registry(operation)
        encoded = canonical_json_bytes(snapshot) + b"\n"
        digest = sha256_bytes(encoded)
        destination = self.config.snapshot_dir / f"snapshot-{digest}.json"
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise ServiceError("snapshot_digest_collision")
        else:
            atomic_write(destination, encoded)
        pointer = {
            "schema_version": "superintendent-resident-snapshot-pointer-v1",
            "snapshot_sha256": digest,
            "snapshot_path": destination.name,
            "observed_at": observed_at,
            "source_commit": self.config.source_commit,
        }
        atomic_write(self.config.snapshot_dir / "CURRENT.json", canonical_json_bytes(pointer) + b"\n")
        return {**pointer, "size_bytes": len(encoded)}

    def query_row(self, table: str, column: str, value: str) -> dict[str, Any]:
        allowed = {
            ("tasks", "task_id"),
            ("work_grants", "grant_id"),
            ("leases", "lease_id"),
            ("receipts", "receipt_id"),
        }
        if (table, column) not in allowed:
            raise RequestError("unsupported_query")

        def operation(_registry: AuthorityRegistry, connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(f"SELECT * FROM {table} WHERE {column}=?", (value,)).fetchone()
            if row is None:
                from .service_common import NotFoundError

                raise NotFoundError(f"{table}_not_found:{value}")
            result = dict(row)
            for key in list(result):
                if key.endswith("_json") and isinstance(result[key], str):
                    result[key] = json.loads(result[key])
            return result

        return self._with_registry(operation)

    def mutate(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        def operation(registry: AuthorityRegistry, _connection: sqlite3.Connection) -> dict[str, Any]:
            authority = registry.authority
            if action == "issue_epoch":
                return {"epoch": authority.issue_epoch(payload["epoch"])}
            if action == "declare_capability":
                return {"declaration_id": authority.declare_capability(payload["capability"])}
            if action == "create_task":
                return {"task_id": authority.create_task(payload["task"], payload["created_at"])}
            if action == "approve_task":
                return {
                    "transition_id": authority.approve_task(
                        payload["task_id"], payload["at"], payload.get("actor", "owner")
                    )
                }
            if action == "transition_task":
                return {
                    "transition_id": authority.transition_task(
                        task_id=payload["task_id"],
                        to_state=payload["to_state"],
                        occurred_at=payload["at"],
                        actor=payload["actor"],
                        evidence=payload.get("evidence") or {},
                        expected_from=payload.get("expected_from"),
                    )
                }
            if action == "issue_grant":
                return {"grant_id": authority.issue_grant(payload["grant"], payload["created_at"])}
            if action == "accept_grant":
                return {"lease_id": authority.accept_grant(payload["grant_id"], payload["at"])}
            if action == "start_task":
                return {"task_id": authority.start_task(payload["grant_id"], payload["at"])}
            if action == "heartbeat":
                authority.heartbeat(payload["lease_id"], payload["at"])
                return {"lease_id": payload["lease_id"], "heartbeat_at": payload["at"]}
            if action == "revoke_lease":
                authority.revoke_lease(
                    payload["lease_id"],
                    payload["at"],
                    payload["reason"],
                    payload.get("actor", "owner"),
                )
                return {"lease_id": payload["lease_id"], "state": "revoked"}
            if action == "register_receipt":
                return {
                    "receipt_id": registry.register_terminal_receipt(
                        payload["receipt"], payload["created_at"]
                    )
                }
            if action == "complete_task":
                return {
                    "receipt_id": authority.complete_task(
                        payload["task_id"], payload["receipt_id"], payload["at"], payload["actor"]
                    )
                }
            if action == "register_artifact":
                try:
                    data = base64.b64decode(payload["content_base64"], validate=True)
                except Exception as exc:
                    raise RequestError("artifact_content_base64_invalid") from exc
                if len(data) > MAX_ARTIFACT_BYTES:
                    raise RequestError("artifact_too_large")
                return registry.register_artifact_bytes(
                    logical_name=payload["logical_name"],
                    data=data,
                    media_type=payload.get("media_type") or "application/octet-stream",
                    authority_class=payload["authority_class"],
                    source=payload["source"],
                    created_at=payload["created_at"],
                    active=payload.get("active", True),
                )
            if action == "register_replica":
                return {
                    "replica_id": registry.register_replica(
                        artifact_id=payload["artifact_id"],
                        plane=payload["plane"],
                        locator=payload["locator"],
                        persistence_state=payload["persistence_state"],
                        observed_at=payload["observed_at"],
                        observed_sha256=payload.get("observed_sha256"),
                        observed_size_bytes=payload.get("observed_size_bytes"),
                        index_state=payload.get("index_state"),
                        required=payload.get("required", True),
                        last_error=payload.get("last_error"),
                    )
                }
            if action == "register_pointer":
                return {
                    "pointer_id": registry.register_pointer(
                        pointer_name=payload["pointer_name"],
                        plane=payload["plane"],
                        locator=payload["locator"],
                        target_artifact_id=payload.get("target_artifact_id"),
                        declared_sha256=payload.get("declared_sha256"),
                        observed_sha256=payload.get("observed_sha256"),
                        observed_at=payload["observed_at"],
                    )
                }
            if action == "reconcile":
                return registry.reconcile(
                    logical_name=payload["logical_name"],
                    required_planes=payload.get("required_planes") or [],
                    observed_at=payload["observed_at"],
                )
            raise RequestError(f"unknown_action:{action}")

        return self._with_registry(operation)

    def _scheduler_loop(self) -> None:
        interval = self.config.scheduler_interval_seconds
        if interval <= 0:
            return
        while not self.stop_event.wait(interval):
            try:
                self.tick()
            except Exception as exc:
                self.last_scheduler_tick = {
                    "observed_at": self.clock(),
                    "error": f"{type(exc).__name__}:{exc}",
                }

    def start(self, background: bool = True) -> None:
        if self._started:
            raise ServiceError("service_already_started")
        self.process_lock.acquire()
        try:
            verification = self.verify()
            if not verification["valid"]:
                raise ServiceError("startup_verification_failed:" + json.dumps(verification, sort_keys=True))
            if self.config.startup_reconcile:
                recovery = self.tick(self.clock())
                if not recovery["journal"].get("valid") or not recovery["materialized"].get("valid"):
                    raise ServiceError("startup_reconciliation_failed")
            from .service_http import ResidentHTTPServer, ResidentHandler

            self.server = ResidentHTTPServer(
                (self.config.host, self.config.port), ResidentHandler, self
            )
            self.stop_event.clear()
            if self.config.scheduler_interval_seconds > 0:
                self.scheduler_thread = threading.Thread(
                    target=self._scheduler_loop,
                    name="superintendent-lease-scheduler",
                    daemon=True,
                )
                self.scheduler_thread.start()
            self._started = True
            if background:
                self.server_thread = threading.Thread(
                    target=self.server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    name="superintendent-http",
                    daemon=True,
                )
                self.server_thread.start()
        except BaseException:
            self.process_lock.release()
            raise

    def serve_forever(self) -> None:
        self.start(background=False)
        assert self.server is not None
        try:
            self.server.serve_forever(poll_interval=0.05)
        finally:
            self.stop_event.set()
            self.server.server_close()
            if self.scheduler_thread is not None:
                self.scheduler_thread.join(timeout=5)
            self.process_lock.release()
            self._started = False

    def stop(self) -> None:
        if not self._started:
            self.process_lock.release()
            return
        self.stop_event.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.server_thread is not None and self.server_thread is not threading.current_thread():
            self.server_thread.join(timeout=5)
        if self.scheduler_thread is not None:
            self.scheduler_thread.join(timeout=5)
        self.process_lock.release()
        self._started = False
