# Zeus Dual-Track Metric Spine — End-State Structural Audit

**Reviewer**: architect agent (Opus, independent e2e audit post-P9C close)
**HEAD audited**: `0a760bb` (P9C close, pushed)
**Date**: 2026-04-19
**Mandate**: end-state structural verification — is dual-track actually landed, or phase-narrative fiction?
**Method**: disk-verified reads of authority docs + real code + live DB row counts. Phase narrative explicitly excluded as evidence.

---

## Executive verdict: **PARTIAL**

Structural skeleton of dual-track is real and traceable in code. Decision-path seams are wired with metric awareness. **However, the dual-track world is empty of LOW data and the HIGH track was not re-canonicalized onto the v2 substrate**. The "shipped" claim is load-bearing only for HIGH stability; LOW is code-ready but data-bankrupt, and Gates C + D are structurally unclosed. If Golden Window lifts tomorrow, shadow mode will produce LOW lineage rows but they will all be Level-4 uncalibrated rejects — no usable LOW probabilities will flow.

---

## D1. MetricIdentity spine — is it ROW TRUTH?

**Score: ~65%**. Structurally present and correctly typed at signal seams; weakened at database/state-row seams.

- `src/types/metric_identity.py:14-73` — `MetricIdentity` is a frozen dataclass with post-init cross-pairing guard (TypeError on `high + low_temp`). `from_raw()` is the single legal string boundary. This is the correct Fitz "make category impossible" antibody.
- **Typed (MetricIdentity) in signal layer** — `src/signal/day0_router.py:31`, `day0_signal.py:54`, `ensemble_signal.py:144,244`, `day0_window.py:29`, `day0_extrema.py:31`. Signal construction is fully typed.
- **Bare string (str) in decision/state layer** — `src/engine/evaluator.py:90,135`, `src/calibration/manager.py:128,246`, `src/engine/replay.py:244,1167,1995`, `src/state/portfolio.py:152`, `src/state/db.py:1138,3154`, `src/calibration/store.py:455`, `src/state/truth_files.py:51,92`. These are internal seams, not just JSON/SQL boundaries — a Position (held in memory) carries `temperature_metric: str = "high"` at `portfolio.py:152`, and `get_calibrator` accepts `str` at `manager.py:128`.
- **Normalization choke-point**: `evaluator.py:707` calls `_normalize_temperature_metric(...)` which returns a MetricIdentity internally but its `.temperature_metric` string attribute is re-extracted (`evaluator.py:740,966`) and handed down as str.

**Verdict**: MetricIdentity is the signal-layer spine but not the runtime spine. The type never crosses the signal→evaluator→calibration boundary as a typed object. This is an SD-1 relaxation in practice — "Bare strings for 'high'/'low' are only allowed at serialization boundaries" (zeus_dual_track_architecture.md:86-87) is violated by Position, get_calibrator, and replay's entry functions. Inside those boundaries, bare strings are circulating as row identity.

**Risk**: a future caller could pass `temperature_metric="high_temp"` (observation field) or `"low "` (trailing whitespace) to `get_calibrator` and receive None (legacy-fallback silence) rather than a TypeError. The immune system has antibodies at the signal layer only.

---

## D2. Dual-track row coexistence — can HIGH + LOW same (city, target_date) coexist?

**Score: ~95% (schema) / 0% (data).**

All v2 UNIQUE constraints include `temperature_metric` as required by INV-14 (`architecture/invariants.yaml:102-110`):
- `settlements_v2`: `UNIQUE(city, target_date, temperature_metric)` — `v2_schema.py:63`
- `ensemble_snapshots_v2`: `UNIQUE(city, target_date, temperature_metric, issue_time, data_version)` — `v2_schema.py:152`
- `calibration_pairs_v2`: `UNIQUE(city, target_date, temperature_metric, range_label, lead_days, forecast_available_at, bin_source, data_version)` — `v2_schema.py:210-211`
- `platt_models_v2`: `UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)` — `v2_schema.py:248`
- `historical_forecasts_v2`: `UNIQUE(city, target_date, source, temperature_metric, lead_days)` — `v2_schema.py:309`

All five tables carry `CHECK (temperature_metric IN ('high', 'low'))` constraints. Schema-wise, Gate A is structurally closed.

**However**: `sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM platt_models_v2 / calibration_pairs_v2 / settlements_v2 / ensemble_snapshots_v2 / historical_forecasts_v2"` returns **0, 0, 0, 0, 0** for live state. The v2 world is schema-only. No row has ever been written. The schema's ability to hold dual-track is untested in production.

---

## D3. Day0 dual-track routing — is Day0Router called at every Day0 seam?

