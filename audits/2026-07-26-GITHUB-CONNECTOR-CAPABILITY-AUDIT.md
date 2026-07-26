---
document_type: ChatGPT GitHub connector capability audit
snapshot_local: "2026-07-26 PDT"
snapshot_utc: "2026-07-26"
owner: Ali Zaidi
repository: zaidifm/chatgpt
scope: "Authorized destructive testing confined to zaidifm/chatgpt"
authority: observed_behavioral_baseline
privacy: public_repository
---

# GitHub Connector Capability Audit

## Bottom line

The GitHub connector is a substantial repository-control plane, not merely a search integration. In the authorized `zaidifm/chatgpt` laboratory it successfully initialized an empty repository, created and rewrote Git history, managed issues and pull requests, submitted reviews and inline threads, ran and re-ran GitHub Actions, downloaded artifacts into the ChatGPT VM, and round-tripped generated binary media back into GitHub.

Its principal walls are architectural rather than permission-related:

1. repository access remains tool-mediated and does not give the ChatGPT Linux VM GitHub DNS, sockets, credentials, or `git clone` access;
2. there is no connector action that uploads a mounted local file directly to GitHub;
3. binary writes require model-mediated base64 content in a tool argument, which is unsuitable and demonstrably unreliable for larger payloads;
4. several read surfaces are poorer than the corresponding write surfaces;
5. search, ID normalization, pagination, status reporting, and error normalization contain material defects or omissions;
6. ordinary GitHub rules still apply, including the prohibition on self-approval and self-requested changes.

The practical doctrine is: use the connector for bounded repository operations, Git objects, issues, PRs, and Actions control; use Actions artifacts as the reliable GitHub-to-container byte bridge; do not treat the model itself as a bulk binary transport.

## Scope and authorization

- Authenticated GitHub account: `zaidifm` / S. Ali Zaidi.
- Target repository: `zaidifm/chatgpt`.
- Repository permission reported by GitHub: `admin` with `maintain`, `push`, `pull`, and `triage`.
- Every mutation in this audit was confined to this repository.
- The repository was intentionally created as a destructive-test laboratory.

This is a dated behavioral baseline, not a promise that the same tools, permissions, wrappers, or service health will exist in another conversation or rollout cohort.

## Capability map

### Repository identity and discovery

Successful:

- authenticated-user lookup;
- repository lookup by full name, numeric ID, repository URL, and nested GitHub URL;
- collaborator permission lookup;
- global repository search;
- installation-scoped repository search;
- code-search index availability reporting.

Observed quirks:

- repository metadata continued to report `size: 0` after multiple commits and binary objects;
- one installation-scoped search response exposed repository description and indexing state while the ordinary repository response did not;
- connector installation visibility, tool inclusion, authorization, and dependency health remain separate states.

### File and URL reads

Successful:

- fetch UTF-8 files by repository/path/ref;
- fetch the same file as base64;
- fetch GitHub blob, raw, and contents URLs through the general URL fetch action;
- read line windows while retaining the full blob SHA;
- retrieve commit diffs and per-file patches;
- retrieve PR-wide and per-file patches.

Boundaries:

- `fetch_blob` assumes UTF-8 and failed on arbitrary binary with a wrapped `UnicodeDecodeError`;
- the same binary was recoverable through `fetch_file(..., encoding="base64")`;
- no general repository archive download or clone-to-container action was exposed;
- direct `git clone https://github.com/zaidifm/chatgpt.git` from the VM failed because ordinary public DNS remained blocked.

### Contents API writes

Successful:

- initialize an empty repository by creating the first file and commit;
- create UTF-8 files on default or named branches;
- replace files using the current blob SHA;
- delete files using the current blob SHA;
- return new commit and content SHAs after update.

Concurrency behavior:

