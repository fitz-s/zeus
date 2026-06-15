# Stage 6b — joint_q_band implementation report

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (Create src/probability/joint_q_band.py
block lines 546-588; Stage 6 block lines 1127-1144) reconciled against
docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — no live edits).

## What this module is

`JointQBand` is the coherent credible band on the joint q: it propagates the
PARAMETER POSTERIOR through the q integrator and reads marginal quantiles ONLY AFTER
each draw has been renormalized to the probability simplex. It is the corrected
replacement for the live defect `_build_fused_q_bounds`
(`src/data/replacement_forecast_materializer.py:1425-1426`), which takes
`np.percentile(probs, 5, axis=0)` over a `(draws × bins)` grid of RAW per-bin mass
with NO per-row simplex renormalization.

## Files written (new only — no live file touched)

- `src/probability/joint_q_band.py`
  - `PredictiveParameterDraw` (dataclass, spec lines 550-555; EXACT field names:
    `mu_native`, `sigma_native`, `debias_shift_native`, `center_error_native`).
  - `JointQBand` (dataclass, spec lines 557-570; EXACT field names: `joint_q`,
    `samples`, `q_lcb`, `q_ucb`, `alpha`, `basis`, `sample_hash`) with `assert_valid`
    (spec line 568-570: `samples.ndim == 2`, `samples >= 0`,
    `np.allclose(samples.sum(axis=1), 1.0, atol=1e-9)`).
  - `JointQBandError` (fail-closed).
  - `draw_mu(pd, rng)` / `draw_sigma(pd, rng)` (spec line 577-578) — the
    parameter-posterior draws.
  - `integrate_all_bins(pd_k, omega)` (spec line 580) — REUSES `build_joint_q(pd_k,
    omega).q`, which already integrates every bin AND does `q = q / q.sum()`.
  - `build_joint_q_band(pd, omega, *, n_draws, alpha)` — the per-draw algorithm
    (spec lines 572-585), `basis="PARAMETER_POSTERIOR_SIMPLEX_V1"`.
- `tests/probability/test_joint_q_band.py`
  - Spec-named RED-on-revert tests:
    `test_every_band_sample_row_sums_to_one`,
    `test_modal_lcb_does_not_collapse_from_raw_bin_percentile`.
  - Supporting contract tests: `test_band_is_deterministic_for_fixed_inputs`,
    `test_band_refuses_ineligible_distribution`, `test_band_refuses_degenerate_request`,
    `test_drawn_sigma_never_below_realized_floor`.

## The corrected transformation (operator law: bad output is mathematically impossible)

For each draw `k`:
1. `mu_k = draw_mu(pd)` — drawn from the center-parameter posterior
   `N(pd.mu_native, center_parameter_se)`.
2. `sigma_k = draw_sigma(pd)` — drawn from the width posterior, floored positive AND
   at the realized floor.
3. `q_k = integrate_all_bins(pd_k, omega)` where `pd_k = replace(pd, mu_native=mu_k,
   sigma_native=sigma_k)`. `integrate_all_bins` IS `build_joint_q(pd_k, omega).q`,
   which performs `q = q / q.sum()` as the last step of its single transform — so
   EVERY `q_k` is ALREADY on the probability simplex (`sum(q_k) == 1`). The per-row
   renormalization happens INSIDE the generator, before any row is stacked.
4. `samples[k, :] = q_k`.

`q_lcb = np.quantile(samples, alpha, axis=0)` / `q_ucb = np.quantile(samples, 1 -
alpha, axis=0)` are therefore marginal quantiles of COHERENT joint distributions.

There is NO floor/cap/clamp/sanity-check/shadow-flag bolted onto a collapsed value.
The generator is the fix: the only way a row enters `samples` is as
`build_joint_q(pd_k, omega).q`, which is on the simplex by construction. A row that
does not sum to 1 is unconstructable; `assert_valid` is a cheap re-proof, not a
renormalization gate. Verified: reverting `integrate_all_bins` to raw un-normalized
mass (the `_build_fused_q_bounds` behavior) makes `build_joint_q_band` itself raise
in `assert_valid`, and BOTH spec-named tests go RED (see below).

