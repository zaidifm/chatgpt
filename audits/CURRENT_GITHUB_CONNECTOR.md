---
document_type: GitHub connector current-baseline pointer
snapshot_local: "2026-07-26 PDT"
repository: zaidifm/chatgpt
authority: observed_behavioral_baseline
---

# Current GitHub Connector Baseline

The authoritative dated baseline is `2026-07-26-GITHUB-CONNECTOR-CAPABILITY-AUDIT.md`.

Key status:

- authenticated as `zaidifm` with repository admin permission;
- text, Git-object, issue, PR, review, reaction, merge, Actions, log, artifact, and rerun operations work;
- Actions artifact download is a real GitHub-to-VM file bridge;
- the media lab completed a binary round trip through Actions;
- direct VM GitHub networking remains blocked;
- no native mounted-file upload action exists;
- larger model-mediated binary base64 writes failed integrity checks;
- binary `fetch_blob` is broken, while `fetch_file(..., base64)` works;
- branch search is nonfunctional in this repository;
- legacy combined commit status does not report Actions Checks;
- error and REST/GraphQL ID normalization remain inconsistent.

Do not overwrite the dated report. Replace this pointer only after a new immutable audit and comparison exist.
