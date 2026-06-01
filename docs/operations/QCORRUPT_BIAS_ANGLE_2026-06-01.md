# QCORRUPT — BIAS-CORRECTION / ENS-ERROR ANGLE — Singapore warm shift

Created: 2026-06-01
Last reused or audited: 2026-06-01
Authority basis: read-only root-cause probe (HEAD 6fcd05a69f); config/settings.json edli_v1; model_bias_ens VERIFIED store; src/engine/event_reactor_adapter.py

## VERDICT: CONFIRMED

The Singapore q-vector warm shift (modal 31 → traded peak 32) is caused by the
**A4 empirical-Bayes ENS mean-bias correction layer**, which is **ENABLED LIVE**
and warms every Singapore member by **+1.584 °C**.

## Exact value applied to Singapore

- Store row (state/zeus-world.db, `model_bias_ens`):
  `city=Singapore, season=JJA, metric=high, month=6, live_data_version=ecmwf_opendata_mx2t3_local_calendar_day_max`,
  **`effective_bias_c = -1.5836`**, `weight_live = 1.0`, `authority = VERIFIED`,
  `error_model_family = edli_per_city_v1`, `coverage_months = 6`, `gate_set_hash = a4_canonical_2026_05_31`.
- Singapore settles in **°C** → `eff_native = -1.5836` (no ×1.8).
- Correction math: `corrected = members - eff_native = members - (-1.5836) = members + 1.5836`.
  **Sign: WARM. Magnitude: +1.58 °C on every member.**

## Does it explain 31 → 32?

YES. Reproduced numerically: warming the operator's raw histogram
(30:0.314, 31:0.588, 32:0.059; modal 31) by +1.58 °C collapses bin-31 mass
(0.61 → 0.14) and makes **bin 32 modal (0.46)** — matching the traded
q_YES(32)=0.565 / q_YES(31)=0.124. The ~+1°C off-modal shift is exactly this layer.

## File:line evidence

- Flag (ON): `config/settings.json` → `edli_v1.edli_bias_correction_enabled = true`
  (ACTIVATED 2026-05-31; note self-cites Singapore "32%->63%").
- Correction fn: `src/engine/event_reactor_adapter.py:3487` `_maybe_apply_edli_bias_correction`
  — subtraction at line ~3559 `corrected = np.asarray(members) - eff_native`.
- Call site: `src/engine/event_reactor_adapter.py:3313` (members corrected BEFORE `_snapshot_p_raw` at 3318).
- Store read: `src/calibration/ens_bias_repo.py:614` `read_bias_model` (exact live args resolve the row).
- Sign convention: `effective_bias_c = mean(forecast - observed)`; cold forecast → negative → members warmed (docstring 3497-3502).

## Reconciliation — Tokyo paradox (4°C cold)

NOT wrong-sign, NOT inconsistent. The correction direction is identical and
correct for all cities (all warm a cold forecast):
- Tokyo JJA high `effective_bias_c = -3.447` → correction warms Tokyo +3.45 °C.
- Taipei JJA high `effective_bias_c = -1.803` → warms +1.80 °C.
- Singapore JJA high `-1.584` → warms +1.58 °C.
The "Tokyo still 4°C cold vs ECMWF deterministic" observation is consistent
with EITHER (a) the observation being read against the RAW (pre-correction)
forecast, OR (b) Tokyo's true cold bias exceeding the stored −3.45 (under-warmed).
Both leave the layer's sign correct. There is no inconsistent / wrong-sign
application — every city warms.

## Predictive-error / scale layer — REFUTED as cause

`src/calibration/ens_error_model.py` (the `T_draw = member − λ·bias + N(0, total_residual_sd)`
location+scale+SNR-gate layer) is **NOT wired into the live reactor p_raw path** —
zero refs to ens_error_model / predictive_error / residual_sd / total_residual_sd
in `event_reactor_adapter.py`. The live warm shift comes solely from the flat
mean-bias subtraction, NOT from variance widening or the MC predictive-error term.

## Operator-memory reconciliation

The prior note ("bias-corrections OFF / weight_live=0.0, ensemble COLD-biased")
is STALE. As of 2026-05-31 the A4 path was flipped ON
(`edli_bias_correction_enabled=true`) and the Singapore VERIFIED row carries
`weight_live=1.0`. So "WARM shift surprising" resolves: it is the cold-bias
correction now ACTIVE, doing exactly what it was activated to do (warm the
cold forecast). Whether +1.58 °C is the RIGHT magnitude vs current truth is the
open question — but the layer, sign, and magnitude are identified.
