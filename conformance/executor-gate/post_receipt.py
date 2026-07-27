#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, urllib.request
from pathlib import Path

def api(url: str, method="GET", data=None):
    token = os.environ["GITHUB_TOKEN"]
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response) if response.length != 0 else None

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
        print("duplicate receipt already present; no comment posted")
        return 0

    issue = event["issue"]
    comments = api(issue["comments_url"] + "?per_page=100")
    if any(outcome["marker"] in (comment.get("body") or "") for comment in comments):
        print("receipt appeared during run; no duplicate posted")
        return 0

    task = outcome.get("task") or {}
    state = "completed" if decision == "execute" else "rejected"
    receipt = {
        "schema_version": "superintendent-terminal-receipt-v1",
        "state": state,
        "task_id": task.get("task_id"),
        "idempotency_key": task.get("idempotency_key"),
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
    posted = api(issue["comments_url"], method="POST", data={"body": body})
    print(json.dumps({"comment_id": posted.get("id"), "state": state}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
