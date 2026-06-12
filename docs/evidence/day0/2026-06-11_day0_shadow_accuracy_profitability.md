# Day0 Shadow Receipts — Settlement-Graded Accuracy & Profitability Study

<!-- Created: 2026-06-11 -->
<!-- Last reused/audited: 2026-06-11 -->
<!-- Authority basis: operator directive 2026-06-11 ~08:25Z — analyze accumulated day0
     shadow receipts for ACCURACY and PROFITABILITY, settlement-graded; produce a
     promotion-readiness verdict against the settings-note bar (>51% after-cost win-rate
     on ~150-270 settled samples). Never-submit guarantee pinned by
     tests/engine/test_day0_shadow_receipt_enrichment.py. Settlement semantics law:
     src/contracts/settlement_semantics.py. Fee law: 0.05 × p × (1−p) × shares
     (28/28 reconciled, docs/evidence/reconciliation/2026-06-10_wallet_history_reconcile.md). -->
<!-- Method: PURE READ-ONLY. Live DBs opened mode=ro only. No daemon/flag/src-live edits.
     Live trading was running during capture (receipt counts drift upward between queries) —
     all figures are a SNAPSHOT as of the timestamps below. -->

## Snapshot provenance

| Surface | Path | Opened | As-of |
|---|---|---|---|
| day0 receipts | `state/zeus-world.db` → `no_trade_regret_events` | `mode=ro` | created_at ∈ [2026-06-10T00:33Z, 2026-06-11T08:24Z] |
| richer no-submit receipts | `state/zeus-world.db` → `edli_no_submit_receipts` | `mode=ro` | (verified: holds ZERO day0 rows) |
| settlement truth | `state/zeus-forecasts.db` → `settlement_outcomes` (authority='VERIFIED') | `mode=ro` | 6 VERIFIED cells for 2026-06-10; **0** for 2026-06-11 |
| settlement truth (alt) | `state/zeus_trades.db` → `settlements` | `mode=ro` | 0 rows (empty; canonical truth is `settlement_outcomes`) |

Day0 receipt count drifted 1763 → 1783 → 1785 across queries (live writer active). Figures below use the 1785-row terminal snapshot unless noted.

---

## TL;DR verdict

**INSUFFICIENT — no gradeable settled day0 sample exists. Promotion CANNOT be evaluated yet.**

The binding constraint is **not** sample size against the 150–270 bar; it is that **the day0 enrichment pipeline is not producing analyzable candidates**, and the candidates that exist do not intersect the settled set.

Two disjoint, both-empty populations:

1. **1776 of 1785** day0 receipts are **bare scope-gate receipts** — `direction`, `q_live`, `bin_label`, `trade_score`, and the ENTIRE profitability/grading layer (`hypothetical_fill_price`, `c_fee_adjusted`, `would_have_won`, `would_have_filled`, `later_outcome`, `hypothetical_order_type/status`) are **NULL**. 92 of these fall in a VERIFIED-settled cell, but with **zero decision content** there is nothing to grade.
2. **9 of 1785** receipts carry a candidate (`direction=buy_no`, `q_live`, `q_lcb`, `trade_score`). **All 9 target 2026-06-11, which has 0 VERIFIED settlements**, so none are gradeable. They are also **degenerate**: `q_live≈1.0, q_lcb=0.0, trade_score=0.0, native_quote_available=0` — not viable trade candidates.

**Gradeable intersection (candidate-bearing ∩ VERIFIED-settled) = 0.** Accuracy, calibration, certified-bounds honesty, and profitability are all **non-derivable** from the current receipt corpus.

