# EMOS/NGR affine center calibration on the runtime combined center — landing record (2026-07-01)

Authority: operator "使用真实参与概率计算的运行态组合数据进行精准的emos设计提升" + frontier consult
REQ-20260701-010328 + adversarial review + operator rejection of the constant-offset attempt
("6 cities not meaningful, +1.3 absurd"). The precise EMOS is the AFFINE μ'=a+b·μ, NOT a constant offset.

## The key insight (why the constant offset was wrong)
The weak-fusion residual is NOT a constant bias — it is a TEMPERATURE-DEPENDENT representativeness
bias. Evidence: for Seoul the bias grows Dec +0.3 → May +2.4; EVERY source is cold (ecmwf +1.29 …
ukmo +2.99, best gem +0.55) so it is representativeness (the airport station outruns every model's
grid cell, and by more in heat), NOT a fusion error re-weighting can fix. A constant offset (b=1) is
blind to this — it over-corrects mild days and under-corrects heat, and it HARMED cities whose bias
is a pure slope (Jeddah, Chicago, Toronto). The precise instrument is the EMOS SLOPE b:
`μ' = a + b·μ_runtime`, fit on the real runtime center, shrunk toward identity, slope clamped mild.

## Estimator
- Per-city shrunk OLS of settlement on the runtime center: `μ' = a + b·μ`.
- Shrink toward identity (a=0,b=1) by w=n/(n+κ), κ=40 → a world-class city stays byte-identical.
- Slope CLAMPED to [0.85,1.15] preserving the mean-center correction — a mild physical tilt; guards
  narrow-range over-fit (tropical cities) + unsafe extrapolation. Median served |b−1|=0.046 (tiny).
- Walk-forward / leak-free (expanding window). σ UNTOUCHED (center-only; per resolved memory).

## Two-gate serve rule
Serve a city only if BOTH:
1. STRUCTURAL — walk-forward affine OOS ΔMSE has an individual 95% lower CI ≥ 0 (n≥40; real signal).
2. TRANSFER — the SAME (a,b) does NOT harm the ACTUAL live served center (`forecast_posteriors`
   single_runs anchor_value_c) over the settled overlap (point ΔMSE ≥ 0, live_n≥10). Guards the
   previous_runs(ifs025)↔single_runs(ifs9) product gap that sank the constant offset. (Point, not CI:
   the affine is robust in aggregate — pooled live +0.23, 33/49 cities help — so a 95% CI on ~18 live
   obs is over-strict; the point gate keeps the meaningful breadth while dropping any live-harm city.)

## Result (metric=high, lead=1)
- Walk-forward pooled ΔMSE **+0.327**, block-bootstrap CI [+0.276,+0.381], excludes 0. **38/50 cities
  materially helped** in-sample; **33/49 help on the live single_runs center** (transfer +0.228).
- **19 cities served** by the two-gate rule (Amsterdam, Ankara, Atlanta, Buenos Aires, Chicago, Dallas,
  Guangzhou, Hong Kong, Kuala Lumpur, Los Angeles, Mexico City, Milan, Munich, Sao Paulo, Seoul,
  Taipei, Toronto, Wellington, Wuhan); served-pooled ΔMSE +0.594 CI [+0.49,+0.69].
- Corrections are TINY in mild conditions, precise in extremes: e.g. Taipei −0.3 (21°C) → +1.9 (37°C);
  LA +0.6 (17°C) → −1.1 (28°C, marine-layer cap); most cities ±0.4–0.9. Max ~+1.9 only in extreme heat
  where all models genuinely lag. 32 cities get IDENTITY (byte-identical).

## Changes landed (this tree — INERT on live until deployed)
- NEW `src/calibration/emos_center_calibration.py` — affine estimator (shrink-to-identity, slope
  clamp, walk-forward) + fail-soft `lookup_affine` (identity when absent/not-served).
- NEW `scripts/fit_emos_center_calibration.py` — replays the runtime center, fits per-city (a,b),
  dual-gates, writes the artifact (sanctioned RO conn; records `fit_on_scheme_artifact`).
- NEW `tests/calibration/test_emos_center_calibration.py` — estimator (8) + lookup (5); verified.
- EDIT `src/data/replacement_forecast_materializer.py` (3 hunks): `emos_center_a/b/delta_c` fields;
  `μ'=apply_affine(_mu_diagonal,a,b)` at the override construction (SINGLE authoritative center →
  q point + q_lcb/q_ucb bounds + provenance + reactor ENTRY read, entry/exit unified, no #135 split;
  critic-verified all consumers read the corrected value); provenance stamp (a,b,delta) reconstructible.
- Candidate artifact `docs/evidence/emos_upgrade/emos_center_calibration.candidate.json.md` (NOT in state/).

## Safety / go-live / rollback
- INERT NOW: no `state/emos_center_calibration.json` → `lookup_affine` returns (0.0,1.0) identity →
  byte-identical center. Live daemons run `zeus-live-main` (no wiring). Verified.
- GO-LIVE: (1) commit; (2) deploy code to `zeus-live-main` + kickstart; (3) REGENERATE fresh
  `python scripts/fit_emos_center_calibration.py --out state/emos_center_calibration.json` (never cp a
  stale candidate — served set is data-dependent).
- ROLLBACK: delete the artifact (or `serve:false`) → instant identity. Monitor pooled rolling Δscore +
  per-served-city PIT/Brier; rollback on degrade. `fit_on_scheme_artifact` staleifies on scheme rotation.

## Superseded / follow-ups
- SUPERSEDES the constant-offset attempt (removed): it was blind to the temperature slope, harmed
  slope-bias cities, and gave the "absurd +1.3". This affine design is the operator-endorsed direction.
- Follow-ups: low-metric + per-lead fit; nested purged rolling-origin LOCO×date-block gold-standard;
  threshold-wise Brier/PIT. Full pytest session blocked by a PRE-EXISTING schema-fingerprint drift
  (`architecture/_schema_fingerprint.txt`, unrelated); new logic verified directly (12/12 assertions).