**Score: 100% for production decision paths.**

Grep of all `Day0Signal(` / `Day0HighSignal(` / `Day0LowNowcastSignal(` / `Day0Router.route`:
- `src/engine/evaluator.py:887` — entry seam uses `Day0Router.route(Day0SignalInputs(...))`.
- `src/engine/monitor_refresh.py:312` — exit/monitor seam uses `Day0Router.route(...)`.
- `src/signal/day0_high_signal.py:68` — only internal construction of `Day0Signal(...)`; the Day0Signal class at `day0_signal.py:85-88` raises TypeError when called with LOW ("Day0Signal is HIGH-only. Use Day0Router.route() for metric-dispatched construction").

Only two production consumers exist, both route through `Day0Router`. The LOW construction guard at `day0_signal.py:85-88` (Phase 6 antibody, re-guarded `413d5e0`) makes the wrong code structurally unwritable. This is an actual Fitz-grade antibody — not a test, a runtime TypeError that kills any attempt to route LOW through the HIGH path.

---

## D4. DT#1–#7 landing matrix

| DT | Score | Evidence | Notes |
|---|---|---|---|
| DT#1 commit ordering | **85%** | `cycle_runner.py:397-430` uses `commit_then_export` pattern; 8 call sites across `portfolio.py`, `harvester.py`, `decision_chain.py`, `canonical_write.py`. INV-17 at `invariants.yaml:126-132`. | Structural wrapper exists and is called at the cycle orchestration layer. NC-13 semgrep rule `zeus-no-json-before-db-commit` is declared (`negative_constraints.yaml:79-85`) but I did not verify the semgrep ruleset content. Cycle-orchestration compliance only; no proof all side-effect paths are gated. |
| DT#2 RED force-exit | **80%** | `cycle_runner.py:306-320` calls `_execute_force_exit_sweep`; sweep marks `exit_reason="red_force_exit"` (`cycle_runner.py:97`); `portfolio.py:316-329` actuates via `ExitDecision(RED_FORCE_EXIT)` with `day0_active` exception. | The Phase 9B "inert marker" bug (sweep marks only, no actuator) was resolved at commit `b73927c` (`portfolio.py:316-329`). Day0 positions are skipped per comment at `portfolio.py:304-306` — partial coverage, documented as orthogonal. |
| DT#3 FDR family canonicalization | **40%** | `src/strategy/selection_family.py:83-101` — `make_family_id()` is explicitly **deprecated** with a DeprecationWarning. `evaluator.py:539` comment says "do NOT reconstruct via make_family_id/make_edge_family_id here". INV-22 at `invariants.yaml:157-166` says one choke-point helper + test. | Deprecated is not the same as canonicalized. The "one choke-point" invariant is declared, but the current codebase is mid-migration: the old choke-point is marked dead, and the NC-15 test target `test_fdr_family_key_is_canonical` exists in `test_dual_track_law_stubs.py` (stub). Evidence that all call sites use the new `make_hypothesis_family_id` is not in hand. |
| DT#4 chain three-state | **90%** | `src/state/chain_state.py:17-20` defines `ChainState` enum with CHAIN_SYNCED / CHAIN_EMPTY / CHAIN_UNKNOWN. `classify_chain_state()` at L26 is pure-function with deterministic transition table. 3 consumers: `chain_reconciliation.py`, `lifecycle_events.py`, `chain_state.py`. | Structurally sound. INV-18 at `invariants.yaml:133-140` backed by test stub. Stale-guard 6h window enforces CHAIN_UNKNOWN on recently-verified empties. |
| DT#5 Kelly executable-price | **60%** | `src/strategy/kelly.py:33-79` accepts `float \| ExecutionPrice`. When ExecutionPrice passed, `assert_kelly_safe()` runs. Bare float path preserved for backward compat. NC-14 + INV-21 at `invariants.yaml:92-101`. | The type system is polymorphic, not structural. Comment at `kelly.py:51-54` admits "Migration of remaining bare-float callers (replay.py:1300, scripts/...) is a later-phase chore". The gate is opt-in, not forced. 4 call sites (`evaluator.py:235,247`, `replay.py:1357`, `kelly.py:33`) — I did not verify which pass typed ExecutionPrice vs bare float. Failure category is not yet impossible. |
| DT#6 graceful degradation | **85%** | `cycle_runner.py:234-254` handles `portfolio_loader_degraded`: runs `tick_with_portfolio`, flips `risk_level = DATA_DEGRADED`, does not raise. `cycle_runner.py:355-360` honors DATA_DEGRADED in entry block reasons. INV-20 at `invariants.yaml:149-156`. | Phase 9A MINOR-M4 intentional overwrite is documented. Full law compliance: monitor/exit/reconciliation continue read-only, new-entry suppressed via `_risk_allows_new_entries(RiskLevel.GREEN)`. Good. |
| DT#7 boundary-day | **45%** | Clause 3 wired: `evaluator.py:738-751` calls `_read_v2_snapshot_metadata → boundary_ambiguous_refuses_signal`. Clauses 1 (leverage reduction) + 2 (oracle-penalty isolation) are **explicitly deferred** per `src/contracts/boundary_policy.py:17-25`. | Pre-Golden-Window: v2 is empty → helper returns `{}` → gate returns False → no refusal (`evaluator.py:733-735`). The wire exists and will fire post-data-lift. Clauses 1-2 are honest TODOs in a module docstring. Honest but incomplete: only 1/3 of DT#7 is landed. |

