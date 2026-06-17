# Stage 6a Implementation Report — `joint_q` (one normalized joint distribution, Σq = 1)

Created: 2026-06-14
Authority basis: `docs/rebuild/consult_build_spec.md` lines 505-544 (JointQ dataclass
509-521 + `assert_valid`; `build_joint_q` point integration 523-541; family switch;
`q = q/q.sum()` normalization) and the Stage 6 block lines 1127-1144; reconciled
against `docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md` (GREENFIELD —
no live edits; HK `oracle_truncate` MUST thread into the q integration).

## What was built

One greenfield module and one greenfield test file. No live file was touched.

- **`src/probability/joint_q.py`**
  - `JointQ` (frozen dataclass, spec 509-521) — EXACT spec field names:
    `omega`, `q`, `q_by_bin_id`, `predictive_distribution_id`, `q_source`,
    `q_sum`, `identity_hash`. `assert_valid()` asserts `np.all(q >= 0)` and
    `|q.sum() - 1| <= 1e-9` (spec 519-521, verbatim).
  - `QSource` literal domain (spec 515):
    `SETTLEMENT_STATION_NORMAL_V1 | DAY0_HIGH_MAX_NORMAL_V1 | DAY0_LOW_MIN_NORMAL_V1`,
    mapped from `pd.distribution_family` via `_FAMILY_TO_Q_SOURCE`.
  - `build_joint_q(pd, omega)` (spec 523-541) — the ONE point-q integration:
    per-bin settlement-preimage integral under the family-specific law, then
    `q = np.clip(probs, 0.0, None)` and the SINGLE `q = q / q.sum()`.
  - `_identity_hash(...)` — receipt anchor over `pd.identity_hash` +
    `omega.topology_hash` + `rounding_rule` + `q_source` + the mass vector.
  - `JointQError` — fail-closed signal (ineligible pd / missing day0 extreme /
    degenerate non-positive total mass). NOT a value clamp; a refusal.

- **`tests/probability/test_joint_q.py`** — 6 tests (see Test results).

## Spec lines implemented (exact mapping)

| Spec | Implemented as |
|---|---|
| 509-517 `JointQ` fields | `JointQ` frozen dataclass, identical field names |
| 519-521 `assert_valid` | `JointQ.assert_valid` (q >= 0; \|Σq − 1\| ≤ 1e-9) |
| 527-537 family switch | `if family == "NORMAL" / DAY0_HIGH_MAX_NORMAL / DAY0_LOW_MIN_NORMAL` |
| 530 `settlement_preimage(bin, omega.resolution)` | NORMAL: rule threaded into `bin_probability_settlement(...)` (which derives the preimage from `settlement_preimage_offsets`); DAY0: `day0_bin_preimage_native(lo,hi, rounding_rule=rule)` |
| 532 `normal_interval(mu, sigma, lo, hi)` | live `emos.bin_probability_settlement(mu, sigma, bin_low, bin_high, rounding_rule=rule)` |
| 534 `day0_high_interval(observed_extreme, mu, sigma, lo, hi)` | live `probability_high_day0_bin(obs_high, lo, hi, normal_cdf)` with `normal_cdf = Φ((x−mu)/σ)` |
| 536 `day0_low_interval(...)` | live `probability_low_day0_bin(obs_low, lo, hi, normal_cdf)` |
| 539-540 `clip` + `q/q.sum()` | `np.clip(probs, 0.0, None)` then single `q = q / total` |
| 500-501 "No mass leak / No executable-subset renormalization" | integration runs over the COMPLETE `omega.bins` (incl. `executable=False` tail/shoulder bins); one normalization over the complete set |
| 1127-1144 Stage 6 goal / RED-on-revert names | both spec-named tests authored |

## Drift resolved (spec → live), and how

1. **`settlement_preimage(bin, omega.resolution)` (spec 530) does not exist.**
   Drift ledger MINOR row: there is no bare `settlement_preimage`; the live source
   is `settlement_semantics.settlement_preimage_offsets:57`. Resolved by NOT
   re-deriving the preimage here: the NORMAL lane calls the live
   `emos.bin_probability_settlement`, which internally derives the preimage from
   `settlement_preimage_offsets` given the `rounding_rule` kwarg; the DAY0 lane
   calls the live `day0_conditioner.day0_bin_preimage_native`, which wraps the same
   offsets fn. So the q integration is byte-identical to the engine's settlement
   preimage on both lanes, and there is exactly ONE place that names the rounding
   rule (`omega.resolution.rounding_rule`).

