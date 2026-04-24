# Official Graph Refresh Integration Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: governance/tooling-usage
Phase: implementation

## Objective

Verify which official `code-review-graph` refresh path is actually working on
this workstation, refresh the local graph with official commands when needed,
and record the official-first operating method in Zeus guidance without
modifying upstream skills or inventing a custom refresh mechanism.

## Source Basis

- Official upstream README and CLI docs for `code-review-graph`
- Local workstation evidence:
  - `code-review-graph status --repo /Users/leofitz/.openclaw/workspace-venus/zeus`
  - `~/.codex/config.toml`
  - `~/.code-review-graph/`
  - active `code-review-graph serve` processes

## Scope

Allowed:

- `AGENTS.md`
- `architecture/topology.yaml`
- `architecture/code_review_graph_protocol.yaml`
- `docs/reference/modules/code_review_graph.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_graph_refresh_official_integration/**`
- local execution of official `code-review-graph status|update|watch|daemon` commands for verification

Forbidden:

- upstream skill or package source edits
- repo-local custom refresh scripts
- changes to runtime/source behavior
- staging `.code-review-graph/graph.db`
- edits to archive bodies or current source/data truth

## Acceptance

- official refresh path is classified from workstation evidence
- one official refresh/status cycle is verified locally
- root `AGENTS.md` tells agents to prefer official graph operations
- graph protocol doc records official-first refresh order and blocker/warning policy
- packet evidence records that Zeus should integrate usage guidance, not replace official behavior

## Verification

- `code-review-graph status --repo /Users/leofitz/.openclaw/workspace-venus/zeus`
- `code-review-graph update --repo /Users/leofitz/.openclaw/workspace-venus/zeus`
- `code-review-graph status --repo /Users/leofitz/.openclaw/workspace-venus/zeus`
- `python scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-23_graph_refresh_official_integration/plan.md --json`
- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --work-record --changed-files <packet files> --work-record-path docs/operations/task_2026-04-23_graph_refresh_official_integration/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files <packet files> --receipt-path docs/operations/task_2026-04-23_graph_refresh_official_integration/receipt.json --json`
- `python scripts/topology_doctor.py closeout --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-23_graph_refresh_official_integration/plan.md --work-record-path docs/operations/task_2026-04-23_graph_refresh_official_integration/work_log.md --receipt-path docs/operations/task_2026-04-23_graph_refresh_official_integration/receipt.json --json`
- `git diff --check -- <packet files>`
