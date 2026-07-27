# Owner-Gated Executor Conformance

This lab treats a GitHub issue as a bounded work grant and an `owner-approved` label event as a procedural owner gate.

The executor validates:

- schema;
- authority epoch;
- grant expiry;
- declared capability and operation;
- bounded canonical input size;
- expected deterministic output hash;
- durable idempotency through an existing receipt marker.

Issue-level workflow concurrency serializes duplicate label events. A duplicate event exits without producing another artifact or receipt. Rejected grants receive a typed terminal receipt. Successful grants publish a deterministic artifact and one canonical receipt comment.

This is a **procedural** gate because both ChatGPT connector actions and Ali's manual GitHub actions use `zaidifm`. A cryptographically independent owner gate requires a separate executor identity or owner-only signature.
