from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

SERVICE_VERSION = "0.3.0"
MAX_REQUEST_BYTES = 2_097_152
MAX_ARTIFACT_BYTES = 1_048_576


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


class ServiceError(RuntimeError):
    status = HTTPStatus.CONFLICT


class AuthenticationError(ServiceError):
    status = HTTPStatus.UNAUTHORIZED


class RequestError(ServiceError):
    status = HTTPStatus.BAD_REQUEST


class NotFoundError(ServiceError):
    status = HTTPStatus.NOT_FOUND


class ServiceLockError(ServiceError):
    status = HTTPStatus.LOCKED


class ProcessLock:
    """Advisory process lock. The kernel releases it when a process dies."""

    def __init__(self, path: str | Path, metadata: dict[str, Any]):
        self.path = Path(path)
        self.metadata = metadata
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            holder = handle.read().strip()
            handle.close()
            raise ServiceLockError(f"single_writer_lock_held:{holder or 'unknown'}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(self.metadata, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


@dataclass(frozen=True)
class ServiceConfig:
    db_path: Path
    bearer_token: str
    host: str = "127.0.0.1"
    port: int = 0
    lock_path: Path | None = None
    snapshot_dir: Path | None = None
    scheduler_interval_seconds: float = 1.0
    source_commit: str | None = None
    startup_reconcile: bool = True

    def normalized(self) -> "ServiceConfig":
        import ipaddress

        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise RequestError("service_host_must_be_numeric_loopback") from exc
        if not address.is_loopback:
            raise RequestError("service_host_not_loopback")
        if len(self.bearer_token) < 16:
            raise RequestError("bearer_token_too_short")
        if not 0 <= self.port <= 65535:
            raise RequestError("invalid_port")
        db_path = self.db_path.resolve()
        return ServiceConfig(
            db_path=db_path,
            bearer_token=self.bearer_token,
            host=self.host,
            port=self.port,
            lock_path=(self.lock_path or db_path.with_suffix(db_path.suffix + ".lock")).resolve(),
            snapshot_dir=(self.snapshot_dir or db_path.parent / "snapshots").resolve(),
            scheduler_interval_seconds=max(0.0, self.scheduler_interval_seconds),
            source_commit=self.source_commit,
            startup_reconcile=self.startup_reconcile,
        )
