from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from .authority import AuthorityError
from .canonical import canonical_json_bytes
from .registry import RegistryError
from .service_common import (
    MAX_REQUEST_BYTES,
    SERVICE_VERSION,
    AuthenticationError,
    NotFoundError,
    RequestError,
    ServiceError,
)


class ResidentHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler, runtime):
        self.runtime = runtime
        super().__init__(server_address, handler)


class ResidentHandler(BaseHTTPRequestHandler):
    server: ResidentHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = canonical_json_bytes(payload) + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header("WWW-Authenticate", 'Bearer realm="superintendent"')
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestError("invalid_content_length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise RequestError("request_body_too_large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise RequestError("request_json_invalid") from exc
        if not isinstance(value, dict):
            raise RequestError("request_json_must_be_object")
        return value

    def _authenticated(self) -> None:
        self.server.runtime.authenticate(self.headers.get("Authorization"))

    def _dispatch(self) -> tuple[int, dict[str, Any]]:
        path = urlparse(self.path).path.rstrip("/") or "/"
        runtime = self.server.runtime
        if self.command == "GET" and path == "/healthz":
            return HTTPStatus.OK, {
                "status": "ok",
                "service": "superintendent-resident",
                "version": SERVICE_VERSION,
            }
        self._authenticated()
        if self.command == "GET" and path == "/v1/status":
            return HTTPStatus.OK, {
                "status": "ready",
                "service_version": SERVICE_VERSION,
                "started_at": runtime.started_at,
                "source_commit": runtime.config.source_commit,
                "verification": runtime.verify(),
                "scheduler": runtime.last_scheduler_tick,
            }
        if self.command == "GET" and path == "/v1/export":
            return HTTPStatus.OK, runtime.export_state()
        if self.command == "GET":
            parts = path.split("/")
            if len(parts) == 4 and parts[1:3] == ["v1", "tasks"]:
                return HTTPStatus.OK, runtime.query_row("tasks", "task_id", parts[3])
            if len(parts) == 4 and parts[1:3] == ["v1", "grants"]:
                return HTTPStatus.OK, runtime.query_row("work_grants", "grant_id", parts[3])
            if len(parts) == 4 and parts[1:3] == ["v1", "leases"]:
                return HTTPStatus.OK, runtime.query_row("leases", "lease_id", parts[3])
            if len(parts) == 4 and parts[1:3] == ["v1", "receipts"]:
                return HTTPStatus.OK, runtime.query_row("receipts", "receipt_id", parts[3])
        if self.command != "POST":
            raise NotFoundError("route_not_found")
        body = self._body()
        if path == "/v1/epochs":
            return HTTPStatus.OK, runtime.mutate("issue_epoch", {"epoch": body})
        if path == "/v1/capabilities":
            return HTTPStatus.OK, runtime.mutate("declare_capability", {"capability": body})
        if path == "/v1/tasks":
            return HTTPStatus.OK, runtime.mutate("create_task", body)
        if path.startswith("/v1/tasks/"):
            parts = path.split("/")
            if len(parts) != 5:
                raise NotFoundError("route_not_found")
            task_id, action = parts[3], parts[4]
            if action == "approve":
                return HTTPStatus.OK, runtime.mutate("approve_task", {**body, "task_id": task_id})
            if action == "transition":
                return HTTPStatus.OK, runtime.mutate("transition_task", {**body, "task_id": task_id})
            if action == "complete":
                return HTTPStatus.OK, runtime.mutate("complete_task", {**body, "task_id": task_id})
        if path == "/v1/grants":
            return HTTPStatus.OK, runtime.mutate("issue_grant", body)
        if path.startswith("/v1/grants/"):
            parts = path.split("/")
            if len(parts) != 5:
                raise NotFoundError("route_not_found")
            grant_id, action = parts[3], parts[4]
            if action == "accept":
                return HTTPStatus.OK, runtime.mutate("accept_grant", {**body, "grant_id": grant_id})
            if action == "start":
                return HTTPStatus.OK, runtime.mutate("start_task", {**body, "grant_id": grant_id})
        if path.startswith("/v1/leases/"):
            parts = path.split("/")
            if len(parts) != 5:
                raise NotFoundError("route_not_found")
            lease_id, action = parts[3], parts[4]
            if action == "heartbeat":
                return HTTPStatus.OK, runtime.mutate("heartbeat", {**body, "lease_id": lease_id})
            if action == "revoke":
                return HTTPStatus.OK, runtime.mutate("revoke_lease", {**body, "lease_id": lease_id})
        if path == "/v1/receipts":
            return HTTPStatus.OK, runtime.mutate("register_receipt", body)
        if path == "/v1/artifacts":
            return HTTPStatus.OK, runtime.mutate("register_artifact", body)
        if path == "/v1/observations/replicas":
            return HTTPStatus.OK, runtime.mutate("register_replica", body)
        if path == "/v1/observations/pointers":
            return HTTPStatus.OK, runtime.mutate("register_pointer", body)
        if path == "/v1/reconcile":
            return HTTPStatus.OK, runtime.mutate("reconcile", body)
        if path == "/v1/scheduler/tick":
            return HTTPStatus.OK, runtime.tick(body.get("at"))
        if path == "/v1/snapshots":
            observed_at = body.get("observed_at")
            if not isinstance(observed_at, str):
                raise RequestError("observed_at_required")
            return HTTPStatus.OK, runtime.write_snapshot(observed_at)
        raise NotFoundError("route_not_found")

    def _handle(self) -> None:
        try:
            status, payload = self._dispatch()
        except (KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": f"invalid_request:{exc}"})
            return
        except (AuthenticationError, NotFoundError, RequestError) as exc:
            self._json(exc.status, {"status": "error", "error": str(exc)})
            return
        except (AuthorityError, RegistryError, ServiceError, sqlite3.DatabaseError) as exc:
            self._json(getattr(exc, "status", HTTPStatus.CONFLICT), {"status": "error", "error": str(exc)})
            return
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"status": "error", "error": f"internal_error:{type(exc).__name__}"},
            )
            return
        self._json(status, payload)

    do_GET = _handle
    do_POST = _handle
