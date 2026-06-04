<!--
Created: 2026-06-03
Last audited: 2026-06-03
Authority basis: D2 bias-family unify / wiring verdict 2026-06-03
-->

# D2 bias-family entry/exit unify — BEFORE / AFTER (read-only, operator review)

**Flag:** `feature_flags.exit_bias_family_unify_enabled` — default **FALSE (shadow)**.
**Live change when OFF:** ZERO (regression-proven: 3-failed/13-passed identical with and
without this branch on the existing monitor + FT-wiring suites; the 3 failures pre-exist on
base `98519b6125` and are unrelated FT-wiring fixture drift).

This document is the BEFORE/AFTER the flag-ON behavior would produce on the EXIT/MONITOR and
cycle-evaluator FT read sites. Generated read-only from the live world DB
(`state/zeus-world.db`, `mode=ro`) on 2026-06-03. No DB was written, no live flag flipped.

## What changes when the flag is ON

| Path | TODAY (flag OFF) | WITH flag ON |
|---|---|---|
| EDLI reactor ENTRY | `edli_per_city_v1` bias-shift + identity-Platt (LIVE; `edli_bias_correction_enabled=true`) | unchanged |
| Cycle-evaluator FT entry (`evaluator.py`) | `full_transport_v1` (0 rows) gated by `full_transport_live_enabled=false` → **plain p_raw + real Platt (uncorrected)** | `edli_per_city_v1` bias-shift + **identity-Platt** → matches reactor entry |
| EXIT monitor (`monitor_refresh.py`) | `full_transport_v1` (0 rows) gated by `full_transport_live_enabled=false` → **plain p_raw + real Platt (uncorrected)** | `edli_per_city_v1` bias-shift + **identity-Platt** → matches reactor entry |

So the flag closes the D2 asymmetry: EXIT/MONITOR belief stops being uncorrected-while-entry-
is-corrected, and the permanently-dead `full_transport_v1` exit route is bypassed.

## Coverage of the corrected EXIT (live DB, today)

- `edli_per_city_v1` VERIFIED rows: **71** — all with `weight_live=1.0` (all apply).
- Distinct cities that gain a corrected EXIT/evaluator read: **50** (was 0 — 100% were a
  0-row miss because the legacy ft read used `month=0` + computed `lead_bucket` while the
  stored rows are `month∈{5,6}`, `lead_bucket='LEGACY_POOLED'`).
- Bias direction: 60/71 rows have negative `effective_bias_c` (cold forecast) → members are
  **warmed** on exit (member shift = `-effective_bias_c`); 11 rows positive → members cooled.

## Per-city EXIT belief shift for the LIVE target month (June, month=6)

`member_shift_native` is what is subtracted-as-correction from the member extrema before
p_raw on the exit/monitor path. `+` = exit forecast warmed vs today's uncorrected exit.
F-settled cities (San Francisco, Seattle) show the ×1.8 native-unit conversion.

