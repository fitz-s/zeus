# End-to-end money-path audit — where alpha leaks, with real-chain evidence (2026-06-23)

Created: 2026-06-23
Last audited: 2026-06-23
Authority basis: standing mission — "diagnose the pipeline end to end, from forecast data to
market decision engine, continuous analysis, only real market chain evidence (not test/replay),
EVERY real chain decision audited with reality; correct entry, monitor, exit/settle; constantly
re-evaluate before-fill / holding / near-settle." This is the SYSTEMATIC (not single-order) audit.

## Verdict: book is NET −1.92/contract (14d, 133 settled). TWO unified root causes, not many bugs.

The pipeline was audited phase-by-phase on the live chain. Discovery and exit are HEALTHY; the
leak is concentrated in (1) the FILL lane and (2) q CALIBRATION. Exit/re-evaluation is NOT the leak.

| Phase | Real-chain measurement (live DBs, 2026-06-23) | Verdict |
|---|---|---|
| Forecast/belief breadth | 38 cities / 42 families fresh in 90 min; 49/49 cities in 6h (universe=49 cities ⇒ ~1000+ bin-markets) | HEALTHY (post Open-Meteo fix) |
| Entry candidate generation | 84 ENTRY venue_commands created in 24h across markets | HEALTHY — generation is broad, NOT "1 order" |
| **FILL** | **2/84 entries FILLED** (76 CANCELLED, 5 EXPIRED, 1 ACKED); venue_order_facts 24h: 2905 EXPIRED, 112 LIVE, 103 CANCEL_CONFIRMED, **only 4 MATCHED + 4 PARTIAL** | **COLLAPSED — maker rests below ask never fill; the few fills are adversely selected** |
| q calibration | of the `fresher_cycle_existed=1` losers: **21 had fresh belief STILL AGREEING** (won=0, fresh_q_supports=1) vs **1** genuine reversal | **OVER-CONFIDENT — q_lcb not a true conservative bound** |
| Exit / monitor / re-eval | only 1 genuine reversal-miss in 14d; losers' fresh belief agreed ⇒ holds were correct | HEALTHY — not a false-exit / missed-reversal problem |
| Settlement profitability | net −1.92/contract; SKILL_WIN +11.2, 15%+ q_lcb-edge +3.3 (alpha is REAL); diluted by STALE_DECISION −4.9, buy_no −3.0, 5–15% q-edge band −3.0 | NOT YET PROFITABLE |

## Why "looks like only 1 order"

NOT a generation problem. 84 entry commands were created in 24h across many markets, but the
maker-rest placement (post-only GTC below ask) fills only when the market trades INTO the rest —
so **2 of 84 filled**, the rest cancel/expire. Two live fills ≈ "one position" to the eye. The
collapse is the FILL lane, downstream of broad, healthy generation.

## Why "not profitable" — the two unified root causes

1. **Maker adverse-fill (FILL lane).** 2905 order-facts EXPIRED, 4 MATCHED. A below-ask BUY maker
   rest can only fill if the market moves down into it — i.e. precisely when the market just
   re-priced against us. The realized fill population is therefore adversely selected vs the
   ex-ante candidate population. Fix direction (consult-confirmed, operator-law-legal): uncertified
   maker → **taker-if-edge-survives-spread** (trade as taker at the fresh ask iff the conservative
   edge still clears the all-in executable cost; the spread IS the cost, not a throttle).

2. **q over-confidence (CALIBRATION).** The decisive split: among the `fresher_cycle_existed=1`
   loss bucket (−6.8/contract), **21/22 losers had the fresh forecast STILL supporting the
   position** — re-evaluating against the freshest cycle would NOT have exited them. The forecast
   was simply over-confident; q_lcb did not sit below realized frequency (buy_no realized ~65% vs
   q_lcb ~0.83; 5–15% claimed-edge band realized 65.7% vs 72.3% breakeven). Fix direction: an
   always-on **execution-conditioned q_exec_lcb** (hierarchical shrinkage to a covered parent;
   never a flag/shadow; thin cell shrinks, never abstains-to-halt) so over-confident mid-edge cells
   fail the honest `q > price + cost` gate. NOT a hardcoded edge floor (operator law), NOT overfit
   to 14d.

