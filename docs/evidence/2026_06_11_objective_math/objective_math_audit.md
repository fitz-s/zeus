# Trading-objective math-design audit — class-conditional verdict + redesign

Created: 2026-06-11
Authority basis: operator claim 2026-06-11 ("数学设计失误 … 反复选择风险收益都欠佳的那几个交易");
mechanism hypothesis in src/strategy/live_inference/direction_law.py:9-15;
replacement chain docs/authority/replacement_final_form_2026_06_09.md (no market term);
legacy α-fusion src/strategy/market_fusion.py:262 compute_posterior.
All DB access read-only (`mode=ro&immutable=1`).

---

## 0. Mechanism — confirmed in code (the operator's three claims)

1. **Ranking objective ≈ max(q_lcb − price).** The replacement-path q_lcb (per-bin YES
   lower bound) feeds `_per_bin_yes_q_lcb` → `utility_ranker.robust_probabilities` → π →
   ΔU (`event_reactor_adapter.py:7120-7160, 7220-7300`). The market price enters ONLY as
   the execution cost, never into the belief q. So the conservative edge peaks exactly
   where model q disagrees most with market — the operator's mechanism #1, verbatim in
   `direction_law.py:9-15`.
2. **σ_pred FLATTENS q.** `replacement_final_form_2026_06_09.md §1d/§1e`: σ_pred =
   max(1.0, sqrt(fused.sd² + σ_resid²)); q[bin] = N(μ*, σ_pred) integrated over settlement
   bins (`materializer:1180-1254` bootstrap). Live σ_pred mean = **1.76 °C** (n=161,
   range 1.00–3.50). A Normal with σ≈1.8 spreads mass off the near-center bins.
3. **No market term in the replacement chain.** `compute_posterior` (the SINGLE legacy
   blending authority, `market_fusion.py:262`) defaults to `MODEL_ONLY_POSTERIOR_MODE`
   (α=1.0, market input forbidden). The replacement materializer never calls it. Confirmed:
   raw NWP fusion vs market ⇒ max-edge = max-disagreement candidates rank first.

---

## Part A — class-conditional coverage against the settled record

Classes (per the operator's spec, computed via the repo's own
`SettlementSemantics.for_city` rounding + `direction_law.bin_forecast_distance`):
- **C1** forecast-bin NO (settled(μ) preimage lands in the bin)
- **C2** boundary-zone NO (μ within 0.25 settlement-step of a shared boundary)
- **C3** adjacent NO (raw μ-distance ≤ 1.5 steps, not C1/C2)
- **C4** far NO (the rest)

μ = `provenance_json.anchor_value_c` (fused center). Latest posterior strictly before the
target-day UTC start (no lookahead). Winner determined by settlement-value preimage
(`winning_bin` short-label cannot be string-joined to the question-string bin labels —
matched by `range_low ≤ settlement_value ≤ range_high` instead).

### A.1 — settled record (q_json point distribution, n=150 cells, target 2026-06-08..06-10)

This is the **AIFS-member-vote-soft-anchor** shape (`q_shape="aifs_member_votes_soft_anchor"`).
Market-implied YES = mean of YES/NO top bid/ask from `executable_market_snapshots`
(zeus_trades.db), latest before target-day start.

| cls | n_bins | claim qYES | mkt YES | real YES | claim qNO | mkt NO | real NO | NOgap_q (pts) | NOgap_mkt (pts) | realized NO-edge ¢ |
|----|-------:|-----------:|--------:|---------:|----------:|-------:|--------:|--------------:|----------------:|-------------------:|
| C1 | 150  | 0.056 | 0.064 | 0.067 | 0.944 | 0.936 | 0.933 | **+1.1** | +0.8 | −0.4 |
| C2 | 1    | 0.323 | 0.195 | 0.000 | 0.677 | 0.805 | 1.000 | −32.3 (n=1) | −12.8 | +19.5 |
| C3 | 60   | 0.208 | 0.205 | 0.233 | 0.792 | 0.795 | 0.767 | **+2.6** | −0.3 | −2.7 |
| C4 | 1439 | 0.090 | 0.092 | 0.088 | 0.910 | 0.908 | 0.912 | **−0.2** | +0.2 | +0.4 |

