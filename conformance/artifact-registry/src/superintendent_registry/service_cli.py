from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from .canonical import canonical_json_bytes
from .service import ResidentRuntime, ServiceConfig, ServiceError, _atomic_write


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Run the loopback-only Superintendent resident service")
    root.add_argument("--db", required=True)
    root.add_argument("--host", default="127.0.0.1")
    root.add_argument("--port", type=int, default=0)
    root.add_argument("--lock-file")
    root.add_argument("--snapshot-dir")
    root.add_argument("--scheduler-interval", type=float, default=1.0)
    root.add_argument("--source-commit")
    root.add_argument("--token-file")
    root.add_argument("--ready-file")
    root.add_argument("--no-startup-reconcile", action="store_true")
    return root


def _token(args: argparse.Namespace) -> str:
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    value = os.environ.get("SUPERINTENDENT_BEARER_TOKEN")
    if not value:
        raise ServiceError("bearer_token_missing")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    runtime = ResidentRuntime(
        ServiceConfig(
            db_path=Path(args.db),
            bearer_token=_token(args),
            host=args.host,
            port=args.port,
            lock_path=Path(args.lock_file) if args.lock_file else None,
            snapshot_dir=Path(args.snapshot_dir) if args.snapshot_dir else None,
            scheduler_interval_seconds=args.scheduler_interval,
            source_commit=args.source_commit,
            startup_reconcile=not args.no_startup_reconcile,
        )
    )
    stop_requested = threading.Event()

    def request_stop(_signum=None, _frame=None):
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        runtime.start(background=True)
        if args.ready_file:
            host, port = runtime.address
            ready = {
                "status": "ready",
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "source_commit": args.source_commit,
            }
            _atomic_write(Path(args.ready_file), canonical_json_bytes(ready) + b"\n")
        print(json.dumps({"status": "ready", "url": runtime.base_url}, sort_keys=True), flush=True)
        stop_requested.wait()
        return 0
    except ServiceError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 73
    finally:
        runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
