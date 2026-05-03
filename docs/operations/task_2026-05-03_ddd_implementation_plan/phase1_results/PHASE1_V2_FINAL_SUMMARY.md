# Phase 1 v2 — Final Summary

Created: 2026-05-03  Authority: RERUN_PLAN_v2.md

> **Paris excluded**: Paris pending workstream A resync; rerun for Paris after A completes.

## Structural Change (Operator Decision 2026-05-03)

```
OLD: fire if cov < floor - sigma_90
NEW: fire if cov < floor
sigma → diagnostic only (logged, not in trigger or floor selection)
```

---

## C1 — Floor Reselection Headline

- 46 cities processed
- 2 policy overrides: Denver, Lagos
- Safety minimum floor: 0.35

### Floors changed vs H1 σ-aware values (|Δ| ≥ 0.01)

| city | H1 floor (σ-aware) | v2 floor (p05) | Δ |
|---|---|---|---|
| Amsterdam | 0.8500 | 1.0000 | +0.15 |
| Ankara | 0.8500 | 1.0000 | +0.15 |
| Atlanta | 0.8500 | 1.0000 | +0.15 |
| Auckland | 0.8500 | 1.0000 | +0.15 |
| Austin | 0.8500 | 1.0000 | +0.15 |
| Beijing | 0.8500 | 1.0000 | +0.15 |
| Buenos Aires | 0.8500 | 1.0000 | +0.15 |
| Busan | 0.8500 | 1.0000 | +0.15 |
| Chengdu | 0.8500 | 1.0000 | +0.15 |
| Chicago | 0.8500 | 1.0000 | +0.15 |
| Chongqing | 0.8500 | 1.0000 | +0.15 |
| Dallas | 0.8500 | 1.0000 | +0.15 |
| Denver | 0.3500 | 0.8500 | +0.50 |
| Guangzhou | 0.8500 | 1.0000 | +0.15 |
| Helsinki | 0.8500 | 1.0000 | +0.15 |
| Houston | 0.8500 | 1.0000 | +0.15 |
| Jakarta | 0.3500 | 0.7143 | +0.36 |
| Jeddah | 0.8500 | 1.0000 | +0.15 |
| Karachi | 0.8500 | 1.0000 | +0.15 |
| Kuala Lumpur | 0.8500 | 1.0000 | +0.15 |
| London | 0.8500 | 1.0000 | +0.15 |
| Los Angeles | 0.8500 | 1.0000 | +0.15 |
| Lucknow | 0.3500 | 1.0000 | +0.65 |
| Madrid | 0.8500 | 1.0000 | +0.15 |
| Manila | 0.8500 | 1.0000 | +0.15 |
| Mexico City | 0.8500 | 1.0000 | +0.15 |
| Miami | 0.8500 | 1.0000 | +0.15 |
| Milan | 0.8500 | 1.0000 | +0.15 |
| Munich | 0.8500 | 1.0000 | +0.15 |
| NYC | 0.8500 | 1.0000 | +0.15 |
| San Francisco | 0.8500 | 1.0000 | +0.15 |
| Sao Paulo | 0.8500 | 1.0000 | +0.15 |
| Seattle | 0.8500 | 1.0000 | +0.15 |
| Seoul | 0.8500 | 1.0000 | +0.15 |
| Shanghai | 0.8500 | 1.0000 | +0.15 |
| Shenzhen | 0.5500 | 0.7143 | +0.16 |
| Singapore | 0.8500 | 1.0000 | +0.15 |
| Taipei | 0.8500 | 1.0000 | +0.15 |
| Tokyo | 0.8500 | 1.0000 | +0.15 |
| Toronto | 0.8500 | 1.0000 | +0.15 |
| Warsaw | 0.8500 | 1.0000 | +0.15 |
| Wellington | 0.8500 | 1.0000 | +0.15 |
| Wuhan | 0.8500 | 1.0000 | +0.15 |

---

## C2 — Metric-Specific Binning Headline

### HIGH bins

| bin | N | mean_error |
|---|---|---|
| exact 0 | 4,796 | 0.7653 |
| (0, 0.05) | 3 | 0.9526 |
| [0.05, 0.10) | 0 | n/a |
| [0.10, 0.20) | 106 | 0.7590 |
| [0.20, 0.30) | 44 | 0.7459 |
| [0.30, 0.50) | 32 | 0.7677 |
| [0.50, 1.0] | 11 | 0.8599 |

### LOW bins

| bin | N | mean_error |
|---|---|---|
| exact 0 | 2,259 | 0.7051 |
| (0, 0.05) | 3 | 0.6674 |
| [0.05, 0.10) | 0 | n/a |
| [0.10, 0.20) | 56 | 0.7648 |
| [0.20, 0.30) | 19 | 0.7445 |
| [0.30, 0.50) | 15 | 0.7069 |
| [0.50, 1.0] | 9 | 0.9070 |

---

## C3 — Bootstrap CIs Headline

Bootstrap: 1000 iterations, resampling unit = decision_group_id

- **HIGH**: 2 adjacent pairs statistically indistinguishable
- **LOW**: 2 adjacent pairs statistically indistinguishable

---

## C4 — Small Sample Floor Headline

- 96/100 (city, metric) pairs: N* identified
- When N < N*: DDD multiplier forced to curve_max (0.91× Kelly)

---

## C5 — Peak Window Radius Headline

- 347 (city, metric, season) entries need expanded radius (miss > 5% at ±3)

---

## Acceptance Gate Status

| Gate | Status | Evidence |
|---|---|---|
| C1: floors use p05 not σ-aware | CLOSED | 46 cities |
| C2: metric-specific cov | CLOSED | HIGH + LOW separate |
| C3: bootstrap on decision_group_id | CLOSED | 1000 iters |
| C4: small_sample_floor | CLOSED | N* found 96/100 |
| C5: peak_window radius | CLOSED | 347 expansions needed |
| Paris | OPEN | Pending workstream A |

---

## Remaining Open Items

1. **Paris**: re-run C1-C5 after workstream A completes.
2. **No-train-data cities** (HK, Istanbul, Moscow, Tel Aviv): fail-CLOSED when DDD wired live.
3. **H7** (ACF lag mismatch): σ diagnostic-only now; low priority.
4. **H4** (load_platt_model_v2 frozen filter): forward-fix in v2 live wiring.

---

## Next Actions

1. Wire C1 floors into DDD live trigger (`cov < final_floor`, no σ).
2. Wire C4 small_sample_floor: gate discount when N < N*.
3. Apply C5 radius expansions for flagged entries.
4. Add σ to diagnostic dashboard (log but don't use in trigger).
5. Re-run after Paris workstream A completes.