## What is NOT the problem (ruled out with evidence)

- **Exit / re-evaluation logic.** Only 1 genuine reversal-miss in 14d; 21/22 stale losers had fresh
  belief agreeing. The system holds correctly when belief agrees and is not panic-exiting. The
  "constantly re-evaluate" machinery is present and behaving; tightening it would not recover the loss.
- **Discovery breadth.** 38–49 cities / 42 families fresh; broad. The Open-Meteo eastward fix
  (this session) restored belief for 7 previously-blacked-out eastern-hemisphere cities (Beijing/
  Chengdu/Busan/Helsinki/London/HongKong/Guangzhou — fresh 2026-06-24 posteriors @08:57Z verified).

## Fixes shipped this session (money-path relevant)

- **Open-Meteo eastward anchor selection** (coverage-aware `_latest_manifest`) — restores belief/
  discovery across the eastern hemisphere; directly reduces future STALE_DECISION losses (a stale-
  belief source was forecast-pipeline blackout). Deployed, real-chain proven.
- **Modal-only buy_yes on the LIVE reactor path** (`direction_law.py`) — reverts the 2026-06-15
  σ-distance relaxation that admitted over-confident adjacent-bin YES; verdict now == FDE. Deployed.
- (Earlier) ECMWF sibling-import PYTHONPATH fix; ChainState fleet restart; EV-based near-settle exit.

## Remaining (the two root causes above) — ordered by money-path impact

