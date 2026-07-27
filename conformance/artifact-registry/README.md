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

## Authority and task lifecycle

Version 0.2 adds the first operational authority layer:

- monotonically increasing authority epochs;
- capability declarations with explicit operations, limits, and health;
- idempotent task intake;
- validated task-state transitions;
- bounded work grants;
- executor leases, heartbeat, expiry, and revocation;
- `unknown` state when a running executor loses its lease;
- terminal-receipt completion;
- stale-epoch fencing after promotion;
- a hash-chained event journal;
- SQLite triggers preventing event, event-chain, transition, and receipt mutation or deletion.

The materialized task state is mutable, but every accepted transition is append-only and tied to a chained event. The verifier reconstructs each task's transition history and checks that it matches the materialized current state.

### Authority example

```bash
PYTHONPATH=src python3 -m superintendent_registry --db registry.sqlite3 \
  issue-epoch --payload epoch.json

PYTHONPATH=src python3 -m superintendent_registry --db registry.sqlite3 \
  declare-capability --payload capability.json

PYTHONPATH=src python3 -m superintendent_registry --db registry.sqlite3 \
  create-task --payload task.json --created-at 2026-07-27T10:01:00Z
```

A lost lease never silently becomes `failed`. A dispatched or running task becomes `unknown`, requiring reconciliation before resume, takeover, completion, cancellation, or quarantine.
