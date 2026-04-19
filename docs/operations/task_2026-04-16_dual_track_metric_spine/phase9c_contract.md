# Phase 9C Contract — Dual-Track Main-Line Closure

**Written**: 2026-04-18 post P9B close (`69978af` on origin).
**Branch**: `data-improve`.
**Mode**: Gen-Verifier. critic-dave cycle 1 (fresh spawn, succeeds critic-carol after her 3-cycle retirement).
**User ruling 2026-04-18**: "P9C 可以直接推进，不需要拆... 先完成P9C+critic然后我们最后再做一轮多角度验证" — single commit, no split; post-P9C multi-angle e2e verification.

## CRITICAL finding (P9C scout 2026-04-18)

**L3 — `get_calibrator` is metric-blind** (`src/calibration/manager.py:123`). Signature `get_calibrator(conn, city, target_date)` has NO `temperature_metric` parameter. Queries `load_platt_model(conn, bk)` where `bk = bucket_key(cluster, season)`. `platt_models_v2` schema is indexed by `(metric_identity, cluster, season)` but the read path does not filter on metric.

**Silent failure mode**: A LOW candidate flowing through evaluator/replay/monitor_refresh will receive the HIGH Platt model. Result: systematically wrong p_cal for LOW → wrong alpha fusion → wrong edge → wrong Kelly size → miscalibrated LOW trades. This is the structural CRITICAL that blocks actual LOW activation.

Callers affected (must be updated to pass metric):
- `src/engine/monitor_refresh.py:135, 324` (already metric-aware at position level per P9C scout verification)
- `src/engine/replay.py:1197` (P9A threaded metric through run_replay → _replay_one_settlement)
- `src/engine/evaluator.py:891` (candidate flow already carries metric)

## Scope — ONE commit delivers

### S1 — L3 CRITICAL: get_calibrator metric-aware

`src/calibration/manager.py`:
- `get_calibrator(conn, city, target_date, temperature_metric: str = "high")` adds param
- `bucket_key` upgraded or parallel keyer added to include metric in the primary bucket
- `load_platt_model` query WHERE clause filters on metric when reading v2 table
- Hierarchical fallback (cluster+season → season-only → global → None) preserves metric discrimination at each tier

Callers updated:
- `monitor_refresh.py:135, 324` pass `position.temperature_metric`
- `replay.py:1197` pass `temperature_metric` (threaded from run_replay public entry P9A)
- `evaluator.py:891` pass from candidate metric

### S2 — A3: Day0LowNowcastSignal.p_vector

`src/signal/day0_low_nowcast_signal.py`:
- Add `def p_vector(self, bins, n_mc=None, rng=None) -> np.ndarray:` that returns `np.array([self.p_bin(b.lo, b.hi) for b in bins])`
- No delegation to HIGH (R-BE invariant intact)
- Match Day0HighSignal.p_vector signature (bins, n_mc, rng) so Day0Router dispatch is type-compatible

### S3 — A1: B093 half-2 conditional v2 read

`src/engine/replay.py:_forecast_rows_for (L242-264)`:
- Check if `historical_forecasts_v2` has rows for city+date; if yes, query v2 with `AND temperature_metric = ?`
- Else fall back to legacy `forecasts` (preserves zero-data Golden Window behavior)
- Thread `temperature_metric` param (caller already has it via P9A run_replay threading)

### S4 — A4: DT#7 full enforcement (evaluator wire)