| Metric | Value | n (settled, gradeable) | Verdict | Binding constraint |
|---|---|---|---|---|
| Per-receipt would-have-won accuracy | — | **0** | INSUFFICIENT | No receipt is both candidate-bearing and settled; `would_have_won` 0% populated |
| Probability calibration (q deciles) | — | **0** | INSUFFICIENT | Only 9 q values exist, all ≈1.0 (degenerate), none settled |
| Certified-bounds honesty (settled-bin q∈[lcb,ucb]) | — | **0** | INSUFFICIENT | q_lcb=0.0 on all 9; no settled join |
| After-cost win-rate (>51% bar) | — | **0** | INSUFFICIENT | `hypothetical_fill_price` & `c_fee_adjusted` 0% populated; no fill simulation persisted |
| Total simulated PnL | — | **0** | INSUFFICIENT | Same — no fill/cost layer |
| buy_no vs buy_yes split | — | **0** (9 buy_no, 0 buy_yes, none settled) | INSUFFICIENT | No settled candidate of either side |
| Intraday hours-to-settlement curve | — | **0** | INSUFFICIENT | No settled candidate to place on the curve |
| Sample sufficiency vs 150–270 bar | 0 / 150 | **0** | INSUFFICIENT | Pipeline-coverage failure dominates; sample-count question is moot until coverage is fixed |

---

## 1. INVENTORY

### 1.1 Counts

- **Total day0 receipts** (`rejection_reason='DAY0_SCOPE_SHADOW_ONLY'`): **1785**
- **rejection_stage**: `EXECUTOR_EXPRESSIBILITY` = 1765 (bare scope-gate); `TRADE_SCORE` = 20 (pipeline actually ran)
- **created_at range**: 2026-06-10T00:33Z … 2026-06-11T08:24Z

### 1.2 By target_date

| target_date | n | settled? |
|---|---|---|
| 2026-06-09 | 35 | partially (lead-late stragglers) |
| 2026-06-10 | 1166 | only 6 VERIFIED cells exist for this date |
| 2026-06-11 | 582 | **0 VERIFIED** (Asia/Pacific not yet in `settlement_outcomes`) |

### 1.3 Direction / metric

- direction: `NULL` = 1774, `buy_no` = 9, **`buy_yes` = 0**
- metric: high = 1560, low = 223
- distinct `family_id` carried = **20** (the rest NULL)
- top cities: Tokyo 85, Shanghai 80, London 77, Paris 75, NYC 56, Miami 56 (broad city coverage; the receipts ARE being emitted per-family, they just carry no decision content)

### 1.4 Enrichment coverage census (ALL 1785 rows)

| Column | Non-NULL | % |
|---|---|---|
| causal_snapshot_id | 1785 | 100% |
| q_live | 9 | 0.50% |
| q_lcb_5pct | 9 | 0.50% |
| p_fill_lcb | 9 | 0.50% |
| trade_score | 9 | 0.50% |
| native_quote_available | 9 | 0.50% |
| executable_snapshot_id | 9 | 0.50% |
| direction | 9 | 0.50% |
| bin_label | 9 | 0.50% |
| **c_fee_adjusted** | **0** | **0%** |
| **c_cost_95pct** | **0** | **0%** |
| **hypothetical_order_type** | **0** | **0%** |
| **hypothetical_fill_status** | **0** | **0%** |
| **hypothetical_fill_price** | **0** | **0%** |
| **later_outcome** | **0** | **0%** |
| **would_have_won** | **0** | **0%** |
| **would_have_filled** | **0** | **0%** |

**The profitability and grading layer is 0% populated.** Only the causal snapshot id is universal. The shadow comparator's own truth source (`edli_no_submit_receipts`) holds **zero** day0 rows — day0 receipts write exclusively to `no_trade_regret_events`.

### 1.5 Why coverage is near-zero (root cause)

`day0_remaining_day_q_enabled = True` is set in `config/settings.json` (edli_v1, operator note 2026-06-10). But the q-population path in `src/engine/event_reactor_adapter.py:9588` is doubly gated:

```
if family.event_type == "DAY0_EXTREME_UPDATED" and _day0_remaining_day_q_enabled():
    _day0_rd_members = _day0_remaining_day_members(...)   # returns None unless fresh
                                                          # day0_hourly_vectors persisted
```

Only `DAY0_EXTREME_UPDATED` families with a freshly-persisted high-res remaining-day hourly vector reach the q-construction block. The overwhelming majority of day0 receipts are emitted at the **scope-gate boundary before any pipeline proof runs** (stage `EXECUTOR_EXPRESSIBILITY`, 1765/1785), so they carry no candidate. The 20 that ran the pipeline (`TRADE_SCORE` stage) produced only 9 with a non-NULL q — and those 9 are degenerate (below).

