# DDD Phase 1 Rerun Plan — v2

Created: 2026-05-03
Supersedes: `RERUN_PLAN.md` (v1 framed E8 as "Tokyo bulk-regen leakage". That framing was wrong — see §1 below.)
Authority: review.md (v1) + review2.md + E8 audit pack (`phase1_results/E8_audit/01-08`)
Status: **Live activation = SHADOW_ONLY until P0 + P1 close**

## §1 — What v1 got wrong

v1 treated E8 as "system-wide bulk-regeneration leakage" and proposed a `recorded_at < 2026-04-28` time-window filter. Three audit cycles (E8.5 pipeline causality, E8.6 intrinsic time integrity, E8.7 reload idempotency) revealed:

1. **Pipeline causality clean** (`05_pipeline_causality.md`):
   `rebuild_calibration_pairs_v2.py` keys outcome strictly to `target_date`; `refit_platt_v2.py` pulls all `training_allowed=1 AND authority='VERIFIED'` pairs (production design). No post-target_date contamination.

2. **Intrinsic time fields preserved** (`06_intrinsic_time_field_integrity.md`):
   `forecast_available_at` variety still matches `lead_days` variety per `(city, target_date)` (median 8.0 each). Outcome ↔ observation extremum 5/5 PASS.

3. **Reload idempotent** (`07_reload_idempotency_evidence.md`):
   `provenance_json` carries per-row `payload_hash` (sha256). 22GB pre-reload backup exists at `~/.Trash/zeus-world.db.pre-hk-paris-release-2026-05-02`. Raw vendor archives in `./raw/oracle_shadow_snapshots/`.

**Conclusion**: import / fit / recorded timestamps are pure metadata. They tell us "the last regen ran on 2026-04-28→05-02". They do not enter any DDD/Platt formula. Math is independent of these timestamps.

The E8 alarm was a SYMPTOM ("timestamps collapsed") mistaken for a DISEASE ("data corrupted"). Provenance loss is real but cosmetic.

## §2 — What is actually broken (review2 cross-checked)

review2.md raised 13 items. Verified each:

### Confirmed real, requires Phase 1 rerun

| # | Finding | Evidence | Phase | Priority |
|---|---|---|---|---|
| **H1** | Observed-row denominator: zero-coverage days vanish | `p2_4_curve_breakpoints.py:99,168-170` direct read | P0 | **CRITICAL** |
| **H2** | HIGH-window coverage used for LOW errors in §2.4 | `p2_4_curve_breakpoints.py:145,189` direct read | P0 | **CRITICAL** |
| **H6** | Row-N inflated by `(lead_days × bins)` repetition; not independent | §2.4 reports N=7,371 in zero-bin which is `(city,day,metric,lead) ≈ 47×120×2×8`; independent (city,day) ≈ 5,640 | P1 | HIGH |
| **H5** | §2.5 (small_sample_floor) and §2.6 (peak_window radius) never executed | Original plan acknowledged; pending since 2026-05-03 | P1 | HIGH |
| **H7** | ACF computed only to lag 14 but σ_window chosen at 90 days | `p2_3_sigma_window_acf.py` direct read | P2 | LOW (doc-fix may suffice) |

### Real but architectural / outside Phase 1 scope

| # | Finding | Where it belongs |
|---|---|---|
| **H4** | `load_platt_model_v2` (`store.py:628`) has no `recorded_at <= frozen` filter; future mass-refit goes immediately live | v2 forward-fix (§5) |
| **H3** | DDD currently NOT wired into live; when wired, null floor (HK/Istanbul/Moscow/Tel Aviv) must fail-CLOSED, not inherit `oracle_penalty.py`'s silent-allow precedent | v2 forward-fix (§5) |
| **H8** | Paris LFPB vs LFPG station drift claim | Separate source-routing audit, not DDD Phase 1 |

### Rejected (review2 overreach / strawman)

