# U0R_PROOF_RESULT — Verdict on the U0R-Bayes Settlement-Fusion Proof Package

<!--
Created: 2026-06-08
Last reused or audited: 2026-06-08
Authority basis: U0R_BAYES_SPEC.md (T1-T4, §4 algorithm, §5 PROOF PACKAGE A0..F1, 5 proof targets, acceptance
  gates, §7 antibodies). Contract: prove on VERIFIED settlement BEFORE any production wiring (measure-before-build).
Author role: INDEPENDENT SKEPTIC. Did NOT run the tournament; re-probed the headline from a clean re-implementation.
Truth: zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED' (read-through, Data phase). Walk-forward strict.
Evidence + re-probe scripts (offline, no src touched, no live-DB writes):
  /Users/leofitz/zeus/.omc/research/polyweather_eval/u0r_forecast_tournament.md   (full skeptic report)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/scripts/skeptic_recheck.py   (independent posterior recompute)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/scripts/skeptic_audits.py    (6-point audit + un-diluted re-derivation)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/u0r_q_lcb_coverage.md         (ladder, 5 targets, coverage)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/u0r_proper_scores_by_lead_metric_region.csv (181 rows)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/u0r_regional_override_ablation.csv (D1 vs D0)
  /Users/leofitz/zeus/.omc/research/polyweather_eval/u0r_alias_dedup_report.csv     (icon_seamless≡icon_d2)
Live-path cross-refs: ROUTING_REALITY_PER_REGION.md, REGIONAL_IMPROVEMENT.md, REALIGN_0_1_AUTHORITY.md.
-->

## VERDICT: PARTIAL_REGIONAL_ONLY

The **forecast core** (0.1° anchor + EB bias + decorrelated globals + Bayesian shrink-to-equal) is **PROVEN
and promotable** on proper scores. The **regional add** (ICON-D2 EU / AROME FR) is **proven in-domain but
small, lead-1-only, and not wired in live Zeus** → **shadow-only / defer**. The headline survived an
independent skeptic re-probe unchanged (one number improved). This is **measure-before-build satisfied for the
core**, not for regionals.

## Skeptic re-probe — does the headline hold? YES

Re-implemented the posterior + proper scores from the spec with **zero import** of the tournament engine:

- **3-cell recompute** (C1/D1, high/low): μ*, σ, and Brier match the stored predictions to **5 decimal
  places**. Paris-D1 correctly uses both regionals; Paris-low correctly scores against the low-settlement.
- **(2) Leakage / walk-forward:** 0/36 (city,metric,lead) groups violate MIN_TRAIN=25; earliest-cell μ
  reproduces walk-forward. No same-day leak.
- **(3) Domain:** icon_d2 used ONLY at the 5 data-present in-box cities; Moscow 0/0; AROME 0 at every
  non-France city; D1≡D0 exactly on 1727/1727 no-regional cells. No out-of-domain leak.
- **(4) Dedup:** `icon_seamless` in 0/27684 used_models lists (never enters fusion); bit-identical to icon_d2
  in all 10 cities; `icon_eu` is a distinct model (mean\|Δ\|=0.51°C). No double-count.
- **(5) Unit:** HIGH [0,36]°C / LOW [−8,20]°C (Celsius); LOW-vs-low 0/480 mismatch; 0/185 LOW>HIGH swaps.
- **(6) Bootstrap:** D1−D0 EU-indomain stable across seeds {20260608,777,13}; C1−B1 and C1−A1 reproduce.

**Skeptic addition — the headline is conservative, not inflated:** restricted to the 420 cells that truly use
a regional (dropping 163 flag-in_box-but-no-data cells), D1−D0 rises to **−0.00743 CI[−0.0141,−0.0009]** and
stays significant. The reported n=583 figure was *diluted downward*. Separately, the `in_icon_d2_box` polygon
flag is **loose** (Madrid/Warsaw/Helsinki/Istanbul flagged in_box but have no ICON-D2 data); the no-leak
guarantee rests on the data-presence gate, not the flag — **tighten the polygon before wiring**.

## 5 proof targets

| # | Target | Verdict | Key number (paired block-bootstrap, Brier unless noted) |
|---|---|---|---|
| T1 | 0.1° anchor necessary | **PASS** | C1−A1 ΔBrier **−0.0415 CI[−0.0498,−0.0326]** (n=2307); anchor-as-prior beats no-prior-equal at FR (−0.0254) and via logloss EU-wide |
| T2 | regional conditionally valuable + no out-domain leak | **PASS (lead-1)** | EU-indomain HIGH L1 D1−D0 **−0.00535 CI[−0.0102,−0.0007]** + top3 **+0.012 CI[+0.003,+0.022]**; Moscow D1−D0 = **0.0 exactly** |
| T3 | decorrelated globals reduce structural/tail error | **PASS (in-domain)** | EU-indomain B1−A1 ΔBrier **−0.0426 CI[−0.0605,−0.0237]**, Δlogloss **−0.114 CI[−0.205,−0.019]** |
| T4 | learned/covariance weights not worth >2pp | **PASS (parsimony)** | C1−B1 sub-2pp where Σ thin (EU −0.0090); >2pp ONLY at Paris (−0.0254, Σ well-estimated) |
| T5 | Bayesian shrink ≥ equal-weight, never catastrophic | **PASS** | C1 vs B1 & C1 vs C0: all regions ΔBrier ≤ 0; worst CI upper bound ≈ +0.0004; seed-stable |

