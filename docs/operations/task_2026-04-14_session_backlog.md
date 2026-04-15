# Backlog snapshot — 2026-04-14

Rolling task list captured at the end of the calibration-refactor session
(commits `df13308` / `45745ba` / `854cf5d` / `ed13310`). Categorized by
unblocking condition, not by invention date. Task IDs match the session
TaskList; items referenced by letter (`B`, `F`, `G.6`, …) are the K4 /
100-bug change labels from the prior session plan.

---

## 1. In-progress (running background work)

| ID | Task | State |
|----|------|-------|
| **#55** | `backfill_wu_daily_all.py --all --missing-only --days 834` (2026-04-14 restart after killing stale-Layer3 PID 62635) | Sequential PID **49371** on Step 2 (Open-Meteo hourly). Step 1 WU done at 12:44. Many `SSL: UNEXPECTED_EOF_WHILE_READING` / `Connection reset by peer` chunks on Open-Meteo API side — all logged as `FAILED` in `data_coverage` with 1h retry embargo, fillback will re-fetch. |
| **#57** | [DATA LOSS EVENT] `historical_forecasts` / forecasts table Rainstorm-migrated 171K rows wiped | Will re-populate from `forecasts` table after backfill completes via `scripts/etl_historical_forecasts.py`. Blocked on backfill. |
| *waiter* | `post_sequential_fillback.sh` PID **50114** | Sleeping, polls PID 49371 every 60s. Will kick off WU `--all --missing-only` fillback + HKO refresh + `hole_scanner --scan all` when sequential exits. |

Monitor `bl49mvsry` persistent-tails the rebuild log. No manual intervention required unless mass SSL errors escalate to full-city failures.

## 2026-04-15 Work Record — Refit Preflight / Data-Rebuild Closure

