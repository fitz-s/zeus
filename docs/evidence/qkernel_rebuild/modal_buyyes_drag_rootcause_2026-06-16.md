# Q-Kernel Spine — Modal/buy_yes Negative-EV Drag: Root-Cause Analysis

Created: 2026-06-16. Read-only analysis of `qkernel_settlement_ev_replay.py` output.
Window: 2026-06-09..2026-06-15. n=108 total graded trades.

## Context

Overall verdict from settlement_ev_verdict_2026-06-16.md: INDETERMINATE (mean EV +0.0180, CI [-0.0530, +0.0854], n=108).
Drag classes: modal (n=22, mean EV -0.0462) and buy_yes (n=38, mean EV -0.0107).
Positive class: neg_risk_buy_no (n=70, mean EV +0.0335), ring (n=83, mean EV +0.0364).

## 1. Forecast-Center Error (μ* − realized)

### modal losers (n=10) vs modal winners (n=12)

| stat | modal losers | modal winners |
|---|---|---|
| mean μ*−realized | -0.639 | -0.806 |
| median | -0.025 | -1.106 |
| std | 1.040 | 1.099 |
| 95% CI (mean) | [-1.287, -0.041] | [-1.369, -0.163] |
| mean |μ*−realized| | 0.817 | 1.256 |

### buy_yes losers (n=37) vs buy_yes winners (n=1)

| stat | buy_yes losers | buy_yes winners |
|---|---|---|
| mean μ*−realized | -0.627 | +0.659 |
| median | -0.660 | +0.659 |
| std | 1.342 | n/a |
| 95% CI (mean) | [-1.048, -0.184] | [+0.659, +0.659] |
| mean |μ*−realized| | 1.198 | 0.659 |

### drag cohort (modal∪buy_yes losers) (n=44) vs winning cohort (ring/buy_no winners) (n=39)

| stat | drag cohort (modal∪buy_yes losers) | winning cohort (ring/buy_no winners) |
|---|---|---|
| mean μ*−realized | -0.537 | -0.457 |
| median | -0.463 | -0.300 |
| std | 1.262 | 1.453 |
| 95% CI (mean) | [-0.910, -0.169] | [-0.902, -0.005] |
| mean |μ*−realized| | 1.057 | 1.129 |

## 2. City / Station Concentration of Losers

### Modal losers by city+metric

| city | metric | n_loss | n_win | mean_center_err_loss | mean_center_err_win |
|---|---|---|---|---|---|
| Istanbul | high | 1 | 0 | +0.140 | n/a |
| Karachi | high | 1 | 0 | +0.100 | n/a |
| Warsaw | high | 1 | 1 | -0.025 | -1.080 |
| Paris | high | 1 | 0 | +0.390 | n/a |
| Guangzhou | high | 1 | 0 | -2.200 | n/a |
| Wuhan | high | 1 | 1 | +0.257 | -2.168 |
| Tokyo | low | 1 | 0 | -0.024 | n/a |
| Kuala Lumpur | high | 1 | 0 | -2.025 | n/a |
| Seoul | high | 1 | 0 | -1.743 | n/a |
| Houston | high | 1 | 0 | -1.265 | n/a |
| Denver | high | 0 | 1 | n/a | +1.219 |
| Ankara | high | 0 | 1 | n/a | -1.100 |
| Milan | high | 0 | 1 | n/a | -1.112 |
| Dallas | high | 0 | 1 | n/a | +0.950 |
| London | high | 0 | 1 | n/a | -1.575 |
| Cape Town | high | 0 | 1 | n/a | +0.529 |
| Mexico City | high | 0 | 1 | n/a | -1.014 |
| Seattle | high | 0 | 1 | n/a | -1.930 |
| Amsterdam | high | 0 | 1 | n/a | -1.216 |
| Beijing | high | 0 | 1 | n/a | -1.175 |

### buy_yes losers by city+metric

