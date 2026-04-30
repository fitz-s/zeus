# Codex Prompt — Phase D Epistemic Posterior Split

Task: pricing semantics authority cutover, Phase D only.

Goal: make posterior fusion consume only calibrated belief and named MarketPriorDistribution, never raw quote floats.

Modify `compute_posterior` or equivalent to accept:

```text
p_cal_yes
MarketPriorDistribution | None
posterior_mode
```

Allowed modes:

- `model_only_v1` corrected baseline.
- `legacy_vwmp_prior_v0` explicit legacy only, not promotion evidence.
- `yes_family_devig_v1_shadow` shadow-only until OOS evidence.

Delete/quarantine sparse monitor vector fallback as a prior source.

Add tests:

- raw quote float rejected.
- ask/depth changes do not change posterior.
- market prior distribution changes posterior with trace id.
- legacy prior live without flag rejects.

Do not implement live market-prior estimator in this phase.