**Items below 80%**: DT#3, DT#5, DT#7. DT#3 is the most concerning because FDR family drift is a **Fitz Constraint #3 immune system** failure mode — it silently corrupts calibration across the whole system, not just LOW.

---

## D5. Gates A–F actual state

- **Gate A (schema)**: CLOSED. UNIQUE(..., temperature_metric) on all 5 v2 tables. CHECK constraint on {'high','low'}. See D2.
- **Gate B (observation)**: CLOSED. `observation_client.py:38` — `low_so_far: float` is required and never-None; `__post_init__` at L44-45 raises ValueError if absent. Three provider paths populate it (L262, L329, L400). Evaluator at `evaluator.py:876-885` rejects LOW candidates missing `low_so_far` with `OBSERVATION_UNAVAILABLE_LOW`.
- **Gate C (HIGH v2 parity)**: **NOT CLOSED**. `ensemble_snapshots_v2` has 0 rows; `historical_forecasts_v2` has 0 rows; `platt_models_v2` has 0 rows. HIGH canonical cutover to `tigge_mx2t6_local_calendar_day_max_v1` is code-ready — `metric_identity.py:82` defines the canonical `data_version` — but no live HIGH write target was cut over. Backfill scripts exist (`refit_platt_v2.py`, `rebuild_calibration_pairs_v2.py`) but none have executed against live data per the row counts. `get_calibrator` at `manager.py:159-168` reads v2 first then falls back to legacy `platt_models` — "legacy fallback only for HIGH" means HIGH is **still running on legacy tables**. Gate C was meant to close **before** LOW landed. It did not.
- **Gate D (low historical purity)**: **PARTIALLY CLOSED**. Schema carries `training_allowed` + `causality_status` NOT NULL on `ensemble_snapshots_v2` (`v2_schema.py:133-143`) and `calibration_pairs_v2` (`v2_schema.py:206-208`). CHECK constraints enumerate the causality values. DB-enforced gates exist. Zero LOW rows means the enforcement is untested. Boundary-ambiguous refuse mechanism (`evaluator.py:742`) is active but reads empty v2 → no-op.
- **Gate E (low shadow)**: **CODE-READY**. `run_replay` threads `temperature_metric` from public entry (`replay.py:1995`, `1167`, `1252`). `_forecast_rows_for` has v2-first/legacy-fallback conditional (`replay.py:242-310`). Day0Router routes LOW to `Day0LowNowcastSignal`. Calibrator lookup is metric-aware. Shadow would execute if LOW data existed. It does not.
- **Gate F (low activation)**: NOT CLOSED per plan scope.

**Critical finding**: Gate C was explicitly declared in-scope per `plan.md:152-153` ("high canonical cutover is explainable"). The claim that "dual-track main line is closed" rests on Gate C being closed. It is not closed by evidence — it is closed by documentation. The HIGH lane still reads from legacy `platt_models` in production.

---

## D6. Silent-bypass hazards

**Low**. Grep of `temperature_metric = "high"` literals in src/ finds only:
- `src/types/metric_identity.py:32,79` — error message string + canonical HIGH_LOCALDAY_MAX constant. Both expected.

Default parameter values `temperature_metric: str = "high"` appear at `evaluator.py:90`, `calibration/manager.py:128,246`, `replay.py:244,318,356,385,1167,1995`, `state/portfolio.py:152`. These defaults are **structurally appropriate**:
- `run_replay(temperature_metric="high")` default matches plan scope (HIGH is canonical).
- `get_calibrator(temperature_metric="high")` default — here is the one real hazard: if a future call site forgets to thread the metric through, LOW would silently get HIGH calibration. The P9C L3 fix closed the known call sites but the default itself is a latent hazard — it violates the spirit of SD-1.