Date: 2026-04-15
Branch: data-improve
Task: Close the active refit-preflight/data-rebuild repair batch after K1-K8 slice work, including canonical rebuild contracts, calibration n_eff/refit behavior, live signal/bin math repairs, script retirements, and matching tests/docs.
Changed files: `.claude/CLAUDE.md`, `config/cities.json`, `pytest.ini`, `architecture/data_rebuild_topology.yaml`, `architecture/test_topology.yaml`, `architecture/topology.yaml`, `docs/authority/zeus_live_backtest_shadow_boundary.md`, `docs/known_gaps.md`, `docs/operations/data_rebuild_plan.md`, `docs/operations/task_2026-04-14_session_backlog.md`, `docs/reference/zeus_domain_model.md`, `scripts/backfill_hko_daily.py`, `scripts/backfill_hourly_openmeteo.py`, `scripts/backfill_ogimet_metar.py`, `scripts/backfill_wu_daily_all.py`, `scripts/etl_tigge_calibration.py`, `scripts/etl_tigge_direct_calibration.py`, `scripts/etl_tigge_ens.py`, `scripts/generate_calibration_pairs.py`, `scripts/migrate_add_authority_column.py`, `scripts/onboard_cities.py`, `scripts/rebuild_calibration.py`, `scripts/refit_platt.py`, `src/calibration/decision_group.py`, `src/calibration/effective_sample_size.py`, `src/calibration/manager.py`, `src/calibration/platt.py`, `src/calibration/store.py`, `src/config.py`, `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`, `src/data/ingestion_guard.py`, `src/data/market_scanner.py`, `src/engine/cycle_runtime.py`, `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`, `src/execution/harvester.py`, `src/signal/day0_signal.py`, `src/signal/ensemble_signal.py`, `src/state/chain_reconciliation.py`, `src/state/data_coverage.py`, `src/state/db.py`, `src/state/portfolio.py`, `src/strategy/kelly.py`, `src/strategy/market_analysis.py`, `src/strategy/market_fusion.py`, `src/types/market.py`, `state/assumptions.json`, `tests/test_architecture_contracts.py`, `tests/test_authority_gate.py`, `tests/test_calibration_manager.py`, `tests/test_center_buy_repair.py`, `tests/test_cities_config_authoritative.py`, `tests/test_config.py`, `tests/test_data_rebuild_relationships.py`, `tests/test_etl_recalibrate_chain.py`, `tests/test_execution_price.py`, `tests/test_fdr.py`, `tests/test_ingestion_guard.py`, `tests/test_k2_live_ingestion_relationships.py`, `tests/test_k3_slice_p.py`, `tests/test_k3_slice_q.py`, `tests/test_k8_slice_r.py`, `tests/test_k8_slice_s.py`, `tests/test_lifecycle.py`, `tests/test_live_safety_invariants.py`, `tests/test_market_analysis.py`, `tests/test_platt.py`, `tests/test_pnl_flow_and_audit.py`, `tests/test_rebuild_pipeline.py`, `tests/test_runtime_guards.py`, `zeus_data_inventory.xlsx`
Summary: Retired legacy TIGGE/direct calibration scripts fail-closed, kept canonical calibration pair rebuild/refit as the active path, added n_eff/group bootstrap/bin-grid/logit/market-imputation guards, aligned city config and WU station registries with audited Polymarket source changes, preserved monitor/operator visibility, updated city assumptions for the 51-city set, and removed the stale root data inventory after the active evidence copy moved under docs artifacts.
Verification: `python scripts/topology_doctor.py --source/--tests/--scripts/--docs/--data-rebuild/--strict --summary-only`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --summary-only`; `python -m py_compile` over dirty `src/**` and `scripts/**` Python files; pytest Group A `tests/test_calibration_manager.py tests/test_data_rebuild_relationships.py tests/test_etl_recalibrate_chain.py tests/test_rebuild_pipeline.py` (68 passed, 6 skipped); Group B `tests/test_platt.py tests/test_fdr.py` (37 passed); Group C `tests/test_ingestion_guard.py tests/test_k2_live_ingestion_relationships.py tests/test_runtime_guards.py` (163 passed); Group D targeted lifecycle/architecture/pnl tests (18 passed across selected assertions); new K3/K8 slice tests (23 passed). Full `pytest -q` was also attempted after adding `pytest.ini`; remaining failures are outside this package's acceptance boundary and come from historical/live-state-dependent tests (`tests/test_cross_module_relationships.py`, `tests/test_riskguard.py`) plus legacy paper-mode healthcheck tests.
Next: Commit the closed data-rebuild/refit batch without `state/status_summary.json` or `.claude/worktrees/`, then keep remaining full-suite historical cleanup as a separate packet.

**2026-04-14 upstream audit mark:** bug-audit rows **#35** and **#37**
are repaired in the live WU daily-observation ingest path:
`daily_obs_append` now preserves WU auth/rate-limit/server/parse/empty-payload
failure meanings in `data_coverage.reason`, and live observation refreshes use
explicit `ON CONFLICT DO UPDATE` instead of SQLite `REPLACE`. The bug-audit
workbook has been marked accordingly. Hold broader calibration-affecting fixes
until WU/TIGGE backfill/download completes and the recalibration input set is
available for targeted selection.

**2026-04-14 status reconciliation:** the workbook and this backlog use
different ID namespaces. The workbook now marks Excel rows **#30, #33, #34,
#58** as eliminated by the refit-preflight/input-contract repair, and marks
**#29, #31, #32, #35, #59** as changed or half-fixed rather than fully closed.
In this backlog namespace, refit-preflight items **#41, #42, #46, #48, #49**
are implemented/resolved as described below; **#43** is helper-only until the
live evaluator gate is wired, and **#50** is partial until canonical group shape
validation is unit-aware. Do not translate backlog IDs directly into Excel bug
IDs.

Refit scheduling status is also scoped: `_etl_recalibrate` no longer runs
`refit_platt.py` automatically, but `harvester.py` still has a live
`maybe_refit_bucket` seam after settlement learning writes. Treat scheduled
refit as disabled and harvester-triggered refit as a remaining seam, not as a
closed canonical-cascade story.

---

## 2. Unblocked after backfill + TIGGE complete

These are the **post-download ETL cascade** that produces the 9 empty derived tables required by live-engine calibration.

| ID | Task | Depends on | Notes |
|----|------|------------|-------|
| **#63** | Post-fillback derived ETL cascade (11 steps) | Backfill (#55) + TIGGE raw ingest | Order: `rebuild_settlements --no-dry-run` → `rebuild_calibration_pairs_canonical --no-dry-run --force` (NEW path from `df13308`, replaces `generate_calibration_pairs.py`) → `etl_historical_forecasts.py` ∥ `etl_forecast_skill_from_forecasts.py` → `etl_hourly_observations.py` ∥ `etl_diurnal_curves.py` ∥ `etl_temp_persistence.py` → `refit_platt.py` → `hole_scanner --scan all` → `hole_scanner --report`. |
| **#61** | TIGGE raw→DB transfer rewrite | User's TIGGE download | Current 41,261 `ensemble_snapshots` rows all `data_version='tigge_step024_v1_*'` (partial, unaudited). The new rebuild script refuses to touch them without `--allow-unaudited-ensemble`. Full TIGGE ingest needs a rewrite of the raw→DB transfer path to produce audited `authority='VERIFIED'` rows with non-partial `data_version`. |
| **#52** | Change L — run TIGGE multi-step GRIB extraction | TIGGE download complete | Upstream of #61. |
| **#53** | Change M — create `scripts/ingest_grib_to_snapshots.py` | #52 | Placeholder is registered and fail-closed; actual audited GRIB→`ensemble_snapshots` writer remains pending. |

**Critical architectural note:** task #63 used to read `generate_calibration_pairs.py` at step 2. The 2026-04-14 refactor (`df13308`) replaced that with `scripts/rebuild_calibration_pairs_canonical.py`, which no longer depends on `market_events` (which was lost in #57 and cannot be recovered per user directive — `rainstorm.db` is rejected as unaudited). The new script runs end-to-end from `observations` + `ensemble_snapshots.members_json` alone. See `~/.claude/plans/logical-chasing-ritchie.md` for the full design.

---

## 3. 100-bug calibration fixes (blocked on `calibration_pairs` being populated)

All four depend on #63 producing `calibration_pairs` rows under the new canonical grid before they can be verified end-to-end. Code can be written now; tests must pass with a seeded in-memory DB fixture.

| ID | Change | File:line | Rationale |
|----|--------|-----------|-----------|
| **#47** | J — eps value | `src/calibration/*` | OPEN QUESTION: current code uses `eps=0.01`, math spec (`docs/reference/zeus_math_spec.md`) says `1e-6`. Pick one and align both code + spec. Neither is obviously wrong — `0.01` is conservative against numerical underflow at Platt edges, `1e-6` is theoretically tighter. User decision required. |
| **#48** | K — Platt bootstrap by decision_group | `src/calibration/platt.py` | IMPLEMENTED in refit-preflight repair: `ExtendedPlattCalibrator.fit(..., decision_group_ids=...)` bootstraps by decision group when IDs are provided. |
| **#49** | N — live `_bin_probability` histogram equivalence | `src/strategy/market_analysis.py`, `src/types/market.py`, `src/signal/ensemble_signal.py` | IMPLEMENTED in refit-preflight repair: live and signal paths share bin containment/count helpers. |
| **#50** | G.7 — `maturity_level` uses `n_eff` not row count | `src/calibration/manager.py`, `scripts/refit_platt.py`, `src/calibration/effective_sample_size.py` | DONE. Unit-aware group-shape gate: `_canonical_pair_groups_valid` now accepts `unit` param and validates against `F_CANONICAL_GRID.n_bins`/`C_CANONICAL_GRID.n_bins`. `effective_sample_size._raise_on_group_collision` also unit-aware via city→unit lookup. |

---

## 4. Deferred / quarantined changes from prior plan

These are NOT unblocked by post-download work; they're small targeted cleanups the prior plan deferred out of scope.

| ID | Change | File:line | Priority |
|----|--------|-----------|----------|
| **#41** | G.6 — delete `store.py decision_group_id` fallback | `src/calibration/store.py` | IMPLEMENTED in refit-preflight repair: `add_calibration_pair` now requires nonblank explicit `decision_group_id`; call sites use `compute_id()`. |
| **#42** | H — `Bin` ±inf + `to_json_safe/from_json_safe` | `src/types/market.py` | IMPLEMENTED in refit-preflight repair with staged `None`/±inf compatibility and JSON sentinels. |
| **#43** | I — `validate_bin_topology` helper | `src/types/market.py` | DONE. Helper implemented + evaluator entry gate wired in `evaluator.py` (catches `BinTopologyError` → MARKET_FILTER rejection). |
| **#45** | B — `load_cities` metadata validation | `src/config.py:load_cities` | DONE. Explicit validation for `unit`, `timezone`, `wu_station`, `country_code`, and `lat`/`lon` with clear error messages. All 51 cities pass. |
| **#51** | Test file prune — remove dead relationship tests | `tests/test_cross_module_relationships.py` | DONE. R4 (_load_baselines_from_risk_history removed) and R5 (positions.json decoupled) deleted. R2 RETAINED (detects live orphan positions bug). R3 RETAINED (Phase 2 re-enablement). R6 RETAINED (active fill-status regression guard). K2 coverage is ORTHOGONAL (weather ETL), NOT superseding (trade lifecycle). |

---

## 5. Operational / security

| ID | Task | Action |
|----|------|--------|
| **#62** | WU_API_KEY rotation (security) | Transition key `e1f10a1e78da46f5b10a1e78da96f525` is still exported inline by `scripts/post_sequential_fillback.sh` and `scripts/resume_backfills_sequential.sh`. **Operator action:** (a) rotate at weather.com, (b) set new key in operator env (`~/.zshrc` / launchd plist), (c) delete the two inline `export WU_API_KEY=...` blocks and re-commit the scripts. Currently blocks clean fresh-clone deployment. |
| **#64** | K2 Phase C cleanup (deferred reviewer findings) | PARTIAL. `_log_availability_failure` fd leak FIXED (try/finally pattern). Remaining: `source_applies_to_city → cities.json` config; `forecasts.rebuild_run_id → ForecastRow`; shared Open-Meteo client extraction; post-ingestion relationship tests for Layer 3 replacement. |

---

## 6. Open questions requiring user decision

| ID | Question | Options |
|----|----------|---------|
| **#46** | ETL script fate for `scripts/etl_tigge_calibration.py` and `scripts/etl_tigge_ens.py` | RESOLVED in refit-preflight repair: direct TIGGE calibration writers are stale-deprecated and fail-closed. Audited replacement remains #53 (`ingest_grib_to_snapshots.py`). |
| **#47** | Eps value (see §3 above) | `0.01` (current code, conservative) vs `1e-6` (math spec, tight) |
| **#56** | Plan patch v2.2 → v2.3: physical-data-only scope narrowing | Whether to ship a trimmed v2.3 plan that explicitly excludes market-events recovery (aligned with 2026-04-14 refactor decision) |

---

## 7. Completed in this session (for session-local orientation only — not backlog items)

- `#54` Change C — `IngestionGuard` unit + hemisphere rigor
- `#58` `backfill_hko_daily.py` created + running
- `#59` K2 live-ingestion packet (4 append modules + hole scanner + data_coverage ledger)
- `#60` Layer 3 seasonal envelope deletion (`ff287ad`)
- `#65` `src/contracts/calibration_bins.py` — canonical bin grid contract
- `#66` Extract `p_raw_vector_from_maxes` free function
- `#67` Add `bin_source` column to `calibration_pairs`
- `#68` `scripts/rebuild_calibration_pairs_canonical.py`
- `#69` Tests R1-R13 in `tests/test_calibration_bins_canonical.py` (28/28 green)
- `#70` Unit tests + dry-run smoke + commit (`df13308`)

---

## Data-loss event registry (per CLAUDE.md `DATA_REBUILD_LIVE_MATH_CERTIFICATION_BLOCKED`)

**2026-04-12 / #57 — Rainstorm migration data loss.** `rainstorm.db` no longer exists at its canonical path. Tables lost:

- `market_events` (0 rows in zeus-world.db)
- `token_price_log` (0)
- `market_price_history` (0)
- `chronicle` (0)
- `outcome_fact` (0)
- `opportunity_fact` (0)
- `historical_forecasts` (0)

Tables preserved (Zeus wrote them directly, not via Rainstorm migration):

- `observations` (33k+ rows, rebuilt by backfill)
- `ensemble_snapshots` members_json (40,350 rows, partial TIGGE — to be overwritten)
- `solar_daily`, `forecasts`

**Recovery decision (2026-04-14, user directive):** `rainstorm.db` is rejected as recovery source because it never passed audit. The calibration path was refactored (commit `df13308`) to eliminate the `market_events` dependency entirely. `market_price_history`, `chronicle`, `outcome_fact`, `opportunity_fact` remain empty and are NOT on the critical path for live math — live scanning + K2 live-ingestion will populate them going forward.

---

## References

- Prior session plans: `docs/operations/data_rebuild_plan.md`, `docs/operations/current_state.md`
- Calibration refactor plan: `~/.claude/plans/logical-chasing-ritchie.md`
- Active topology law: `AGENTS.md`, `architecture/topology.yaml`, `architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`
- Commit trail (this session, data-improve branch):
  - `df13308` feat(calibration): canonical-bin Platt training path (code only, no retraining)
  - `45745ba` docs(topology): register calibration refactor in machine manifests
  - `854cf5d` feat(topology): advisory/precommit/closeout modes + close registration gaps
  - `ed13310` ops(K2): sequential backfill restart with --all --missing-only + WU_API_KEY guard
