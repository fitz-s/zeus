# Review 5.23 Findings (Revised 2026-05-24)

> Revised: 2026-05-24. Original: review5.23.md. Triage/status: docs/operations/review5_23_triage.md

---

## 2. P0 Findings

### P0-1 — Release gate 仍然没有证明 executable forecast truth，只证明了 readiness proxy

**Severity:** P0 release blocker
**Files:** scripts/check_live_release_gate.py, src/state/readiness_repo.py, src/data/executable_forecast_reader.py
**Money path:** source truth → forecast signal → executable edge → release authorization

`_check_forecast_executable_bundle()` 的实际证明对象只是 readiness_state SQL proxy。
它没有调用 `read_executable_forecast()`，也没有验证 source_run, source_run_coverage, ensemble_snapshots, extrema-authority classification, selected bundle, member floor, complete/partial policy。

更糟糕的是 self-test fixture 直接 SQL 插入一个 LIVE_ELIGIBLE readiness row，且没有 `expires_at`。
canonical writer `write_readiness_state()` 明确规定 `status == "LIVE_ELIGIBLE"` 时 `expires_at` 必须存在，否则 raise。

**Live-money failure scenario**
1. readiness_state 里有 LIVE_ELIGIBLE row，expires_at=NULL 或 strategy_key=NULL。
2. 实际 source_run_coverage 或 ensemble_snapshots 缺失、过期、non-contributing。
3. Release gate PASS。
4. Operator 以为 forecast leg live-ready；evaluator/runtime 实际没有 executable forecast truth。

**Required fix (minimal):**
```sql
status='LIVE_ELIGIBLE'
AND expires_at IS NOT NULL
AND expires_at > ?
AND strategy_key IS NOT NULL
```
Note: `source_run_id IS NOT NULL` NOT added — `entry_readiness_writer.py` explicitly passes
`source_run_id=None` for LIVE_ELIGIBLE rows (verified 2026-05-24).

**Required fix (best):** call `read_executable_forecast()` and verify full bundle chain.

**Required tests**
1. NULL expires_at LIVE_ELIGIBLE → FAIL. [DONE]
2. NULL strategy_key LIVE_ELIGIBLE → FAIL. [DONE]
3. Missing source_run/coverage/snapshot chain → FAIL. [OPEN]
4. Full chain through canonical writer + read_executable_forecast() → PASS. [OPEN]

---

### P0-2 — Forecast bundle selector can be short-circuited by latest scope-level readiness

**Severity:** P0/P1 source-truth blocker
**Files:** src/data/executable_forecast_reader.py, src/data/producer_readiness.py, src/state/readiness_repo.py, src/state/source_run_coverage_repo.py
**Money path:** source truth → forecast signal → executable edge

`read_executable_forecast()` hard-blocks on latest scope-level producer readiness BEFORE enumerating candidate bundles:
```python
producer = _latest_producer_readiness(...)
if producer_reason is not None:
    return BLOCKED  # 00Z valid bundle never evaluated
```

Producer readiness is scope-level (`scope_key` has no `source_run_id`). A later 12Z blocked run overwrites scope readiness → 00Z FULL_CONTRIBUTOR is never enumerated.

**Failure scenario:** 00Z FULL_CONTRIBUTOR exists; 12Z blocked/partial arrives; scope readiness = blocked; gate rejects both.

**Required fix:** evaluate readiness per candidate inside `_evaluate_candidate()`, not as pre-enumeration hard gate.

**Required test:** 00Z FULL_CONTRIBUTOR + 12Z blocked → reader returns 00Z bundle.

---

## 3. P1 Findings

### P1-1 — Production Day0 WU observation path cannot prove full local-day coverage interval

**Severity:** P1 physical source-truth blocker
**File:** src/data/observation_client.py; contrast with src/data/day0_observation_reader.py
**Money path:** observation source truth → settlement_capture / day0_nowcast

**Corrected reasoning** (midnight example was bad):