- duplicate create returned HTTP 422 with GitHub's misleading `"sha" wasn't supplied` message;
- stale-SHA update returned HTTP 409;
- stale-SHA delete returned HTTP 409;
- correct-SHA delete succeeded and subsequent read returned 404.

The contents API therefore provides useful optimistic locking for sequential agents, but it is text-only in this wrapper.

### Low-level Git data

Successful:

- create UTF-8 blobs;
- create base64 binary blobs;
- create trees from scratch;
- create trees from a known base-tree SHA;
- create commits with one or multiple parents;
- create branches from refs or unattached commit SHAs;
- fast-forward refs;
- reject non-fast-forward ref movement by default;
- force-rewind and recover refs when explicitly requested;
- create a synthetic two-parent merge commit;
- compare refs and commits with per-file statistics.

Important asymmetry:

- the connector can create arbitrary Git DAGs, but `fetch_commit` does not expose the commit's parent list or tree SHA;
- there is no general tree-read action, so incremental low-level tree construction requires a tree SHA retained from an earlier `create_tree` receipt;
- no tag creation, tag-object read/write, general ref namespace, branch deletion, signature control, or author/committer override was exposed.

### Binary and media transfer

Exact direct binary writes were demonstrated for:

| Object | Bytes | Local/GitHub blob SHA |
|---|---:|---|
| arbitrary binary probe | 264 | `1f4c396b0acd8c0a34faa8de2672b2511965deb4` |
| directional atlas PNG | 6,696 | `51cff290605997ab7317aca8a0b79fc25229ad36` |
| animated Pikachu WebP | 2,450 | `384fc95a999752a91279c3a416788a8bcad3652f` |
| Actions-generated frame-strip PNG round trip | 3,387 | `60275298583b0f946597d9d6db5f151f2df5ad8a` |

Larger model-mediated binary calls failed integrity comparison:

| Intended object | Bytes | Expected Git blob SHA | Returned Git blob SHA |
|---|---:|---|---|
| parade GIF | 59,665 | `69ca7fa236ce01b30db36949fa2939bceec5b54f` | `132f5d96d7bf89a5de56db29c86b9e2b9c5fed6b` |
| source ZIP | 26,218 | `ceaa8715efbca9a24b5a4ffe278046c2c419ae84` | `3a8e3781dbb373096e5129931644a3ecdc65830a` |
| 16-frame GIF | 8,972 | `17c2f924365b6dfec074cb7acec79202c982018d` | `c374dec2f2fffa19ba2792bec9af8db872b518c2` |
| compact parade GIF | 4,471 | `5825c007f6c76ad1e7fdfd7939b7fd96509bf469` | `930adf1f271f65cf7dc3e5a2e78c9a5cc08d8d25` |

These failures do not prove GitHub's Git-data API corrupts base64. Smaller direct writes were exact, and the wrapper's earlier arbitrary-binary test was exact. They establish that the present **model-mediated invocation path** is not a dependable bulk binary upload protocol: the model must copy an entire base64 string into JSON, there is no mounted-file argument, and integrity can fail before GitHub stores the object.

The failed blobs were removed from the current media-lab tree. Their commit remains in history as audit evidence.

### Branches and search

Successful:

- create branch;
- create branch from commit SHA;
- update branch ref;
- compare branches;
- search commits;
- search code on the default branch;
- search issues and pull requests;
- search repositories.

Defects and limitations:

- branch search returned an empty array for `main`, exact existing branch names, and prefix queries;
- code search did not find a unique probe token that existed only on a non-default branch;
- search indexing is asynchronous and not an authority source for current branch state;
- no branch-list or branch-delete action was exposed.

### Issues

Successful:

- create issues with assignees and labels;
- fetch issues;
- update title and body;
- additive label mutation;
- label removal;
- full label replacement;
- assignee add, remove, and replacement;
- close with `not_planned`;
- reopen with `reopened`;
- lock and unlock conversations;
- create and update issue comments;
- add, list, and remove issue-comment reactions;
- search issues;
- convert an existing issue into a pull request.

Observed wrapper behavior:

- the PR-conversation comment creator also works for ordinary issues because GitHub models PR conversations as issues;
- some aggregated comment reads returned null timestamps despite richer endpoints exposing them;
- no issue-comment delete action was exposed.

### Pull requests

Successful:

- create draft and non-draft PRs;
- convert issues to PRs;
- fetch rich PR content, normalized PR metadata, diffs, changed filenames, and file patches;
- update PR title and body;
- label PRs;
- close and reopen PRs;
- mark ready for review and convert back to draft;
- add top-level comments;
- add comment reviews;
- add inline review comments;
- edit inline comments;
- reply to inline threads;
- resolve and unresolve review threads;
- add/list/remove PR and review-comment reactions;
- list reviews;
- squash-merge with an expected head SHA.

GitHub-enforced boundaries:

- requesting review from the PR author returned HTTP 422;
- approving one's own PR returned HTTP 422;
- requesting changes on one's own PR returned HTTP 422;
- dismissing a `COMMENTED` review was rejected;
- auto-merge failed because the repository setting was disabled.

Normalization defects:

- REST-backed PR reads returned numeric user IDs while GraphQL-backed draft/thread operations sometimes returned `id: null`;
- review-list and thread-list APIs returned GraphQL node IDs, while edit/reaction APIs often required numeric REST IDs;
- `fetch_pr_comments` was the practical bridge because it returned numeric IDs for top-level, review, and inline comments;
- creation initially reported `mergeable: false`, while later reads reported `true`, reflecting GitHub's asynchronous mergeability calculation;
- some no-op reviewer operations returned partially null PR statistics.

### GitHub Actions

Successful:

- commit workflow files under `.github/workflows`;
- discover PR-triggered runs by commit SHA;
- read run state and conclusion;
- read jobs and step conclusions;
- download decoded job logs;
- enumerate artifacts;
- download artifact ZIPs into `/mnt/data` through a true connector-to-container file bridge;
- re-run all failed jobs;
- re-run one specific job;
- observe a new job ID under the same logical run ID on later attempts.

Demonstrated runs:

- success-path connector audit: run `30200716452`, artifact `8631548382`;
- intentional failure path: run `30200735543`, artifact `8631553612`;
- clean overworld media lab: run `30203792957`, artifact `8632447932`.

The media-lab artifact:

- GitHub digest: `sha256:13f859933c8edbdf9459108dfc92529a8280f80dcbba5182bea9fd5c7e588d90`;
- downloaded VM SHA-256: identical;
- contained a 16-frame GIF, a 12-frame atlas-scan GIF, a 384×384 PNG frame strip, and a manifest;
- every output matched its manifest size and SHA-256.

Limitations:

- workflow-run discovery currently filters to pull-request-triggered runs and returns only the first page;
- jobs and artifacts wrappers return the first page, with jobs limited to the latest attempt;
- job-log responses can be truncated by model response limits;
- legacy combined commit status returned an empty list for both successful and failed Actions runs because it does not surface GitHub Checks;
- no workflow-dispatch action, run cancellation, artifact deletion, or check-suite/check-run read surface was exposed.

### Reactions and IDs

Reaction add/list/remove worked for:

- issue comments;
- pull requests;
- inline PR review comments.

Stable numeric reaction IDs were returned. The more troublesome ID boundary was between GraphQL node IDs and REST numeric comment/review IDs. Durable automation should record every mutation receipt and avoid assuming IDs are interchangeable across connector actions.

### Network and file-plane separation

The GitHub connector did not alter the VM network policy:

- GitHub tool calls succeeded;
- direct VM GitHub DNS and `git clone` failed;
- no GitHub credential was exposed to shell or Python;
- normal repository reads did not materialize files into `/mnt/data`;
- Actions artifact download did materialize a ZIP into `/mnt/data`;
- the private-user-image downloader rejected ordinary repository URLs and only accepts `private-user-images.githubusercontent.com` attachments.

This confirms that GitHub access is a server-side connector plane, not ambient VM networking.

### Error handling

Good error cases retained HTTP semantics:

- 404 missing content;
- 409 stale content SHA;
- 422 duplicate create, non-fast-forward ref update, self-review restrictions, and invalid reviewer request;
- clear `INVALID_ARGUMENT` for unsupported private-image URLs.

Poorly normalized cases were wrapped as generic `UNKNOWN`, including:

- auto-merge disabled by repository settings;
- attempting to dismiss a non-dismissible comment review;
- binary `fetch_blob` Unicode decoding failure.

Automations should parse the nested message when available and must not treat `UNKNOWN` as evidence that no actionable cause exists.

## Pokémon overworld media experiment

The user supplied `pokemon(1).zip`, approximately 154 MB with 57,472 entries. The overworld layout contained:

- four directional folders;
- two frames per direction;
- 564 normal filename variants;
- 564 shiny variants;
- 12 female variants;
- 12 shiny-female variants.

A local full pass generated 1,152 animated GIFs and a 4,261,451-byte atlas ZIP with SHA-256:

`480fc94aaf9252ab800c82eae4d764e0eaa84eed980f433df5b4596a1311963d`

The durable GitHub lab uses two connector-transferred inputs that passed byte-level verification:

- `media-lab/overworld/samples/25.webp`;
- `media-lab/overworld/directional-atlas.png`.

GitHub Actions then creates:

- `pikachu-turntable.gif`;
- `pikachu-frame-strip.png`;
- `directional-atlas-scan.gif`;
- `manifest.json`.

The downloaded frame-strip PNG was committed back to the PR branch as `media-lab/overworld/roundtrip/pikachu-frame-strip.png`, completing the byte path:

```text
user upload
  -> ChatGPT VM extraction and transformation
  -> GitHub binary commit
  -> GitHub Actions media transformation
  -> Actions artifact download into ChatGPT VM
  -> local hash/media verification
  -> GitHub binary re-upload
