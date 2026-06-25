# q_lcb Suppression Root Cause — 2026-06-22

**Status**: DIAGNOSIS COMPLETE — no live code changed  
**Authority**: docs/authority/regime_unification_2026-06-12.md  
**Mission**: Surface the suppression mechanism killing mid-price NO trades despite real +0.166 edge  

---

## Evidence baseline

Team-lead confirmed live mid-bucket alpha:
- `buy_no` cost 0.50–0.70 bucket: `q_lcb = 0.795`, fill price = 0.634, realized win-rate = 0.80 → **+0.166 real edge**  
- Yet today's gate rejects ALL mid-price NO candidates with `edge_lcb ≈ 0`

---

## Three Suppression Mechanisms

### Mechanism 1 — OOF Guard deflates q_lcb BELOW cost (primary suppressor)

**Files**: `src/decision/qlcb_reliability_guard.py`, `src/decision/family_decision_engine.py:1005–1122`

**Pipeline**:
1. Band produces `q_lcb_band_no` (5th-pct NO probability) — may be positive edge
2. `_apply_oof_guard` looks up cell `(metric|lead_bucket|NO|bin_position|qb_idx)`
3. Returns `q_safe = min(q_lcb_band, L_g)` where `L_g = Wilson-95 lower bound`
4. `edge_lcb = q_safe - cost`

**Runtime numbers for NO:bab7626f** (cost=0.6712, pt_ev=+0.129):
- `q_point ≈ 0.800` (strong above cost)
- OOF cell resolves — L_g **BELOW cost**: `L_g ≈ 0.668 < 0.671`
- `q_safe = min(q_lcb_band, 0.668) = 0.668`
- `edge_lcb = 0.668 - 0.6712 = -0.003`  ← log confirmed (`e=-0.00310`)
- Candidate blocked at `edge_survivors` filter (`edge_lcb > 0.0` fails)

**Why L_g < cost for mid-price NOs?**

OOF cells for NO in q_lcb bucket [0.60–0.70) with thin-moderate n produce L_g well below bucket floor:

```
Cell key                           hit_rate   n    L_g (Wilson-95)
high|L1|NO|modal|qb10              0.692      78   0.600   ← L_g < 0.67 = fail zone
high|L1|NO|modal|qb12              0.695     177   0.635   ← fail zone
high|L2_3|NO|modal|qb11            0.727     139   0.660   ← fail zone
high|L2_3|NO|nonmodal|qb9          0.778      54   0.671   ← borderline
low|L2_3|NO|nonmodal|qb12          0.732     149   0.667   ← fail zone for cost > 0.667
low|L4P|NO|modal|qb13              0.543      35   0.406   ← severe fail
low|L2_3|NO|modal|qb14             0.429      14   0.244   ← catastrophic (n<N_MIN=30 → ABSTAIN)
```

The OOF artifact was built on `2026-06-18` with training data that predates the current sigma + Option C regime. The filled trades that proved +0.166 edge were in a **different sigma regime** than what current candidates are being graded against.

**The artifact cells for qb9-qb12 (q_lcb 0.45–0.65) show L_g systematically below the typical NO cost range (0.60–0.70).** This means the OOF guard is calibrated on old data where those q_lcb bands were weaker — it is now mis-deflating the stronger q_lcb bands that the new precision fusion produces.

---

### Mechanism 2 — Thin OOF cell ABSTAIN (secondary suppressor)

**File**: `src/decision/qlcb_reliability_guard.py:399–412`  
**Threshold**: `N_MIN = 30`

Any OOF cell with `n < 30` → `abstain=True` → `q_safe = 0` → `edge_lcb = -cost` → hard rejection.

**Examples of thin cells that block mid-price NOs**:
- `low|L2_3|NO|modal|qb13`: n=8 → ABSTAIN
- `low|L1|NO|modal|qb14`: n=9 → ABSTAIN  
- `low|L2_3|NO|modal|qb14`: n=14 → ABSTAIN
- `low|L2_3|NO|nonmodal|qb9`: n=34 (barely above N_MIN=30), L_g=0.447 → severe deflation

These thin cells block NOs in off-season / low-lead / modal-bin markets regardless of band quality.

---

### Mechanism 3 — delta_u_at_min = 0.0 blocks candidates with positive edge

**Files**: `src/decision/payoff_vector.py:633,704-706,828-861`, `src/contracts/executable_cost_curve.py:293-297`

**Runtime numbers for NO:979ff343** (cost=0.6251, edge_lcb=+0.076, optimal_dU=+0.056):
```
SELECT_GATE_DIAG: edge=1 live=0
tops=[NO:979ff3 dlok=1 adm=1 coh=1 exec=1 e=+0.0762 dU=+0.05607 dUmin=+0.00000]
```

**Edge passed** (`edge_lcb=+0.076 > 0`) but `live_candidate_passes` fails on `delta_u_at_min > 0.0`.

**Root cause chain** (`payoff_vector.py:633` → `executable_cost_curve.py:293`):

1. `_feasible_stake_bounds` computes `lo = fee_model.all_in_price(levels[0].price) × min_order_size`  
   (line 633) — this assumes `levels[0]` has ≥ `min_order_size` shares of depth

2. If `levels[0].size < min_order_size` (e.g., 3 shares available at best ask, min_order=5):  
   - `_walk_for_stake(lo)` buys all 3 shares from level 0, continues to level 1  
   - At higher level-1 price, remaining USD may fill only 1–2 more shares  
   - Final `shares_filled = 3 + 1 = 4 < 5 - 1e-9` → **ValueError**