| City | metric | eff_bias_c (°C) | member_shift (native) | unit | exit warms/cools |
|---|---|---|---|---|---|
| Tokyo | high | -4.847 | +4.847 °C | C | warms (largest) |
| San Francisco | high | -3.984 | **+7.171 °F** (×1.8) | F | warms |
| Guangzhou | high | -3.673 | +3.673 °C | C | warms |
| Mexico City | high | -3.586 | +3.586 °C | C | warms |
| Tel Aviv | high | -3.336 | +3.336 °C | C | warms |
| Panama City | high | -3.131 | +3.131 °C | C | warms |
| Kuala Lumpur | high | -3.109 | +3.109 °C | C | warms |
| Paris | high | -2.945 | +2.945 °C | C | warms |
| Ankara | high | -2.817 | +2.817 °C | C | warms |
| Munich | high | -2.804 | +2.804 °C | C | warms |
| Denver | high | -2.522 | +2.522 °C | C | warms |
| Miami | high | -2.489 | +2.489 °C | C | warms |
| NYC | high | -2.353 | +2.353 °C | C | warms |
| Sao Paulo | high | -2.314 | +2.314 °C | C | warms |
| Wellington | high | -2.273 | +2.273 °C | C | warms |
| Manila | high | -2.038 | +2.038 °C | C | warms |
| Shanghai | high | -1.815 | +1.815 °C | C | warms |
| Taipei | high | -1.793 | +1.793 °C | C | warms |
| Toronto | high | -1.752 | +1.752 °C | C | warms |
| Los Angeles | high | -1.747 | +1.747 °C | C | warms |
| Paris | low | -1.720 | +1.720 °C | C | warms |
| Singapore | high | -1.703 | +1.703 °C | C | warms |
| Houston | high | -1.683 | +1.683 °C | C | warms |
| Beijing | high | -1.581 | +1.581 °C | C | warms |
| NYC | low | -1.524 | +1.524 °C | C | warms |
| Seattle | high | -1.256 | **+2.261 °F** (×1.8) | F | warms |
| Shenzhen | high | -1.218 | +1.218 °C | C | warms |
| Milan | high | -1.128 | +1.128 °C | C | warms |
| London | high | -1.096 | +1.096 °C | C | warms |
| Moscow | high | -0.902 | +0.902 °C | C | warms |
| Chicago | high | -0.808 | +0.808 °C | C | warms |
| Shanghai | low | -0.780 | +0.780 °C | C | warms |
| Qingdao | high | -0.609 | +0.609 °C | C | warms |
| London | low | -0.588 | +0.588 °C | C | warms |
| Istanbul | high | -0.536 | +0.536 °C | C | warms |
| Miami | low | -0.324 | +0.324 °C | C | warms |
| Seoul | low | -0.312 | +0.312 °C | C | warms |
| Tokyo | low | -0.304 | +0.304 °C | C | warms |
| Helsinki | high | -0.288 | +0.288 °C | C | warms |
| Madrid | high | -0.271 | +0.271 °C | C | warms |
| Busan | high | -0.179 | +0.179 °C | C | warms |
| Seoul | high | +0.131 | -0.131 °C | C | cools |
| Buenos Aires | high | +0.095 | -0.095 °C | C | cools |
| Wuhan | high | +0.076 | -0.076 °C | C | cools |
| Warsaw | high | +0.000 | -0.000 °C | C | ~none |
| Chongqing | high | -0.000 | +0.000 °C | C | ~none |
| Amsterdam | high | -1.318 | +1.318 °C | C | warms |
| Atlanta | high | -1.734 | +1.734 °C | C | warms |
| Austin | high | -0.747 | +0.747 °C | C | warms |
| Cape Town | high | -0.950 | +0.950 °C | C | warms |
| Chengdu | high | +1.319 | -1.319 °C | C | cools |
| Jeddah | high | +1.713 | -1.713 °C | C | cools |
| Karachi | high | +1.069 | -1.069 °C | C | cools |
| Lucknow | high | +1.143 | -1.143 °C | C | cools |

(May, month=5 rows exist for Dallas/Jakarta/Lagos/SF/Seattle/Seoul/Shanghai/Shenzhen/Singapore/
Sao Paulo/Taipei/Tel Aviv/Tokyo/Toronto/Warsaw/Wellington/Wuhan — used for May targets; the
live exits today target June.)

## Belief-delta interpretation

The shift is in member-extrema space (°C/°F). The resulting **probability** delta depends on
where the bin sits relative to the (shifted) ensemble and the bin width — a +2 °C warm-shift
moves probability mass up-temperature, raising P(higher bins) and lowering P(lower bins). The
exact per-bin p_cal delta is position-dependent and is NOT recomputed here (it requires live
member arrays per held position). The magnitude table above is the upstream driver; large-shift
cities (Tokyo, SF, Guangzhou, Mexico City, Tel Aviv) are where the exit belief moves most.

## Why this is the SAME treatment as entry (not a new asymmetry)

The flag-ON exit/evaluator path subtracts the **same** `effective_bias_c` shift the reactor
entry subtracts (`_maybe_apply_edli_bias_correction`), with the **same** unit conversion, and
then uses **identity-Platt** (the A4 train/serve lockstep — Platt models were fit on the
UNCORRECTED p_raw domain). It deliberately does NOT use the legacy `full_transport_v1`
mechanism (which residual-widens the MC AND runs real Platt) — that would have been a half-fix
introducing a different entry/exit asymmetry. See the A4-coupling section of the D2 report.

## Unshadow gate (operator)

Same gate as `edli_bias_correction_enabled`: per-city exit-vs-entry belief-delta review (this
doc) + settled-truth confirmation the corrected EXIT belief tracks reality. Do NOT promote on
row-democracy alone; confirm the corrected exit does not flip a held position the wrong way on
the large-shift cities first.
