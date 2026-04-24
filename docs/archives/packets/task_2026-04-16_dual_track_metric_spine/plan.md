# Dual-Track Metric Spine Refactor — Plan

Date: 2026-04-16
Branch: data-improve (refactor may fork its own branch once Phase 1 opens)
Program: Zeus Dual-Track Metric Spine + Death-Trap Remediation

## Purpose

This is a **governance + architecture refactor**, not a feature. Its job is to
stop Zeus from running forever on a single-track `daily-max` worldview that
cannot admit `daily-low` without corrupting row identity, training purity, and
Day0 causality — while simultaneously remediating six fatal runtime traps that
current `data-improve` code leaves open.

The working constitution for this refactor is:

> Install first-class metric spine, then rebuild the world.

## Scope

This packet is **not** the Topology Enforcement Hardening packet (owned by a
separate agent). Map / manifest maintenance is deferred to closeout; do not
interleave it with this work.

### In scope
- Dual-track metric identity (high + low), SD-1 … SD-8 of the v2 refactor package
- World DB v2 table family (schema; no migration of live writes yet)
- Observation client `low_so_far` closure
- Day0 runtime split (`Day0HighSignal` / `Day0LowNowcastSignal` / router)
- `high` canonical product cutover to `mx2t6_local_calendar_day_max_v1`
- `low` historical lane with boundary quarantine and causality status
- Metric-aware rebuild / Platt / bin lookup
- Low shadow-only observation window
- Low limited activation (gated by rollout doctrine)
- **Death-trap remediation law + code**: commit ordering, RED force-exit,
  FDR family canonicalization, chain three-state, Kelly executable-price,
  graceful degradation, boundary-day settlement policy

### Out of scope for this packet
- Topology enforcement hardening (separate agent)
- V1 refactor package cleanup (one-off `chore:` commit, separate)
- New feature work unrelated to metric identity or the 6 death traps

## Authority surfaces touched

- `AGENTS.md` (root) — probability chain + durable boundaries + forbidden moves
- `docs/authority/zeus_current_architecture.md` — new law sections §13–§21
- `docs/authority/zeus_dual_track_architecture.md` — **new authority file**,
  consolidates SD-1..SD-8, World DB v2, gate grammar, death-trap law
- `docs/operations/data_rebuild_plan.md` — canonical products, eligibility
  gates, separation law
- `docs/operations/current_state.md` — active-work registration for this packet
- Later phases: `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`,
  `architecture/kernel_manifest.yaml`, `src/**`, `scripts/**`, `tests/**`

All writes to `architecture/**` and `docs/authority/**` trigger the
planning-lock; every phase carries its own evidence file in this packet.

## Phases

### Phase 0 — Documentation constitution **(this phase)**

Purpose: make the dual-track worldview and the death-trap remediation law
legible to every downstream phase. Zero code changes.

Deliverables:

1. Root `AGENTS.md` patched to describe dual-track probability chains, snapshot
   import law, and daily-low Day0 law.
