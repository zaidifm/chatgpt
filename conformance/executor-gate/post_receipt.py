#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

from receipt_utils import find_valid_receipt, get_paginated_comments


def post_json(url: str, token: str, data: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-url", default="")
    args = parser.parse_args()

    event = json.loads(Path(args.event).read_text())
    outcome = json.loads(Path(args.outcome).read_text())
    decision = outcome["decision"]
    if decision == "duplicate":
        print("authenticated receipt already present; no comment posted")
        return 0

    token = os.environ["GITHUB_TOKEN"]
    issue = event["issue"]
    task = outcome.get("task") or {}
    idempotency_key = str(outcome["idempotency_key"])
    comments = get_paginated_comments(issue["comments_url"], token)
    if find_valid_receipt(comments, marker=outcome["marker"], task=task) is not None:
        print("authenticated receipt appeared during run; no duplicate posted")
        return 0

    state = "completed" if decision == "execute" else "rejected"
    receipt = {
        "schema_version": "superintendent-terminal-receipt-v1",
        "state": state,
        "task_id": task.get("task_id"),
        "idempotency_key": idempotency_key,
        "authority_epoch": task.get("authority_epoch"),
        "capability": task.get("capability"),
        "source_sha": outcome.get("source_sha"),
        "run_id": outcome.get("run_id"),
        "run_attempt": outcome.get("run_attempt"),
        "result_sha256": outcome.get("result_sha256") or None,
        "artifact_id": args.artifact_id or None,
        "artifact_url": args.artifact_url or None,
        "errors": outcome.get("errors") or [],
    }
    body = (
        outcome["marker"]
        + "\n\n### Superintendent terminal receipt\n\n```json\n"
        + json.dumps(receipt, indent=2, sort_keys=True)
        + "\n```"
    )
    posted = post_json(issue["comments_url"], token, {"body": body})
    print(json.dumps({"comment_id": posted.get("id"), "state": state}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
