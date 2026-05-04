# `docs/operations/` — Authoritative Index

**Purpose:** every directory and top-level file under `docs/operations/`
must be registered here.  If it isn't on this page, it's a candidate
for archival — see `POLICY.md` for the closeout rule.

**Last reviewed:** 2026-05-04 (post PR #55 + #56 + #58 merge).

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
| `observations_k1_migration.md` | **archive candidate** | K1 schema migration was one-shot. |
| `pr39_source_contract_artifact_audit.md` | **archive candidate** | PR #39 audit; PR is merged. |

## Task directories — closeout status

A task directory captures a finite operation with a known closeout
trigger (PR merge or operator declaration).  After closeout, the
directory should EITHER be moved under `archive/` OR keep a
`STATUS.md` with the closeout receipt — see `POLICY.md`.

| Directory | Anchor PR / commit | Status | Last touched |
|---|---|---|---|
| `task_2026-04-26_ultimate_plan/` | (multi-commit) `2cb1c421` | **closed** — pre-PR-37 ingest plan, completed | 2026-04 |
| `task_2026-04-28_contamination_remediation/` | (multi-commit) `2cb1c421` | **closed** — contamination pass, completed | 2026-04 |
| `task_2026-04-29_design_simplification_audit/` | (multi-commit) `2cb1c421` | **closed** — design audit, completed | 2026-04 |
| `task_2026-05-01_tigge_5_01_backfill/` | (multi-commit) `39ee725b` | **closed** — TIGGE 5.01 backfill, completed | 2026-05-01 |
| `task_2026-05-01_ultrareview25_remediation/` | (multi-commit) `1db2b7fb` | **closed** — ultrareview-25 remediation, completed | 2026-05-01 |
| `task_2026-05-02_data_daemon_readiness/` | (multi-commit) `e0835ccb` | **closed** — data daemon readiness substrate, completed | 2026-05-02 |
| `task_2026-05-02_full_launch_audit/` | PR #37 + post-PR47 follow-ups | **closed** — full launch audit, multi-PR completed | 2026-05-02 |
| `task_2026-05-02_live_entry_data_contract/` | (planning artifacts) `2cb1c421` | **review** — multiple PLAN_v[1,2,3] suggest abandoned drafts | 2026-05-02 |
| `task_2026-05-02_oracle_lifecycle/` | (multi-commit) `bdd4be3a` | **closed** — oracle resilience review fixes, completed | 2026-05-02 |
| `task_2026-05-02_review_crash_remediation/` | PR #37 `b1ce90d0` | **closed** — live entry guards + ENS snapshot, merged | 2026-05-02 |
| `task_2026-05-02_settlement_pipeline_audit/` | (audit artifacts) `2cb1c421` | **closed** — settlement audit, completed | 2026-05-02 |
| `task_2026-05-02_strategy_update_execution_plan/` | (multi-commit) `97bd37e9` | **closed** — promotion-evidence guard, completed | 2026-05-02 |
| `task_2026-05-03_ddd_implementation_plan/` | DDD v2 commits `650136bd` etc. | **closed** — DDD v2 redesign live-wired, 38 docs is excessive — needs INDEX of its own or pruning | 2026-05-04 |
| `task_2026-05-04_live_block_root_cause/` | PR #54 `da4db622` | **closed** — live-block antibodies + EntriesBlockRegistry, merged | 2026-05-04 |
| `task_2026-05-04_oracle_kelly_evidence_rebuild/` | PR #56 `b337aacf` | **closed** — oracle/kelly evidence rebuild, merged 2026-05-04T13:50Z | 2026-05-04 |
| `task_2026-05-04_strategy_redesign_day0_endgame/` | PR #53 `da5a7526` | **closed** — Day0-as-endgame strategy redesign, merged | 2026-05-04 |
| `task_2026-05-04_tigge_ingest_resilience/` | PR #55 + #58 `9e96cbde` | **closed** — TIGGE 12z resilience, merged 2026-05-04T14:14Z; live-blocker partially open (data backfill) — see `POST_PR55_PR56_REALIGNMENT.md` | 2026-05-04 |

## Active operation (only one allowed unless explicit branching)

If you are starting a new operation, add a row here BEFORE creating the
directory (see POLICY.md §3).  When the operation closes, move the row
to the table above and either archive or annotate its dir with
`STATUS.md`.

| Operation | Started | Trigger | Anchor PR / branch |
|---|---|---|---|
| _(none currently active — next operator: claim before starting)_ | | | |

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
4. `observations_k1_migration.md` — K1 migration was one-shot; archive.
5. `pr39_source_contract_artifact_audit.md` — PR #39 long merged; archive.