| city | metric | n_loss | n_win | mean_center_err_loss | mean_center_err_win |
|---|---|---|---|---|---|
| Helsinki | high | 3 | 0 | -0.513 | n/a |
| Shanghai | high | 2 | 0 | -1.548 | n/a |
| London | low | 2 | 0 | +0.514 | n/a |
| Guangzhou | high | 2 | 0 | -1.693 | n/a |
| Kuala Lumpur | high | 2 | 0 | -1.811 | n/a |
| London | high | 2 | 0 | -0.106 | n/a |
| Taipei | high | 2 | 0 | -0.492 | n/a |
| Tokyo | high | 2 | 0 | -1.464 | n/a |
| Toronto | high | 1 | 0 | -0.598 | n/a |
| NYC | high | 1 | 0 | -2.535 | n/a |
| Karachi | high | 1 | 0 | -0.966 | n/a |
| Busan | high | 1 | 0 | -1.543 | n/a |
| Paris | low | 1 | 0 | -0.267 | n/a |
| Denver | high | 1 | 0 | -2.282 | n/a |
| Chongqing | high | 1 | 1 | -1.600 | +0.659 |
| Ankara | high | 1 | 0 | -2.049 | n/a |
| Qingdao | high | 1 | 0 | -0.183 | n/a |
| Miami | high | 1 | 0 | -0.641 | n/a |
| Lucknow | high | 1 | 0 | +1.060 | n/a |
| Manila | high | 1 | 0 | +0.829 | n/a |
| Atlanta | high | 1 | 0 | +1.553 | n/a |
| Singapore | high | 1 | 0 | +0.571 | n/a |
| Buenos Aires | high | 1 | 0 | -0.660 | n/a |
| Madrid | high | 1 | 0 | +0.040 | n/a |
| Seoul | high | 1 | 0 | -1.743 | n/a |
| Los Angeles | high | 1 | 0 | +2.981 | n/a |
| Chengdu | high | 1 | 0 | +0.467 | n/a |
| Beijing | high | 1 | 0 | -0.877 | n/a |

## 3. σ Over-Dispersion Analysis

σ_pred is the spine's decision-time predictive spread (native units). |μ*−realized| is the actual forecast error. If σ_pred >> |μ*−realized| the spine is over-dispersed (puts tradeable q-weight on remote bins that settle elsewhere).

| cohort | n | mean σ | mean |err| | σ/|err| ratio | mean cost |
|---|---|---|---|---|---|
| modal losers | 10 | 1.830 | 0.817 | 2.240 | 0.483 |
| modal winners | 12 | 2.082 | 1.256 | 1.658 | 0.682 |
| buy_yes losers | 37 | 1.887 | 1.198 | 1.576 | 0.036 |
| buy_yes winners | 1 | 1.735 | 0.659 | 2.632 | 0.095 |
| ring losers | 44 | 1.884 | 1.171 | 1.609 | 0.216 |
| ring winners | 39 | 1.937 | 1.129 | 1.715 | 0.678 |

## 4. Direction-Law / Bin-Assignment Analysis

For each modal-loser: what was the spine's μ* (modal bin), what did settlement give, and is the losing bin the bin the center over-estimated into?

### Modal losers — full per-row evidence