`high_so_far`/`low_so_far` are extrema over an interval. Current WU path uses `hours=23`, filters to `target_day`, sets `coverage_status="OK"` whenever rows exist. Does NOT prove:
- coverage starts at local-day start
- no internal gap
- DST 23/25-hour local-day handled
- `sample_count >= threshold`
- provider returned full target-day (not trailing rolling window)

`day0_observation_reader.py` has the correct `MAX(running_max)` aggregate logic but is NOT wired to production (docstring confirms).

**Required fix:** production `get_current_observation()` returns coverage interval proof; Day0 fails closed unless full interval covered. Wire `read_day0_observed_extrema_v2()` when `observation_instants_v2` has current rows.

---

### P1-2 — Day0 observation-lock classification compares raw float to integer settlement bins

**Severity:** P1 contract/physics mismatch
**Files:** src/engine/evaluator.py, src/contracts/settlement_semantics.py
**Money path:** observation truth → contract semantics → strategy routing

Raw `observed_high` float compared to integer settlement bin boundaries. Settlement rounding (WU half-up, HKO truncation) not applied. Example: raw 35.6°F → WU settles to 36°F → bin 36-37°F → raw comparison says NOT entered; settled comparison says entered.

**Required fix:** apply `SettlementSemantics.for_city(city).round_single(observed_high_raw)` before bin comparison. **[DONE]**

---

### P1-3 — P_raw Monte Carlo is not exactly replayable because RNG seed is implicit

**Severity:** P1 statistical/replay blocker
**Files:** src/signal/ensemble_signal.py, src/engine/evaluator.py, scripts/rebuild_calibration_pairs.py
**Money path:** forecast signal → calibration → edge → sizing → learning/replay

`p_raw_vector_from_maxes()` uses `np.random.default_rng()` with no seed. Runtime high MC count reduces variance but does not make decisions replayable. Near-threshold decisions can flip across evaluations.

**Required fix:** deterministic seed from sha256(snapshot_id, source_run_id, coverage_id, city_id, target_local_date, temperature_metric, data_version, bin_grid_hash, strategy_key, n_mc). Persist seed + p_raw_vector_hash.

---

### P1-4 — EvidenceReport exposes REGRET_ONLY_SCOPE, but PromotionReadinessValidator ignores it

**Severity:** P1 promotion/live-expansion blocker
**Files:** src/analysis/evidence_report.py, src/analysis/promotion_readiness.py
**Money path:** learning → promotion → live strategy eligibility

`EvidenceReport` sets `cohort_scope_status = "REGRET_ONLY_SCOPE"` when experiment/cohort scoped.
`PromotionReadinessValidator.assess()` ignores it — uses only `tier_current`, `ci_lower`, `breakeven`.
Warning flag exists but promotion decision ignores it.

**Required fix:** hard gate `if report.cohort_scope_status != "FULL_SCOPE": return NOT_READY`.

---

### P1-5 — Forecast PARTIAL_CONTRIBUTOR policy is internally inconsistent

**Severity:** P1/P2 source-truth semantics
**Files:** src/data/forecast_extrema_authority.py, src/data/executable_forecast_reader.py
**Money path:** forecast source truth → executable forecast

`classify_forecast_extrema_authority()` returns PARTIAL_CONTRIBUTOR for `boundary_ambiguous=True`.
But `read_executable_forecast_snapshot()` blocks `boundary_ambiguous != 0` as CAUSALITY_NOT_OK before classifier runs. Comments say partial cases can pass through — conflict.

**Required fix:** pick one policy (strict: always block; or haircut: allow with strategy profile flag).

---

### P1-6 — Release-gate self-test proves a non-canonical forecast readiness artifact

**Severity:** P1 release proof quality
**File:** scripts/check_live_release_gate.py
**Money path:** release gate → source truth

Self-test fixture directly SQL-inserts LIVE_ELIGIBLE row omitting `expires_at`, `strategy_key`, `source_run_id`, `source_id`, `dependency_json`. Canonical `write_readiness_state()` would reject LIVE_ELIGIBLE without `expires_at`.

