# Release audits

`release-audits/` stores the review evidence required before every maintainer push or release. Only version reports matching the exact pattern `release-audits/v<major>.<minor>.<patch>.md` are excluded from the package fingerprint and release diff. This README and any other governance files in the directory remain part of the release payload.

## Release payload

In a Git repository, the payload is the union of `git ls-files` tracked paths and `git ls-files --others --exclude-standard` non-ignored untracked paths. Explicit exclusions are version audit reports, `__pycache__`, `*.pyc`, root `SHA256SUMS`, and `.tony-agents-pack` runtime artifacts. A tracked `.env` or log remains in the payload and is separately subject to secret review; an ignored, untracked local `.env` or log is not a release path and therefore does not affect the fingerprint or diff. Outside a Git repository, fingerprint tests fall back to filesystem traversal with the same explicit exclusions.

Each tracked fingerprint entry comes entirely from the Git index: relative path, index mode (`100644`, `100755`, or `120000`), blob SHA, and raw blob bytes read with `git cat-file blob`. A symlink blob contains the link target and is never followed. The fingerprint is independent of checkout newline conversion and filesystem executable semantics. Untracked entries can be fingerprinted for diagnostics, but PASS requires staging every release file; `check` rejects unstaged payload paths only when Git recognizes them as real content differences.

## Required flow

1. Stage the release payload with `git add -A`, then run `python3 scripts/release_gate.py fingerprint` and collect the staged/worktree diff, changed files, removed files, changed agents, breaking impact, and validation/test results. The gate refuses PASS while any non-ignored release file remains untracked.
2. Explicitly invoke the `github` agent with `MODE=RELEASE_GATE` and all required input fields. The agent remains hard read-only and returns report content only. The lists must come from the real Git payload diff, not a transcription of unverified input.
3. The main AI writes that exact reviewed report to `release-audits/v<VERSION>.md`, then stages that audit report separately with `git add release-audits/v<VERSION>.md`.
4. Run `python3 scripts/release_gate.py check`. The gate independently recomputes the real Git diff from `base_ref` to the current worktree or target commit, including tracked changes, deletions, both sides of renames, and non-ignored untracked files. It requires exact set equality. Fix any failure, recompute the fingerprint, and request a new review.
5. Commit the reviewed release source and its audit. The pre-push hook accepts `target_ref: WORKTREE:<fingerprint>` because version audit reports do not affect the fingerprint.

## Report schema

Use `python3 scripts/release_gate.py template` for an INCONCLUSIVE skeleton. Frontmatter requires `reviewer: github`, `changed_files`, `removed_files`, and `changed_agents` as JSON arrays or comma-separated strings, plus `breaking_impact: none | additive | breaking`. Use `[]` or `none` for an empty set.

The level-2 headings must be exactly these nine in this order, with no additional level-2 headings: `Scope`, `Evidence`, `Findings`, `Agent Links`, `Improvements`, `Blockers`, `Unverified`, `Migration`, `Hand-off`.

- Scope lists every changed/removed path and changed agent in code formatting; empty sets are explicit `changed_files: none`, `removed_files: none`, or `changed_agents: none`.
- Evidence is a table with exact header `| evidence-id | check | result | evidence |`. It contains PASS rows for validation, tests, and fingerprint.
- Findings is a single `none` or a table with exact header `| finding-id | severity | status | summary |`. Severity is P0-P3 and status is OPEN/FIXED/ACCEPTED_RISK. PASS requires every P0/P1 to be FIXED; open P2/P3 IDs are referenced in Improvements or Hand-off.
- Agent Links contains only each exact `blob/v<VERSION>/agents/<name>.md` URL bullet, or `none — no agent contract changes`.
- Improvements is a table with exact header `| improvement-id | user-value | evidence-ref |`; each evidence-ref exists in Evidence.
- Blockers and Unverified each contain only `none` for PASS.
- Migration contains exactly four ordered key-value lines: `breaking-impact`, `ordinary-users`, `maintainers`, `upgrade`. Breaking upgrades cite README/update or an explicit command.
- Hand-off is a table with exact header `| owner | action | status |`. Status is READY, COMPLETE, BLOCKED, or UNAFFECTED; PASS cannot contain BLOCKED.

Empty values and placeholders such as `PENDING`, `TODO`, `TBD`, `placeholder`, or `待补` cannot pass.

## Maintainer migration for v3

The `github` RELEASE_GATE required input/output contract and maintainer push process are breaking changes in v3. Maintainers must run `./scripts/setup-hooks.sh`, obtain a fresh audit using the v3 schema before each push, and include that audit in the commit. CI checkout uses `fetch-depth: 0` so the base tags and history needed by the gate are available. Git for Windows runs the shell hook through Git Bash; where shell hooks are unavailable, manually run validation, unittest discovery, and `python scripts/release_gate.py check --commit <HEAD_SHA>` before pushing.

Ordinary users installing or updating the agent pack do not install this repository hook and do not create release audits. Their installation state schema is unchanged; they can update through the README workflow.
