# Operations Package Cleanup Packet

Date: 2026-04-26
Branch: `main`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

The midstream remediation mainline had accumulated many sibling
`docs/operations/task_*` folders for phases that belong to one package. That
made the operations surface noisy and encouraged future agents to create a new
top-level package for every phase.

## Scope

In scope:

- Consolidate midstream remediation phase folders under
  `docs/operations/task_2026-04-23_midstream_remediation/phases/`.
- Keep unrelated packages, such as Packet Runtime and graph/rendering work,
  separate.
- Update packet-routing guidance so phases of one package stay inside one
  package folder.
- Update `zpkt start` to support `--package <task_...>` for phase-local packet
  creation.
- Keep the PR #17 review-fix code/test changes in the package-local phase
  folder.

Out of scope:

- Archiving old package evidence.
- Runtime artifact tracking policy.
- Production DB mutation or P4 population.

## Verification

- `python3 -m py_compile scripts/zpkt.py scripts/_zpkt_scope.py tests/test_zpkt.py`
- `pytest -q tests/test_zpkt.py`
- PR #17 targeted verification from the sibling review-fix phase.
- Topology planning-lock, map-maintenance, receipt, script, test, freshness,
  and whitespace checks.

## Closeout

- Same-package midstream phase evidence now lives under one package root.
- The prompt/source of the earlier bad decision was corrected in
  `docs/operations/AGENTS.md`, `docs/README.md`,
  `architecture/naming_conventions.yaml`, `architecture/artifact_lifecycle.yaml`,
  `architecture/change_receipt_schema.yaml`, and `scripts/zpkt.py`.