| # | review2 claim | Why rejected |
|---|---|---|
| R1 | "k=0 cannot mean 'no sample-size penalty needed'" | Strawman. Operator's k=0 ruling explicitly preserved `(1+k/√N)` skeleton + §2.5 small_sample_floor as small-N defense |
| R2 | "DDD must be gate-first, discount-second (rearchitect)" | Misreads the layered design: catastrophic kill at `<0.35` IS the gate; 2-9% curve IS the discount above the gate |
| R3 | "9% cap meaningless under Kelly" | Same as R2 — kill threshold already does size→0 work for catastrophic days |
| R4 | "Lagos 0.45 = boiled-frog" | Policy disagreement. Operator's Ruling B engaged this trade-off explicitly |
| R5 | "Denver/Paris 0.85 = policy not statistical" | Operator's Ruling A already labels this as asymmetric-loss policy override; review2 just wants the label visible in deliverables (✓ — will add `floor_source: policy/empirical` field in v2 outputs) |

## §3 — H1 results revealed an algorithm-level inverted incentive

H1 rerun ran (`p2_rerun_v2_h1_fix.{py,json,md}`). Key findings:

### 3.1 Floor movers (3 cities ≥ 0.05)

| City | orig_floor | new_floor (algo) | mechanism |
|---|---|---|---|
| Denver | 0.85 (Ruling A) | 0.35 | 4 zero train days revealed → σ 0.064→0.158 (×2.5) → algorithm absorbs as noise → lowest fallback |
| Paris | 0.85 (Ruling A) | 0.60 | algorithm unchanged (0 zero train days); the "delta" was just exposing pre-existing Ruling A override |
| Lucknow | 0.50 (σ-aware) | 0.35 | 1 zero train day → σ 0.085→0.111 → algorithm picks lower fallback |

### 3.2 Lagos: σ-band model is the wrong frame

Lagos train σ barely moved (0.178 → 0.189; only 1 zero train day). **But Lagos test window has 23/120 (19%) zero-coverage days**. Train σ failed to capture this because outage events are intermittent and didn't happen to fall in train.

Implication: Ruling B's "σ-band absorbs Lagos infrastructure noise" was framed wrong. Lagos's actual problem is **outage-frequency**, not high variance within positive coverage. σ-band absorption fundamentally cannot model this — needs a separate outage-rate term.

### 3.3 The structural bug (operator-confirmed 2026-05-03)

Original algorithm: `fire if cov < floor - σ`, FP constraint ≤ 1%.

**This has an inverted incentive**: σ ↑ (worse infrastructure / more outages) → algorithm forced to lower floor to maintain FP rate → DDD fires less → city with bad infra gets the LEAST DDD protection. This is exactly the LIVE_MONEY_RISK that review2 §3.3 flagged. Denver's H1 result (4 zero days → algorithm says 0.35) is the empirical demonstration.

### 3.4 Decision (operator 2026-05-03): structural fix

Replace the σ-band trigger with a clean floor-only trigger:

```
OLD:  fire if cov < floor - σ_90
NEW:  fire if cov < floor
```

σ becomes diagnostic (logged, monitored), not part of the trigger. The floor itself encodes "what's the lowest coverage we tolerate as noise vs. risk" — that's the policy/empirical question, not "how much σ-band absorption is allowed".

Consequence for floor selection:
- Floor = max(p05_of_historical_cov, per-city safety floor from policy)
- σ does NOT enter the floor recommendation either — only the cov histogram does
- Outage frequency (count of zero-cov days / total days) becomes a separate first-class metric, not absorbed into σ

Ruling A's intuition (asymmetric loss → don't let σ absorb outages) is now algorithm-level, not case-by-case override.

Ruling B (Lagos 0.45) needs separate re-examination: the floor itself is the question now, not σ-absorption. Likely keeps 0.45 if the rationale shifts to "p10 of Lagos train cov is 0.45 and that's the policy minimum we accept", but explicitly NO σ-band magic.

### 3.5 Re-run required after structural fix

Once algorithm changes, re-run §2.1 (floor recommendation), §2.4 (curve binning) without σ-band. P0/H1 results above already give the inputs (cov histograms with zero-days). The new algorithm just needs to be applied.

## §4 — Execution phases

### P0 — H1 expected-slot fix (in-flight)

Dispatched: sonnet executor (`p2_rerun_v2_h1_fix.py`) — running in background.

Scope: HIGH metric only. Re-derive every (city, date) coverage with `cov=0` for zero-row days; recompute train/test stats, σ_90 distribution, §2.4 shortfall×error binning.

