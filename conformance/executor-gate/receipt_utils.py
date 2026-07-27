from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Callable, Iterable

ACTIONS_RECEIPT_AUTHOR = "github-actions[bot]"
RECEIPT_SCHEMA = "superintendent-terminal-receipt-v1"
RECEIPT_RE = re.compile(
    r"###\s+Superintendent terminal receipt\s*```json\s*(\{.*?\})\s*```",
    re.S,
)
REQUIRED_RECEIPT_FIELDS = {
    "schema_version",
    "state",
    "task_id",
    "idempotency_key",
    "authority_epoch",
    "capability",
    "source_sha",
    "run_id",
    "run_attempt",
    "result_sha256",
    "artifact_id",
    "artifact_url",
    "errors",
}


def _paged_url(url: str, page: int) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["per_page"] = ["100"]
    query["page"] = [str(page)]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query, doseq=True), parsed.fragment)
    )


def get_paginated_comments(
    url: str,
    token: str,
    *,
    opener: Callable[..., object] | None = None,
    max_pages: int = 1000,
) -> list[dict]:
    open_request = opener or urllib.request.urlopen
    comments: list[dict] = []
    for page in range(1, max_pages + 1):
        request = urllib.request.Request(
            _paged_url(url, page),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with open_request(request, timeout=20) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("comments endpoint returned non-list payload")
        comments.extend(batch)
        if len(batch) < 100:
            return comments
    raise RuntimeError("comment pagination exceeded safety limit")


def parse_valid_receipt_comment(
    comment: dict,
    *,
    marker: str,
    task: dict,
) -> dict | None:
    if (comment.get("user") or {}).get("login") != ACTIONS_RECEIPT_AUTHOR:
        return None
    body = comment.get("body") or ""
    if marker not in body:
        return None
    match = RECEIPT_RE.search(body)
    if not match:
        return None
    try:
        receipt = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(receipt, dict) or not REQUIRED_RECEIPT_FIELDS.issubset(receipt):
        return None
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        return None
    if receipt.get("state") not in {"completed", "rejected"}:
        return None
    for key in ("task_id", "idempotency_key", "authority_epoch", "capability"):
        if receipt.get(key) != task.get(key):
            return None
    if not isinstance(receipt.get("errors"), list):
        return None
    return receipt


def find_valid_receipt(
    comments: Iterable[dict],
    *,
    marker: str,
    task: dict,
) -> dict | None:
    for comment in comments:
        receipt = parse_valid_receipt_comment(comment, marker=marker, task=task)
        if receipt is not None:
            return receipt
    return None
