# Real-chain settled profitability audit — is the book profitable? (2026-06-23)

Created: 2026-06-23
Last audited: 2026-06-23
Authority basis: standing mission — "EVERY real chain decision audited with reality"; "make sure they are profitable"; real settled evidence only, no test/replay.

## Answer: NOT yet profitable. Net −1.9 per-contract over 133 settled decisions / 14d.

Per-contract realized P&L = (won ? 1−fill : −fill), summed over settlement_attribution rows with a fill.

| cut | n | win-rate | avg fill | net per-contract P&L |
|---|---|---|---|---|
| ALL (14d) | 133 | 57.1% | 0.586 | **−1.92** |

Breakeven win-rate at avg fill 0.586 = 58.6%; realized 57.1% → net loss.

## Where the loss concentrates

By category:
| category | n | wr | net |
|---|---|---|---|
| SKILL_WIN | 39 | 100% | **+11.21** |
| LUCKY_WIN | 3 | 100% | +0.96 |
| UNATTRIBUTABLE_Q_MISSING (pre-fix toxic YES) | 27 | 22% | −1.01 |
| SKILL_LOSS | 6 | 0% | −3.69 |
| MISCALIBRATED_LOSS | 6 | 0% | −4.50 |
| STALE_DECISION | 52 | 54% | **−4.89** |

By direction: buy_no 104 @ 67.3% (breakeven 70.2%) → **−3.03**; buy_yes 29 @ 20.7% → +1.11 (cheap YES).

## Root cause: q_lcb over-confidence (NOT exit-failure)

1. **Losers' fresh belief STILL AGREED.** Among the "fresher-cycle-existed" losers, 21/22 had `fresh_q_supports_position=1` — the forecast never saw the loss coming. So these are NOT missed reversals (exit logic is not the culprit here); they are calibration misses.
2. **q_lcb does not discriminate winners from losers** (buy_no, cert-q present): losers q_live 0.862 / q_lcb 0.826; winners q_live 0.901 / q_lcb 0.837. Near-identical. A genuine conservative lower bound would sit below realized frequency; here realized (≈65%) << q_lcb (0.826).
3. **q_lcb edge band vs realized (buy_no, 14d):**
   - 5–15% claimed edge: 35 trades, realized 65.7% vs 72.3% breakeven → **−2.30** (false edge).
   - 15%+ claimed edge: 11 trades, realized 90.9% vs 60.9% breakeven → **+3.30** (genuine edge).

So the alpha is REAL at the strong-edge end (15%+ q_lcb-edge, and fresh buy_no ≈80%); it is diluted to net-negative by over-confident mid-edge (5–15%) entries whose q_lcb claims edge that does not realize.

## Implication
- Deployed correctness fixes (modal-only buy_yes, EV-based near-settle exit, σ-scale k restore) remove specific pathologies but do NOT fix the core calibration over-confidence.
- The genuine fix: make q_lcb a true conservative lower bound (realized win-rate ≥ q_lcb coverage) so over-confident mid-edge cells fail the honest `q_lcb > price + cost` gate — NOT a hardcoded edge throttle (operator law) and NOT overfit to 14d (must survive walk-forward; prior per-cell/city/σ-repr approaches hit the ~91-bet thin-data wall).
- Caution: 14d, small per-band n. Treat the band split as a hypothesis to validate walk-forward, not a tuning target.
