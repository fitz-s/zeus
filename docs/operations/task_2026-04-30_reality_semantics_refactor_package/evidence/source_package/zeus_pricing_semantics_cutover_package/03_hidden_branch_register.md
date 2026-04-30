# 03 — Hidden Branch Register

This register is the completeness contract. Each branch must be covered by implementation, tests, or explicit deferral.

| # | Hidden branch | Known truth | Unresolved uncertainty | Required resolution | Second-order consequences / tests |
|---:|---|---|---|---|---|
| 1 | Absolute-best architecture claim | No live quant design is absolutely best; only evidence-bound and monitorable | Future market microstructure/model performance can invalidate assumptions | State defensible claim, not absolute certainty | Prevent overclaiming; package remains testable |
| 2 | Three-layer physical isolation | User explicitly requires Epistemic/Microstructure/Execution isolation | Exact package/module split depends on topology doctor | Add import fences, type fences, semgrep rules | Tests fail if epistemic imports CLOB or microstructure imports weather |
| 3 | Legacy live fail-open | Uploaded spec requires legacy conflation named and fail-closed by default | Whether config already has exact flag name | Add `ALLOW_LEGACY_VWMP_PRIOR_LIVE=false` | Live entry count may drop to zero; this is correct |
| 4 | Raw quote as market prior | Token quote is observation, not prior by default | Whether any estimator has OOS validation | `MarketPriorDistribution | None`; `model_only_v1` baseline | Posterior no longer changes with ask/depth unless named estimator changes |
| 5 | Market-prior family completeness | Priors over bins need complete family and de-vig policy | Weather markets may have missing/closed child markets | Shadow-only `yes_family_devig_v1` until validated | Avoid sparse vector masquerading as distribution |
| 6 | NO payoff vs NO cost | `P_no=1-P_yes` valid for payoff; `NO_price=1-YES_quote` not generally executable | Binary-specific shortcut may be valid only under strict token proof | Use native NO token orderbook for cost | Prevent buy_no edge hallucination |
| 7 | Fee rate assumption | User pseudo uses 2%; Polymarket fee rates are market/category/feesEnabled facts | Actual per-market fee params require live query | Fee rate comes from market metadata; 2% allowed only test fixture | Kelly sizing changes materially |
| 8 | Fee formula | Fee is price-dependent and match-time; makers not charged | Maker/taker status unknown before fill under may-take | Conservative path assumes worst-case taker | Report realized maker/taker and fee deltas |
| 9 | CLOB sweep size circularity | Cost depends on trade size; Kelly often determines size | Which optimizer should be first implementation | MVP fixed-point/reject-shrink; future cost-curve Kelly | Prevent positive edge at $5 becoming negative at $500 |
| 10 | Top-of-book vs depth | Best ask alone insufficient for larger size | Visible depth may be stale/incomplete | `simulate_clob_sweep` and depth status | Add depth-constrained test |
| 11 | Order policy ambiguity | Zeus execution law says limit-only/provide liquidity but code can jump to ask | Whether near-term wants passive or may-take | Define `LIMIT_MAY_TAKE_CONSERVATIVE` explicitly | Venue adapter cannot silently choose FOK/FAK/GTC |
| 12 | Post-only support | Polymarket supports post-only with GTC/GTD; crossing post-only rejects | Zeus adapter support uncertain | Defer `POST_ONLY_PASSIVE_LIMIT` to future policy | Avoid mixing maker economics with taker sizing |
| 13 | FDR timing | Live FDR must operate after snapshot/cost fixed | Existing family ids may not include cost basis | Include token/snapshot/cost/order policy in hypothesis id | Late reprice invalidates FDR selection |
| 14 | Late reprice after FDR | Current runtime mutates decision edge/size after selection | Full call graph requires topology | Remove/reject corrected late reprice | Test selected hypothesis exactly materializes |
| 15 | Executor recompute | Current executor can derive limit from posterior/VWMP style context | All callsites unknown until topology | Corrected executor accepts `FinalExecutionIntent` only | Test no `p_posterior + vwmp` path in corrected mode |
| 16 | Snapshot producer gap | Known gaps say no production snapshot producer/refresher found | External/out-of-branch producer could exist | Build/identify single canonical producer | Missing snapshot = structured no-trade/no-exit |
| 17 | Snapshot freshness/hash | Orderbook hash/timestamp/tick changes matter | Freshness threshold needs operational tuning | Snapshot id/hash/freshness mandatory | Reject stale or tick-changed book |
| 18 | Tick/min-order | Polymarket rejects invalid tick prices; min order matters | Market-specific values vary | Validate before cost basis and final intent | Prevent rejected live orders |
| 19 | V2 compatibility envelope | Known gaps identify `legacy:`/collapsed token identity issue | Actual adapter internals require topology | Certified path must use snapshot-bound envelope | Test no `legacy-compat` for live evidence |
| 20 | Negative-risk events | Neg-risk changes conversion economics and requires `negRisk` option | Whether weather family is neg-risk/augmented per market | Preserve metadata; defer arbitrage estimator | Prevent unsupported event-coupling assumptions |
| 21 | Augmented negative-risk placeholders | Placeholder/Other outcomes change event semantics | Applicability to weather markets uncertain | Ignore placeholders unless named; flag unsupported | Avoid invalid family completeness |
| 22 | Partial fills | Partial fills affect remaining exposure | Current residual accounting incomplete per gaps | Fill facts reduce exposure; cancel remainder recorded | Monitor/exit must know remaining shares |
| 23 | Exit symmetry | Corrected entry with legacy exit remains unsafe | Exact exit paths need topology | Held-token sell quote cost basis mandatory | Exit EV compares sell value vs hold value |
| 24 | Monitor quote/probability conflation | Current monitor can construct sparse `p_market` vector | Full variants unknown | Split probability refresh from quote refresh | Test quote change does not alter posterior |
| 25 | Mixed semantics reports | Legacy/corrected economics cannot aggregate | Existing reports may aggregate broadly | Hard-fail or segregate by `pricing_semantics_version` | Promotion evidence cleanly separated |
| 26 | Historical depth missing | Corrected executable economics need point-in-time depth/hash | Some rows may have snapshots; unknown | No snapshot/depth = diagnostic only | Stop backtest from laundering legacy ROI |
| 27 | Source truth blockers | Current source validity has Paris/HK blockers | Updated evidence may exist later | Keep pricing packet separate | Pricing fix cannot claim live alpha ready |
| 28 | Calibration readiness | Current known gaps list empty calibration/model tables | Updated evidence may exist later | `model_only_v1` corrected baseline but not promotion | Separate model skill from execution semantics |
| 29 | Risk RED/ORANGE behavior | RED must cancel/sweep; known gaps say proxy/incomplete | Exact command worker status unknown | Treat as live-promotion blocker | Entry safety not enough if forced exit fails |
| 30 | Collateral/allowance | Live orders require wallet/collateral/allowances | Current wallet balance/gates can change | Keep live readiness gate | Prevent theoretical intent from becoming impossible order |
| 31 | Adverse selection | May-take/resting limit can be adversely selected | No model in first packet | Telemetry only first; future model | Promotion requires fill-quality evidence |
| 32 | Queue priority/fill probability | Resting limit may never fill | No model in first packet | Conditional-on-fill Kelly; log unfilled/cancel | Do not claim realized edge from unfilled orders |
| 33 | Maker/taker realized status | Fee assumption differs by maker/taker | Unknown until fill | Worst-case taker for sizing; realized status in fill fact | Report fee/edge delta |
| 34 | Tick-size changes mid-cycle | Polymarket has tick size changes; old tick causes rejects | Frequency unknown | Fresh snapshot check before submit | Reject if current tick != cost basis tick |
| 35 | Active positions mixed cohort | Future positions may have legacy/corrected entries | Current open positions may be zero but not durable | Store semantics per position | Exit/report by cohort |
| 36 | Shadow vs live vs backtest | These are distinct runtime modes | Existing mode plumbing may blur | Semantics version and runtime mode must be explicit | Shadow cannot submit; backtest cannot invent depth |
| 37 | Codex scope contamination | Zeus requires topology doctor/planning lock | Exact allowed files unknown until run | Every phase has scoped prompt and stop conditions | Prevent broad unsafe patch |
| 38 | `BinEdge` deprecation | Current object bundles multiple meanings | Removing at once may be too wide | Transitional sidecar only; no live authority | Tests forbid Kelly/executor authority from BinEdge fields |
| 39 | `ExecutionPrice` false safety | Typed price can still wrap wrong origin then fee-adjust | Existing callsites unknown | Add origin/cost-basis lineage, not just type | Prevent fee-adjusted implied probability |
| 40 | Backtest/promotion split | Model skill and live executable economics are different | Promotion thresholds TBD | Separate reports/gates | Prevent model-only skill from proving execution economics |
| 41 | Market data websocket vs polling | Fresh quote source impacts staleness and book drift | First packet may use polling only | Preserve source lineage and hash | Later WS can upgrade without changing semantics |
| 42 | Cancel policy | Limit may rest and become stale | Timeout policy must be explicit | `cancel_after` in final intent | Avoid stale unbounded exposure |
| 43 | Price precision/rounding | Decimal precision matters near ticks | Existing float usage common | Decimal in microstructure and final intent | Prevent accidental tick violation |
| 44 | Family caps and correlated bins | Multi-bin markets are correlated | Exact family caps existing policy unknown | Keep risk caps after FDR/cost fixed | Avoid selecting multiple correlated bets wrongly |
| 45 | Settlement probability remains bin-level | Microstructure token identity may not map trivially to bin labels | Market parsing may be imperfect | Token/bin mapping must be validated | Prevent trading wrong token for right belief |

## Mandatory verification loop per high-impact branch

For every branch above, Codex must answer:

1. Did this change preserve the original target, or did it drift into generic refactor?
2. Which authority surface governs it?
3. What fact is known, and what remains unresolved?
4. What test or gate prevents regression?
5. What second-order effect can break live trading even if the main path passes?
