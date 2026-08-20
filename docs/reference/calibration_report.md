# Zeus calibration: frozen decisions versus settlement

## Read this first

**Current verdict: adverse.** Across **587** settled positions with a resolvable frozen decision probability, the Brier skill score against the observed-base-rate forecast is **-0.155**. Because it is negative, these probabilities performed worse in this sample than always predicting the **49.4%** base rate.

The score uses the probability recorded before the outcome and a verified settlement; missing probabilities are not imputed. **587/902** settled positions are scoreable. **315** are excluded for missing decision-probability evidence, including **263** classified `UNATTRIBUTABLE_Q_MISSING`; that coverage gap is itself an adverse data-quality result.

**Supports:** calibration of the displayed sample, subject to the shown intervals and the **n ≥ 30** cell floor.

**Does not support:** durable alpha, strategy returns, or firm conclusions for thin cells.

**Data through:** `2026-08-20T21:05:00.222329+00:00` · **Generated:** `2026-08-20T22:38:58.041735+00:00`

Measurement unit: frozen decision probability × verified settlement outcome.

<details>
<summary>Generation and provenance</summary>

Generator: `python3 scripts/generate_calibration_report.py`

Every row in this report comes from `settlement_attribution` (`src/analysis/settlement_skill_attribution.py`), the SOLE table whose `won` / `settled_in_bin` / `settled_value` columns are populated by `grade_receipt()` against a **VERIFIED** `settlement_outcomes` row, and whose `q_live` is the FROZEN decision-time value read from an immutable VERIFIED decision certificate — never a posterior reconstructed after the fact. This is the exact ground-truth law `loop/LEDGER.yaml` states: *"ground truth = decision certificate x real settlement join ONLY"*. This report never reads `forecast_posteriors` or any other table as if it were a settled outcome — the failure mode of a prior calibration store built on settlement midpoints backfilled as forecasts.

- Settled positions loaded: **902**, settlement window `2026-06-09T14:00:29.957276+00:00 .. 2026-08-20T21:05:00.222329+00:00`.
- Of those, **587** (65.1%) carry a resolvable decision-time predicted probability (`q_live`); the remaining 315 have no resolvable immutable decision-q certificate and are excluded from every calibration number below (never imputed) — they are counted, not guessed.
- Thin-sample threshold: **n < 30** per cell, `loop/LEDGER.yaml`'s own rule ("Statistical conclusions require min_n=30 per cell before a status can move off 'open'"). Every table below flags cells under that floor rather than hiding them.

</details>

## Reliability diagram

![Reliability diagram](calibration_reliability.svg)

n = 587 settled positions with a resolvable predicted probability. Dot area is proportional to bin count; the vertical bar is the 95% Wilson interval on the observed win rate; a red dot marks a bin under the n<30 floor.

| bin | n | mean predicted | observed win rate | 95% Wilson interval | flag |
|---|---:|---:|---:|---|---|
| [0.0, 0.1) | 9 | 5.8% | 0.0% (0/9) | [0.0%, 29.9%] | thin (n<30) |
| [0.1, 0.2) | 14 | 14.8% | 0.0% (0/14) | [0.0%, 21.5%] | thin (n<30) |
| [0.2, 0.3) | 13 | 25.6% | 15.4% (2/13) | [4.3%, 42.2%] | thin (n<30) |
| [0.3, 0.4) | 26 | 35.1% | 11.5% (3/26) | [4.0%, 29.0%] | thin (n<30) |
| [0.4, 0.5) | 22 | 44.8% | 13.6% (3/22) | [4.7%, 33.3%] | thin (n<30) |
| [0.5, 0.6) | 20 | 54.3% | 30.0% (6/20) | [14.5%, 51.9%] | thin (n<30) |
| [0.6, 0.7) | 27 | 65.3% | 22.2% (6/27) | [10.6%, 40.8%] | thin (n<30) |
| [0.7, 0.8) | 89 | 75.8% | 49.4% (44/89) | [39.3%, 59.6%] |  |
| [0.8, 0.9) | 200 | 85.4% | 56.0% (112/200) | [49.1%, 62.7%] |  |
| [0.9, 1.0) | 167 | 97.6% | 68.3% (114/167) | [60.9%, 74.8%] |  |

