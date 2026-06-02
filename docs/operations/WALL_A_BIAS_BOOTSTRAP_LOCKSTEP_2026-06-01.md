# Wall A: Bias-Bootstrap Lockstep

**Ruling authority:** `docs/operations/CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md §4.1`
**Applied:** 2026-06-01
**HEAD at fix:** main

---

## Root cause (§2)

`_market_analysis_from_event_snapshot` called `_snapshot_members(snapshot)` then passed
`members` to `_snapshot_p_raw`.  Inside `_snapshot_p_raw`, `_maybe_apply_edli_bias_correction`
corrected a LOCAL rebind of `members`; the corrected array never escaped back to the caller.

`MarketAnalysis(member_maxes=members)` received the OUTER uncorrected (cold) array.
The bootstrap resampled cold members, placing `q_lcb_5pct` approximately `|eff_bias_c|°`
below the warm point posterior — a spurious CI suppressing ~284 genuine +20¢-EV candidates/hr.

Tokyo case: `eff_bias_c = -3.447°C` → raw mean ~24.5°C, corrected ~27.95°C.
Contested bin: 23°C point bin, NO priced ~0.75.
Pre-fix: point NO p_posterior ≈ 0.95, q_lcb_5pct ≈ 0.76 → CI ≈ 0.19 → trade rejected.
Post-fix: bootstrap uses corrected members, q_lcb_5pct ≈ 0.95 → CI ≈ 0 → trade passes.

---

## Fix (§4.1)

**Location 1 — `src/engine/event_reactor_adapter.py`: `_market_analysis_from_event_snapshot`**

Hoisted `_maybe_apply_edli_bias_correction` call ABOVE `_snapshot_p_raw` and `MarketAnalysis`:
- `raw_members = _snapshot_members(snapshot)` (renamed from `members`)
- city lookup + correction call produces `(members, _bias_corrected)` at this level
- `payload["_edli_bias_corrected"] = True` set here on correction
- `_snapshot_p_raw(..., members_already_corrected=True)` to suppress internal re-correction
- `member_maxes=members` now receives the corrected array (unchanged field name)
- `bias_corrected=_bias_corrected` (was hardcoded `False`)

**Location 2 — `_snapshot_p_raw` signature:**

Added `members_already_corrected: bool = False` parameter.
Internal correction block wrapped with `if not members_already_corrected:`.
Existing callers pass no argument → `False` → behavior unchanged (backward compatible).

---

## Tests

File: `tests/engine/test_bootstrap_bias_correction_lockstep.py`

| Test | Pre-fix | Post-fix | Role |
|------|---------|----------|------|
| `test_i_on_head_is_uncorrected` | PASS | XFAIL (antibody) | Confirms bug existed; XPASS → CI fails if reverted |
| `test_i_post_fix_is_corrected` | FAIL | PASS | Bootstrap mean ≈ corrected mean |
| `test_ii_ci_collapses_and_trade_score_positive` | FAIL | PASS | CI collapses, score > 0 for Tokyo +20¢-EV |
| `test_iii_honest_ci_preserved` | PASS | PASS | No-bias/contested bin keeps honest CI > 0.05 |
| `test_iv_no_double_correction` | FAIL | PASS | Single correction, member_maxes exact match |
| `test_v_property_antibody` (54 cells) | FAIL (except eff_bias=0) | PASS all | Grid invariant: structurally unconstructable |

Result post-fix: 58 passed, 1 xfailed.

---

## Scope boundary (§4.2 NOT implemented)

This ruling does NOT rescope `robust_trade_score`'s `q_lcb` gate for the edge-distribution
reinterpretation.  §4.2 is a separate decision.  `λ_edge` is untouched.

---

## Invariant (permanent)

`mean(MarketAnalysis._member_maxes)` must equal the bias-corrected point-posterior member
mean to within floating-point copy tolerance.  Captured by `test_v_property_antibody`
grid (6 eff_bias × 3 spread × 3 lead = 54 parametrized cells).  Any regression in
`_market_analysis_from_event_snapshot` or `_snapshot_p_raw` that re-introduces the
train/serve split will produce XPASS on `test_i_on_head_is_uncorrected` → CI fails.
