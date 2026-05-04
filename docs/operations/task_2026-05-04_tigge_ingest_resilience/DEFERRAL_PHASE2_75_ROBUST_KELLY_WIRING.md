# DEFERRAL — Phase 2.75 Robust Kelly Production Wiring

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Authority basis:** critic-opus second-pass review 2026-05-04 finding MAJOR 5; user decision "option 3, document deferral" 2026-05-04; may4math.md Finding 5.
**Status:** OPEN (unlock-blocker).

---

## What is deferred

`src/strategy/robust_kelly.py` was implemented as Phase 2.75-Phase-1
(commit 082df407): module + `SizingEvidence` dataclass + unit tests.
**The evaluator does not yet call it.**  Production sizing still flows
through legacy `kelly_size` in `src/strategy/kelly.py`, invoked at
`src/engine/evaluator.py:493`.

Phase 2.75-Phase-2 — flipping that call site to `robust_kelly` — is
deferred to a subsequent PR.

## Why deferred (and why placeholder wiring was rejected)

The robust Kelly math depends on five real uncertainty inputs:

| Input | Source needed | Status today |
|---|---|---|
| `platt_param_ci` | Platt parameter bootstrap on `calibration_pairs_v2` | not implemented |
| `decision_group_ci` | DG residual bootstrap (per-DG sample variance) | not implemented |
| `transfer_ci` | `validated_transfers` OOS metrics rolling window | table exists; bootstrap reduction does not |
| `oracle_posterior_upper` | Beta-binomial posterior on Day-0 oracle (may4math.md F3) | not on runtime path |
| `cost_eff_upper` | Slippage + tick + fee + queue-jump worst case | partial (slippage only) |

Three options were considered:

1. **Placeholder zero-width CIs** (`p_lower == p_point`).
   Output ≡ legacy Kelly + records `SizingEvidence`.
   Adds bookkeeping but the math doesn't shrink size — robust Kelly is a
   no-op until real CIs exist.  False sense of robustness.

2. **Conservative ±5% default uncertainty.**
   Materially shrinks size, but the shrinkage is fabricated, not
   evidence-based.  May over- or under-shrink relative to the real
   distribution.  Worse than legacy because it pretends to be calibrated.

3. **Defer + document as unlock-blocker.**  *(Chosen.)*
   Honest gap.  Lock stays at precedence=200 until real CIs land.
   No fake confidence injected into the sizing pipeline.

Per Fitz core methodology — make the category impossible, not just the
instance — option 3 is the only choice that doesn't poison the next
phase's assumptions.

## Unlock-blocker contract

The 4-layer live trading lock — specifically the precedence=200
NULL-expiry row in `control_overrides` — **stays in place until** all of
the following land:

- [ ] Platt parameter bootstrap implemented and writes
      `platt_param_ci` per (cycle, source_id, horizon_profile, DG).
- [ ] DG residual bootstrap implemented and writes `decision_group_ci`.
- [ ] `validated_transfers` rolling-window reduction implemented and
      surfaces `transfer_ci` keyed by `(forecast_domain, calibrator_domain)`.
- [ ] Oracle Beta-binomial posterior wired to runtime; `oracle_posterior_upper`
      flows into `SizingUncertaintyInputs`.
- [ ] `cost_eff_upper` includes tick + fee + queue-jump (not slippage only).
- [ ] Evaluator call site at `src/engine/evaluator.py:493` flips from
      `kelly_size` to `robust_kelly`.
- [ ] Relationship test: live evaluator path produces zero size when
      `evaluate_calibration_transfer` returned `BLOCK` or `SHADOW_ONLY`
      (currently enforced at evaluator gate; must remain enforced after
      flip via robust Kelly's domain-mismatch multiplier).

When all rows are checked, file the unlock PR and remove the
precedence=200 override.

## What is **not** affected by this deferral

- Phase 2 Platt cycle/source_id/horizon_profile stratification — landed.
- Phase 2.5 evidence-based calibration transfer policy — landed and
  wired into evaluator (`CALIBRATION_TRANSFER_SHADOW_ONLY` /
  `CALIBRATION_TRANSFER_BLOCKED` rejections).
- Phase 2.6 `MetricIdentity` source_family + live `data_version`
  derivation — landed and wired (`UNKNOWN_FORECAST_SOURCE_FAMILY` /
  `FORECAST_PROVENANCE_INCONSISTENT` rejections).
- Phase 3 `ENSEMBLE_MODEL_SOURCE_MAP` routing flip — landed.

The Phase 2.75 deferral does **not** weaken the gate-stack on the live
path; the calibration-transfer gate already hard-rejects shadow-only
forecasts upstream of sizing.  Robust Kelly is *additional* protection
against sizing on biased posteriors, not the only protection.

## References

- `src/strategy/robust_kelly.py` (module docstring carries the same
  STATUS block + pointer back here).
- `DESIGN_PHASE2_75_ROBUST_KELLY.md` (design intent).
- `UNLOCK_SEQUENCE.md` (overall unlock order; this deferral applies to
  the post-Phase-3 gate).
- may4math.md Finding 5 — `CRITICAL_QUANT_RISK`,
  `ROBUST_KELLY_NEEDED_NOW`.
- critic-opus second-pass review 2026-05-04 — finding MAJOR 5.
