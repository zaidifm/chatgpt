---
document_type: Superintendent executor conformance report
snapshot_local: "2026-07-26 PDT"
owner: Ali Zaidi
scope: "ChatGPT disposable Linux VM; timeout, cancellation, retry, and checkpoint semantics"
authority: observed_behavioral_baseline
privacy: public_summary_private_raw_evidence
---

# Timeout, Cancellation, Retry, and Checkpoint Conformance

## Bottom line

A lost or terminated caller is not reliable evidence that work stopped. In this harness:

- low `container.exec` `timeout` parameter values did not interrupt commands in the tested calls;
- explicit process-group cancellation terminated foreground and same-group work cleanly;
- a child deliberately detached into a new session survived parent cancellation, was reparented to PID 1, and completed;
- blindly retrying after a side effect but before a terminal receipt duplicated the side effect;
- a durable idempotency record prevented duplication and allowed a second attempt to finalize the same logical task;
- atomic chunk checkpoints resumed after cancellation and produced output byte-identical to an uninterrupted run.

The required Superintendent state after losing contact with a nonterminal executor is therefore **`unknown`**, not `failed` and not `cancelled`. Reconciliation must precede retry.

## Probe results

### Tool timeout parameter

Two calls were deliberately given timeout parameters shorter than their commands:

- `timeout=1` around `sleep 3`;
- `timeout=2000` around an 8.6-second worker.

Both commands completed. The exact parameter semantics may involve undocumented units, clamping, or non-enforcement. The operational conclusion is narrower: **this parameter was not a usable cancellation boundary in the tested calls**.

### Explicit foreground cancellation

GNU `timeout` terminated the worker with `SIGTERM`, returned exit code `124`, and left no final output. The worker's fsynced journal retained the terminal signal event.

### Process-tree behavior

A child in the launcher's process group received `SIGTERM` and stopped without producing final output.

A child started with `setsid` had its own session and process group. When the parent timed out, the child continued under PID 1 and produced its final receipt. Thus, cancellation must target the complete authorized process tree or use a supervised cgroup/lease model; parent termination alone is insufficient.

### Retry ambiguity

The naïve task incremented a durable counter before sleeping. It was terminated before writing a receipt. Retrying incremented the counter again. Final state:

```text
business side effects: 2
terminal receipts: 1
```

This is the canonical dangerous `unknown` state: the caller lacks a receipt, but the effect already occurred.

### Idempotent retry

The corrected worker stored an idempotency key and side-effect value transactionally in SQLite. The first attempt committed the effect and was terminated. The second attempt found the existing task record, did not repeat the effect, and completed the receipt.

```text
business counter: 1
attempts: 2
task state: completed
receipt value: 1
```

### Checkpoint recovery

A deterministic twenty-chunk job was interrupted after six atomic chunks. The next attempt validated the checkpoint, resumed from the remaining chunks, and produced a 1,310,720-byte output with the same SHA-256 as an uninterrupted baseline:

```text
8fe0475a653b277b73db10b981f8e4c69b67f904294cde89f9141492f56cf289
```

## Required executor state machine

```text
submitted
  -> running
  -> completed | rejected | cancelled

running + caller/tool loss
  -> unknown

unknown
  -> reconcile terminal receipt
  -> reconcile live lease/process
  -> reconcile checkpoint/journal
  -> reconcile committed side effect
  -> resume | finalize | cancel | quarantine
```

`unknown` must never be converted directly into automatic retry unless all of the following hold:

1. the logical task has a durable idempotency key;
2. effects are pure, transactional, or recorded with the task key;
3. no valid active lease or detached executor remains;
4. retry resumes or finalizes the task rather than replaying the effect;
5. the new attempt emits one canonical terminal receipt.

## Superintendent implications

Every work grant should include:

- task ID;
- idempotency key;
- authority epoch;
- executor identity;
- process/lease identity;
- issued and expiry timestamps;
- side-effect class;
- checkpoint location;
- terminal-receipt destination.

Every executor should:

- journal `started`, checkpoint, committed-effect, signal, and terminal events with fsync or equivalent durability;
- keep temporary output private until validation and atomic publication;
- expose reconciliation separately from execution;
- make duplicate delivery safe;
- refuse a retry while an unexpired lease remains unless an owner-authorized takeover increments the fencing epoch.

## Rubric

| Dimension | Score |
|---|---:|
| Contract | 2/2 |
| Authority | 2/2 |
| Correctness | 2/2 |
| Idempotency | 2/2 |
| Failure containment | 2/2 |
| Recovery | 2/2 |
| Provenance | 2/2 |
| Portability | 1/2 |
| **Total** | **15/16** |

The test passes for the ChatGPT VM. Portability remains partial until the same logical task and receipt contract are exercised in GitHub Actions and reconciled across both nodes.

The full scripts, raw journals, and outputs are retained in the private Library evidence package. This public repository contains the behavioral summary and normalized results.