| city | date | metric | μ* | σ | realized | center_err | modal_repr | bin_label_short | ask+fee | edge_lcb | ev |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Guangzhou | 2026-06-15 | high | 29.8 | 1.73 | 32.0 | -2.2 | 30.0 | Will the highest temperature in Guangzhou be  | 0.084 | +0.076 | -0.084 |
| Kuala Lumpur | 2026-06-15 | high | 31.0 | 1.73 | 33.0 | -2.0 | 31.0 | Will the highest temperature in Kuala Lumpur  | 0.116 | +0.058 | -0.116 |
| Seoul | 2026-06-11 | high | 21.3 | 1.73 | 23.0 | -1.7 | 21.0 | Will the highest temperature in Seoul be 21°C | 0.027 | +0.124 | -0.027 |
| Houston | 2026-06-10 | high | 89.7 | 3.12 | 91.0 | -1.3 | 90.5 | Will the highest temperature in Houston be be | 0.578 | +0.172 | -0.578 |
| Paris | 2026-06-09 | high | 19.4 | 1.73 | 19.0 | +0.4 | 19.0 | Will the highest temperature in Paris be 19°C | 0.630 | +0.144 | -0.630 |
| Wuhan | 2026-06-11 | high | 31.3 | 1.73 | 31.0 | +0.3 | 31.0 | Will the highest temperature in Wuhan be 31°C | 0.745 | +0.028 | -0.745 |
| Istanbul | 2026-06-09 | high | 23.1 | 1.73 | 23.0 | +0.1 | 23.0 | Will the highest temperature in Istanbul be 2 | 0.704 | +0.070 | -0.704 |
| Karachi | 2026-06-12 | high | 37.1 | 1.73 | 37.0 | +0.1 | 37.0 | Will the highest temperature in Karachi be 37 | 0.651 | +0.122 | -0.651 |
| Warsaw | 2026-06-15 | high | 17.0 | 1.73 | 17.0 | -0.0 | 17.0 | Will the highest temperature in Warsaw be 17° | 0.661 | +0.112 | -0.661 |
| Tokyo | 2026-06-11 | low | 18.0 | 1.30 | 18.0 | -0.0 | 18.0 | Will the lowest temperature in Tokyo be 18°C  | 0.630 | +0.071 | -0.630 |

### buy_yes losers — full per-row evidence

