#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from receipt_utils import find_valid_receipt, get_paginated_comments

TASK_RE = re.compile(r"<!--\s*superintendent-task\s*(\{.*?\})\s*-->", re.S)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def canonical(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include timezone")
    return parsed.astimezone(dt.timezone.utc)


def get_comments(event: dict, comments_file: str | None) -> list[dict]:
    if comments_file:
        payload = json.loads(Path(comments_file).read_text())
        if not isinstance(payload, list):
            raise ValueError("comments fixture must be a list")
        return payload
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return []
    return get_paginated_comments(event["issue"]["comments_url"], token)


def write_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    delimiter = f"SUPERINTENDENT_{uuid.uuid4().hex}"
    with open(output_file, "a", encoding="utf-8") as handle:
        handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def validate_identifier(task: dict, key: str, errors: list[str]) -> str | None:
    value = task.get(key)
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        errors.append(f"invalid_identifier:{key}")
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--comments-file")
    parser.add_argument("--now")
    args = parser.parse_args()

    event = json.loads(Path(args.event).read_text())
    policy = json.loads(Path(args.policy).read_text())
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    task: dict = {}
    task_parsed = False
    body = event.get("issue", {}).get("body") or ""
    label = event.get("label", {}).get("name")

    if event.get("action") != "labeled":
        errors.append("event_action_not_labeled")
    if label != policy["approval_label"]:
        errors.append("wrong_approval_label")

    match = TASK_RE.search(body)
    if not match:
        errors.append("task_marker_missing")
    else:
        try:
            parsed_task = json.loads(match.group(1))
            if not isinstance(parsed_task, dict):
                errors.append("task_not_object")
            else:
                task = parsed_task
                task_parsed = True
        except Exception as exc:
            errors.append(f"task_json_invalid:{type(exc).__name__}")

    required = [
        "schema_version", "task_id", "idempotency_key", "authority_epoch",
        "expires_at", "capability", "operation", "input", "expected_sha256",
    ]
    if task_parsed:
        for key in required:
            if key not in task:
                errors.append(f"missing:{key}")

        task_id = validate_identifier(task, "task_id", errors)
        idempotency_key_value = validate_identifier(task, "idempotency_key", errors)

        if task.get("schema_version") != "superintendent-task-v1":
            errors.append("unsupported_task_schema")
        if task.get("authority_epoch") != policy["current_epoch"]:
            errors.append("stale_authority_epoch")
        capability = policy["allowed_capabilities"].get(task.get("capability"))
        if not capability:
            errors.append("unsupported_capability")
        elif task.get("operation") != capability["operation"]:
            errors.append("unsupported_operation")
        expected_sha256 = task.get("expected_sha256")
        if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
            errors.append("expected_sha256_invalid")
        try:
            now = parse_time(args.now) if args.now else utcnow()
            if parse_time(task["expires_at"]) <= now:
                errors.append("grant_expired")
        except Exception as exc:
            errors.append(f"expires_at_invalid:{type(exc).__name__}")
    else:
        task_id = None
        idempotency_key_value = None

    fallback_key = "invalid-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    idempotency_key = idempotency_key_value or fallback_key
    marker = f"<!-- {policy['receipt_marker_prefix']}{idempotency_key} -->"

    comments = get_comments(event, args.comments_file)
    duplicate_receipt = find_valid_receipt(comments, marker=marker, task=task)
    decision = "reject" if errors else ("duplicate" if duplicate_receipt else "execute")
    result_sha256 = ""

    if decision == "execute":
        payload = canonical(task["input"])
        limit = policy["allowed_capabilities"][task["capability"]]["max_input_bytes"]
        if len(payload) > limit:
            errors.append("input_too_large")
            decision = "reject"
        else:
            result_sha256 = hashlib.sha256(payload).hexdigest()
            if result_sha256 != task["expected_sha256"].lower():
                errors.append("expected_sha256_mismatch")
                decision = "reject"
            else:
                artifact_dir = output_dir / "artifact"
                artifact_dir.mkdir(exist_ok=True)
                (artifact_dir / "canonical-input.json").write_bytes(payload + b"\n")
                execution_receipt = {
                    "schema_version": "superintendent-execution-receipt-v1",
                    "state": "completed",
                    "task_id": task_id,
                    "idempotency_key": idempotency_key,
                    "authority_epoch": task["authority_epoch"],
                    "capability": task["capability"],
                    "operation": task["operation"],
                    "input_sha256": result_sha256,
                    "output_sha256": result_sha256,
                    "source_sha": os.environ.get("GITHUB_SHA"),
                    "run_id": os.environ.get("GITHUB_RUN_ID"),
                    "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                    "executor": "github-actions",
                }
                (artifact_dir / "execution-receipt.json").write_text(
                    json.dumps(execution_receipt, indent=2, sort_keys=True) + "\n"
                )

    outcome = {
        "decision": decision,
        "errors": errors,
        "task": task,
        "marker": marker,
        "idempotency_key": idempotency_key,
        "result_sha256": result_sha256,
        "issue_number": event.get("issue", {}).get("number"),
        "repository": event.get("repository", {}).get("full_name"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "source_sha": os.environ.get("GITHUB_SHA"),
    }
    (output_dir / "outcome.json").write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n")
    for name, value in [
        ("decision", decision),
        ("task_id", task_id or "invalid"),
        ("idempotency_key", idempotency_key),
        ("result_sha256", result_sha256),
    ]:
        write_output(name, value)
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