1. **FILL lane: maker → taker-if-edge-survives-spread** (consult BLOCKER #2). Directly addresses the
   2/84 fill collapse and the adverse-selection that makes filled trades unprofitable.
2. **q_exec_lcb execution-conditioned conservative bound** (consult BLOCKER #1). Addresses the
   21/22-agree over-confidence; first commit must be the minimal honest increment buildable from the
   133 settled rows + live receipts, always-on, no shadow, no halt, no overfit.

These two are coupled: a fill-aware q_exec_lcb needs the taker reroute to have a settlement-
conditioned population to certify. Sequencing is being finalized with the consult (round 3).

## Commit-1 attempt + the decisive walk-forward result (2026-06-23, q_exec_lcb)

Built the always-on q_exec_lcb estimator (src/decision/q_exec_lcb.py, commit 1ac42bd1): isotonic
(PAVA, no buckets) 5% beta lower bound of realized `won` over `raw_side_prob` per
(actual_exec_class × side), parent fallback, never-abstain, min(model_q_lcb, block_lb). 7/7 unit
tests green (deflates over-confident bounds, monotone, maker-never-borrows-taker).

THEN validated it on the real chain as the consult required — as-of WALK-FORWARD (fit only on rows
settled before each row's decision time) over the 54 settled rows that carry q_lcb+q_live+fill
(scripts/q_exec_lcb_backtest.py):

| set | n | win-rate |
|---|---|---|
| admitted under model q_lcb (all) | 54 | 0.667 |
| **admitted under q_exec_lcb (edge>0)** | 21 | **0.43** |
| **de-admitted by q_exec_lcb** | 33 | **0.82** |

q_exec_lcb is ANTI-PREDICTIVE on this sample: it de-admits the 82%-winners and keeps the
43%-winners. Root cause = REGIME MIX + thin data — the 54 rows span the pre-fix losing regime and
the post-fix winning regime; fitting the empirical bound on early losers deflates it and kills the
later winners. This is the same thin-data/regime-mix wall the operator's "must survive walk-forward,
no overfit, real-chain only" law exists to catch.

DECISION (operator-law-compliant, honest): the estimator is CORRECT but the DATA is not ready.
Do NOT wire q_exec_lcb as the live gate now — deploying it would HARM (kill winners). Per the
consult's own fork, the honest commit-1 is to PERSIST ExecutionOutcomeFact as a first-class
money-path receipt (non-shadow — changes no admission yet) so a CLEAN post-fix population accrues,
then certify q_exec_lcb on a homogeneous regime before it gates capital. The fill-lane maker→taker
reroute is coupled (the consult: "maker→taker without q_exec_lcb just crosses the same over-
confident false edge at the ask"), so it waits on the same certification.

NET this session: the IMMEDIATE money-path improvements that ARE deployed+proven are modal-only YES
(live) + Open-Meteo eastward belief (live) — both reduce the documented loss sources (over-confident
adjacent YES, stale belief). The q-calibration + fill-lane root fixes are built/specified but gated
on clean-regime data accrual, NOT deployed (a deploy would fail walk-forward and harm). The backtest
harness is the standing gate that will say when q_exec_lcb is finally certifiable.

## Fill-lane: full mechanism traced (2026-06-23), why it stays at ~3.5%

Real-chain (zeus_trades.db, last 24h): 88 INTENT_CREATED → 87 SUBMIT_ACKED → 78 CANCEL_ACKED →
6 EXPIRED → 3 FILL_CONFIRMED + 4 PARTIAL. Last 3h: 57 acked, 52 cancelled, 2 filled — no improvement.
Cancel events carry source=`maker_rest_escalation`, action=`CANCEL_REPLACE`, reasons `BOOK_MOVED`
(≈1.5¢ drift) and `CONFIRMED_VALUE_REFRESH`. The rest-then-cross escalation lane DID fire 66×
(TAKER_ESCALATED_AFTER_REST) but converted to only 3 fills.

Ruled out as the cause: (a) entries-pause override — `control_overrides` has 0 active rows; (b)
reprice clock-reset churn — the 300s maker-window gate on the BOOK_MOVED/VALUE_REFRESH pulls
(continuous_redecision.py:1709-1755, REST_BOOK_DRIFT_TICKS + value_refresh_min_age_seconds) is
DEPLOYED (live-main byte-identical to git HEAD) and each position has ~1 command (no multi-command
churn); (c) exit/re-eval logic — healthy (EXIT_SAVED_LOSS=25).

Remaining mechanism (the real collapse): the mode decision still PREFERS maker
(mode_consistent_ev.select_mode_consistent_ev: ev_maker scaled by p_fill_maker=0.10 GUESS beats
ev_taker×(1+0.15) on many candidates; TAKER hysteresis defaults maker on knife-edge), so entries
rest below ask and only fill on adverse down-moves (~10.8% measured, 0 at p0.3-0.8); and the 66
escalation crosses do not convert (re-rest as maker on re-certification, or the cross is itself
screen-cancelled / FOK-fails before executing). This is consult BLOCKER #2 (maker placement requires
maker-fill settlement authority; else recost taker, submit iff q_exec_lcb_taker > taker_all_in_cost,
else reject+continue). It is COUPLED to q_exec_lcb (#1): doing maker→taker with the current
over-confident q_lcb would cross the same false edge at the ask. q_exec_lcb is not yet certifiable
(54 settled q_lcb rows, all <3 days). So the fill-lane root fix is gated on the same clean-regime
accrual — there is no safe single-knob fix that respects "correct AND gain" today.

CONCLUSION (evidence-complete): discovery is broad, re-eval works across all phases, exit is not the
leak. The book is net-negative from exactly two coupled, data-gated root causes — maker-preference
adverse fills + q over-confidence. The honest path to profitability is: the deployed quality fixes
(modal-only, Open-Meteo) make accruing settled rows cleaner → re-run scripts/q_exec_lcb_backtest.py
as rows accrue → when it certifies, deploy q_exec_lcb + the maker→taker reroute together. This is
data-time-bound, not analysis-bound; forcing either fix now would lose money (walk-forward-proven).