The `_fit_from_pairs` guard at `manager.py:263-270` is a good Fitz-grade antibody: on-the-fly refit returns None for LOW, preventing legacy write pollution. The write-side two-seam close from Phase 9C.1 is an actual antibody, not a note.

No hardcoded `"high"` in decision paths outside legitimate defaults.

---

## D7. Critical remaining gaps if Golden Window lifts tomorrow

**Top 3, ranked by production blast radius:**

1. **Gate C never actually closed — HIGH runs on legacy tables.** The P7A/P7B/P8/P9 narrative declared cutover, but `platt_models_v2` has 0 rows. `get_calibrator` at `manager.py:165-168` explicitly has a "legacy fallback only for HIGH" branch that is the live production path today. If a data migration runs tomorrow and copies HIGH into v2 without careful de-duplication, the `is_active` UNIQUE constraint will collide or HIGH will be served from two sources. Blast radius: all HIGH positions.

2. **No LOW training data exists anywhere.** `calibration_pairs_v2` has 0 rows. Even if the Golden Window lifts and LOW markets open tomorrow, `get_calibrator` for LOW will return `(None, 4)` at `manager.py:226`. Level 4 (n<15) bypasses Platt entirely per spec §3.3 (`manager.py:91-106`) and applies a 3× edge-threshold multiplier at `edge_threshold_multiplier` (`manager.py:121`). In practice, most LOW candidates will fail the edge threshold and be rejected with `no_calibrator` — making LOW "deployed" but inert. Shadow lineage will exist but contain no calibrated probabilities.

3. **Bare-string temperature_metric traverses Position, get_calibrator, and state/db.** A mis-typed LOW at any of 13+ call sites (grep in D1) fails silently — it returns None from `load_platt_model_v2` and falls back to legacy, where LOW is not present, so it returns `(None, 4)`. Silent degrade instead of TypeError. This is exactly the Fitz Constraint #2 translation-loss failure: types aren't enforcing invariants, comments are.

**Secondary gaps (would not block production but erode immune system):**

- DT#5 opt-in Kelly structural gate: 4 call sites, unclear how many pass ExecutionPrice.
- DT#7 clauses 1-2 deferred (leverage reduction, oracle penalty isolation): documented TODO in boundary_policy.py.
- DT#3 FDR family canonicalization mid-migration: deprecated old function, new function exists, but no evidence every call site was updated.

---

## Direct answer: would shadow mode produce usable LOW lineage tomorrow?

**No.** Shadow mode would produce LOW lineage *rows* (evaluator.py decision records, replay.py backtest rows), but those rows would all carry:
- `cal = None`, `cal_level = 4` (no calibrator — `manager.py:226`)
- `p_cal = p_raw.copy()` (uncalibrated passthrough — `evaluator.py:980`)
- `edge_threshold_multiplier(4) = 3.0×` (`manager.py:121`)

At 3× edge threshold with uncalibrated raw ensemble probability and no market feedback loop, LOW would fail the majority of edge gates and reject with `rejection_reason='no_calibrator'` (design-declared fail-closed per `zeus_dual_track_architecture.md:146-148`). Shadow *lineage* would exist; usable *probabilities* would not. "Shadow observation window" is nominally open (Gate E) but gathers no calibrable signal because `calibration_pairs_v2` has zero rows — which requires ingest of historical LOW forecasts (Phase 5 scope) that never ran against live data.

This is fail-closed by design and not a bug — but it means the "DUAL-TRACK MAIN LINE CLOSED" commit message at `0a760bb` is **structural readiness, not deployment**. The ship is seaworthy; the cargo was never loaded.

---

## Fitz Four-Constraints scoring

| Constraint | Score | Evidence |
|---|---|---|
| 1. Structural > patches | **7/10** | Real antibodies: Day0Signal TypeError guard (day0_signal.py:85-88), MetricIdentity cross-pairing ValueError (metric_identity.py:30-39), ChainState enum (chain_state.py:17-20), `_fit_from_pairs` LOW refusal (manager.py:263-270). These make wrong code unwritable. However DT#3 and DT#5 are polymorphic not structural. |
| 2. Translation loss | **6/10** | Code/types/tests dense at signal boundary. Runtime boundary still carries bare strings through Position, get_calibrator, replay public API. The design intent "MetricIdentity is row truth" survives in signal but not in state. |
| 3. Immune system | **6/10** | Multiple runtime antibodies present (TypeErrors, ValueErrors). Missing: no sensor for "v2 tables are empty" — the system does not know it is running on a skeleton. The Phase 9C close declared dual-track closed while v2 tables are empty; no automated check caught this. |
| 4. Data provenance | **8/10** | `authority` field present on settlements_v2, ensemble_snapshots_v2, calibration_pairs_v2, platt_models_v2, rescue_events_v2 with CHECK IN ('VERIFIED','UNVERIFIED','QUARANTINED'). `provenance_json` on settlements_v2 and ensemble_snapshots_v2. `causality_status` enum enforced in schema. Strongest layer. |

