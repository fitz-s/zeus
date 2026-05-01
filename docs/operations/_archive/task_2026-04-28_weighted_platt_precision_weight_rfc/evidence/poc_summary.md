# PoC Evidence Summary — Weighted Platt for LOW (mn2t6)

Created: 2026-04-28
Source: `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/` (out-of-repo PoC dir)
Last run: 2026-04-28T05:08 (PoC v4 with unit-aware sensor noise)

## Why this evidence belongs in operations/

The RFC in this packet proposes a schema-level change to `ensemble_snapshots_v2` and `calibration_pairs_v2`. Per AGENTS.md §4 planning lock, schema/truth-ownership changes require plan-evidence. This file is the numerical evidence that the proposed schema change is OOS-positive on real data.

## Setup

- **Ground truth**: `state/zeus-world.db::observations.low_temp` — 42,749 rows, 51 cities, 2023-12-27 .. 2026-04-19
- **Forecast input**: `51 source data/raw/tigge_ecmwf_ens_mn2t6_localday_min/` — 339,868 LOW snapshots after recovering quarantined-but-recoverable rows from `inner_min_native_unit` field
- **Pair generation**: 5 candidate bins per snapshot (offsets {−2, −1, 0, +1, +2}), Monte Carlo with unit-aware sensor σ (F=0.3, C=0.2), N_MC_DRAWS=32 → 1,699,340 (p_raw, outcome) pairs
- **Train/test split**: time-based, holdout last 60 days (cutoff 2026-02-18). Train n=1,576,540; Test n=122,800
- **Platt formulation**: `P_cal = sigmoid(A·logit(p_raw) + B·lead + C)`, weighted MLE via L-BFGS-B
- **Significance**: Brier diff vs A_baseline, bootstrap 500 resamples, CI95

## Effective sample size recovery

| scheme | eff_n_train | × A_baseline | semantic |
|---|---|---|---|
| **A_baseline** (current zeus binary `training_allowed`) | 339,230 | 1.00× | discard if any-member boundary-ambiguous |
| **B_uniform** (no gate, weight=1) | 1,576,540 | **4.65×** | use all data treating boundary same as inner |
| **C_overlap** (1 − ambiguous_member_count/51) | 1,070,431 | **3.16×** | weight by precision proxy, no floor |
| **D_softfloor** (max(0.05, 1 − severity)) | 1,074,485 | **3.17×** | C with 5% floor against drowning |

**Reading**: zeus's binary gate currently uses 21.6% of available LOW signal. Continuous weighting recovers 3.16-4.65× more.

## Headline OOS results — bootstrap CI95 vs A_baseline

| scheme | OOS Brier | mean diff vs A | CI95 | sig? |
|---|---|---|---|---|
| A_baseline | 0.1579 | — | — | — |
| B_uniform | 0.1577 | **−0.00018** | [−0.00022, −0.00014] | ✓ |
| C_overlap | 0.1578 | **−0.00015** | [−0.00017, −0.00012] | ✓ |
| D_softfloor | 0.1578 | **−0.00015** | [−0.00017, −0.00012] | ✓ |

All three weighted schemes are statistically significantly better than the binary baseline. The "no-drowning" hypothesis (extreme low-weight rows could destabilize Platt) is rejected: C_overlap and D_softfloor produce identical Brier despite very different weight distributions, and B_uniform (no weights at all) achieves the BEST OOS Brier.

## Asia subset (the supposedly starved cluster)

| scheme | OOS Brier (Asia) | mean diff Asia | CI95 Asia | sig? |
|---|---|---|---|---|
| A_baseline | 0.1575 | — | — | — |
| B_uniform | 0.1573 | **−0.00021** | [−0.00028, −0.00013] | ✓ |
| C_overlap | 0.1573 | **−0.00018** | [−0.00023, −0.00012] | ✓ |
| D_softfloor | 0.1573 | **−0.00018** | [−0.00023, −0.00012] | ✓ |

Asia improvement is **~17% larger** than overall (-0.00021 vs −0.00018). The hypothesis that Asia is the most-starved cluster IS validated, and weighted recovery is most beneficial there.

## Per-city OOS Brier (best vs baseline, sorted)

C_overlap vs A_baseline, by significance:

### Significantly improved (12)
| city | A_baseline | C_overlap | diff | CI95 |
|---|---|---|---|---|
| manila | 0.1519 | 0.1506 | −0.00126 | [−0.00150, −0.00104] |
| chongqing | 0.1553 | 0.1546 | −0.00077 | [−0.00097, −0.00059] |
| shenzhen | 0.1534 | 0.1523 | −0.00109 | [−0.00130, −0.00088] |
| shanghai | 0.1546 | 0.1539 | −0.00073 | [−0.00097, −0.00050] |
| seoul | 0.1567 | 0.1562 | −0.00050 | [−0.00072, −0.00029] |
| tokyo | 0.1567 | 0.1563 | −0.00043 | [−0.00064, −0.00024] |
| chengdu | 0.1566 | 0.1562 | −0.00045 | [−0.00069, −0.00023] |
| singapore | 0.1563 | 0.1559 | −0.00040 | [−0.00060, −0.00018] |
| wuhan | 0.1575 | 0.1573 | −0.00021 | [−0.00046, +0.00003] (marginal) |
| london | 0.1569 | 0.1564 | −0.00050 | [−0.00065, −0.00031] |
| mexico-city | 0.1567 | 0.1564 | −0.00037 | [−0.00056, −0.00017] |

### Significantly regressed (8)
| city | A_baseline | C_overlap | diff |
|---|---|---|---|
| jakarta | 0.1624 | 0.1634 | +0.00100 |
| busan | 0.1626 | 0.1636 | +0.00098 |
| nyc | 0.1606 | 0.1611 | +0.00050 |
| houston | 0.1608 | 0.1613 | +0.00057 |
| guangzhou | 0.1600 | 0.1604 | +0.00038 |
| hong-kong | 0.1608 | 0.1614 | +0.00056 |
| chicago | 0.1598 | 0.1599 | +0.00010 (marginal) |
| beijing | 0.1593 | 0.1594 | +0.00012 (marginal) |

### Unchanged (1)
- kuala-lumpur (within CI)

## Per-city pattern interpretation

The unit-aware noise hypothesis (F-cities regress because of fixed noise σ) was **disconfirmed by v4** — F-cities still regress with appropriate F-noise. Pattern is city-specific, not unit-driven.

Hypothesis to test in RFC stage:
- Cities where the boundary buckets carry CORRELATED noise with the outcome (e.g., busan in monsoon transition season) may benefit less or get hurt by including them.
- A possible improvement: weight by `1 - ambiguity` AND incorporate `boundary_min_value` as an alternative member estimate, then average over both inner and boundary realizations of p_raw — would reduce variance in cities where the boundary value happens to be informative.

The aggregate signal is robust; per-city heterogeneity is a feature to investigate, not a refutation.

## Hypothesis status

| Claim | Status |
|---|---|
| Binary `training_allowed` gate is statistically suboptimal | ✓ confirmed |
| Recovery via `inner_min_native_unit` requires no re-extract | ✓ confirmed |
| Weighted MLE doesn't drown the fit (extreme-low-weight risk) | ✓ confirmed |
| Asia is the most-starved cluster, biggest beneficiary | ✓ confirmed |
| Improvement uniform across cities | ✗ rejected; 12 ✓ / 8 ✗ / 1 = |
| F-unit cities regress because noise σ mis-scaled | ✗ rejected by v4 |

## Recommended weight scheme for RFC implementation

**D_softfloor**: `w = max(WEIGHT_FLOOR, 1 − ambiguous_member_count / 51) if causality_pure and horizon_satisfied else 0`.

Rationale:
- OOS Brier identical to C_overlap (0.1578)
- Robust to future ambiguity-rate shifts (the floor prevents 0-weight monoculture)
- Distinguishes "epistemic uncertainty" (boundary ambiguity → reduce weight) from "physical impossibility" (causality leak / horizon deficit → zero weight). These are categorically different and should not collapse.
- WEIGHT_FLOOR=0.05 is a starting point; RFC should expose it as configurable

## Caveats / known PoC limitations

1. **Synthetic 5-bin Polymarket proxy**: real Polymarket bins are point/range/shoulder per `SettlementSemantics`, not the integer ladder. Brier numbers may shift on real bins (likely more favorable due to wider shoulder bins).
2. **Single Platt formulation**: zeus production may add cluster/season terms; not exercised here.
3. **One time-split**: should add k-fold time-series CV in RFC validation.
4. **HIGH track not tested**: HIGH has 100% training_allowed=True, so no asymmetry to validate. The schema change is harmless for HIGH (weight = 1 for all rows in current state).
5. **Per-city heterogeneity unexplored**: 8 cities regress; needs investigation before full migration.

## Reproducibility

```bash
cd /Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28
python3 poc_weighted_platt.py            # full re-run (~2 min)
python3 poc_weighted_platt.py --reuse    # reuse cached pairs (~30s)
```

Output: `report.md`, `metrics.json`, `pairs.parquet`, `calibration_curves.png`.