```

## Exposed capability gaps

No current connector action was exposed for:

- native local-file upload from `/mnt/data` to GitHub;
- repository clone/archive materialization;
- listing or deleting branches;
- tags and releases;
- repository settings mutation;
- branch protection and rulesets;
- GitHub secrets, variables, environments, or deployments;
- Actions workflow dispatch or cancellation;
- check runs and check suites;
- issue or PR comment deletion;
- general tree reads;
- Git LFS;
- GitHub Pages configuration;
- project boards, discussions, wikis, or packages;
- release-asset transfer.

Absence from the exposed catalog is not a statement about the underlying GitHub API. It is a boundary of this connector surface in this conversation.

## Recommended operating doctrine

1. **Keep authority in Git and Superintendent.** Record exact repository, branch, commit, PR, run, artifact, and tool-receipt identifiers.
2. **Use the contents API for bounded text edits.** Respect current blob SHAs and serialize same-path writes.
3. **Use low-level Git objects for atomic multi-file commits.** Retain every created tree SHA because the read surface cannot rediscover it cleanly.
4. **Use Actions artifacts for substantial binary output.** They provide a real file bridge into the VM with digest metadata.
5. **Do not use model-generated base64 as bulk transport.** For larger inputs, generate or fetch them inside Actions, use another authenticated storage bridge, or add a connector action that accepts a mounted file reference.
6. **Treat search as discovery, not authority.** Verify refs, commits, and exact paths directly.
7. **Treat workflow runs, not legacy combined status, as CI truth.** The status endpoint omits Checks.
8. **Preserve mutation receipts.** REST and GraphQL IDs are not reliably interchangeable.
9. **Record failure detail.** Generic wrapper errors often contain a useful nested GitHub validation message.
10. **Re-probe after connector or harness changes.** Especially binary upload, branch search, pagination, Checks support, and file materialization.

## Epistemic status

- **Observed:** directly exercised through the connector or running VM.
- **Inferred:** architectural conclusion from repeated observations, explicitly not an implementation contract.
- **Unsupported:** absent from the exposed tool catalog, not necessarily absent from GitHub itself.
- **Transient:** results such as mergeability, indexing, Actions state, and service health that can change asynchronously.

The machine-readable JSON and CSV beside this report are the preferred baseline for future automated comparison.
