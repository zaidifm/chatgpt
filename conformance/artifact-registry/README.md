# Superintendent Artifact and Receipt Registry

This is the first inspectable authority-core prototype for Superintendent. It does not attempt to be a distributed database. It establishes the contracts and reconciliation behavior that disposable executors, Git, Library, and later node agents must obey.

## Authority model

- **Git and the registry** carry authoritative content identity and operational state.
- **Library** is a durable retrieval and handoff replica, not version control.
- **`/mnt/data`** is disposable execution space and never authoritative.
- Every artifact is content-addressed by SHA-256.
- Every mutation appends a hashed event.
- Every reconciliation emits a canonical machine-readable receipt.
- Conflicting bytes, receipts, or simultaneous active versions fail closed into quarantine.

## Implemented contracts

JSON Schema files are provided for:

- artifact;
- task;
- work grant;
- execution receipt;
- terminal receipt;
- authority epoch;
- capability declaration;
- continuation capsule;
- reconciliation result.

## Reconciliation states

- `verified:<plane>`
- `persisted_not_indexed`
- `stale_pointer`
- `missing_replica`
- `changed_bytes`
- `duplicate_logical_name`
- `conflicting_receipts`
- `missing_artifact`

Decisions are:

- `accepted`
- `accepted_pending_index`
- `pointer_update_required`
- `repair_required`
- `quarantined`
- `missing_authority`

## Local test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## CLI example

```bash
PYTHONPATH=src python3 -m superintendent_registry --db registry.sqlite3 init
PYTHONPATH=src python3 -m superintendent_registry --db registry.sqlite3 \
  register-artifact \
  --logical-name example/current \
  --path README.md \
  --authority-class git-authoritative \
  --source '{"repository":"zaidifm/chatgpt","commit":"...","path":"README.md"}' \
  --created-at 2026-07-27T08:00:00Z
```

External connectors do not run inside this process. Their observations are supplied as explicit receipts containing stable IDs, locators, hashes, sizes, persistence state, and indexing state. This separation prevents the registry from pretending that connector presence equals connector health.