## Decomposition — reliability vs resolution vs base rate

Murphy (1973) two-term decomposition of the Brier score: `Brier = reliability - resolution + uncertainty` (reliability/resolution computed on the 10 bins above — an expected small residual against the exact per-observation Brier is the discretization cost of binning).

| quantity | value | reads as |
|---|---:|---|
| n | 587 | settled positions with a resolvable predicted probability |
| base rate (uncertainty term) | 49.4% | fraction of settled positions that won |
| reliability | 0.0819 | miscalibration — **lower is better**, 0 = perfectly calibrated |
| resolution | 0.0396 | informativeness — **higher is better**, 0 = no better than the base rate |
| uncertainty | 0.2500 | irreducible variance of a coin at the base rate, `p(1-p)` |
| Brier score | 0.2887 | mean squared error of the predicted probability, lower is better |
| Brier skill score vs base rate | -0.155 | `1 - Brier/uncertainty` — positive beats always guessing the base rate, negative is worse than that |

**The pooled Brier skill score is negative (-0.155).** Across all 587 settled positions with a resolvable predicted probability — including `STALE_DECISION` rows, whose decision posterior was, by definition, already outdated — the stated probability is a worse predictor than simply guessing the 49.4% base rate. This is a real finding, not an artifact of the calculation; see the attribution-class cut below for where it concentrates.

## Cut: by side

| side | n (settled) | n (predicted-prob resolvable) | mean predicted | observed win rate | 95% Wilson interval | flag |
|---|---:|---:|---:|---:|---|---|
| buy_yes | 236 | 142 | 49.1% | 21.1% | [15.2%, 28.6%] |  |
| buy_no | 666 | 445 | 86.5% | 58.4% | [53.8%, 62.9%] |  |
| unknown | 0 | 0 | n/a | n/a | n/a | no resolvable predicted probability |

## Cut: by strategy

| strategy_key | n (settled) | n (predicted-prob resolvable) | mean predicted | observed win rate | 95% Wilson interval | flag |
|---|---:|---:|---:|---:|---|---|
| center_buy | 21 | 16 | 20.3% | 6.2% | [1.1%, 28.3%] | thin (n<30) |
| chain_only_reconciliation | 32 | 25 | 48.6% | 0.0% | [0.0%, 13.3%] | thin (n<30) |
| day0_nowcast_entry | 221 | 140 | 80.7% | 45.0% | [37.0%, 53.3%] |  |
| forecast_qkernel_entry | 404 | 282 | 77.9% | 52.5% | [46.7%, 58.2%] |  |
| opening_inertia | 151 | 110 | 84.4% | 62.7% | [53.4%, 71.2%] |  |
| settlement_capture | 21 | 14 | 97.9% | 64.3% | [38.8%, 83.7%] | thin (n<30) |
| unknown (no position_current match) | 52 | 0 | n/a | n/a | n/a | no resolvable predicted probability |

## Cut: by lead time (entry to settlement)

817/902 settled positions have a resolvable entry timestamp (`trades.position_events`, immutable append-only entry events); 85 predate that event log and are excluded from this cut (counted, not guessed).

| lead time | n (settled) | n (predicted-prob resolvable) | mean predicted | observed win rate | 95% Wilson interval | flag |
|---|---:|---:|---:|---:|---|---|
| <24h | 13 | 7 | 88.2% | 28.6% | [8.2%, 64.1%] | thin (n<30) |
| 24-72h (1-3d) | 698 | 484 | 77.8% | 49.6% | [45.2%, 54.0%] |  |
| 72-168h (3-7d) | 42 | 32 | 77.4% | 53.1% | [36.4%, 69.1%] |  |
| 168h+ (7d+) | 64 | 38 | 89.6% | 78.9% | [63.7%, 88.9%] |  |