Determinism: the draws are seeded from `pd.identity_hash`, so the band and its
`sample_hash` are reproducible for a fixed `(pd, omega, alpha, n_draws)` — a receipt
is verifiable (Stage 6 live signal: "q band receipts include sample hash and row-sum
stats").

## Drift resolved (toward the live type, per the ledger directive)

The spec algorithm (line 577) writes `mu_k = draw_mu(pd.center, pd.sigma_components)`.
The brief named `src/forecast/center.py (CenterEstimate — center_parameter_se for the
mu draw)`. But the LIVE `CenterEstimate` (`src/forecast/center.py`) has NO
`center_parameter_se` field — confirmed by `grep -n "center_parameter_se" center.py`
→ NOT FOUND. The center-parameter standard error lives on
`SigmaComponents.center_parameter_se_native` (`src/forecast/sigma_authority.py:191`),
which `build_sigma` populates via `center_parameter_se_sigma` (`sigma_authority.py:329`).

Resolution (drift ledger "prefer the Actual-live column"):
- The mu-draw SE is read from `pd.sigma_components.center_parameter_se_native`
  (`_center_parameter_se`). The "center_parameter_se" the spec names is this live field.
- The mu-draw MEAN is the SERVED `pd.mu_native` (which is `day0.center_after_native`
  when day0 is active — the exact center the point q integrates around), NOT
  `pd.center.mu_native` (the pre-day0 center). This keeps the parameter draw
  self-consistent with the served point distribution.
- The sigma-draw dispersion is derived from `pd.sigma_components.model_dispersion_native`
  (the estimated part of the served width), and the sigma draw is floored at
  `pd.sigma_components.realized_floor_native` so no draw is sub-realized (the
  sigma-authority invariant holds per draw, by construction of the floor inside
  `draw_sigma` — not a post-hoc clamp on the assembled band). Verified by
  `test_drawn_sigma_never_below_realized_floor`.

No other drift. `build_joint_q` already exists in this worktree (Stage 6a) and exposes
the integrator the spec's `integrate_all_bins` names; it is reused unchanged. The
spec's `replace(pd, ...)` is `dataclasses.replace` on the frozen `PredictiveDistribution`
(verified it carries `mu_native`/`sigma_native` and `replace` preserves the rest).

## RED-on-revert verification

The corrected `integrate_all_bins` was temporarily reverted to the broken
`_build_fused_q_bounds` behavior (raw per-bin mass over the executable subset, NO
per-row `q = q / q.sum()`). Result:

```
FAILED tests/probability/test_joint_q_band.py::test_every_band_sample_row_sums_to_one
FAILED tests/probability/test_joint_q_band.py::test_modal_lcb_does_not_collapse_from_raw_bin_percentile
2 failed
```

Both fail at `JointQBand.assert_valid` —
`np.allclose(self.samples.sum(axis=1), 1.0, atol=1e-9)` — "a draw was not
renormalized to the simplex (the defect this module replaces)". The module was then
restored byte-for-byte (confirmed via git: both files remain new/untracked with no
stray edits) and all 6 tests pass.

`test_modal_lcb_does_not_collapse_from_raw_bin_percentile` additionally ISOLATES the
`q = q / q.sum()` step: over the SAME seeded `(mu_k, sigma_k)` draws and the SAME
narrow listed window (`b25, b26, b27`, with the modal bin `b25` at the lower edge so
draws jittering down spill out of the window and the rows sum to < 1), it compares
the modal bin's alpha-quantile (a) over RAW un-renormalized window mass (the defect)
vs (b) over the SAME mass with each row divided by its own sum (the fix). The RENORM
modal q_lcb is ~0.10 higher than the RAW one (renorm ≈ 0.58 vs raw ≈ 0.48) — the
renormalization is what stops the modal collapse. A revert that drops `q = q / q.sum()`
erases that margin and breaks the band's row-sum invariant.

## Test results

`tests/probability/test_joint_q_band.py` — 6 passed:
```
......                                                                   [100%]
6 passed in 4.91s
```

Money-path / live-inference unaffected (no live file touched):
```
tests/money_path tests/strategy/live_inference
331 passed in 4.28s
```

## Integration note

This module is NOT wired into the reactor here (greenfield). The live wiring (replace
`replacement_forecast_materializer.py:_build_fused_q_bounds` and the
`event_reactor_adapter.py:_side_q_lcb_from_yes_samples` live usage with
`build_joint_q_band`) is Stage-6 integration / Wave 5, per spec lines 1135-1136.
The NO band consumer reads `q_no_samples = 1 - q_yes_samples` from `JointQBand.samples`
(drift ledger calibration_forecast row).