`src/engine/evaluator.py` candidate-decision flow:
- Call `boundary_ambiguous_refuses_signal(snapshot_dict)` at candidate evaluation; when True, reject with `rejection_reason="DT7_boundary_day_ambiguous"`
- Exact insertion point: `evaluator.py` candidate-rejection gate chain (scout cited file:line context)
- Leverage reduction + oracle-penalty-per-city isolation: remain **deferred to later phase** — those require monitor_refresh LOW data flow AND a per-city oracle structure change. P9C delivers the REFUSAL gate (clause 3 of DT#7) which is the primary risk-containment behavior.

### S5 — B1: `--temperature-metric` CLI flag

`scripts/run_replay.py`:
- Add `--temperature-metric` argparser arg with `choices=["high", "low"]`, `default="high"`
- Thread to `run_replay(temperature_metric=args.temperature_metric)`

### S6 — B3: save_portfolio source param (minimal)

`src/state/portfolio.py:save_portfolio`:
- Add `source: str = "internal"` kwarg — logged into truth-annotated JSON payload
- NO runtime enforcement — caller-side discipline per DT#6 §B Interpretation B
- Callers at `cycle_runner.py:411` + `harvester.py:401` updated to pass origin tag ("reconciliation" / "fill_event" / "settlement")

### S7 — C2: R-BY.2 antibody strengthening

`tests/test_dual_track_law_stubs.py`:
- Extend R-BY.2 with inverse fixture: Day0 position with HIGH exit trigger (not red_force_exit). Assert evaluate_exit returns normal day0 decision, trigger is neither RED_FORCE_EXIT nor silently missing. Closes critic-carol cycle-3 L15 asymmetric-discrimination gap.

### S8 — L2: dedicated test file

`tests/test_phase9c_gate_f_prep.py` NEW — all P9C antibodies live here (not piled into shadow_code.py). Convention correction per critic-carol cycle-3 + P9C scout L2 finding.

### S9 — L1: settlements_v2 writer policy doc

`docs/authority/zeus_dual_track_architecture.md §4` append — document settlements_v2 as "read-only query symmetry surface; live settlement truth arrives via external Polymarket oracle + WU observations (legacy writers at `src/execution/harvester.py` etc.). Future-phase backfill tooling may populate v2 for historical replay symmetry (currently zero-row per Golden Window policy)". No P9C code for a v2 settlements writer; explicit doc of external-only.

## Out-of-scope (forward-log to post-dual-track cleanup packet)

**B2** Strict ExecutionPrice-only kelly_size migration. Polymorphic `float | ExecutionPrice` preserved. 10+ caller sites (replay.py:1300 + 6 test files) not yet upgraded. Cleanup packet, not dual-track closure.

**B4** `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` semantic re-decision. Current behavior is documented in DT#6 §B as intentional (periodic review clause). Not a dual-track blocker.

**A4 full** DT#7 leverage reduction + oracle isolation. Require monitor_refresh LOW data flow + per-city oracle structure. Post-activation enhancements.

**DT#7 full refinement** beyond the refusal gate. Covered by P9C S4 (clause 3 — refusal) which is the hardest structural gate; clauses 1+2 are leverage/isolation policy layers.

## Acceptance gates

1. **Full regression ≤ baseline**: 144 failed / 1856 passed / 93 skipped (post-P9B close 69978af). Post-P9C target: 144 failed, ≥1863 passed (+7 min from new antibodies), zero new failures.
2. **L3 CRITICAL fix verified**: direct probe — construct a DB with HIGH + LOW Platt models for same city+season; get_calibrator with temperature_metric="low" returns LOW model, not HIGH.
3. **Regression targeted unchanged-green**: P5/P6/P7A/P7B/P8/P9A/P9B antibodies.
4. **critic-dave cycle 1 PASS**.
5. **Hard constraints**: no TIGGE import, no v2 table population, no DDL, Golden Window intact.

## Hard constraints (forbidden moves)

- No TIGGE data import.
- No v2 table INSERT/UPDATE in source code.
- No SQL DDL changes.
- No change to `kelly_size` signature (B2 deferred).
- No change to `_TRUTH_AUTHORITY_MAP` values (B4 deferred).
- No monitor_refresh.py logic changes — scout confirmed already metric-aware.
- No breaking changes to existing `_forecast_rows_for` callers.

## R-letter range

P9C uses **R-BZ, R-CA, R-CB, R-CC, R-CD, R-CE** (6 new antibodies).

Plus R-BY.2 strengthening in place (same letter, extended fixture).

## P3.1 guard-removal vocab check

S1 (L3 CRITICAL fix) ADDS a guard — metric-filter in platt_models_v2 query. Not removal. No P3.1 vocab grep needed.
S4 (DT#7 wire) ADDS a rejection gate. Not removal.
Other items are additions, not guard removals.
P3.1 clean.

## critic-dave cycle 1 brief (FRESH spawn, adversarial-opening per rotation)

- First review by critic-dave. Inherits:
  - critic-beth 3-cycle learnings (`phase7_evidence/critic_beth_phase7a_learnings.md`, `_phase7b_learnings.md`)
  - critic-carol 3-cycle learnings (`phase8_evidence/critic_carol_phase8_learnings.md`, `phase9_evidence/critic_carol_phase9a_learnings.md`, `_phase9b_learnings.md`)
  - Onboarding brief: `phase9_evidence/critic_dave_onboarding_brief.md` (written by carol at her retirement)
- Opening mode: **ADVERSARIAL 15-min** per carol's cycle-2 L6 recommendation + her explicit dave note "3-streak PASS prior not evidence".
- Agent type: `general-purpose` (Write/Edit to own evidence dir enabled — methodology fix from carol cycle 3).
- Task 0 (per carol's onboarding brief): verify CRITICAL-1 resolution persists in P9C-touched code paths (force-exit sweep actuator wiring still intact after S1/S4 changes).
- Task 1: deep scrutiny on L3 CRITICAL fix — is the metric-filter truly applied at every Platt read seam? (Hierarchical fallback is 4-tier — each tier must preserve metric discrimination.)

## Forward-log → post-P9C multi-angle e2e audit

After P9C lands + critic-dave PASSes, user-triggered final verification per 2026-04-18 ruling ("最后再做一轮多角度验证"): re-dispatch the 3 e2e audits that were prematurely killed (architect structural / coverage + law / runtime trace). They'll validate dual-track ACTUALLY landed vs phase-narrative claims.

## Evidence layout

- Contract: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9c_contract.md`
- Evidence dir (shared with P9A/P9B): `phase9_evidence/`
- critic-dave cycle 1 wide review: `phase9_evidence/critic_dave_phase9c_wide_review.md`
- critic-dave cycle 1 learnings: `phase9_evidence/critic_dave_phase9c_learnings.md`
- New test file: `tests/test_phase9c_gate_f_prep.py`

---

*Authored*: team-lead (Opus, main context), 2026-04-18.
*Authority basis*: user ruling 2026-04-18 (single commit, not split) + P9C scout verification 2026-04-18 (L3 CRITICAL + A1-A4 + B1/B3 + C2 + L1/L2 findings) + critic-carol cycle-3 retirement handoff.
