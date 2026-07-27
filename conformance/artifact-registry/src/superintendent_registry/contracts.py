from __future__ import annotations

import re
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")

SCHEMA_VERSIONS = {
    "artifact": "superintendent-artifact-v1",
    "task": "superintendent-task-v1",
    "work_grant": "superintendent-work-grant-v1",
    "execution_receipt": "superintendent-execution-receipt-v1",
    "terminal_receipt": "superintendent-terminal-receipt-v1",
    "authority_epoch": "superintendent-authority-epoch-v1",
    "capability_declaration": "superintendent-capability-declaration-v1",
    "continuation_capsule": "superintendent-continuation-capsule-v1",
    "reconciliation_result": "superintendent-reconciliation-result-v1",
}

REQUIRED_FIELDS = {
    "artifact": {"schema_version", "artifact_id", "logical_name", "sha256", "size_bytes", "media_type", "authority_class", "created_at", "source"},
    "task": {"schema_version", "task_id", "idempotency_key", "authority_epoch", "capability", "operation", "input"},
    "work_grant": {"schema_version", "grant_id", "task_id", "authority_epoch", "executor", "issued_at", "expires_at", "permitted_actions", "forbidden_actions"},
    "execution_receipt": {"schema_version", "receipt_id", "task_id", "idempotency_key", "authority_epoch", "executor", "state", "created_at"},
    "terminal_receipt": {"schema_version", "receipt_id", "task_id", "idempotency_key", "authority_epoch", "executor", "state", "created_at"},
    "authority_epoch": {"schema_version", "epoch", "issued_at", "issued_by"},
    "capability_declaration": {"schema_version", "capability_id", "executor", "operations", "declared_at"},
    "continuation_capsule": {"schema_version", "capsule_id", "project", "created_at", "authority_cutoff", "current_state", "next_actions"},
    "reconciliation_result": {"schema_version", "reconciliation_id", "logical_name", "decision", "states", "observed_at", "evidence", "actions"},
}


def validate_contract(kind: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if kind not in SCHEMA_VERSIONS:
        return [f"unknown_contract_kind:{kind}"]
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    expected = SCHEMA_VERSIONS[kind]
    if payload.get("schema_version") != expected:
        errors.append(f"schema_version_expected:{expected}")
    for field in sorted(REQUIRED_FIELDS[kind] - payload.keys()):
        errors.append(f"missing:{field}")

    for field in ("artifact_id", "task_id", "idempotency_key", "grant_id", "receipt_id", "capability_id", "capsule_id", "reconciliation_id", "logical_name"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or not ID_RE.fullmatch(value):
                errors.append(f"invalid_identifier:{field}")

    for field in ("sha256", "output_sha256", "input_sha256"):
        if field in payload and payload[field] is not None:
            value = payload[field]
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"invalid_sha256:{field}")

    for field in ("authority_epoch", "epoch", "size_bytes"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, int) or value < 0:
                errors.append(f"invalid_nonnegative_integer:{field}")
    return errors
