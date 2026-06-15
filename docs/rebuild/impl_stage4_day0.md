# Stage 4 — Day0 Conditioner: Implementation Report

**Module:** `stage4_day0`
**Date:** 2026-06-14
**Authority:** `docs/rebuild/consult_build_spec.md` (Stage 4 block lines 1091-1107; "Create
`src/forecast/day0_conditioner.py`" block lines 273-342) reconciled against
`docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md`.
**Scope:** GREENFIELD — new files only, no live-file edits. Wired into the reactor later (integration), not now.

---

## Goal

Observed running extreme is ground truth. On an active trading day the settlement value of a HIGH
market is `Y = max(observed_high_so_far, X_remaining)` and of a LOW market is
`Y = min(observed_low_so_far, X_remaining)`, with `X_remaining ~ N(mu, sigma)`. Every settlement bin
entirely below the observed running high (or entirely above the observed running low) is physically
impossible and must carry `q = 0` after the settlement preimage.

The defect this replaces: the live day0 lane integrated the bare predictive Normal `X ~ N(mu, sigma)`
over the bins, so a Tokyo family with an observed-so-far high of ~21 could still place probability on a
26 bin **and** on bins *below* 21 — both impossible once 21 has been observed.

---

## Files written

| File | Symbols |
|---|---|
| `src/forecast/day0_conditioner.py` | `Day0ObservationState` (dataclass), `Day0Conditioning` (dataclass), `Day0Status` (Literal alias), `probability_high_day0_bin`, `probability_low_day0_bin`, `day0_bin_preimage_native`, `condition_day0` |
| `tests/forecast/test_day0_extreme_conditioner.py` | 7 tests (3 spec-named RED-on-revert + 4 fail-closed / preimage guards) |

---

## Spec lines implemented

- **Lines 277-288 — `Day0ObservationState`**: implemented with the EXACT field names and order
  (`observed`, `station_id`, `source`, `samples_count`, `latest_observed_at_utc`,
  `observed_high_native`, `observed_low_native`, `observed_extreme_native`, `raw_observation_hash`).
  `frozen=True`. `Optional[...]` for the nullable fields per the spec's `| None`.
- **Lines 289-298 — `Day0Conditioning`**: implemented with the EXACT field names
  (`active`, `observed_extreme_native`, `support_lower_native`, `support_upper_native`,
  `center_before_native`, `center_after_native`, `status`) and the EXACT `status` Literal domain
  `{"NO_DAY0", "HIGH_CLAMPED", "LOW_CLAMPED", "OBS_SOURCE_MISSING_REFUSED"}`. `frozen=True`.
- **Lines 299-318 — center clamp (support transform's center)**: `condition_day0` applies
  `mu_after = max(mu_before, observed_high)` + `support_lower = observed_high` for high markets, and
  `mu_after = min(mu_before, observed_low)` + `support_upper = observed_low` for low markets. This is
  the **support transform's center**, not a cap on a separately-computed mu (see "How the broken
  output is impossible" below).
- **Lines 320-329 — `probability_high_day0_bin(obs_high, lo, hi, normal_cdf)`**: implemented VERBATIM —
  `if hi <= obs_high: return 0.0` / `if lo <= obs_high < hi: return normal_cdf(hi)` /
  `return normal_cdf(hi) - normal_cdf(lo)`.
- **Lines 331-340 — `probability_low_day0_bin(obs_low, lo, hi, normal_cdf)`**: implemented VERBATIM —
  `if lo >= obs_low: return 0.0` / `if lo < obs_low <= hi: return 1.0 - normal_cdf(lo)` /
  `return normal_cdf(hi) - normal_cdf(lo)`.
- **Lines 1091-1107 — Stage 4 RED-on-revert test names**: all three authored under their spec names:
  `test_high_bins_below_observed_high_have_zero_probability`,
  `test_low_bins_above_observed_low_have_zero_probability`,
  `test_observed_extreme_clamps_center`.

`day0_bin_preimage_native` is a small added helper (not a spec dataclass) that wraps the live contract
`settlement_preimage_offsets` to turn a bin label set into the `(lo, hi)` preimage interval the spec's
two probability functions consume. It is the seam by which `lo`/`hi` arrive correctly preimage-expanded
(and per-city rounding-rule-aware), as the Stage 4 "modify q integration to use DAY0_HIGH_MAX_NORMAL
and DAY0_LOW_MIN_NORMAL" live-signal requires — without it the caller would have to re-derive the
preimage and could silently default to WMO for HK.

---

## How the broken output is made mathematically impossible (operator law)

The impossible-bin `q = 0` is produced by the **settlement-conditioned probability transform itself**,
not by a sanity check / cap / haircut applied to a bare-Normal output:

- For a HIGH bin with `hi <= obs_high`, `probability_high_day0_bin` returns `0.0` by definition of
  `Y = max(obs_high, X)` — `Y >= obs_high >= hi` can never land in `[lo, hi)`. There is no bare-Normal
  mass that gets "zeroed"; the mass is never computed because `Y` cannot reach the bin.
- The center clamp is the **mean of the conditioned settlement variable `Y`** (which can never be below
  the observed high), not a post-hoc cap. The conditioned bin probabilities are exact regardless of
  where `mu_before` sits relative to the observed extreme; the clamp records the support-corrected
  center on the receipt so the mu*-vs-observed-extreme decoupling is visible.

A detector that catches a bad value and leaves the transform broken would violate the operator law;
none exists here.

---

## Drift resolved

| Drift | Resolution |
|---|---|
| Spec prose/pseudocode references a bare `settlement_preimage(bin, omega.resolution)` (`:530`), but the live symbol is `settlement_preimage_offsets` (`src/contracts/settlement_semantics.py:57`); a bare `settlement_preimage` does not exist (drift ledger MINOR row). | `day0_bin_preimage_native` **wraps/calls** `settlement_preimage_offsets(rounding_rule, half_step=...)` and forms `[bin_low + low_offset, bin_high + high_offset)`. No bare `settlement_preimage` is imported. |
| The day0 extreme is the INPUT to the estimator/integrator, **not** a downstream clamp (drift ledger module-specific note). | The `obs_high`/`obs_low` extreme is threaded as a parameter INTO `probability_high_day0_bin` / `probability_low_day0_bin` (it shapes the support of `Y` directly). The center clamp in `condition_day0` is the support transform's center, recorded on `Day0Conditioning`, not a cap applied after a bare-Normal q is produced. |
| `normal_cdf` shape: the spec's probability functions take a `normal_cdf` callable; whether it is the standard Normal `Phi(z)` or the predictive `Phi((x-mu)/sigma)` is unstated. | Resolved toward `normal_cdf(x) = Phi((x - mu) / sigma)` — the predictive Normal CDF already folded with `(mu, sigma)` — so the verbatim bin-mass formulae `normal_cdf(hi)` / `1 - normal_cdf(lo)` / `normal_cdf(hi) - normal_cdf(lo)` are correct as written and the caller supplies whichever `(mu, sigma)` the estimator chose (typically the support-clamped `center_after_native`). Recorded in the module docstring. |
| Live dependency types: `ForecastCase` (`src/forecast/types.py`), `EventResolution.rounding_rule` Literal (`src/probability/event_resolution.py:74`), and the integrator convention (`bin_probability_settlement` at `src/calibration/emos.py:580`, preimage interval `[bin_low + low_offset, bin_high + high_offset)`). | Read all three; the preimage helper matches `emos.py:639-655` exactly (offsets from `settlement_preimage_offsets`, lower `bin_low + low_offset`, upper `bin_high + high_offset`, open shoulders -> `-inf`/`+inf`). `rounding_rule` accepted as `str` (superset of the `EventResolution` Literal) so the helper validates via the contract fn rather than a second Literal. |
| GREENFIELD — no live edits (drift ledger module-specific). | Honored: only the two new files were created. `src/engine/event_reactor_adapter.py:11074-11091` was read READ-ONLY to confirm the live day0 branch and the observed-extreme shape it consumes; no live file was touched. |

---

## RED-on-revert verification

Temporarily reverted the corrected transform to the broken bare-Normal behavior the spec replaces
(removed the `hi <= obs_high -> 0.0` / `lo >= obs_low -> 0.0` branches so both probability functions
return the bare Normal interval `normal_cdf(hi) - normal_cdf(lo)`, and set
`center_after = center_before` to remove the clamp). All three spec-named tests FAILED:

```
FAILED tests/forecast/test_day0_extreme_conditioner.py::test_high_bins_below_observed_high_have_zero_probability
FAILED tests/forecast/test_day0_extreme_conditioner.py::test_low_bins_above_observed_low_have_zero_probability
FAILED tests/forecast/test_day0_extreme_conditioner.py::test_observed_extreme_clamps_center
3 failed in 0.87s
```

Restored -> all 7 pass. Each test is constructed so the bare-Normal interval over the impossible bin is
strictly > 1e-6 (asserted in-test), so only the corrected transform can return `0.0`; and the clamp test
uses `mu_before` on the wrong side of the observed extreme so a no-clamp revert leaves the center
unmoved and fails.

---

## Test results

### `tests/forecast/test_day0_extreme_conditioner.py`

```
.......                                                                  [100%]
7 passed in 0.85s
```

Tests:
- `test_high_bins_below_observed_high_have_zero_probability` (spec-named RED-on-revert)
- `test_low_bins_above_observed_low_have_zero_probability` (spec-named RED-on-revert)
- `test_observed_extreme_clamps_center` (spec-named RED-on-revert)
- `test_no_day0_observation_is_inactive_and_does_not_clamp` (fail-closed: `NO_DAY0`)
- `test_observed_but_relevant_side_missing_is_refused` (fail-closed: `OBS_SOURCE_MISSING_REFUSED`)
- `test_oracle_truncate_preimage_asymmetric_bin_bounds` (HK truncation preimage threads through)
- `test_open_shoulders_are_infinite` (open-shoulder preimage)

### Money-path unaffected

```
/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/money_path tests/strategy/live_inference
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.66s
```

GREENFIELD module touches no live import path; money-path and live-inference suites are green.