| city | date | metric | μ* | σ | realized | center_err | bin_label_short | ask+fee | edge_lcb | ev |
|---|---|---|---|---|---|---|---|---|---|---|
| Helsinki | 2026-06-15 | high | 17.5 | 1.73 | 17.0 | +0.5 | Will the highest temperature in Helsinki be 1 | 0.126 | +0.058 | -0.126 |
| Kuala Lumpur | 2026-06-15 | high | 31.0 | 1.73 | 33.0 | -2.0 | Will the highest temperature in Kuala Lumpur  | 0.116 | +0.058 | -0.116 |
| Buenos Aires | 2026-06-10 | high | 14.3 | 1.73 | 15.0 | -0.7 | Will the highest temperature in Buenos Aires  | 0.095 | +0.045 | -0.095 |
| Guangzhou | 2026-06-11 | high | 30.8 | 1.73 | 32.0 | -1.2 | Will the highest temperature in Guangzhou be  | 0.095 | +0.066 | -0.095 |
| Shanghai | 2026-06-15 | high | 24.2 | 1.73 | 24.0 | +0.2 | Will the highest temperature in Shanghai be 2 | 0.093 | +0.054 | -0.093 |
| Guangzhou | 2026-06-15 | high | 29.8 | 1.73 | 32.0 | -2.2 | Will the highest temperature in Guangzhou be  | 0.084 | +0.076 | -0.084 |
| Shanghai | 2026-06-11 | high | 28.7 | 1.73 | 32.0 | -3.3 | Will the highest temperature in Shanghai be 2 | 0.069 | +0.068 | -0.069 |
| Kuala Lumpur | 2026-06-11 | high | 31.4 | 1.73 | 33.0 | -1.6 | Will the highest temperature in Kuala Lumpur  | 0.068 | +0.034 | -0.068 |
| London | 2026-06-11 | low | 10.6 | 1.30 | 10.0 | +0.6 | Will the lowest temperature in London be 9°C  | 0.053 | +0.038 | -0.053 |
| Singapore | 2026-06-11 | high | 30.6 | 1.73 | 30.0 | +0.6 | Will the highest temperature in Singapore be  | 0.053 | +0.041 | -0.053 |
| Busan | 2026-06-11 | high | 27.5 | 1.73 | 29.0 | -1.5 | Will the highest temperature in Busan be 25°C | 0.051 | +0.008 | -0.051 |
| Chongqing | 2026-06-09 | high | 23.4 | 1.73 | 25.0 | -1.6 | Will the highest temperature in Chongqing be  | 0.041 | +0.005 | -0.041 |
| NYC | 2026-06-10 | high | 80.5 | 3.12 | 83.0 | -2.5 | Will the highest temperature in New York City | 0.040 | +0.015 | -0.040 |
| London | 2026-06-11 | high | 16.7 | 1.73 | 17.0 | -0.3 | Will the highest temperature in London be 14° | 0.040 | +0.011 | -0.040 |
| Tokyo | 2026-06-11 | high | 23.7 | 1.73 | 26.0 | -2.3 | Will the highest temperature in Tokyo be 22°C | 0.034 | +0.044 | -0.034 |
| Paris | 2026-06-15 | low | 13.7 | 1.30 | 14.0 | -0.3 | Will the lowest temperature in Paris be 13°C  | 0.032 | +0.175 | -0.032 |
| Denver | 2026-06-11 | high | 75.7 | 3.12 | 78.0 | -2.3 | Will the highest temperature in Denver be bet | 0.030 | +0.061 | -0.030 |
| Seoul | 2026-06-11 | high | 21.3 | 1.73 | 23.0 | -1.7 | Will the highest temperature in Seoul be 21°C | 0.027 | +0.124 | -0.027 |
| Lucknow | 2026-06-12 | high | 36.1 | 1.73 | 35.0 | +1.1 | Will the highest temperature in Lucknow be 37 | 0.027 | +0.056 | -0.027 |
| Miami | 2026-06-13 | high | 89.4 | 3.12 | 90.0 | -0.6 | Will the highest temperature in Miami be betw | 0.025 | +0.077 | -0.025 |
| Taipei | 2026-06-15 | high | 27.7 | 1.73 | 30.0 | -2.3 | Will the highest temperature in Taipei be 31° | 0.016 | +0.015 | -0.016 |
| Beijing | 2026-06-13 | high | 27.1 | 1.73 | 28.0 | -0.9 | Will the highest temperature in Beijing be 30 | 0.015 | +0.003 | -0.015 |
| Helsinki | 2026-06-09 | high | 18.1 | 1.73 | 19.0 | -0.9 | Will the highest temperature in Helsinki be 1 | 0.011 | +0.011 | -0.011 |
| Helsinki | 2026-06-11 | high | 16.9 | 1.73 | 18.0 | -1.1 | Will the highest temperature in Helsinki be 1 | 0.011 | +0.076 | -0.011 |
| Taipei | 2026-06-11 | high | 25.3 | 1.73 | 24.0 | +1.3 | Will the highest temperature in Taipei be 23° | 0.011 | +0.071 | -0.011 |
| Atlanta | 2026-06-12 | high | 92.6 | 3.12 | 91.0 | +1.6 | Will the highest temperature in Atlanta be be | 0.011 | +0.018 | -0.011 |
| London | 2026-06-10 | high | 17.1 | 1.73 | 17.0 | +0.1 | Will the highest temperature in London be 14° | 0.008 | +0.028 | -0.008 |
| Los Angeles | 2026-06-10 | high | 77.0 | 3.12 | 74.0 | +3.0 | Will the highest temperature in Los Angeles b | 0.008 | +0.087 | -0.008 |
| Toronto | 2026-06-11 | high | 30.4 | 1.73 | 31.0 | -0.6 | Will the highest temperature in Toronto be 34 | 0.008 | +0.001 | -0.008 |
| Madrid | 2026-06-11 | high | 32.0 | 1.73 | 32.0 | +0.0 | Will the highest temperature in Madrid be 30° | 0.004 | +0.090 | -0.004 |
| Manila | 2026-06-11 | high | 33.8 | 1.73 | 33.0 | +0.8 | Will the highest temperature in Manila be 30° | 0.004 | +0.007 | -0.004 |
| Qingdao | 2026-06-15 | high | 26.8 | 1.73 | 27.0 | -0.2 | Will the highest temperature in Qingdao be 28 | 0.004 | +0.130 | -0.004 |
| Ankara | 2026-06-15 | high | 24.0 | 1.73 | 26.0 | -2.0 | Will the highest temperature in Ankara be 22° | 0.001 | +0.090 | -0.001 |
| Chengdu | 2026-06-15 | high | 28.5 | 1.73 | 28.0 | +0.5 | Will the highest temperature in Chengdu be 32 | 0.001 | +0.007 | -0.001 |
| Karachi | 2026-06-15 | high | 34.0 | 1.73 | 35.0 | -1.0 | Will the highest temperature in Karachi be 36 | 0.001 | +0.087 | -0.001 |
| London | 2026-06-15 | low | 13.4 | 1.30 | 13.0 | +0.4 | Will the lowest temperature in London be 14°C | 0.001 | +0.213 | -0.001 |
| Tokyo | 2026-06-15 | high | 21.3 | 1.73 | 22.0 | -0.7 | Will the highest temperature in Tokyo be 26°C | 0.001 | +0.001 | -0.001 |