## OM9-only → U0R before/after (HIGH lead=1, aggregate Brier; lower=better)

| A0 raw anchor | A1 +EB | B1 +globals | C0 +Bayes-diag | C1 +covariance | D1 +regional |
|---|---|---|---|---|---|
| 0.8073 | 0.7448 | 0.7059 | 0.7000 | 0.6868 | 0.6816 |

**A0→C1 (core) = −0.120 Brier (−14.9%); A0→D1 = −0.126 (−15.6%).** Logloss 1.806→1.411 (−21.9%); top3
0.706→0.887. The core delivers ~95% of the gain; the regional add contributes only the final −0.005. The
monotone ladder A0>A1>B1≥C0≥C1≥D1 holds at every rung.

## Which regionals to wire

| candidate | offline proof | live status | recommendation |
|---|---|---|---|
| **ICON-D2 EU** | in-domain HIGH lead-1 ΔBrier −0.00535 (−0.00743 un-diluted) + top3 +0.012, **significant** but small; 4-5 cities only; leads 2/3 absent | **does not exist in live** (no spec, no ingest, no rows, no calibration bucket) | **SHADOW-ONLY / DEFER.** Wire only after ingest+calibration path exists, polygon tightened, multi-city in-domain panel confirms, leads 2/3 excluded |
| **AROME FR** | Paris directional only (n=132/151, ns) | does not exist in live | **DEFER** — no significant standalone gain; revisit with more France cities/settlement |
| **Forecast core (anchor+EB+decorrelated globals+Bayes-shrink, C1)** | proven, large, robust, no defects; uses globally-available families | partially present (per-city EB exists but flag-OFF; soft-anchor live) | **PROMOTE** under existing shadow→veto→size-down→promote evidence gate |
| HRRR / AIFS | not in dataset | n/a | HRRR stays retracted (ΔS≤0); AIFS E0/E1 is an honest no-op — needs a column re-fetch to decide |

**Rationale:** the regional gain is sub-1pp Brier, lead-1-only, on a 4-5 city panel, against a large wiring
cost with no live calibration bucket (REGIONAL_IMPROVEMENT.md §4). The cost/benefit does not clear a
production gate today. The forecast **core** — which produces the bulk of the improvement using model families
already available — is the promotable artifact.

## Promotion-gate verdict

- **Forecast core:** meets §5 forecast-authority acceptance (beats OM9-only AND globals-equal on proper
  scores; no leakage/dedup/unit defects; q_lcb honest at operating thresholds). **PROMOTE (flag-gated, shadow
  first).**
- **Regional add:** in-domain gain + no out-domain leak proven, but not cost-justified and not wired.
  **SHADOW-ONLY / DEFER.**
- **Trading authority:** not claimed (0 live settled cells; after-cost EV on same-CLOB replay still owed —
  OBSERVE_BASELINE.md).

## Open questions

1. **LOW proof is Paris-only** (n=151, +weak London n=34); 8 EU cities have ZERO VERIFIED LOW settlement
   (EU-indomain/low collapses to n=9). LOW regional value is unproven outside Paris. Needs LOW-settlement
   backfill.
2. **Regionals at leads 2/3 untested** — physically absent in the OM previous-runs window; D1≡D0 by
   construction. If a short-horizon regional history is fetched, T2 should be re-run at leads 2/3.
3. **`in_icon_d2_box` polygon is loose** — 4 out-of-domain cities flagged in_box. Tighten to the true
   Central-Europe polygon so the domain gate and the data-presence gate agree before any wiring.
4. **AIFS E0/E1 undecided** — never carried in the dataset (honest no-op). Needs an AIFS-column re-fetch to
   evaluate the diversity/uncertainty-feature claim.
5. **Hyperparameters set a-priori, not tuned** — absolute scores are not optimal (deltas are robust). A
   nested-CV tune could lift absolute calibration but risks test leakage; out of scope for the proof.
6. **q_lcb per-region calibration deferred** — global haircut/shift under-covers at the stringent thr=0.20
   diagnostic in-domain; acceptable at the operating point but should be region-calibrated in production.
7. **Live skill still unmeasurable** — 0 of the live `replacement_0_1` posteriors join VERIFIED settlement
   (all future-dated); the core promotion rests on the same-family offline proof until June+ cells settle.