2. **`normal_interval` / `day0_high_interval` / `day0_low_interval` (spec 532-536)
   are spec pseudonyms.** Resolved toward the live symbols:
   `emos.bin_probability_settlement` (NORMAL), and
   `day0_conditioner.probability_high_day0_bin` / `probability_low_day0_bin`
   (DAY0). The day0 functions take a `normal_cdf` callable, not `(mu, sigma)`; I
   build `normal_cdf(x) = float(scipy.stats.norm.cdf((x − mu)/σ))` — the SAME
   scipy norm CDF `bin_probability_settlement` uses, so both lanes integrate the
   identical underlying Gaussian. (scipy's CDF already returns 0/1 at ±inf, so
   open shoulders need no special-casing.)

3. **HK `oracle_truncate` rounding rule (spec [HIGH], drift ledger V3/V4 — the
   `build_emos_q` defect that silently defaulted WMO).** Resolved by threading
   `omega.resolution.rounding_rule` EXPLICITLY into both integrators on every
   bin; the rule is never defaulted. There is a single `rule =
   omega.resolution.rounding_rule` binding and both lanes consume it, so WMO-for-HK
   is structurally unconstructable. RED-on-revert test #2 proves it.

4. **`predictive_distribution_id` (spec 514) source.** The live
   `PredictiveDistribution` (Stage 3) carries `identity_hash`, not a separate id.
   Resolved by setting `predictive_distribution_id = pd.identity_hash` (the
   stable receipt anchor of the exact pd q ran over).

5. **`pd.day0.observed_extreme_native` (spec 534/536).** Confirmed present on the
   live `Day0Conditioning` (`day0_conditioner.py`). Used directly as the observed
   support bound for the DAY0 families; a DAY0 family whose `day0` has no observed
   extreme is refused (`JointQError`).

## Operator-law compliance (corrected transformation, no detector-on-broken-transform)

- The Σq = 1 contract is produced by the SINGLE `q = q / q.sum()` line at the end
  of the one transform — `assert_valid` re-checks it but the equality is
  guaranteed by construction, not enforced by a separate renormalization gate.
- The HK preimage fix is a transformation property (one rule binding threaded into
  the integrators), not a clamp on a mis-integrated value.
- The DAY0 impossible-bin q = 0 comes from the settlement-conditioned transform
  (`probability_high/low_day0_bin`), not a sanity check on a bare-Normal output.
- The two `JointQError` raises are fail-closed REFUSALS (ineligible pd; degenerate
  non-positive total mass = incomplete support), not value clamps that leave a
  broken transform in place. A complete MECE Omega with σ > 0 always carries
  positive total mass, so the degenerate-mass branch is an unreachable-by-valid-
  input guard against dividing into NaN, not a fallback shim.

## RED-on-revert verification (both spec-named tests)

Verified by temporarily injecting each defect and confirming the matching test fails;
file then restored byte-identical (`diff` empty, no markers leaked).

- Inject **executable-subset renormalization** (spec 500-501 defect: zero the
  non-executable bins before normalizing) →
  `test_q_sum_one_for_every_family` FAILS:
  `AssertionError: non-executable tail mass leaked ... assert 0.0 > 0.2`.
- Inject **WMO default** (`rule = "wmo_half_up"`, dropping the threaded rule) →
  `test_hk_oracle_truncate_threaded_into_q_integration` FAILS: HK q is identical
  to WMO q (`np.allclose` true).

## Test results

`/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/probability/test_joint_q.py`

```
......                                                                   [100%]
6 passed in 0.86s
```

Tests:
- `test_q_sum_one_for_every_family` (spec RED-on-revert; Σq = 1 over complete Omega
  for NORMAL / DAY0_HIGH / DAY0_LOW + HK NORMAL; no-mass-leak / executable-subset < 1)
- `test_q_sum_one_holds_when_day0_zeros_impossible_bins` (DAY0 collapse zeros
  sub-observed bins yet Σq = 1)
- `test_hk_oracle_truncate_threaded_into_q_integration` (spec RED-on-revert; HK q ≠ WMO q)
- `test_ineligible_distribution_is_refused_not_served_degenerate`
- `test_day0_family_without_observed_extreme_is_refused`
- `test_identity_hash_is_deterministic_and_pd_linked`

## Money-path unaffected

`/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/money_path tests/strategy/live_inference`

```
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.21s
```

## Files written

- `src/probability/joint_q.py` (new)
- `tests/probability/test_joint_q.py` (new)
- `docs/rebuild/impl_stage6_joint_q.md` (this report)
