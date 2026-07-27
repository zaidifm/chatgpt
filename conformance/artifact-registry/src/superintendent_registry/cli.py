from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .database import connect
from .registry import Registry, RegistryError, observe_file


def load_json(value: str) -> Any:
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(value)


def emit(value: Any) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="superintendent-registry")
    root.add_argument("--db", required=True)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("init")

    artifact = sub.add_parser("register-artifact")
    artifact.add_argument("--logical-name", required=True)
    artifact.add_argument("--path", required=True)
    artifact.add_argument("--authority-class", required=True)
    artifact.add_argument("--source", required=True)
    artifact.add_argument("--created-at", required=True)
    artifact.add_argument("--media-type")
    artifact.add_argument("--inactive", action="store_true")

    active = sub.add_parser("set-active")
    active.add_argument("--artifact-id", required=True)
    active.add_argument("--active", choices=["true", "false"], required=True)
    active.add_argument("--observed-at", required=True)

    replica = sub.add_parser("register-replica")
    replica.add_argument("--artifact-id", required=True)
    replica.add_argument("--plane", required=True)
    replica.add_argument("--locator", required=True)
    replica.add_argument("--persistence-state", required=True)
    replica.add_argument("--observed-at", required=True)
    replica.add_argument("--observed-sha256")
    replica.add_argument("--observed-size-bytes", type=int)
    replica.add_argument("--index-state")
    replica.add_argument("--optional", action="store_true")
    replica.add_argument("--last-error")
    replica.add_argument("--observe-path")

    pointer = sub.add_parser("register-pointer")
    pointer.add_argument("--pointer-name", required=True)
    pointer.add_argument("--plane", required=True)
    pointer.add_argument("--locator", required=True)
    pointer.add_argument("--target-artifact-id")
    pointer.add_argument("--declared-sha256")
    pointer.add_argument("--observed-sha256")
    pointer.add_argument("--observed-at", required=True)

    receipt = sub.add_parser("register-receipt")
    receipt.add_argument("--payload", required=True)
    receipt.add_argument("--created-at", required=True)

    contract = sub.add_parser("register-contract")
    contract.add_argument("--kind", required=True)
    contract.add_argument("--payload", required=True)
    contract.add_argument("--created-at", required=True)

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--logical-name", required=True)
    reconcile.add_argument("--required-plane", action="append", default=[])
    reconcile.add_argument("--observed-at", required=True)

    export = sub.add_parser("export")
    export.add_argument("--output")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    connection = connect(args.db)
    registry = Registry(connection)
    try:
        if args.command == "init":
            emit({"status": "initialized", "db": str(Path(args.db))})
        elif args.command == "register-artifact":
            payload = registry.register_artifact_file(logical_name=args.logical_name, path=args.path,
                authority_class=args.authority_class, source=load_json(args.source), created_at=args.created_at,
                media_type=args.media_type, active=not args.inactive)
            emit(payload)
        elif args.command == "set-active":
            registry.set_artifact_active(args.artifact_id, args.active == "true", args.observed_at)
            emit({"artifact_id": args.artifact_id, "active": args.active == "true"})
        elif args.command == "register-replica":
            observed_sha = args.observed_sha256
            observed_size = args.observed_size_bytes
            if args.observe_path:
                observation = observe_file(args.observe_path)
                observed_sha = observation.sha256
                observed_size = observation.size_bytes
            replica_id = registry.register_replica(artifact_id=args.artifact_id, plane=args.plane,
                locator=load_json(args.locator), persistence_state=args.persistence_state, observed_at=args.observed_at,
                observed_sha256=observed_sha, observed_size_bytes=observed_size, index_state=args.index_state,
                required=not args.optional, last_error=args.last_error)
            emit({"replica_id": replica_id})
        elif args.command == "register-pointer":
            pointer_id = registry.register_pointer(pointer_name=args.pointer_name, plane=args.plane,
                locator=load_json(args.locator), target_artifact_id=args.target_artifact_id,
                declared_sha256=args.declared_sha256, observed_sha256=args.observed_sha256, observed_at=args.observed_at)
            emit({"pointer_id": pointer_id})
        elif args.command == "register-receipt":
            emit({"receipt_id": registry.register_terminal_receipt(load_json(args.payload), args.created_at)})
        elif args.command == "register-contract":
            emit({"record_id": registry.register_contract(args.kind, load_json(args.payload), args.created_at)})
        elif args.command == "reconcile":
            emit(registry.reconcile(logical_name=args.logical_name, required_planes=args.required_plane, observed_at=args.observed_at))
        elif args.command == "export":
            result = registry.export_state()
            if args.output:
                Path(args.output).write_bytes(canonical_json_bytes(result) + b"\n")
                emit({"status": "exported", "output": args.output})
            else:
                emit(result)
        else:
            raise AssertionError(args.command)
    except RegistryError as exc:
        emit({"status": "error", "error": str(exc)})
        return 2
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
