#!/usr/bin/env python3
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import receipt_utils

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


def terminal_comment(task: dict, *, author: str = "github-actions[bot]") -> dict:
    key = task["idempotency_key"]
    marker = f"<!-- superintendent-receipt:{key} -->"
    receipt = {
        "schema_version": "superintendent-terminal-receipt-v1",
        "state": "completed",
        "task_id": task["task_id"],
        "idempotency_key": key,
        "authority_epoch": task["authority_epoch"],
        "capability": task["capability"],
        "source_sha": "abc123",
        "run_id": "123",
        "run_attempt": "1",
        "result_sha256": task["expected_sha256"],
        "artifact_id": "456",
        "artifact_url": "https://example.invalid/artifact",
        "errors": [],
    }
    return {
        "user": {"login": author},
        "body": marker + "\n\n### Superintendent terminal receipt\n\n```json\n" + json.dumps(receipt) + "\n```",
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
            stdout=subprocess.DEVNULL,
        )
        outcome = json.loads((output / "outcome.json").read_text())
        assert outcome["decision"] == expected_decision, (name, outcome)
        if expected_decision == "execute":
            assert (output / "artifact/execution-receipt.json").exists()
            assert outcome["result_sha256"] == EXPECTED
        return outcome


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_pagination():
    page_two_task = dict(BASE)
    page_two_task["task_id"] = "PAGE-TWO"
    page_two_task["idempotency_key"] = "page-two"
    pages = {
        1: [{"id": index} for index in range(100)],
        2: [terminal_comment(page_two_task)],
    }

    def opener(request, timeout=20):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        page = int(query["page"][0])
        return FakeResponse(json.dumps(pages[page]).encode())

    comments = receipt_utils.get_paginated_comments(
        "https://example.invalid/comments",
        "token",
        opener=opener,
    )
    assert len(comments) == 101
    marker = "<!-- superintendent-receipt:page-two -->"
    assert receipt_utils.find_valid_receipt(
        comments,
        marker=marker,
        task=page_two_task,
    ) is not None


run("valid", BASE)
stale = dict(BASE); stale["authority_epoch"] = 0
run("stale_epoch", stale, expected_decision="reject")
expired = dict(BASE); expired["expires_at"] = "2020-01-01T00:00:00Z"
run("expired", expired, expected_decision="reject")
unsupported = dict(BASE); unsupported["capability"] = "shell-anything-v1"
run("unsupported", unsupported, expected_decision="reject")
run("empty_task", {}, expected_decision="reject")
injected = dict(BASE); injected["task_id"] = "x\ndecision=reject"
run("output_injection", injected, expected_decision="reject")
forged = {"user": {"login": "mallory"}, "body": "<!-- superintendent-receipt:selftest-001 -->"}
run("forged_marker", BASE, comments=[forged], expected_decision="execute")
malformed_bot = {"user": {"login": "github-actions[bot]"}, "body": "<!-- superintendent-receipt:selftest-001 -->"}
run("malformed_bot_receipt", BASE, comments=[malformed_bot], expected_decision="execute")
run("authenticated_duplicate", BASE, comments=[terminal_comment(BASE)], expected_decision="duplicate")
test_pagination()
print(json.dumps({"status": "pass", "expected_sha256": EXPECTED, "cases": 10}, sort_keys=True))
