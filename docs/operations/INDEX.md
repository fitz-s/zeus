# `docs/operations/` — Authoritative Index

**Purpose:** every directory and top-level file under `docs/operations/`
must be registered here.  If it isn't on this page, it's a candidate
for archival — see `POLICY.md` for the closeout rule.

**Last reviewed:** 2026-05-04.

---

## Permanent observation surfaces (long-lived, no merge tied)

These directories collect rolling operational data and are not
expected to close out.  Keep them under their existing names.

| Surface | Owner | Purpose |
|---|---|---|
| `activation/` | runtime | Activation evidence captures (rollout gating). |
| `attribution_drift/` | runtime | Drift attribution snapshots per cycle. |
| `calibration_observation/` | runtime | Live calibration sample log. |
| `edge_observation/` | runtime | Edge-decision rejection counters per stage. |
| `learning_loop_observation/` | runtime | Feedback loop instrumentation outputs. |
| `ws_poll_reaction/` | runtime | Websocket-vs-poll reaction-time evidence. |

## Permanent reference docs (top-level .md, semi-stable)

| File | Status | Purpose |
|---|---|---|
| `AGENTS.md` | active | Scoped agent rules for `docs/operations/`. |
| `current_state.md` | active | Live operational state checkpoint. |
| `current_data_state.md` | active | Data-side state checkpoint. |
| `current_source_validity.md` | active | Source-validity status (what's hot/cold). |
| `packet_scope_protocol.md` | active | Packet scope conventions. |
| `tigge_daemon_integration.md` | active | TIGGE daemon wiring reference. |
| `live_rescue_ledger_2026-05-04.md` | **review** | Per-day live-rescue log; needs rotation policy if this grows. |

## Task directories — closeout status

A task directory captures a finite operation with a known closeout
trigger (PR merge or operator declaration).  After closeout, the
directory should EITHER be moved under `archive/` OR keep a
`STATUS.md` with the closeout receipt — see `POLICY.md`.

| Directory | Anchor PR / commit | Status | Last touched |
|---|---|---|---|
| `task_2026-04-26_ultimate_plan/` | (multi-commit) `2cb1c421` | **closed** — pre-PR-37 ingest plan, completed | 2026-04 |
| `task_2026-05-02_live_entry_data_contract/` | (planning artifacts) `2cb1c421` | **review** — multiple PLAN_v[1,2,3] suggest abandoned drafts | 2026-05-02 |
| `task_2026-05-03_ddd_implementation_plan/` | DDD v2 commits `650136bd` etc. | **closed** — DDD v2 redesign live-wired, 38 docs is excessive — needs INDEX of its own or pruning | 2026-05-04 |

## Active operation (only one allowed unless explicit branching)

If you are starting a new operation, add a row here BEFORE creating the
directory (see POLICY.md §3).  When the operation closes, move the row
to the table above and either archive or annotate its dir with
`STATUS.md`.

| Operation | Started | Trigger | Anchor PR / branch |
|---|---|---|---|
| _(none currently active — next operator: claim before starting)_ | | | |

## Archived

These items have been moved to `docs/operations/archive/` per `POLICY.md`.

| Item | Status | Purpose |
|---|---|---|
| `observations_k1_migration.md` | archived | K1 schema migration was one-shot. |
| `pr39_source_contract_artifact_audit.md` | archived | PR #39 audit; PR is merged. |
| `PROPOSALS_2026-05-04.md` | archived | Operational improvement proposals. |
| `task_2026-04-28_contamination_remediation/` | archived | contamination pass, completed |
| `task_2026-04-29_design_simplification_audit/` | archived | design audit, completed |
| `task_2026-05-01_tigge_5_01_backfill/` | archived | TIGGE 5.01 backfill, completed |
| `task_2026-05-01_ultrareview25_remediation/` | archived | ultrareview-25 remediation, completed |
| `task_2026-05-02_data_daemon_readiness/` | archived | data daemon readiness substrate, completed |
| `task_2026-05-02_full_launch_audit/` | archived | full launch audit, multi-PR completed |
| `task_2026-05-02_oracle_lifecycle/` | archived | oracle resilience review fixes, completed |
| `task_2026-05-02_review_crash_remediation/` | archived | live entry guards + ENS snapshot, merged |
| `task_2026-05-02_settlement_pipeline_audit/` | archived | settlement audit, completed |
| `task_2026-05-02_strategy_update_execution_plan/` | archived | promotion-evidence guard, completed |
| `task_2026-05-04_live_block_root_cause/` | archived | live-block antibodies + EntriesBlockRegistry, merged |
| `task_2026-05-04_oracle_kelly_evidence_rebuild/` | archived | oracle/kelly evidence rebuild, merged 2026-05-04 |
| `task_2026-05-04_strategy_redesign_day0_endgame/` | archived | Day0-as-endgame strategy redesign, merged |
| `task_2026-05-04_tigge_ingest_resilience/` | archived | TIGGE 12z resilience, merged 2026-05-04 |
| `task_2026-05-04_zeus_may3_review_remediation/` | archived | empty, archived for naming-collision avoidance |

## Cross-references (artifacts that live outside `docs/operations/`)

| File | Purpose |
|---|---|
| `architecture/improvement_backlog.yaml` | Typed registry for capsule-emitted improvement insights (P3 V1).  Capsule writes here instead of leaving lessons in chat history. |
| `scripts/check_pr_identity_collisions.py` + `.github/workflows/pr_identity_collision_check.yml` | Pre-merge identity-collision detection (P1).  Posts an advisory comment when two open PRs both add a class with the same name in identity-bearing files. |
| `src/state/schema_introspection.py` | `has_columns()` helper (P2) — call this from any code path that depends on a column added by a staged migration. |

## Triage backlog (one-time cleanup, 2026-05-04)

Items above marked **review** or **archive candidate** need an
operator decision in the next housekeeping pass:

1. `task_2026-05-02_live_entry_data_contract/` — multiple PLAN_v[1,2,3]
   files suggest abandoned drafts; either anchor to a merged PR or
   delete.
2. `task_2026-05-03_ddd_implementation_plan/` — 38 docs in one task
   dir is a smell; needs an internal INDEX or thinning.
3. `live_rescue_ledger_2026-05-04.md` — needs a per-day rotation
   policy or it'll keep growing.