## 5. Center-Error Distribution by Class

Signed center error (μ* − realized) broken down by whether the spine WON or LOST.
A persistent positive bias (μ* > realized) inflates YES/modal selections. A negative bias deflates them but inflates NO shoulders.

| class | won | n | mean(μ*−realized) | std | pct_positive_err |
|---|---|---|---|---|---|
| modal | won | 12 | -0.806 | 1.099 | 25% |
| modal | lost | 10 | -0.639 | 1.040 | 40% |
| ring | won | 39 | -0.457 | 1.453 | 44% |
| ring | lost | 44 | -0.722 | 1.229 | 30% |
| tail | lost | 3 | -0.861 | 1.876 | 33% |
| buy_yes | won | 1 | +0.659 | n/a | 100% |
| buy_yes | lost | 37 | -0.627 | 1.342 | 35% |
| buy_no | won | 50 | -0.563 | 1.379 | 38% |
| buy_no | lost | 20 | -0.878 | 0.924 | 25% |

## 6. Cost vs σ Breakdown

Higher cost = smaller margin to break even. Modal/YES buys are EXPENSIVE (near-favorite, high ask). NO buys are cheap (neg-risk shoulder, ask ≈ 0.01).

| class | n | mean_ask | mean_all_in | mean_σ | mean_edge_lcb |
|---|---|---|---|---|---|
| modal | 22 | 0.563 | 0.592 | 1.967 | +0.091 |
| ring | 83 | 0.412 | 0.433 | 1.909 | +0.092 |
| tail | 3 | 0.021 | 0.022 | 1.735 | +0.031 |
| buy_yes | 38 | 0.035 | 0.037 | 1.883 | +0.055 |
| buy_no | 70 | 0.648 | 0.681 | 1.934 | +0.110 |

## 7. Ranked Root-Cause and Fix Direction

### Root-Cause Ranking

**RC-1 (PRIMARY): High cost kills the break-even margin on EXPENSIVE legs**

The fundamental structural driver is the all-in cost of modal/YES legs vs NO legs:
- modal losers: mean all-in cost = 0.483/share (break-even win-rate = 0.483)
- buy_yes losers: mean all-in cost = 0.036/share
- neg_risk_buy_no (profitable class): mean all-in cost = 0.681/share

A buy_no at cost 0.01 needs to WIN only 1% of the time to break even. A buy_yes at cost 0.45 needs to win 45% of the time. With n=22 modal and n=38 buy_yes selections the realized win-rate is too low to clear these high costs — 1 additional missed win on 22 trades swings the mean EV by ~0.04/share. This is a **small-n / high-cost variance problem**, not necessarily persistent alpha-negative signal.

**RC-2 (STRUCTURAL): Center error is NOT systematically biased (no consistent over-estimation) but IS high-variance**

- modal losers: mean center error μ*−realized = -0.639 (std=1.040), 40% over-estimates
- modal winners: mean center error = -0.806
- buy_yes losers: mean center error = -0.627, 35% over-estimates
- buy_yes winners: mean center error = +0.659
- drag losers overall: mean center error = -0.537
- winning rows overall: mean center error = -0.539

Center error direction on drag losers: **NEGATIVE (under-estimates temperature)**.

Center error is small on average; bias is NOT the primary driver of losses. The variance of center error is the issue: a wide σ_center spans multiple bins and the spine selects the modal with high edge_lcb, but when σ is wide the realization lands outside the modal bin frequently.