3. `robust_at(lo)` in `_PreparedSizing.robust_at` (line 521):  
   - `matrix.payoff(candidate, y, lo)` raises ValueError for ALL outcomes y (the ValueError bubbles from `_all_in_cost` which calls `_walk_for_stake`)  
   - All `ruin[j] = True` for all j  
   - `bad = True` for all 4000 draws  
   - `du = -inf` for all draws  
   - `quantile(-inf…, 0.05) = -inf`

4. `optimize_vector_stake` line 705-706:
   ```python
   delta_u_at_min = _ru(lo)       # = -inf
   if not _math.isfinite(delta_u_at_min):
       delta_u_at_min = 0.0       # ← clamped to exactly 0.0
   ```

5. `live_candidate_passes` (line 829): `economics.delta_u_at_min > 0.0` → `0.0 > 0.0` = **False** → rejected

**Despite**: `edge_lcb = +0.076`, `optimal_delta_u = +0.056` — real edge exists but is blocked by the lo-stake ValueError that fires because best-ask depth is shallow.

**The fix**: `_feasible_stake_bounds` must account for actual depth at level 0. If `levels[0].size < min_order_size`, the true lo needs to walk across levels to find the actual minimum viable stake.

---

## σ Width Context

The team-lead asked: is the served σ (sigma-floor + Option C representativeness) inflating the bootstrap spread → lowering q_lcb_band?

**Answer: YES, but it's secondary to the OOF guard mis-calibration.**

For NO:bab7626f (cost=0.6712, pt_ev=+0.129): `q_point ≈ 0.800` — the band's center is strongly above cost. The 5th-pct NO from the band (`q_lcb_band_no`) should be well above 0.671 if σ is reasonable. The actual log shows `e=+0.129` (pt_ev before cost) and `edge_lcb = -0.003` — meaning `q_safe = 0.668 < q_lcb_band`. The suppression is the OOF guard deflating `q_safe` below `q_lcb_band`, NOT the band σ being too wide.

However, wide σ *does* affect OOF cell assignment: if σ is large, band's 5th-pct NO falls lower (e.g., into qb10 or qb9 instead of qb13), landing in cells with L_g < cost. This is an indirect σ suppression path.

The filled trades that proved +0.166 edge were likely graded in a tighter σ regime where the band's 5th-pct NO stayed in qb13-qb14 ([0.65–0.75) cells with L_g ≥ 0.70). Current σ-widening pushes some candidates into qb9-qb12 cells where L_g is systematically below cost.

---

## Proposed Fixes

### Fix 1 (highest impact): Rebuild OOF artifact on current regime data

The OOF artifact `state/qlcb_oof_reliability.json` was built `2026-06-18` against pre-Option-C corpus. Cells in the 0.55–0.70 q_lcb range now reflect old data. Rebuild on current `zeus-forecasts.db` corpus (strictly-prior rolling-origin, same as build process) with current mu*/sigma/Option-C corrections applied.

**Expected outcome**: L_g values for qb10-qb13 cells rise to reflect the actual hit-rates under the current sigma regime, un-deflating edge_lcb for mid-price NOs.

### Fix 2 (targeted, immediate): Correct `_feasible_stake_bounds` lo computation

`src/decision/payoff_vector.py:633` — replace constant-depth lo with a walk that accounts for actual level 0 size:

```python
# Current (wrong when levels[0].size < min_order_size):
lo = curve.fee_model.all_in_price(best.price) * curve.min_order_size

# Correct: minimum stake that fills min_order_size shares across the ladder
lo = curve.min_viable_stake()  # new helper that walks to find actual lo
```

Or inline: if `best.size >= curve.min_order_size`, the current formula is correct. Otherwise walk to find the stake that buys exactly `min_order_size` shares at the blended price.

**Expected outcome**: `lo` always gives a stake that `_walk_for_stake(lo)` can fill without ValueError, so `robust_at(lo) > 0` for any candidate with genuine positive edge.

### Fix 3 (guard tuning): Raise N_MIN or widen cell merge for thin buckets

`src/decision/qlcb_reliability_guard.py:65: N_MIN = 30`. Cells with n=8-29 currently abstain. Options:
- Merge thin cells into a coarser qb (e.g., merge qb9+qb10+qb11 into one cell)
- Use a pooled L_g from the parent bucket (e.g., entire qb10-qb13 range) when n < N_MIN
- Or apply a flat conservative L_g floor (e.g., 0.55) instead of hard abstain

---

## Summary

Three independent suppression mechanisms, all acting on mid-price NOs (cost 0.50–0.70):

| # | Mechanism | File:line | Trigger | Outcome |
|---|-----------|-----------|---------|---------|
| 1 | OOF L_g < cost | `qlcb_reliability_guard.py:387-398` | Artifact cell L_g below cost | `edge_lcb < 0` → blocked |
| 2 | OOF thin-cell ABSTAIN | `qlcb_reliability_guard.py:399-412` | Cell n < N_MIN=30 | `edge_lcb = -cost` → hard block |
| 3 | `lo` stake ValueError → dU_at_min=0 | `payoff_vector.py:633+705-706` | `levels[0].size < min_order_size` | `0.0 > 0.0` fails → blocked despite +7.6% edge |

The +0.166 real edge proved by settled trades exists. The gate is suppressing it via three distinct mechanisms, none of which reflects genuine "no edge." Rule 1 applies: presumed real, suppressed by defect.

**Primary fix to authorize**: Fix 1 (rebuild OOF artifact on current regime) eliminates Mechanism 1+2 for the majority of candidates. Fix 2 eliminates Mechanism 3.