Deliverables: `phase1/p2_rerun_v2_h1_fix.py`, `phase1_results/p2_rerun_v2_h1_fix.{json,md}`.

Decision points after results land:
- D-P0a: Do any cities' floors move ≥ 0.05? If yes, update `p2_1_FINAL_per_city_floors.json` (write to `_v2` namespace, do NOT overwrite v1).
- D-P0b: Does Lagos σ_90 move enough to invalidate Ruling B's empirical basis? Report to operator for re-ruling.
- D-P0c: Does §2.4 binning still show monotone direction with corrected denominator?

### P0 — H2 metric-specific coverage (after P0/H1 lands)

Trigger: P0/H1 deltas confirmed.

Scope:
1. Define LOW-window per city. Two paths:
   - (a) If `cities.json` has `historical_low_hour`, use that ± 3 (mirror of HIGH).
   - (b) Otherwise derive empirically: per (city, season), the local hour at which `running_min` is most often achieved on `observation_instants_v2`. (This is mini-§2.6.)
2. Re-run §2.4 binning with metric-specific shortfall: HIGH error binned by HIGH-shortfall, LOW error binned by LOW-shortfall.
3. Compare: did the original §2.4 result (mean error 0.74→0.81 across bins) survive the metric split, or was it driven by HIGH-only and LOW had a different / no signal?

Deliverable: `phase1/p2_rerun_v2_h2_fix.py` + `phase1_results/p2_rerun_v2_h2_fix.{json,md}`.

### P1 — H6 independent N + H5 §2.5/§2.6

P1.a — H6 decision-group bootstrap on §2.4:
- Use `decision_group_id` (already in `calibration_pairs_v2`) to identify independent decisions
- Bootstrap mean error per shortfall bin with decision_group as resampling unit
- Report 95% CI per bin
- Likely outcome: confidence intervals will widen substantially; some bins fall below significance threshold

P1.b — H5 §2.5 small_sample_floor:
- Define a minimum independent N per (city, metric) below which DDD is forced to a conservative posture (e.g. `discount = curve_max` until N reached, or trading gated entirely)
- Empirically calibrate the threshold: at what N does winning-bucket Brier ECE stabilize?

P1.c — H5 §2.6 peak_window radius:
- Per (city, metric, season), how often does the actual extreme hour fall outside `historical_peak_hour ± 3`?
- If miss rate > 5%, expand radius or switch to `historical_peak_hour ± 4`
- Cross-check DST edge cases and cross-midnight LOW

### P2 — H7 ACF extension + doc-only fixes

P2.a — Extend `p2_3_sigma_window_acf.py` to lag 90+ on a probe city. Either show meaningful long-memory at lag 60-90 (empirical justification for 90-day window) or document σ_window=90 as a policy choice, not an empirical conclusion.

P2.b — Add `floor_source` field to `p2_1_FINAL_per_city_floors_v2.json`: per city, label as `empirical` / `asymmetric_loss_policy_override` / `infrastructure_reality_policy` / `not_applicable_no_wu`. Reflects review2 §5.5/5.9 valid criticism on transparency.

## §5 — v2 forward-fix (separate workstream, gates Phase 2 live activation)

These are NOT Phase 1 reruns. They are structural fixes required before any DDD live activation, regardless of how Phase 1 closes out.

### F1 — Platt loader snapshot freeze (H4)

Current `load_platt_model_v2` (`src/calibration/store.py:628`) selects via `is_active=1 AND authority='VERIFIED' ORDER BY fitted_at DESC LIMIT 1`. No `recorded_at <= frozen_as_of` filter, no `model_key` pinning.

Required: add a snapshot-pin mechanism so that future mass-refits do not silently take over live serving. Options:
- (a) Config-pinned `model_key` per (city, metric, cluster, season) — explicit pin
- (b) Per-cycle `frozen_as_of` parameter on the loader, passed from the live evaluator
- (c) Both: `model_key` overrides; `frozen_as_of` as default

Recommended: (c). Add `frozen_as_of` parameter to loader signature, default to a config-pinned value that operator updates explicitly when blessing a new calibrator generation.

### F2 — DDD null-floor fail-closed (H3)