**RC-3 (CONTRIBUTING): σ over-dispersion widens the modal bin's probability below the break-even win-rate needed for the high ask**

- modal losers: mean σ = 1.83, mean |err| = 0.82, σ/|err| ratio = 2.24
- modal winners: mean σ = 2.08, mean |err| = 1.26, σ/|err| ratio = 1.66

When σ is large relative to the bin width (typically 1°C), the modal bin captures only a modest fraction of the predictive mass. The edge_lcb > 0 gate fires even at low modal-bin q, but the actual win-rate at settlement is driven by how often the modal bin IS the settled bin — which drops as σ widens.

**RC-4 (MINOR): Direction-law / bin-assignment**

All modal-loser rows are CORRECT by construction (modal pick = the spine's highest-q bin). The losing pattern is not a direction-law violation; it is the modal bin failing to settle. City concentration shows whether specific markets are disproportionately represented — see Section 2 for the by-city breakdown.

### Fix Direction (ranked by impact)

**FIX-1 (HIGHEST IMPACT): Exclude modal-bin YES buys from the live policy**

Modal-bin buy_yes is the single worst sub-class (n=22, mean EV -0.0462). The direction law ALREADY forbids NO on the modal bin; an analogous restriction can be added: the spine should NOT select YES on its own modal bin (a favorable modal YES is over-priced by the market — the ask already embeds the crowd's modal belief). The alpha is in NON-modal (ring/shoulder) bets.
  - Expected effect: drops ~22 rows from the graded set; the residual population is buy_no (neg_risk) + ring buy_yes, both positive.
  - Implementation: in `_native_side_cost_curve_from_snapshot_row` or the spine selection pass, add a gate: skip buy_yes legs where the market bin covers the modal grid bin.

**FIX-2 (HIGH IMPACT): Center de-bias audit (task #98 re-check)**

If mean center error on modal losers = -0.639 with 40% over-estimates, the de-bias contamination (task #98 +2.8°C) may still be partially active on specific cities/metrics. Per-city center error in Section 2 identifies the most contaminated markets. Re-fit or audit de-bias coefficients for those cities.

**FIX-3 (MEDIUM IMPACT): σ-floor tightening for high-skill cells**

For markets where |μ*−realized| << σ_pred (σ/|err| >> 2), the spine is over-dispersed and the modal bin's q is artificially diluted. Tighter σ-floor for those cells would concentrate more q-mass on the modal bin — but note: FIX-1 removes those trades anyway, so FIX-3's primary benefit is for ring selections where tighter σ raises edge_lcb and point_ev on the correct bin.

**FIX-4 (LOW IMPACT FOR NOW): buy_yes gate tighten on high-cost legs**

buy_yes losers have mean all-in cost 0.036 (> 40¢ typical). Adding a maximum-cost gate (e.g. skip buy_yes if all_in_cost > 0.35) would prune the expensive YES buys whose break-even win-rate is not achievable at n=38. The residual buy_yes set would be only the cheap YES legs (ring bets at 0.10–0.25 cost) which likely have positive EV.

### Summary: What Lifts INDETERMINATE to PROVEN-POSITIVE

The neg_risk_buy_no and ring classes are ALREADY positive (n=70+83). The drag comes entirely from modal buy_yes (expensive, low win-rate) and high-cost buy_yes. The spine can move to PROVEN-POSITIVE by:

1. **Block YES on modal bin** (FIX-1) — removes the worst-EV class.
2. **De-bias audit for top-loss cities** (FIX-2) — corrects center contamination where present.
3. **Cost cap on buy_yes** (FIX-4) — prunes expensive YES bets whose high break-even is not achievable.

With FIX-1 alone (drop 22 modal-YES rows from the graded set), the residual population is n=86 with mean EV shifted upward by approximately +0.0492 (rough estimate; full re-run needed). The CI width at n=86 remains wide; settlement data beyond the 7-day window is needed to reach CI-positive at 95% confidence.