---

## Top 5 gaps prioritized by production impact

1. **Gate C not actually closed** — HIGH lane reads legacy `platt_models`; v2 HIGH is an empty schema. `src/calibration/manager.py:165-168`, `state/zeus-world.db` row counts.
2. **No LOW calibration data** — `calibration_pairs_v2` / `platt_models_v2` empty. LOW activation produces uncalibrated (Level-4) rejects. No Phase 5 backfill ever ran against live data.
3. **Internal string boundaries bypass MetricIdentity** — `Position.temperature_metric: str`, `get_calibrator(temperature_metric: str)`, `run_replay(temperature_metric: str)`. Type system does not catch LOW/HIGH confusion at these seams. `src/state/portfolio.py:152`, `src/calibration/manager.py:128`, `src/engine/replay.py:1995`.
4. **DT#5 opt-in Kelly gate** — `ExecutionPrice` path is polymorphic, not mandatory. Bare-float callers still work. `src/strategy/kelly.py:51-54`.
5. **DT#7 clauses 1-2 deferred** — leverage reduction and oracle-penalty isolation are docstring TODOs. `src/contracts/boundary_policy.py:17-25`.

---

## Consensus Addendum

**Antithesis (steelman)**: The phase narrative's claim of closure is defensible if one interprets "dual-track main line closed" as "structural readiness, pre-data." The Golden Window is the user's explicit gate — scoring Gate C as failed presumes Gate C was supposed to close before data migration, but the refactor philosophy allowed schema-first/data-later. v2 being empty is not a bug if the user intended it to be empty during Golden Window.

**Tradeoff tension**: Structural readiness without data migration creates a false sense of completeness. The UNIQUE constraints are untested under load; the metric-aware read paths have never encountered an actual LOW row; the boundary-ambiguous refuse mechanism has never fired. "Closed" in a documentation sense ≠ "exercised" in a runtime sense. The user must decide whether the Golden Window risk (v2 stays empty indefinitely) is smaller than the cutover risk (migrating live HIGH into v2).

**Synthesis**: Explicitly retitle the current state as "Dual-Track Scaffold Complete, Data Migration Pending" rather than "Dual-Track Closed." Add a runtime assertion (`state_get_status`-style) that surfaces v2 row counts in observability — a Fitz immune-system antibody that stops the next agent from hallucinating completion from docs alone. Treat Gate C as explicitly open, with documented trigger conditions.

---

## References (absolute paths)

- `src/types/metric_identity.py:14-90` — MetricIdentity typed spine.
- `src/state/schema/v2_schema.py:47-412` — v2 DDL with metric-discriminated UNIQUE + CHECK.
- `src/engine/evaluator.py:707-900` — decision seam, metric normalization, Day0Router, DT#7 wire at :738-751.
- `src/signal/day0_router.py:49-79` — dual-track dispatch.
- `src/signal/day0_signal.py:85-88` — LOW TypeError guard antibody.
- `src/calibration/manager.py:124-226` — get_calibrator metric-aware fallback with legacy-HIGH-only branch.
- `src/calibration/manager.py:244-323` — `_fit_from_pairs` LOW refusal (P9C.1 two-seam antibody).
- `src/calibration/store.py:353-450` — save/load platt v2.
- `src/engine/replay.py:242-310,1252,1995` — replay metric threading + v2 conditional.
- `src/engine/cycle_runner.py:234-254,306-320,397-430` — DT#6 graceful degrade, DT#2 RED sweep, DT#1 commit-then-export.
- `src/state/portfolio.py:152,316-329,1080-1108` — Position bare string, RED_FORCE_EXIT actuator, save source tagging.
- `src/contracts/boundary_policy.py:17-63` — DT#7 clause 3, clauses 1-2 explicit deferral.
- `src/state/chain_state.py:17-80` — DT#4 three-state enum.
- `src/strategy/kelly.py:33-79` — DT#5 polymorphic Kelly gate.
- `src/strategy/selection_family.py:83-101` — DT#3 deprecated make_family_id.
- `architecture/invariants.yaml:92-166` — INV-14..22 manifest.
- `architecture/negative_constraints.yaml:65-100` — NC-11..15 manifest.
- `state/zeus-world.db` — live state: v2 tables all 0 rows (verified via sqlite3 query).
