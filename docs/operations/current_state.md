# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: **Post-audit P0 data containment Ralph loop — active 2026-04-24**
- Active execution packet: `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`
- Previous package source: `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- Post-audit handoff (zero-context cold-start doc for next session): `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Status: P0 containment implementation/verification in progress.
  - P0 scope: read-only `training-readiness` evidence in
    `scripts/verify_truth_surfaces.py`, static legacy-hourly containment in
    `scripts/semantic_linter.py`, and targeted negative tests.
  - P0 must not mutate `state/**`, `.code-review-graph/graph.db`, `src/**`,
    `docs/authority/**`, or `architecture/**`.
  - P0 closeout requires critic + verifier review before commit/push, then an
    additional third-party critic/verifier pass before freezing P1.
- Prior midstream status: CONDITIONAL milestone materially achieved.
  - **W1–W4**: 100% shipped — T1 + T2 + T3 + T4 + T5 + T6.3 + T6.4 +
    T7 + N1.2 closed or retroactively accounted; T3.4-observe upstream-
    blocked by K4.
  - **Out-of-plan shipped this session**: **T6.4-phase2** (correlation
    crowding via ExitContext portfolio threading → D6 category immunity
    1.0/1.0 when `exit.correlation_crowding_rate > 0`) + **Day0-
    canonical-event feature slice** (new `build_day0_window_entered_
    canonical_write` builder + `DAY0_WINDOW_ENTERED` event type + legacy-
    DB migration; closes T1.c-followup L875).
  - **Data-readiness tail**: S2.1/S2.2/S2.3/S2.4/S2.5/S3.1/REOPEN-1/
    REOPEN-2/DR-33-B all shipped and live-DB-applied on 2026-04-24.
- **W5 TRUSTWORTHY-gate — cannot advance by engineering alone**:
  - **T6.1 / T6.2 / N1.1** require live-readiness B3 + ≥ 30 realized-
    P&L settlements corpus (`portfolio_position_count = 0` today).
  - **T4.2-Phase2** requires 7 continuous days of Phase1 audit-clean
    output (Phase1 landed 2026-04-23 via `0206428`; daemon currently
    auto-paused per `state/auto_pause_failclosed.tombstone`).
  - **T3.4** awaits upstream data-readiness K4 closure.
  See `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` §"Wave 5
  remaining blockers" for structured per-slice unblock conditions.
- **Out-of-plan deferrals tracked** (non-W5, not code-locked):
  T6.3-followup-1 (production corpus bootstrap delta audit), T6.4
  pre-flag-flip operator checklist, Day0-canonical-event production DB
  migration, T1.c-followup L1536/L1569 OBSOLETE_BY_ARCHITECTURE
  operator decision, T1.d NC-12 L70 Phase-7 v2 substrate wait, T6.4
  surrogate MED-5 buy_no kwarg naming approximation, Day0TemporalContext
  fixture promotion, sibling SimpleNamespace stubs in `tests/test_fdr.py`.
  See `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` §"Out-of-
  plan deferrals surfaced during execution".
- **Other open operational items**: DR-33-C operator flag flip,
  forensic C5 (market_slug retrofit — blocked on empty market_events),
  forensic C6 (39,431 observations empty provenance — deferred to
  dedicated packet).
- Authority source for the 36-slice plan: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`
- **Live-DB state post-migration (2026-04-24)**: forecasts 15-col schema
  with rebuild_run_id/data_source_version; settlements UNIQUE(city,
  target_date, temperature_metric) + 3 triggers (authority_monotonic v2
  + verified_insert_integrity + verified_update_integrity);
  observation_instants_v2 32-col with INV-14 spine extension + training_
  allowed + causality_status + source_role. k2_forecasts_daily cron will
  succeed on next tick.

## Concurrent parallel packet

**Data-readiness remediation CLOSED 2026-04-23** (8/8 packets APPROVED by
critic-opus). Closure banner + App-C R3-## traceability:
`docs/operations/task_2026-04-23_data_readiness_remediation/first_principles.md`.
Full audit trail: `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md`.
Outcome: `settlements` table is canonical-authority-grade (1,561 rows,
1,469 VERIFIED + 92 QUARANTINED; INV-14 identity + provenance_json +
`settlements_authority_monotonic` trigger). Rollback chain preserved on
disk (4 snapshot md5 sidecars committed).

**DR-33-A** (live-harvester enablement, code-only scaffold): landed
2026-04-23 at `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/`.
Feature-flagged `ZEUS_HARVESTER_LIVE_ENABLED` default OFF — no runtime
behavior change until explicit operator flip under DR-33-C review.

Scope boundary with midstream retained: upstream-data-readiness owned
`src/data/*`, `src/execution/harvester.py`, `src/state/db.py` settlements
schema (the P-B migration added 5 columns + trigger), plus the new DR-33-A
additions to `architecture/source_rationale.yaml::write_routes::settlement_write`
and `architecture/test_topology.yaml`. Midstream owns `tests/*`,
`src/strategy/*`, `src/engine/evaluator.py`, `src/engine/cycle_runtime.py`,
`src/execution/{executor,exit_triggers}.py`, `src/contracts/*`. Shared
files (`current_state.md`, `known_gaps.md`,
`architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`,
`architecture/test_topology.yaml`) were touched by upstream only at slice
boundaries with surgical diffs to avoid midstream work loss.

## Required evidence

- `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/receipt.json`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`

## Freeze point

- P0 containment may edit only the files listed in
  `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`.
  It must not mutate runtime DBs (`state/**`), `.code-review-graph/graph.db`,
  `src/**`, `docs/authority/**`, or `architecture/**`.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

Use `docs/operations/AGENTS.md` for registered operations-surface classes and
non-default packet/package routing.

Visible non-default packet evidence (post-audit trim, 2026-04-24):

Active / pending — remaining in `docs/operations/`:

- `docs/operations/task_2026-04-23_midstream_remediation/` — W1–W4
  closed; W5 substrate-blocked (see `docs/to-do-list/zeus_midstream_
  fix_plan_2026-04-23.md §"Wave 5 remaining blockers"`).
- `docs/operations/task_2026-04-23_graph_rendering_integration/` —
  implementation-prep stage.
- `docs/operations/task_2026-04-24_p0_data_audit_containment/` —
  active (current mainline per P0 Ralph loop).
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/` —
  NEEDS_OPERATOR_DECISION (D3 in `docs/to-do-list/zeus_operations_
  archive_deferrals_2026-04-24.md`). Planning-lock only; P1/P2
  venue_commands spine never implemented.

Archived 2026-04-24 (moved to `docs/archives/packets/` — 21 packets,
~5.5M freed). Lore cards extracted into `architecture/history_lore.yaml`
for high-density packets. See `docs/archive_registry.md §"2026-04-24
closure archive"` for the full list + lore-card cross-references.

Also `docs/operations/task_2026-04-13_remaining_repair_backlog.md`
(NEEDS_OPERATOR_DECISION single-file — D1+D2 pending TIGGE GRIB ingest
and source-attestation packet rulings) remains in place pending
operator decision.

## Next action

- Finish P0 containment: run targeted tests/topology gates, critic review,
  verifier pass, deslop/reverification, commit, and push scoped files only.
- After P0 close, run the required additional third-party critic/verifier pass
  before freezing the next P1 ralplan.
- Preserve unrelated dirty work and concurrent in-flight edits.