## Cut: by attribution class — the interesting one

The six-class post-settlement grader (`settlement_skill_attribution.py`) explains why a trade won or lost; it does not filter this reliability sample, which includes every causally eligible decision with a frozen `q_live` joined to a verified settlement. Four of the six categories are outcome-degenerate BY CONSTRUCTION (`SKILL_WIN`/`LUCKY_WIN` are defined as `won=1`, `SKILL_LOSS`/`MISCALIBRATED_LOSS` as `won=0`) — a within-category "win rate" for those four is the category's own definition restated, not a calibration statement. The useful cross-class evidence is the decision-time probability itself, certificate coverage, and sample size: a forecast-earned win should carry a HIGH predicted probability; a lucky win, by the taxonomy's own definition, is one the fresh evidence disagreed with. Any separate model-update or promotion gate is outside this report.

| attribution class | n (settled) | n (predicted-prob resolvable) | mean predicted | observed win rate | 95% Wilson interval | flag |
|---|---:|---:|---:|---:|---|---|
| SKILL_WIN | 187 | 178 | 85.5% | 100.0% | [97.9%, 100.0%] |  |
| LUCKY_WIN | 3 | 0 | n/a | n/a | n/a | no resolvable predicted probability |
| SKILL_LOSS | 117 | 114 | 51.2% | 0.0% | [0.0%, 3.3%] |  |
| MISCALIBRATED_LOSS | 89 | 89 | 85.9% | 0.0% | [0.0%, 4.1%] |  |
| STALE_DECISION | 243 | 206 | 81.4% | 54.4% | [47.5%, 61.0%] |  |
| UNATTRIBUTABLE_Q_MISSING | 263 | 0 | n/a | n/a | n/a | no resolvable predicted probability |

### Skill vs lucky — reported either way

**Cannot be computed from `q_live` in the current corpus.** `LUCKY_WIN` has n=3 settled positions and **0** of them carry a resolvable decision-q certificate — the comparison this cut exists to make (mean predicted probability: forecast-earned wins vs lucky wins) is structurally unavailable, not merely thin. `SKILL_WIN` (n=187, 178 with a resolvable `q_live`, mean predicted probability 85.5%) has no `LUCKY_WIN` counterpart to compare against today. This is itself the honest finding: report it as unresolved rather than substitute a different sample.

**`UNATTRIBUTABLE_Q_MISSING`** — n=263 settled positions (29.2% of the whole settled sample) have **no** resolvable immutable decision-q certificate at all: the system's own decision-time belief for roughly 1 in 3 settled trades is unknown, not merely unplotted. That gap in certificate coverage is a data-completeness finding in its own right, independent of what the calibration curve above shows.

## What this supports / does not support

- **n = 587/902** settled positions carry a resolvable predicted probability; every number above is scoped to that n, or to the smaller per-cut subsets shown in their own columns.
- **Supports:** a calibration read on the pooled reliability curve and decomposition above (n=587), and on any cut cell not flagged thin (n≥30).
- **Does not support:** a return/PnL claim of any kind — see the return-scope note below; a `LUCKY_WIN` calibration comparison (n=3, 0 resolvable); a firm read on any cell flagged thin above; a claim that `UNATTRIBUTABLE_Q_MISSING` positions were miscalibrated (their `q_live` is unknown, not zero or bad).
- **Return scope:** this report does not reconstruct a clean strategy return series. The account contains unrelated inventory, the sample is modest and dependent, and this pipeline grades probabilities rather than time-weighted or capital-weighted returns. The low four-figure deployment scale is disclosed as operating scope, not as a statistical reason that return uncertainty is large.