**The `hypothetical_fill_*` / `would_have_won` / `later_outcome` columns appear to have NO writer running at all** on the day0 lane (0/1785). These are the exact fields the directive's profitability and accuracy analyses require. Their absence is the dominant blocker — it is not a sample-size problem, it is a missing-enrichment-job problem.

---

## 2. ACCURACY — NON-DERIVABLE

### (a) Per-receipt would-have-won grading

Requires receipts that are (i) candidate-bearing AND (ii) in a VERIFIED-settled cell.

Cross-join (VERIFIED `settlement_outcomes` × day0 receipts):
- day0 receipts in a VERIFIED-settled cell: **92**
- …of which carry `direction`/candidate: **0**
- candidate-bearing receipts (9) in a settled cell: **0** (all 9 target 06-11, 0 settlements)

**Gradeable n = 0.** No would-have-won grade can be computed. Cross-check against the receipts' own `would_have_won` is also impossible — that column is 0% populated, so the "flag grading-pipeline disagreements" check has no inputs (and, separately, indicates the grading writer is not running).

### (b) Probability calibration (q deciles vs realized win frequency)

The q corpus is 9 values, all ≈ 1.0:

| city | metric | q_live | q_lcb | trade_score | native_quote_available | settled? |
|---|---|---|---|---|---|---|
| Beijing | high | 0.999999999 | 0.0 | 0.0 | 0 | No |
| Shanghai | low | 1.0 | 0.0 | 0.0 | 0 | No |
| Singapore | high | 1.0 | 0.0 | 0.0 | 0 | No |
| Qingdao | high | 0.999999999 | 0.0 | 0.0 | 0 | No |
| Tokyo | low | 1.0 | 0.0 | 0.0 | 0 | No |
| Singapore | high | 1.0 | 0.0 | 0.0 | 0 | No |
| Paris | high | 0.999999999 | 0.0 | 0.0 | 0 | No |
| Istanbul | high | 1.0 | 0.0 | 0.0 | 0 | No |
| Shenzhen | high | 1.0 | 0.0 | 0.0 | 0 | No |

There are no deciles to bucket (one degenerate point mass at q≈1.0), and none are settled. **Non-derivable.** Note `bin_label` holds the full market QUESTION TEXT (e.g. "Will the highest temperature in Beijing be 37°C or higher on June 11?"), not a parsed temperature bin — a buy_no on a tail/extreme question with q≈1.0 (model near-certain NO wins) is the favorite-longshot pattern, but with `trade_score=0.0` and `native_quote_available=0` it is not an executable opportunity.

### (c) Certified-bounds honesty on the day0 lane

Requires the settled-bin q within `[q_lcb, q_ucb]`. All 9 carry `q_lcb=0.0` (degenerate floor), no `q_ucb` field is persisted on this table, and none are settled. **Non-derivable.**

---

## 3. PROFITABILITY — NON-DERIVABLE

The simulation the directive specifies (entry at `hypothetical_fill_price` else snapshot ask, taker fee `0.05·p·(1−p)·shares`, $1/contract or $5–15 envelope-midpoint stake, after-cost PnL per receipt) needs at minimum:
- a candidate side (`direction`) — present on 9/1785;
- a fill price (`hypothetical_fill_price`) — present on **0/1785**;
- a settled outcome (`later_outcome` or a settlement join) — `later_outcome` present on **0/1785**, and the 9 candidate-bearing receipts have **0** settled cells.

With `hypothetical_fill_price` and `c_fee_adjusted` at 0% population and zero settled candidates, **no after-cost PnL, win-rate, by-direction split, price-band split, or intraday hours-to-settlement curve can be produced.** Every profitability sub-analysis returns n=0.

The directive's prior that **buy_yes is a −EV leak** cannot be tested on day0 here: there are **0 buy_yes** day0 candidates in the corpus (all 9 are buy_no). Whether day0 changes the buy_yes verdict is **undetermined**.

---

## 4. SAMPLE SUFFICIENCY

The promotion bar is >51% after-cost win-rate on ~150–270 settled samples. The math-first framing (per `docs/evidence/cycle_phase_qualification/2026-06-11_06z18z_offline_study.md`):

