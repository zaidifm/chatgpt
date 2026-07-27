from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .authority import AuthorityError
from .authority_registry import AuthorityRegistry as Registry
from .canonical import canonical_json_bytes
from .database import connect
from .registry import RegistryError, observe_file


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

    epoch = sub.add_parser("issue-epoch")
    epoch.add_argument("--payload", required=True)

    capability = sub.add_parser("declare-capability")
    capability.add_argument("--payload", required=True)

    task = sub.add_parser("create-task")
    task.add_argument("--payload", required=True)
    task.add_argument("--created-at", required=True)

    transition = sub.add_parser("transition-task")
    transition.add_argument("--task-id", required=True)
    transition.add_argument("--to-state", required=True)
    transition.add_argument("--at", required=True)
    transition.add_argument("--actor", required=True)
    transition.add_argument("--evidence", default="{}")
    transition.add_argument("--expected-from")

    grant = sub.add_parser("issue-grant")
    grant.add_argument("--payload", required=True)
    grant.add_argument("--created-at", required=True)

    accept = sub.add_parser("accept-grant")
    accept.add_argument("--grant-id", required=True)
    accept.add_argument("--at", required=True)

    start = sub.add_parser("start-task")
    start.add_argument("--grant-id", required=True)
    start.add_argument("--at", required=True)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--lease-id", required=True)
    heartbeat.add_argument("--at", required=True)

    expire = sub.add_parser("expire-leases")
    expire.add_argument("--at", required=True)

    revoke = sub.add_parser("revoke-lease")
    revoke.add_argument("--lease-id", required=True)
    revoke.add_argument("--at", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--actor", default="owner")

    complete = sub.add_parser("complete-task")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--receipt-id", required=True)
    complete.add_argument("--at", required=True)
    complete.add_argument("--actor", required=True)

    sub.add_parser("verify-journal")
    sub.add_parser("verify-state")

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
            emit(registry.register_artifact_file(
                logical_name=args.logical_name,
                path=args.path,
                authority_class=args.authority_class,
                source=load_json(args.source),
                created_at=args.created_at,
                media_type=args.media_type,
                active=not args.inactive,
            ))
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
            emit({"replica_id": registry.register_replica(
                artifact_id=args.artifact_id,
                plane=args.plane,
                locator=load_json(args.locator),
                persistence_state=args.persistence_state,
                observed_at=args.observed_at,
                observed_sha256=observed_sha,
                observed_size_bytes=observed_size,
                index_state=args.index_state,
                required=not args.optional,
                last_error=args.last_error,
            )})
        elif args.command == "register-pointer":
            emit({"pointer_id": registry.register_pointer(
                pointer_name=args.pointer_name,
                plane=args.plane,
                locator=load_json(args.locator),
                target_artifact_id=args.target_artifact_id,
                declared_sha256=args.declared_sha256,
                observed_sha256=args.observed_sha256,
                observed_at=args.observed_at,
            )})
        elif args.command == "register-receipt":
            emit({"receipt_id": registry.register_terminal_receipt(load_json(args.payload), args.created_at)})
        elif args.command == "register-contract":
            emit({"record_id": registry.register_contract(args.kind, load_json(args.payload), args.created_at)})
        elif args.command == "reconcile":
            emit(registry.reconcile(logical_name=args.logical_name, required_planes=args.required_plane, observed_at=args.observed_at))
        elif args.command == "issue-epoch":
            emit({"epoch": registry.authority.issue_epoch(load_json(args.payload))})
        elif args.command == "declare-capability":
            emit({"declaration_id": registry.authority.declare_capability(load_json(args.payload))})
        elif args.command == "create-task":
            emit({"task_id": registry.authority.create_task(load_json(args.payload), args.created_at)})
        elif args.command == "transition-task":
            emit({"transition_id": registry.authority.transition_task(
                task_id=args.task_id,
                to_state=args.to_state,
                occurred_at=args.at,
                actor=args.actor,
                evidence=load_json(args.evidence),
                expected_from=args.expected_from,
            )})
        elif args.command == "issue-grant":
            emit({"grant_id": registry.authority.issue_grant(load_json(args.payload), args.created_at)})
        elif args.command == "accept-grant":
            emit({"lease_id": registry.authority.accept_grant(args.grant_id, args.at)})
        elif args.command == "start-task":
            emit({"task_id": registry.authority.start_task(args.grant_id, args.at)})
        elif args.command == "heartbeat":
            registry.authority.heartbeat(args.lease_id, args.at)
            emit({"lease_id": args.lease_id, "heartbeat_at": args.at})
        elif args.command == "expire-leases":
            emit({"expired_lease_ids": registry.authority.expire_leases(args.at)})
        elif args.command == "revoke-lease":
            registry.authority.revoke_lease(args.lease_id, args.at, args.reason, args.actor)
            emit({"lease_id": args.lease_id, "state": "revoked"})
        elif args.command == "complete-task":
            emit({"receipt_id": registry.authority.complete_task(args.task_id, args.receipt_id, args.at, args.actor)})
        elif args.command == "verify-journal":
            emit(registry.verify_event_chain())
        elif args.command == "verify-state":
            emit(registry.authority.verify_materialized_state())
        elif args.command == "export":
            result = registry.export_state()
            if args.output:
                Path(args.output).write_bytes(canonical_json_bytes(result) + b"\n")
                emit({"status": "exported", "output": args.output})
            else:
                emit(result)
        else:
            raise AssertionError(args.command)
    except (RegistryError, AuthorityError) as exc:
        emit({"status": "error", "error": str(exc)})
        return 2
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
