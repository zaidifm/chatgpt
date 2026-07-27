#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, urllib.request
from pathlib import Path

TASK_RE = re.compile(r"<!--\s*superintendent-task\s*(\{.*?\})\s*-->", re.S)

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
        return json.loads(Path(comments_file).read_text())
    url = event["issue"]["comments_url"] + "?per_page=100"
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return []
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)

def write_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")

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
            task = json.loads(match.group(1))
        except Exception as exc:
            errors.append(f"task_json_invalid:{type(exc).__name__}")

    required = [
        "schema_version", "task_id", "idempotency_key", "authority_epoch",
        "expires_at", "capability", "operation", "input", "expected_sha256",
    ]
    for key in required:
        if task and key not in task:
            errors.append(f"missing:{key}")

    if task:
        if task.get("schema_version") != "superintendent-task-v1":
            errors.append("unsupported_task_schema")
        if task.get("authority_epoch") != policy["current_epoch"]:
            errors.append("stale_authority_epoch")
        capability = policy["allowed_capabilities"].get(task.get("capability"))
        if not capability:
            errors.append("unsupported_capability")
        elif task.get("operation") != capability["operation"]:
            errors.append("unsupported_operation")
        try:
            now = parse_time(args.now) if args.now else utcnow()
            if parse_time(task["expires_at"]) <= now:
                errors.append("grant_expired")
        except Exception as exc:
            errors.append(f"expires_at_invalid:{type(exc).__name__}")

    idempotency_key = str(task.get("idempotency_key", "unknown"))
    marker = f"<!-- {policy['receipt_marker_prefix']}{idempotency_key} -->"
    comments = get_comments(event, args.comments_file)
    duplicate = any(marker in (comment.get("body") or "") for comment in comments)
    decision = "reject" if errors else ("duplicate" if duplicate else "execute")
    result_sha256 = ""

    if decision == "execute":
        payload = canonical(task["input"])
        limit = policy["allowed_capabilities"][task["capability"]]["max_input_bytes"]
        if len(payload) > limit:
            errors.append("input_too_large")
            decision = "reject"
        else:
            result_sha256 = hashlib.sha256(payload).hexdigest()
            if result_sha256 != task["expected_sha256"]:
                errors.append("expected_sha256_mismatch")
                decision = "reject"
            else:
                artifact_dir = output_dir / "artifact"
                artifact_dir.mkdir(exist_ok=True)
                (artifact_dir / "canonical-input.json").write_bytes(payload + b"\n")
                execution_receipt = {
                    "schema_version": "superintendent-execution-receipt-v1",
                    "state": "completed",
                    "task_id": task["task_id"],
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
        ("task_id", str(task.get("task_id", "unknown"))),
        ("idempotency_key", idempotency_key),
        ("result_sha256", result_sha256),
    ]:
        write_output(name, value)
    print(json.dumps(outcome, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