- **What is derivable now:** nothing quantitative about day0 accuracy or economics. The corpus has 0 candidate-bearing settled receipts and a 0%-populated profitability layer.
- **What is NOT derivable, and why:** the 150–270 bar is a question about *statistical power over settled trades*. We are not power-limited — we are **coverage-limited at the pipeline**. Until the enrichment job (a) populates `hypothetical_fill_price`/`c_fee_adjusted` and (b) backfills `would_have_won`/`later_outcome` for settled cells, additional calendar time produces more *bare* receipts, not more *gradeable* ones. The settled-sample counter stays pinned at 0 regardless of how long receipts accumulate.
- **Timing context:** the flag flipped ON ~2026-06-10T23:0xZ. The 9 q-bearing receipts cluster around and after that flip (16:07Z, 18:25/18:25Z, 21:47Z, 23:46–23:47Z on 06-10; 03:17Z on 06-11) — consistent with the flag being live but the `DAY0_EXTREME_UPDATED`+fresh-hourly-vector gate firing only rarely. Even with more time, the structural gate and the missing fill/outcome writer cap the gradeable yield near zero.

**Distance to bar: 0 of 150 minimum settled samples (0%).**

---

## 5. VERDICT

**PROMOTION VERDICT: NOT-YET / INSUFFICIENT — do not promote.** No evidence licenses promotion; equally, no evidence condemns the day0 hypothesis. The lane is **un-evaluable in its current state.**

The promotion bar is unmet not because day0 lost, but because **the experiment is not yet instrumented to be graded.** Three concrete pipeline gaps, in priority order, gate any future verdict:

1. **(blocking) No fill/outcome enrichment writer on the day0 lane.** `hypothetical_fill_price`, `hypothetical_fill_status`, `hypothetical_order_type`, `c_fee_adjusted`, `c_cost_95pct`, `would_have_won`, `would_have_filled`, `later_outcome` are 0% populated across all 1785 receipts. Without these, profitability and accuracy are undefined. This is the dominant fix.
2. **(blocking) Candidate content reaches only 9/1785 receipts.** The `DAY0_EXTREME_UPDATED` + fresh-`day0_hourly_vectors` double-gate means almost no day0 family produces a q. Either the gate is too tight or the hourly-vector lane is not feeding most families. Even buy_no candidates that DO fire are degenerate (q≈1.0, trade_score=0, no native quote).
3. **(downstream) Settlement join not yet exercised for day0.** 92 receipts already sit in VERIFIED-settled 06-10 cells; once (1) and (2) populate decision+fill content, those become immediately gradeable. The settlement truth surface (`settlement_outcomes`, authority='VERIFIED') is present and queryable — it is not the bottleneck.

**Recommendation to operator:** keep day0 SHADOW_ONLY (the never-submit guarantee is intact and correctly pinned). Before re-requesting a verdict, the day0 enrichment must (a) populate the hypothetical-fill + settled-outcome columns and (b) widen candidate coverage beyond the 9 degenerate buy_no rows. A re-run of this study after those land — ideally batch-backfilling `would_have_won`/`later_outcome` against the 92 already-settled 06-10 cells — is the cheap path to the first real day0 sample.

---

## Honest limitations

- **Snapshot, not stream.** Live trading wrote receipts during capture; counts drifted 1763→1785. Figures are point-in-time.
- **Shadow ≠ real fills.** Even once `hypothetical_fill_price` is populated, shadow receipts ASSUME a fill at the captured ask. Real books may not offer that depth/price; day0 is the most liquid class but `native_quote_available=0` on all 9 current candidates is a warning that the assumed-fill optimism could be large. Any future after-cost win-rate must be discounted for shadow-vs-real slippage before it clears a live-promotion bar.
- **Settlement recency.** 2026-06-11 (Asia/Pacific) had **0** VERIFIED `settlement_outcomes` at snapshot time despite the directive's expectation they would be settled — settlement ingestion for 06-11 lags the receipt accumulation. This further shrinks any near-term gradeable set.
- **One truth table consulted as canonical.** `settlement_outcomes` (zeus-forecasts, VERIFIED) is the only populated settlement surface; `settlements` (zeus_trades) is empty. HK floor/preimage semantics (`settlement_preimage_offsets`) were not exercised because no HK day0 candidate is gradeable; the law was respected by not hand-rolling any rounding.