**Required fix (minimal):** fixture must include all canonical-writer-required columns. **[PARTIALLY DONE — expires_at + strategy_key now included; full write_readiness_state() path not used]**

**Required fix (best):** use `write_readiness_state()` or `source_run_coverage → build_producer_readiness_for_scope()`.

---

### P1-7 — NegRisk basket proof hash omits the order-book depth used to compute profit

**Severity:** P1 if promoted; P2 while shadow-only
**File:** src/strategy/candidates/neg_risk_basket.py
**Money path:** market prior → deterministic edge → proof/replay

`proof_inputs_hash` includes only emitted legs, `q_star`, `fee_rate`. Omits full book levels that determined vector cost/profit. Two books with same top price but different depth produce same hash with different economics.

**Required fix:** hash canonical full book including all YES/NO levels per leg.

---

### P1-8 — NegRisk basket leg intents use best ask rather than last consumed level at q_star

**Severity:** P1 if promoted; P2 while shadow-only
**File:** src/strategy/candidates/neg_risk_basket.py
**Money path:** deterministic edge → executable vector order

`_build_yes_legs()` / `_build_no_legs()` set `price_limit = best ask`. But `q_star` can consume multiple levels. If q_star > best-ask depth, intended vector cannot execute at the emitted price_limit.

**Required fix:** `price_limit = last consumed level at q_star` (not best ask).

---

## 4. P2 Findings

### P2-1 — Correct Day0 DB extrema reader is explicitly not production-wired

`day0_observation_reader.py` correct but not wired. See P1-1.

### P2-2 — Forecast/Day0 authority headers still cite developer-local paths

`/Users/leofitz/.claude/jobs/...` paths in source headers. **[DONE — all replaced 2026-05-24]**

### P2-3 — ECMWF collector sets FORECAST_SOURCE_ROLE = "diagnostic" vs live entry

`ecmwf_open_data.py` sets `FORECAST_SOURCE_ROLE = "diagnostic"`. Valid values: `"entry_primary"`, `"monitor_fallback"`, `"diagnostic"`. No `"scheduled_collector"` role exists in the Literal type. Fix would need new role value or documented collector/reader role separation.

---

## 5. Money-Path Integrity Table

| Segment | Verdict | Reason |
|---------|---------|--------|
| contract semantics | PARTIAL | Day0 lock classifier now uses settlement rounding |
| source truth | PARTIAL/FAIL | Day0 API lacks coverage interval proof; release gate uses readiness proxy |
| forecast signal | PARTIAL | Bundle selection short-circuitable by latest scope readiness |
| calibration | PARTIAL | P_raw stochastic seed not persisted |
| market prior | PARTIAL | NegRisk vector book proof incomplete |
| executable edge | PARTIAL | Day0 and vector strategies have proof-object mismatches |
| sizing | PARTIAL | MC variance near thresholds without replay seed |
| execution | PASS/PARTIAL | No new direct side-effect blocker found |
| monitoring/release | PARTIAL/FAIL | Gate proves proxy, not full forecast object |
| settlement | PARTIAL | Rounding contract strong; observation lock now uses it |
| learning/promotion | PARTIAL/FAIL | REGRET_ONLY_SCOPE exposed but not blocked by promotion validator |

---

## 6. Repair Packet Status

| Step | Objective | Status |
|------|-----------|--------|
| 1 | Release-gate forecast proxy → executable forecast proof | PARTIAL |
| 2 | Producer readiness bundle-granular | OPEN |
| 3 | Wire Day0 observation coverage proof into production | OPEN |
| 4 | Settlement-rounded observation in lock classification | DONE |
| 5 | Deterministic MC seed + persisted P_raw provenance | OPEN |
| 6 | Promotion validator blocks incomplete cohort scope | OPEN |
| 7 | Resolve PARTIAL_CONTRIBUTOR policy | OPEN |
| 8 | Harden NegRisk vector proof and intent | OPEN |