When DDD is wired into live, the lookup must fail-CLOSED for cities with `floor: null` (Hong Kong, Istanbul, Moscow, Tel Aviv — no `wu_icao_history` primary feed):
- (a) Either skip DDD entirely AND ensure these cities are gated by a separate source-tier readiness check (HKO native / NOAA route / Ogimet)
- (b) OR set `discount = curve_max` (treat as full data-density discount) until the city has a completed source-tier DDD

Do NOT inherit `oracle_penalty.py`'s "unknown pair → 1.0" silent-allow pattern.

## §6 — Acceptance gates

Phase 1 v2 closes when:

- ☑ D-A — Denver asymmetric-loss override removed from floors; algorithm output 0.8786 stands (CLOSED 2026-05-03)
- ☑ D-C — Continuous linear curve replaces 5-segment table (CLOSED 2026-05-03)
- ☑ D-D — σ removed from trigger; diagnostic-only (CLOSED 2026-05-03)
- ☑ Q1 — Two-Rail trigger design implemented in `src/oracle/data_density_discount.py` (CLOSED 2026-05-03)
- ☑ Q2 — Hardened floor method: `max(p05, 0.35)` with `floor_source` field (CLOSED 2026-05-03)
- ☑ Q3 — Continuous linear curve implemented: `D = min(0.09, 0.20 × shortfall)` (CLOSED 2026-05-03)
- ☑ Q4 — σ diagnostic-only; not in trigger or floor selection (CLOSED 2026-05-03)
- ☑ Q5 — Two-component mixture: Rail 1 catches State 0, Rail 2 catches State 1 (CLOSED 2026-05-03)
- ☐ P0/H1 — zero-day fix run, deltas reported, floors updated to `_v2` namespace
- ☐ P0/H2 — LOW-window defined and §2.4 split-metric rerun complete
- ☐ P1/H6 — decision-group bootstrap CIs reported per §2.4 bin
- ☐ P1/H5 — §2.5 small_sample_floor + §2.6 peak_window_radius executed
- ☐ P2/H7 — ACF extended OR σ_window=90 explicitly labeled as policy
- ☑ P2 transparency — `floor_source` field added to v2 floors JSON (CLOSED 2026-05-03)
- ☐ Ruling B empirical re-justification post-H1 (operator decision)

Phase 2 (live activation) gated additionally on:
- ☐ Comprehensive backtest validation (operator deferred — will run separately)
- ☐ F1 — Platt loader snapshot freeze landed and tested
- ☐ F2 — DDD null-floor fail-closed design + tests (partial: fail-closed in module; wiring deferred)

Still open / deferred:
- Paris floor: pending workstream A DB resync
- F1/F2: forward structural fixes, separate workstreams
- D-E: live wiring into `src/engine/evaluator.py` (separate workstream, operator owns)
- Kelly multiplier layer: hand-off at `docs/reference/zeus_kelly_asymmetric_loss_handoff.md`

## §7 — Anti-pattern register (carry-forward from v1)

These are about the rerun process itself; carry over from v1 unchanged:

1. Never overwrite v1 result files; always write to `_v2` namespace
2. Never modify the production DB
3. Never modify existing Phase 1 scripts; write new files
4. Use `.venv/bin/python`, not system python
5. Each rerun produces both `.json` (full data) and `.md` (human-readable digest)
6. Each script's first line records: created date, authority basis, last reused/audited
7. Bootstrap CIs use `decision_group_id` as resampling unit, not row-level

## §8 — Files of record

- v1 audit pack: `phase1_results/E8_audit/01-08*.md`
- v1 synthesis (now superseded): `phase1_results/E8_audit/E8_AUDIT_SYNTHESIS.md`
- v1 rerun plan: `RERUN_PLAN.md` (kept for historical context only)
- v2 rerun plan: `RERUN_PLAN_v2.md` (this file — supersedes v1)
- review.md (v1 adversarial): tribunal that triggered E8 audit
- review2.md (v2 adversarial): correct hit-list source
- canonical reference: `docs/reference/zeus_oracle_density_discount_reference.md`
- v1 floors: `phase1_results/p2_1_FINAL_per_city_floors.json` (DO NOT OVERWRITE)
- v2 floors (when written): `phase1_results/p2_1_FINAL_per_city_floors_v2.json`