**Read:** on the settled record the OLD (AIFS) shape is **well calibrated** — C1 +1.1pt,
C3 +2.6pt, C4 −0.2pt. Model q ≈ market YES in every class. The legitimate far-NO harvest
(C4) is intact (+0.4¢). C3 leans the operator's direction but only +2.6pt / −2.7¢. The
quantitative "20–26pt overconfidence" is **NOT present** in the settled AIFS shape.

**Critical caveat — the settled record does not cover the suspect shape.** The fused-N
σ-flattened q (the shape the operator's live examples describe; `q_lcb_json` populated)
**began producing 2026-06-10 and has ZERO settled observations** (every q_lcb posterior is
for target_date ≥ 06-10 and the 06-10 ones did not join a VERIFIED settlement). So Part A
on the settled record grades the predecessor shape, not the one under indictment.

### A.2 — the suspect shape, direct test (fused-q vs market, UNSETTLED 06-11/06-12, n=161 cells)

No win-rate is available (unsettled), but the operator's mechanism is testable directly:
does the σ-flattened fused q UNDER-weight the near-center bins vs the (sharper) market?

| cls | n_bins | fused qYES | mkt YES | qYES−mkt (pts) | fused qNO | mkt NO | **NO fake-edge ¢** (qNO−mktNO) |
|----|-------:|-----------:|--------:|---------------:|----------:|-------:|-------------------------------:|
| C1 | 161  | 0.098 | 0.107 | −0.9 | 0.902 | 0.892 | +0.9 |
| C3 | 99   | 0.174 | 0.222 | **−4.8** | 0.826 | 0.778 | **+4.8** |
| C4 | 1511 | 0.085 | 0.082 | +0.2 | 0.915 | 0.918 | −0.2 |

**Read — operator directionally CONFIRMED, magnitude modest, location exact.** The fused
shape underweights the **adjacent C3 ring by −4.8pt vs market**, manufacturing a **+4.8¢
phantom NO edge** that the max(q_lcb−price) ranking chases first. C1 is small (and the
direction-law forecast-bin ban already kills it); C4 (the harvest) is untouched (market ≈
model). Robustness: **29/39 C3 cells (74%)** underweight vs market; the worst are severe —
NYC low −54pt, Miami low −49pt, Atlanta high −38pt, **Denver high −34pt** (Denver = the
exact city in the 2026-06-11 incident), Chicago high −32pt. The tail, not the mean, is the
risk.

### Verdict

- The operator is **right about the mechanism and its location**: the fused-N σ shape
  systematically fights its own adjacent-center bins and turns that disagreement into a
  first-ranked NO edge. The direction-law bans (35c5687299/5cd42a5a39) are masking this
  category, not curing it — they remove the symptom bin without making the q honest.
- The operator is **wrong about the magnitude on current data**: ~5¢ class-average, not
  12–26¢. But the **tail** (30–54pt on a third of cells) is large enough to repeatedly
  produce "worst risk-reward" first-ranked trades, exactly as reported.
- **The K=1 design failure:** the tradable q has no market term, so the new sharper-than-σ
  reality near center reads as model edge. K3 settlement-coverage can't fix it tonight
  (zero settled fused-q observations ⇒ per-class SBC is uncalibratable: SBC needs n≥30
  settled per class, we have 0). The legacy antibody — α-weighted market fusion — was
  dropped by the replacement chain and is the predecessor that already makes this category
  impossible.

---

## Part B — redesign (K=1: market-anchored tradable-q cap, single-authority α-fusion)

**Chosen: Option 1 (market-anchored blend), NOT Option 2 (per-class SBC shrink).**
Option 2 is infeasible tonight (zero settled fused-q rows to calibrate per-class bands).
Option 1 directly closes the C3 gap, reuses the existing single blending authority
(`market_fusion.compute_posterior` semantics + `edge.base_alpha` per-level), leaves C4
untouched (market ≈ model there ⇒ near-neutral), and needs no settled fused-q record.

(Implementation + flag + antibody tests documented in the commit; see §B wiring below.)
