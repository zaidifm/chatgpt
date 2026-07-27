#!/usr/bin/env python3
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXECUTOR = ROOT / "execute_task.py"
POLICY = ROOT / "policy.json"
INPUT = {"alpha": 1, "beta": [2, 3], "message": "bounded executor conformance"}
CANONICAL = json.dumps(INPUT, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
EXPECTED = hashlib.sha256(CANONICAL).hexdigest()
BASE = {
    "schema_version": "superintendent-task-v1",
    "task_id": "SELFTEST-001",
    "idempotency_key": "selftest-001",
    "authority_epoch": 1,
    "expires_at": "2030-01-01T00:00:00Z",
    "capability": "canonical-json-sha256-v1",
    "operation": "canonicalize-and-hash",
    "input": INPUT,
    "expected_sha256": EXPECTED,
}

def run(name, task, comments=None, expected_decision="execute"):
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        event = {
            "action": "labeled",
            "label": {"name": "owner-approved"},
            "issue": {
                "number": 1,
                "body": "<!-- superintendent-task\n" + json.dumps(task) + "\n-->",
                "comments_url": "https://invalid.local/comments",
            },
            "repository": {"full_name": "zaidifm/chatgpt"},
        }
        (directory / "event.json").write_text(json.dumps(event))
        (directory / "comments.json").write_text(json.dumps(comments or []))
        output = directory / "output"
        subprocess.run(
            [
                sys.executable,
                str(EXECUTOR),
                "--event", str(directory / "event.json"),
                "--policy", str(POLICY),
                "--out", str(output),
                "--comments-file", str(directory / "comments.json"),
                "--now", "2026-07-26T12:00:00Z",
            ],
            check=True,
        )
        outcome = json.loads((output / "outcome.json").read_text())
        assert outcome["decision"] == expected_decision, (name, outcome)
        if expected_decision == "execute":
            assert (output / "artifact/execution-receipt.json").exists()
            assert outcome["result_sha256"] == EXPECTED
        return outcome

run("valid", BASE)
stale = dict(BASE); stale["authority_epoch"] = 0
run("stale_epoch", stale, expected_decision="reject")
expired = dict(BASE); expired["expires_at"] = "2020-01-01T00:00:00Z"
run("expired", expired, expected_decision="reject")
unsupported = dict(BASE); unsupported["capability"] = "shell-anything-v1"
run("unsupported", unsupported, expected_decision="reject")
marker = "<!-- superintendent-receipt:selftest-001 -->"
run("duplicate", BASE, comments=[{"body": marker}], expected_decision="duplicate")
print(json.dumps({"status": "pass", "expected_sha256": EXPECTED, "cases": 5}, sort_keys=True))