2. `docs/authority/zeus_current_architecture.md` extended with:
   - §13 Metric identity law
   - §14 Runtime-only fallback doctrine
   - §15 Daily low causality doctrine
   - §16 Truth commit ordering law (DT#1)
   - §17 Risk force-exit law (DT#2) — upgrades INV-05 + §6 risk table
   - §18 FDR family canonicalization law (DT#3)
   - §19 Chain-truth three-state law (DT#4)
   - §20 Kelly executable-price law (DT#5, elevates INV-13 from aspirational)
   - §21 Graceful degradation law (DT#6)
   - §22 Boundary-day settlement policy (DT bonus)
3. `docs/authority/zeus_dual_track_architecture.md` created.
4. `docs/operations/data_rebuild_plan.md` patched with dual-track sections.
5. `docs/operations/current_state.md` registers this packet.

### Phase 0b — Machine manifests (separate, adjacent)

Not executed in Phase 0. Adds:
- `architecture/invariants.yaml`: INV-14 … INV-20 for DT#1–#6 + boundary policy.
- `architecture/negative_constraints.yaml`: NC-11 … NC-14 for the enforced
  forbidden patterns.
- Runs after Phase 0 so CI never sees empty law references.

### Phase 1 — MetricIdentity spine

New `src/types/metric_identity.py`. Scanner / evaluator / rebuild / backfill
switch from bare strings to typed identity.

### Phase 2 — World DB v2 tables (no traffic migration)

Add `settlements_v2`, `market_events_v2`, `ensemble_snapshots_v2`,
`calibration_pairs_v2`, `platt_models_v2`, `observation_instants_v2`,
`historical_forecasts_v2`, `day0_metric_fact`. Sandbox validation only.

### Phase 3 — Observation closure + source registry collapse

`observation_client.py` returns `low_so_far`. `daily_obs_append.py` stops
maintaining a parallel station registry.

### Phase 4 — High canonical cutover to `mx2t6_local_calendar_day_max_v1`

Before any low lane writes, high lane is re-canonicalized onto the shared
local-calendar-day geometry. Parity report against current live high.

### Phase 5 — Low historical lane

Raw download, extract, ingest, boundary quarantine, calibration substrate.
`training_allowed` and `causality_status` become DB-enforced gates.

### Phase 6 — Day0 runtime split

`Day0HighSignal`, `Day0LowNowcastSignal`, `day0_router`. Low Day0 with
`N/A_CAUSAL_DAY_ALREADY_STARTED` must not hit a historical Platt lookup.

### Phase 7 — Metric-aware rebuild & model cutover

`rebuild_settlements_v2.py`, `rebuild_calibration_pairs_v2.py`,
`refit_platt_v2.py`, `backfill_tigge_snapshot_p_raw_v2.py`.

### Phase 8 — Low shadow-only window

Low candidates enter evaluator, produce shadow lineage only. Shadow report
answers the rollout-doctrine questions.

### Phase 9 — Low limited activation

Only after Gate F. Narrow city set, single lead family, isolated risk cap.

### Death-trap fix phases (interleaved)

- **DT#1 commit ordering** — lands in Phase 2 (co-located with state writes).
- **DT#2 RED force-exit** — lands in a dedicated risk phase before Phase 9.
- **DT#3 FDR family canonicalization** — lands in Phase 1 (identity work).
- **DT#4 chain three-state** — lands alongside Phase 2 state authority work.
- **DT#5 Kelly executable-price** — lands before Phase 9 activation.
- **DT#6 graceful degradation** — lands with Phase 6 runtime split.
- **Boundary-day settlement policy** — lands with Phase 9 activation gates.

## Gates (from rollout doctrine)

- **Gate A — schema**: sandbox can hold same-city-same-date high + low rows
  across all v2 tables.
- **Gate B — observation**: every main provider yields `low_so_far`; evaluator
  stops rejecting on that field.
- **Gate C — high-v2 parity**: high canonical cutover is explainable.
- **Gate D — low historical purity**: `training_allowed` + `causality_status`
  enforced; boundary quarantine reported.
- **Gate E — low shadow**: shadow trace complete; causality N/A routes
  correctly.
- **Gate F — low activation**: all prior gates pass; rollback rehearsed; risk
  layer distinguishes metric families.

## Rollback

Old tables and old runtime code paths stay readable throughout. Roll back by
disabling low candidate routing, deactivating low Platt models, and reverting
the evaluator router to the high-only branch. Schema v2 data is preserved for
audit.

## Evidence layout

- `phase0_evidence/` — doc patch receipts, topology-doctor outputs.
- `phase1_evidence/` … `phase9_evidence/` — created as each phase opens.
- `receipt.json` — compiled at every high-risk closeout.

## Authority order inside this packet

1. System / developer / user instructions
2. `architecture/**` machine manifests (once Phase 0b lands)
3. `docs/authority/zeus_current_architecture.md` + new
   `zeus_dual_track_architecture.md`
4. Root `AGENTS.md`
5. This `plan.md`
6. Scoped `AGENTS.md` files per directory
