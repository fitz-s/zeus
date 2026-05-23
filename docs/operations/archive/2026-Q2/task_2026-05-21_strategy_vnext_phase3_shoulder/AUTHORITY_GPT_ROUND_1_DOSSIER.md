# ZEUS STRATEGY-UPGRADE DOSSIER — GPT PRO ROUND 1

## 0. Executive Tribunal Verdict

1. **最该立刻推进的不是“天气模型更准”，而是 `MarketAnalysisVNext + Shadow Evidence Layer`。** 当前 Zeus 已有从 forecast → calibration → posterior → FDR → Kelly → CLOB 的主线，但仍容易把 `belief probability`、`market prior`、`YES/NO executable quote`、`fill probability`、`exit value`、`settlement probability` 混成一个对象。README 和 AGENTS 已经把 money path 和 strict contract/source semantics 写成核心路径，但下一轮升级必须把这些对象拆开，否则所有新策略都会在 mid-price、complement-NO、stale quote、fill failure 上产生 phantom edge。Repo authority: `README.md`, `AGENTS.md`, `src/strategy/market_analysis.py`, `src/strategy/market_analysis_family_scan.py`, `src/contracts/executable_market_snapshot_v2.py`, `src/contracts/execution_price.py`. ([GitHub][1])

2. **最大隐藏 edge 是 Day0 measured-temperature 的“事实边界交易”，不是普通 forecast betting。** Day0 高温/低温具有单调事实结构：high-so-far 只能上升，low-so-far 只能下降；某些 threshold 一旦 crossed，部分 bin 的 settlement state 已经事实确定，只是市场、Weather Underground 页面、Polymarket resolution 或 market maker 尚未反映。Zeus 已有 `src/signal/day0_*`, `src/data/observation_client.py`, `src/engine/evaluator.py` 的 Day0 scaffolding，但应升级成 `deterministic-bound + nowcast + quote-staleness + settlement-source-lag` 系统。

3. **最大的假 edge 是“posterior > displayed market probability”。** Polymarket 官方 order book 是 token-level CLOB；best ask 是 buy price，best bid 是 sell price，midpoint只是显示对象；当 spread 大于 $0.10 时，平台可能显示 last traded price 而不是真实 midpoint。所有使用 displayed probability / VWMP / mid / complement 的策略都必须重新以 token ask/bid/depth/fee/tick/min-order/FOK-FAK feasibility 计算。([Polymarket Documentation][2])

4. **Zeus 已经识别了扩张方向，但很多是 stub 而不是 strategy。** `src/strategy/candidates/` 里已有 `stale_quote_detector.py`, `liquidity_provision_with_heartbeat.py`, `neg_risk_basket.py`, `resolution_window_maker.py`, `cross_market_correlation_hedge.py`, `weather_event_arbitrage.py`，但这些目前更像战略 placeholder。它们应先进入 shadow cohort，而不是 live catalog。

5. **Forecast-release timing edge 存在，但半衰期极短。** ECMWF Open Data 是实时 IFS/AIFS subset，00/06/12/18 cycles；官方 dissemination schedule 明确各 step 渐进到达，例如 ENS 00/12 的 day0/day10/day15/derived products 有固定 UTC delivery windows。Zeus 若只按“run ready”粗粒度触发，会错过 `new-step-arrived but book-not-refreshed` 的微窗口。([ECMWF][3])

6. **Shoulder strategy 不是普通 bin alpha；它是 open-ended tail exposure。** `open_shoulder` 在 repo 文档中已被定义为 settlement topology，但 trading meaning 是 short-tail / long-tail / retail lottery / extreme-weather underpricing 的混合体。`shoulder_sell` 可能是 alpha，也可能只是卖灾难尾部的隐性保险；必须单独 FDR family、单独 Kelly haircut、单独 city-regime stress、单独 max exposure cap。

7. **Settlement/source truth 是可交易 surface，也是最大 operational risk。** Polymarket resolution 由 market rules 和 UMA Optimistic Oracle 流程决定，uncontested proposal 通常可在 proposal 后约 2 小时 resolved，disputed 可能进入更长 DVM 流程；weather markets 现实里 source/station 并非抽象不可变。2026 年 Paris weather market 已出现因 airport sensor anomaly / suspected tampering 导致 Polymarket source change 的现实案例。策略必须利用 source lag，但不能押注错误 source。([Polymarket Documentation][4])

8. **数据不足不是停止理由；它是 research design 问题。** Zeus 应建立 evidence ladder：historical weather replay → forecast archive replay → market snapshot reconstruction → synthetic order-book stress → shadow decision log → no-trade log → negative controls → small-N Bayesian promotion gates。Shadow evidence 不能证明 live PnL，但可以淘汰 leakage、non-fill、fee-erased、settlement-mismatch、family-correlation 这五类最危险 false edge。

9. **Portfolio/Kelly/FDR 的最大缺陷是“假分散”。** 天气 bin、city、metric、strategy family、forecast cycle、same weather system、same source station、same market-maker heartbeat 都可能相关。当前 `selection_family.py` 已意识到 full-family FDR，但 family scope 仍需要从 candidate/snapshot 扩到 `hypothesis_family_id × source_truth × weather_system × strategy_family × time_window`。

10. **Claude 第一批实现应是 instrumentation，不是策略上线。** 最先做：`ExecutableMarketSnapshotVNext`, `ShadowDecisionLogVNext`, `Day0BoundState`, `QuoteFreshnessScore`, `FillFeasibilityScore`, `ShoulderExposureLedger`, `StrategyEvidenceCohort`, `NoTradeReasonTaxonomy`, `SettlementCaptureVerifier`, `FamilyEvidenceReport`。

---

## 1. Source Map / Evidence Map

### 1.1 Repo authority 已读/定位路径

**代码已实现 / 运行主线**

* `README.md`: Zeus 被定义为 weather-settlement prediction-market quantitative trading engine；chain 是 contract semantics → source truth → 51 ENS → MC sensor-noise/rounding → calibration → market fusion → bootstrap CI → FDR → Kelly → CLOB → monitoring/exit → settlement/lifecycle learning。Repo also states strategy families: settlement capture, shoulder sell, center buy, opening inertia. ([GitHub][1])
* `AGENTS.md`: money path、runtime entry points、settlement semantics、risk levels、lifecycle enum、chain reconciliation、live/backtest/shadow rules、strategy families、current data boot surfaces。([GitHub][5])
* `src/strategy/market_analysis.py`: 当前 full-distribution edge scan；支持 `p_raw`, `p_cal`, `p_market`, `alpha`, `p_market_no`, `buy_no_quote_available`, executable mask；bootstrap settlement rounding；native buy-NO 被显式区分。
* `src/strategy/market_analysis_family_scan.py`: full-family hypothesis scan；记录每个 bin/direction hypothesis，用于 FDR，而不是只记录 positive edge。
* `src/strategy/selection_family.py`: canonical family IDs，high/low separated，BH gate；但 repo note 显示 active family scope 仍偏 candidate/snapshot，而不是 whole-cycle portfolio。
* `src/strategy/kelly.py`: typed `ExecutionPrice` Kelly；unknown strategy fail-closed；settlement-day observed fraction modifier；strategy/mode/oracle penalty multipliers。
* `src/strategy/strategy_profile.py`: strategy registry / live-shadow-blocked profile gate；unknown fail-closed。
* `src/strategy/market_phase.py`: phase axis: PRE_TRADING, PRE_SETTLEMENT_DAY, SETTLEMENT_DAY, POST_TRADING, RESOLVED；uses local date/DST and market end timestamp.
* `src/contracts/settlement_semantics.py`: WMO half-up rounding, Weather Underground default, HKO oracle_truncate, settlement value gate.
* `src/data/ecmwf_open_data.py`, `src/data/tigge_*`, `src/data/ensemble_client.py`, `src/data/forecast_ingest_protocol.py`: source-run, issue/valid/captured provenance, high/low tracks.
* `src/data/observation_client.py`: Day0 observation priority: WU settlement authority, IEM ASOS fallback, Open-Meteo fallback; `Day0ObservationContext` carries high_so_far/low_so_far/source/observation_time/causality_status.
* `src/engine/evaluator.py`: candidate → decision pure function; executable Day0 sources gate; native buy-NO flags; strategy classification.
* `src/execution/executor.py`, `src/execution/live_executor.py`, `src/execution/fill_tracker.py`, `src/execution/exchange_reconcile.py`, `src/execution/exit_triggers.py`, `src/execution/settlement_commands.py`, `src/execution/harvester.py`: typed limit order execution, fills, reconciliation, exit/settlement lifecycle.
* `src/strategy/candidates/*`: candidate stubs for stale quote, heartbeat liquidity, negative-risk basket, resolution window, cross-market hedge, weather-event arbitrage.
* `tests/*`: broad coverage exists for activation flags, alpha target, attribution, auto pause, backtest/replay, calibration, chain reconciliation, Kelly/FDR/risk, execution, settlement, strategy profile. **REVIEW_REQUIRED:** I inspected the test tree and key paths, but not every test body line-by-line.

**文档声称 / reference layer**

* `docs/README.md`, `docs/reference/market_settlement.md`, `docs/reference/data_and_replay.md`, `docs/reference/execution_lifecycle.md`, `docs/reference/risk_strategy.md`, `docs/operations/*`, `docs/reference/domain_contracts.md`: repo already defines binary market/bin hierarchy, settlement chain, data split, replay truth, risk levels, execution lifecycle, and market-snapshot provenance. ([GitHub][6])

**外部现实核对**

* Polymarket: hybrid-decentralized CLOB，offchain matching/onchain settlement；all orders are limit orders，market order is marketable limit；orderbook is per token with bids/asks/timestamp/min order/tick/negRisk/hash；buy price = best ask, sell price = best bid；orders have GTC/GTD/FOK/FAK/post-only; fees can apply to takers and weather category fee-rate is documented. ([Polymarket Documentation][7])
* YES/NO: Polymarket binary markets have token IDs for Yes and No; outcome tokens are ERC1155 CTF, Yes/No pair is fully backed by $1, can split/merge/redeem; after resolution trading stops and winners redeem. ([Polymarket Documentation][8])
* Negative risk: Polymarket docs define neg-risk multi-outcome mechanics and explicit `negRisk` order option. **REVIEW_REQUIRED:** weather event families must be checked per event via Gamma/CLOB market metadata; do not assume all weather bin families are neg-risk enabled. ([Polymarket Documentation][9])
* ECMWF: Open Data is real-time subset of IFS/AIFS; 00/06/12/18 cycles; high-res and ENS horizons differ by cycle; delivery schedule is progressive and UTC-specific. ([ECMWF][3])
* Observation reality: NOAA GHCNd daily summaries include max/min temperature, update daily, are rebuilt weekly, and real-time ASOS summaries can change; Global Summary of Day may use 24h periods ending at midnight UTC and differ from local-midnight summaries. This validates Zeus’s local-date/source-truth gates. ([NCEI][10])
* Weather market source risk: 2026 Paris Polymarket weather contracts had reported station anomaly/tampering investigation and source switch; this makes station/source integrity a live-money risk surface, not theoretical. ([The Wall Street Journal][11])

**推理判断 / inference**

* `Day0 fact-bound edge`, `quote heartbeat alpha`, `settlement delay capture`, `shoulder as short-tail`, `no-trade as alpha preservation`, `family-relative evidence` are inference from repo semantics + external microstructure reality.
* `market-maker refresh heartbeat` is **UNKNOWN_BUT_TESTABLE** until quote hash / best bid-ask timestamps are logged forward.
* `public snapshot reconstruction` cannot fully recover queue position or canceled offchain orders; official CLOB design already implies public onchain data is insufficient for full quote lifecycle reconstruction.

---

## 2. Current Zeus Strategy Architecture Reconstruction

### 2.1 Current signal chain

Current Zeus architecture is already unusually disciplined:

`contract text / market family / bin topology`
→ `settlement source and rounding semantics`
→ `forecast source/run/captured_at`
→ `ensemble high/low distribution`
→ `sensor noise + settlement rounding`
→ `p_raw`
→ `Platt / empirical calibration`
→ `p_cal`
→ `market prior / alpha fusion`
→ `posterior`
→ `bootstrap CI`
→ `edge hypothesis`
→ `FDR`
→ `Kelly`
→ `riskguard`
→ `CLOB limit order`
→ `fill/position/lifecycle`
→ `settlement/redeem/learning`.

Repo paths: `README.md`, `AGENTS.md`, `src/signal/*`, `src/calibration/*`, `src/strategy/market_analysis.py`, `src/strategy/market_fusion.py`, `src/strategy/selection_family.py`, `src/strategy/kelly.py`, `src/execution/*`, `src/contracts/settlement_semantics.py`.

### 2.2 Current strategy catalog

**Documented / active-intended families**

1. `settlement_capture`: settlement-day / late event edge.
2. `shoulder_sell`: open shoulder bin sell / buy-NO style exposure.
3. `center_buy`: finite center bin buy-YES.
4. `opening_inertia`: market-open or early-listing price inertia.

**Repo-implied candidate families**

5. `stale_quote_detector`
6. `liquidity_provision_with_heartbeat`
7. `neg_risk_basket`
8. `resolution_window_maker`
9. `cross_market_correlation_hedge`
10. `weather_event_arbitrage`

These are present as candidate stubs, so the strategic intent exists but runtime evidence and implementation are not yet sufficient for live promotion.

### 2.3 Calibration chain

* Ensemble distribution produces `p_raw`.
* Settlement semantics applies official rounding/truncation, not Python banker's rounding.
* Calibration via Extended Platt / empirical paths; repo has fallback/bucket/maturity/provenance modules.
* Bootstrap path resamples members and instrument noise, preserving cross-bin dependence better than naive independent bin probabilities.
* Current weakness: calibration confidence and sizing confidence are not sufficiently separated. A calibrated probability can still be untradeable if quote is stale/nonfillable or source truth is ambiguous.

### 2.4 Market analysis chain

Current `market_analysis.py` already includes several strong ideas:

* `p_market_no` native buy-NO support.
* `buy_no_quote_available` gate.
* `executable_mask`.
* bootstrap CI.
* market prior and posterior modes.
* no-legacy quote prior gate.

But `MarketAnalysisVNext` must further split:

* displayed market prior
* executable entry price
* executable exit price
* fee-adjusted cost
* fill feasibility
* quote freshness
* stale-book score
* family completeness
* strategy-specific alpha decay
* evidence quality
* no-trade reason.

### 2.5 Candidate selection / FDR

`market_analysis_family_scan.py` and `selection_family.py` are directionally right: full-family hypotheses should be tracked, not only winners. The remaining problem is **family boundary**. A single candidate/snapshot family is too narrow for live-money trading when bins, cities, same weather system, model cycle, and market-maker quote refresh are correlated.

### 2.6 Kelly / risk

`kelly.py` uses typed execution price and fail-closed unknown strategy. That is good. But Kelly still needs:

* `edge_confidence != size_confidence`
* shadow evidence haircut
* quote-freshness haircut
* fill-probability haircut
* strategy-family correlation haircut
* shoulder-tail haircut
* same-weather-system exposure cap
* settlement-source ambiguity cap.

### 2.7 Execution lifecycle

Execution is already limit-order oriented and conservative:

* typed native token routing
* buy_yes / buy_no direction invariant
* mode timeouts
* fill tracking
* reconciliation
* exit triggers
* settlement commands / harvest.

External CLOB reality makes this non-negotiable: all Polymarket orders are limit orders, market orders are marketable limits, FOK/FAK/GTC/GTD semantics matter, and best ask/bid/depth/tick/min-order determine executable edge. ([Polymarket Documentation][12])

### 2.8 True bottleneck

The true bottleneck is not “forecast skill.” It is:

1. **Decision-time truth provenance**
2. **Executable quote truth**
3. **Fill and exit feasibility**
4. **Strategy-family evidence**
5. **Source/settlement ambiguity**
6. **Portfolio correlation and Kelly inflation**
7. **No-trade evidence loss**

---

## 3. Agent Team Debate

### 3.1 Strategy Expansion Architect

> “The repo’s four named strategies are too narrow. The obvious expansion is not another weather model; it is a strategy ontology around timing, source truth, native NO, relative value, family basket, stale quote, and resolution windows.”

Findings:

* Existing candidate stubs already point toward higher-value families.
* Opening inertia and update reaction should be separated by information source: listing discovery, ECMWF release, Day0 observation, settlement source publication, UMA resolution.
* Relative-value strategies are missing: sibling-bin basket, center-vs-shoulder, cross-city same-airmass, high/low joint consistency.

Critic response: “Most of these are not executable if Zeus does not log quote freshness, depth, and fill probability.”

Architect revision: every new strategy must be shadow-first unless it consumes `ExecutableMarketSnapshotVNext`.

### 3.2 Weather Physics / Day0 Reality Agent

> “Day0 is not forecast. Day0 is partially observed physics plus monotone extrema.”

Findings:

* Daily high: once `high_so_far` crosses a threshold, all below-threshold NO/YES implications become deterministic under correct source/rounding.
* Daily low: often becomes informative earlier than high; once morning low has occurred, further downward movement depends on regime, wind, cloud, front timing.
* Local date and DST are not edge cases; they are contract semantics.
* Station/source mismatch kills Day0 alpha.

Critic response: “Public observation availability is not the same as official settlement source availability.”

Weather agent revision: Day0 VNext must store `observation_available_at`, `provider_timestamp`, `source_match_confidence`, and `settlement_source_lag_state`.

### 3.3 Market Microstructure Agent

> “Posterior edge is pre-trade fiction until transformed into executable token cost.”

Findings:

* Native YES and native NO must be priced separately.
* Complement `1 - YES` is diagnostic, not a buy-NO authority.
* Spread, depth, fee, tick, min order, order type, and fill probability decide whether edge exists.
* Stale quote is only an edge if fillable before market refresh; otherwise it is a screenshot artifact.

Critic response: “But Zeus already has typed `ExecutionPrice` and buy-NO flags.”

Microstructure revision: good foundation, but insufficient without `book_timestamp`, `hash_age`, `best_bid_ask_age`, `last_trade_substitution_flag`, `depth_to_size`, `fee_adjusted_entry_cost`, `FOK/FAK feasibility`.

### 3.4 Shoulder / Tail Strategy Agent

> “Shoulder sell is not a bin strategy. It is selling an open-ended catastrophe tail.”

Findings:

* Open shoulder has unbounded state-space relative to finite bins.
* Selling extreme shoulder can look good in small samples because extreme weather is rare.
* Buying shoulder can be rational during heat domes, cold snaps, station anomalies, source lags, or model regime breaks.
* Shoulder trades must be separated from center-bin FDR and Kelly.

Critic response: “Retail lottery bias might overprice shoulders.”

Shoulder revision: yes, but only if observed quote evidence, stress evidence, and regime filters survive.

### 3.5 Data Scarcity / Evidence Design Agent

> “No live trade data is not a reason to stop; it is a reason to log every non-trade.”

Findings:

* Most false edges can be detected before risking money.
* Shadow decisions need quote state, decision-time forecast/obs, strategy ID, no-trade reason, hypothetical fill simulation, and later settlement.
* Negative controls are essential: markets Zeus should not trade, random threshold offsets, stale snapshots, wrong-city placebo.

Critic response: “Shadow fills can lie.”

Data agent revision: shadow is not proof of PnL; it is a falsification ladder.

### 3.6 Portfolio / Kelly / FDR Agent

> “The largest portfolio risk is hidden multiplicity.”

Findings:

* Same city high/low are separate semantic families but can share weather system risk.
* Sibling bins are mutually exclusive but hypothesis tests are correlated.
* Cross-city trades under same front/heat dome are correlated.
* Market-maker stale quotes may cluster across families.

Critic response: “FDR is already implemented.”

Portfolio revision: current FDR is a local gate; VNext needs global hypothesis family IDs.

### 3.7 Settlement / Lifecycle Agent

> “Post-event pre-resolution is the cleanest source-truth trade and the dirtiest operational risk.”

Findings:

* Once official source has published a decisive value, market may still be tradable before resolution.
* Resolution delay creates settlement capture and exit/redeem decisions.
* UMA dispute/unknown/50-50 paths are low-probability but catastrophic if source ambiguity exists.
* Position lifecycle must distinguish `fact_known`, `source_published`, `venue_resolved`, `redeemable`.

Critic response: “Polymarket stops trading after resolution.”

Settlement revision: strategy window is before resolution, not after; after resolution is lifecycle, not alpha. ([Polymarket Documentation][4])

### 3.8 Adversarial Critic

> “Most proposed edges are artifacts.”

Attack list:

* ECMWF release edge may already be arbitraged by faster bots.
* Day0 observation edge may use non-settlement source.
* Shoulder sell may be short-tail premium.
* Native NO edge may be fake if No book is empty.
* Negative risk may not apply to weather markets.
* Market snapshot reconstruction cannot recover queue priority.
* Small sample shadow success can be selection bias.
* Source manipulation/anomaly can invert deterministic-looking Day0 facts.

### 3.9 Dossier Judge

Final tribunal ruling:

* **Implement instrumentation first.**
* **Promote Day0 fact-bound and stale-book detection to highest shadow priority.**
* **Do not live-promote shoulder sell without shoulder-specific FDR/Kelly/stress.**
* **Treat native NO, quote freshness, and fill feasibility as required market primitives.**
* **Turn every no-trade into evidence.**

---

## 4. Strategy Universe Expansion

### 4.1 Important candidate tribunal cards

Each card follows the required 14-field structure, compressed for implementation handoff.

#### 1. Day0 threshold-already-crossed stale quote capture

1. **策略命题:** if official-matching observation already crossed threshold but market quote still prices uncertainty, buy certain/near-certain side.
2. **Edge 来源:** source-truth lag + market quote stale + deterministic extrema.
3. **现实对象:** target-date high/low bin YES/NO token; mostly finite bins and threshold-adjacent bins.
4. **信息源:** WU/IEM/Open-Meteo with source-match flag; Polymarket token book; settlement semantics.
5. **决策时间:** only observation available at timestamp ≤ decision_time; no future daily summary.
6. **可执行价格:** require best ask/bid, depth to intended size, taker fee, tick, min-order, FOK/FAK feasibility.
7. **市场为何错:** UI/market-maker lag, retail not monitoring station, source page lag.
8. **假象风险:** wrong station, provider lag, observation revision, quote non-fill, stale book canceled before order.
9. **缺数据如何测:** forward shadow with provider timestamp + quote timestamp alignment.
10. **最小 shadow:** log every threshold-crossed state and hypothetical best-ask buy.
11. **最小实现:** `Day0BoundState`, `Day0OpportunityDetector`, `QuoteFreshnessScore`.
12. **最大失败:** contract source mismatch / official revision.
13. **Kelly/FDR:** high edge confidence but size confidence capped until settlement verification; separate Day0 family.
14. **Verdict:** `HIGH_PRIORITY_STRATEGY`, `IMPLEMENTATION_READY_FOR_CLAUDE` for shadow.

#### 2. Day0 threshold-impossible / native buy-NO

1. **命题:** if remaining local-day physics makes threshold impossible or bin impossible, buy native NO rather than infer complement.
2. **Edge:** deterministic/probabilistic bound + native NO mispricing.
3. **对象:** native NO token for impossible high/low bin.
4. **信息源:** current obs, sunset/local time, forecast, diurnal model, native NO book.
5. **决策时间:** before market close; no future obs leakage.
6. **价格:** native NO ask, not `1 - YES_bid`; fee-adjusted.
7. **市场错因:** book makers update YES but not NO, or retail anchors to old forecast.
8. **假象:** “impossible” is often only unlikely; storm/front can change; NO book illiquid.
9. **测法:** classify deterministic vs probabilistic impossible; compare quote/fill/outcome.
10. **shadow:** daily low/high impossible flags after local afternoon/evening.
11. **实现:** `BoundClassification = CERTAIN / IMPOSSIBLE / BOUNDED_UNRESOLVED / PROBABLE`.
12. **失败:** low/high semantics inversion.
13. **Kelly/FDR:** native-NO family; no complement fallback.
14. **Verdict:** `HIGH_PRIORITY_STRATEGY`, `SHADOW_FIRST`.

#### 3. ECMWF release-reaction stale book

1. **命题:** when new ECMWF ENS/Open Data step materially shifts bin probabilities and book hash/quotes lag, trade stale side.
2. **Edge:** forecast-release timing + market-maker heartbeat lag.
3. **对象:** bins with largest Δposterior after 00/12 or 06/18 run.
4. **信息源:** ECMWF captured_at/run/step, old vs new posterior, CLOB book timestamp/hash.
5. **决策时间:** only after files actually available; step-level not run-level.
6. **价格:** best ask/bid before heartbeat refresh; require fill simulation.
7. **市场错因:** retail/market-maker update cadence slower than data arrival.
8. **假象:** model update may be low skill; faster bots may consume; fill impossible.
9. **测法:** shadow by release window cohort, alpha half-life.
10. **shadow:** record Δposterior and book changes every 1–5 minutes around delivery windows.
11. **实现:** `ForecastReleaseEvent`, `AlphaDecayMeter`, `BookHeartbeatMeter`.
12. **失败:** captured_at wrong; using forecast file before public availability.
13. **Kelly/FDR:** release-window correlation family; small sizing.
14. **Verdict:** `SHADOW_FIRST`.

#### 4. Native YES/NO asymmetry exploitation

1. **命题:** buy side with native token mispriced, not complement-implied side.
2. **Edge:** separate YES and NO books, inventory imbalance, stale one-sided quoting.
3. **对象:** YES token ask, NO token ask, bid exit surfaces.
4. **信息源:** token IDs, order books, fee, tick, min-order.
5. **决策时间:** current book only.
6. **价格:** entry cost must walk book to size.
7. **市场错因:** traders focus on YES UI; NO book stale/empty/misaligned.
8. **假象:** empty NO book; complement is not executable; split/merge fees/operational constraints.
9. **测法:** shadow both YES and NO entry costs for every candidate.
10. **shadow:** log `best_yes_ask`, `best_no_ask`, `sum_asks`, `native_gap`.
11. **实现:** `NativeTokenQuoteSurface`.
12. **失败:** wrong token routing.
13. **Kelly/FDR:** direction-specific family.
14. **Verdict:** `IMPLEMENTATION_READY_FOR_CLAUDE` instrumentation; `SHADOW_FIRST` strategy.

#### 5. Post-event pre-resolution settlement capture

1. **命题:** after official source value is effectively known but before Polymarket/UMA resolution, buy winning token below redemption value.
2. **Edge:** source publication lag vs venue resolution lag.
3. **对象:** held or new winning YES/NO token before market resolved.
4. **信息源:** official source, venue market status, UMA/resolution status, book.
5. **决策时间:** after source published; before venue resolved.
6. **价格:** near-$1 ask minus fee/carry/cancel risk.
7. **市场错因:** holders want liquidity; bots not reconciling source.
8. **假象:** source ambiguous, challenge risk, market rules differ, liquidity at $0.99 not worthwhile.
9. **测法:** settlement capture verification by event.
10. **shadow:** log known value time, market resolved time, tradable quotes.
11. **实现:** `FactKnownState`, `ResolutionWindowScanner`.
12. **失败:** UMA unknown/50-50 or disputed source.
13. **Kelly/FDR:** low probability high severity; cap by source ambiguity.
14. **Verdict:** `SHADOW_FIRST`, possible `HIGH_PRIORITY_STRATEGY` after verification.

#### 6. Shoulder sell with regime and stress gates

1. **命题:** sell/buy-NO overpriced extreme shoulder only when regime/stress says tail risk contained.
2. **Edge:** retail lottery overpricing + model tail calibration.
3. **对象:** open_shoulder bin native NO / opposite side.
4. **信息源:** ensemble tail, station history, regime, book, sibling bins.
5. **决策时间:** before event or Day0 bounded phase.
6. **价格:** native NO ask; shoulder liquidity gate.
7. **市场错因:** tail lottery demand.
8. **假象:** short-tail risk premium; rare event sample blindness.
9. **测法:** tail stress replay + weather regime cohorts.
10. **shadow:** shoulder-specific outcomes, not pooled with finite bins.
11. **实现:** `ShoulderStrategyVNext`, `ShoulderExposureLedger`.
12. **失败:** heat dome/cold snap correlated city crash.
13. **Kelly/FDR:** separate family, haircut, max cap.
14. **Verdict:** `SHADOW_FIRST`; live without gates = `DANGEROUS_OR_FALSE_EDGE`.

#### 7. Buy mispriced shoulder during extreme regime

1. **命题:** when ensemble/regime and source reality suggest true tail probability > market, buy shoulder YES.
2. **Edge:** market underprices rare/extreme weather; open-ended payoff state.
3. **对象:** upper/lower open shoulder YES.
4. **信息源:** ECMWF/AIFS/ENS disagreement, alerts, observed regime, book.
5. **决策时间:** release/update/Day0.
6. **价格:** shoulder YES ask/depth.
7. **市场错因:** retail anchors to normal climate.
8. **假象:** ensemble tail overdispersion; bad calibration.
9. **测法:** hindcast extreme regime cohorts.
10. **shadow:** only during defined heat/cold regime.
11. **实现:** `ExtremeRegimeTagger`.
12. **失败:** false extreme signal, wide spread.
13. **Kelly/FDR:** long-tail small size, not offsetting short-tail unless same source/event.
14. **Verdict:** `UNKNOWN_BUT_INTERESTING`.

#### 8. Center-vs-shoulder pair trade

1. **命题:** pair overpriced shoulder with underpriced adjacent finite center bin or vice versa.
2. **Edge:** family-relative misallocation, not absolute forecast.
3. **对象:** sibling bins in same event family.
4. **信息源:** complete family probabilities, native token books.
5. **决策时间:** any phase with family completeness.
6. **价格:** two-leg executable cost and fill synchronization.
7. **市场错因:** UI anchoring / tail lottery / center neglect.
8. **假象:** one leg fills, other does not; family incomplete.
9. **测法:** two-leg shadow fill simulator.
10. **shadow:** paired orders as atomic hypothetical.
11. **实现:** `FamilyRelativeValueScanner`.
12. **失败:** legging risk.
13. **Kelly/FDR:** pair exposure ledger; not counted as independent trades.
14. **Verdict:** `SHADOW_FIRST`.

#### 9. Market-maker heartbeat stale quote sweep

1. **命题:** detect quote refresh cadence; trade immediately after source update before heartbeat refresh.
2. **Edge:** quote hash age / best-bid-ask staleness.
3. **对象:** any token with stale book and new information.
4. **信息源:** orderbook hash/timestamp, market channel updates, forecast/obs event.
5. **决策时间:** after public info update.
6. **价格:** FOK/FAK marketable limit; expected fill before refresh.
7. **市场错因:** batch quoting.
8. **假象:** stale visible quote removed before fill; adverse selection.
9. **测法:** shadow fill feasibility using book hash transitions.
10. **shadow:** log info event → quote refresh latency.
11. **实现:** `BookHeartbeatMeter`, `StaleQuoteScore`.
12. **失败:** non-fill and cancel race.
13. **Kelly/FDR:** execution-alpha family, not model-alpha.
14. **Verdict:** `SHADOW_FIRST`.

#### 10. Cross-city same-weather-system relative value

1. **命题:** same front/heat dome shifts multiple cities; market updates one city but not siblings.
2. **Edge:** correlated weather system + fragmented market attention.
3. **对象:** city bins under same regime.
4. **信息源:** forecast fields, city correlation, market quotes.
5. **决策时间:** after regime forecast update.
6. **价格:** per-city executable quote.
7. **市场错因:** attention/liquidity fragmentation.
8. **假象:** microclimate/source differences; correlation overfit.
9. **测法:** historical same-system replay and forward cohorts.
10. **shadow:** tag weather system ID.
11. **实现:** `WeatherSystemClusterId`.
12. **失败:** false diversification / cluster crash.
13. **Kelly/FDR:** cluster-level cap.
14. **Verdict:** `RESEARCH_ONLY` → `SHADOW_FIRST` after cluster model.

#### 11. Negative-risk / family basket consistency

1. **命题:** if event family is neg-risk enabled or economically exhaustive, basket inconsistencies create arbitrage/relative value.
2. **Edge:** family completeness + token conversion / mutually exclusive bins.
3. **对象:** all sibling YES/NO tokens in event.
4. **信息源:** Gamma/CLOB `neg_risk`, token IDs, full books.
5. **决策时间:** current event metadata.
6. **价格:** basket ask/bid after fees and conversion constraints.
7. **市场错因:** fragmented binary pricing.
8. **假象:** weather event not neg-risk enabled; missing Other/placeholder; legging risk.
9. **测法:** metadata audit + synthetic basket replay.
10. **shadow:** log full-family sum of executable asks/bids.
11. **实现:** `FamilyCompletenessScanner`.
12. **失败:** assume negRisk when absent.
13. **Kelly/FDR:** basket not independent edges.
14. **Verdict:** `UNKNOWN_BUT_INTERESTING`, `SHADOW_FIRST` metadata.

#### 12. No-trade alpha preservation

1. **命题:** systematic no-trade reasons are evidence, not dead logs.
2. **Edge:** avoid fee/spread/decay traps; learn where model edge survives execution.
3. **对象:** every rejected candidate.
4. **信息源:** all above.
5. **决策时间:** every evaluator pass.
6. **价格:** explicit rejected executable cost.
7. **市场错因:** none; this is internal edge preservation.
8. **假象:** over-filtering kills opportunity.
9. **测法:** regret decomposition.
10. **shadow:** log reason taxonomy and later outcome.
11. **实现:** `NoTradeReason`, `RejectedOpportunityLedger`.
12. **失败:** missing decisions cause survivorship bias.
13. **Kelly/FDR:** lowers false discovery and size inflation.
14. **Verdict:** `IMPLEMENTATION_READY_FOR_CLAUDE`.

### 4.2 Complete strategy universe

|  # | Category             | Candidate                      | Edge source                   | Target object         | Data required             | Quote requirement    | Failure mode              | Verdict                                                 |
| -: | -------------------- | ------------------------------ | ----------------------------- | --------------------- | ------------------------- | -------------------- | ------------------------- | ------------------------------------------------------- |
|  1 | implemented/existing | settlement_capture             | source/result lag             | near-settlement token | source + book             | ask/bid/depth        | wrong source              | `SHADOW_FIRST`                                          |
|  2 | implemented/existing | shoulder_sell                  | tail overpricing              | open_shoulder NO      | tail model + book         | native NO            | tail crash                | `SHADOW_FIRST`                                          |
|  3 | implemented/existing | center_buy                     | calibrated model edge         | finite bin YES        | forecast/calibration      | YES ask              | mid illusion              | `IMPLEMENTATION_READY_FOR_CLAUDE` only with VNext quote |
|  4 | implemented/existing | opening_inertia                | listing mispricing            | new market bins       | listing time + book       | opening depth        | bad prior                 | `SHADOW_FIRST`                                          |
|  5 | repo-implied         | stale_quote_detector           | quote lag                     | stale token           | book hash + info event    | fillable stale ask   | non-fill                  | `HIGH_PRIORITY_STRATEGY` shadow                         |
|  6 | repo-implied         | liquidity_heartbeat            | maker cadence                 | all books             | quote stream              | timestamp/hash       | heartbeat overfit         | `SHADOW_FIRST`                                          |
|  7 | repo-implied         | neg_risk_basket                | family consistency            | event basket          | negRisk metadata          | multi-leg executable | negRisk absent            | `UNKNOWN_BUT_INTERESTING`                               |
|  8 | repo-implied         | resolution_window_maker        | source-known venue-unresolved | winning token         | source + status           | near-$1 quote        | challenge/source mismatch | `SHADOW_FIRST`                                          |
|  9 | repo-implied         | cross_market_correlation_hedge | same weather system           | city basket           | cluster model             | multi-city depth     | false diversification     | `RESEARCH_ONLY`                                         |
| 10 | repo-implied         | weather_event_arbitrage        | alert/regime lag              | affected cities       | weather alerts + forecast | quote freshness      | news already priced       | `UNKNOWN_BUT_INTERESTING`                               |
| 11 | obvious              | ECMWF 00z reaction             | new ENS info                  | bins                  | run/step availability     | stale ask            | faster bots               | `SHADOW_FIRST`                                          |
| 12 | obvious              | ECMWF 12z reaction             | new ENS info                  | bins                  | run/step availability     | stale ask            | faster bots               | `SHADOW_FIRST`                                          |
| 13 | obvious              | 06/18 short-run Day0           | neglected cycle               | near-term bins        | open data cycle           | book stale           | weaker horizon            | `SHADOW_FIRST`                                          |
| 14 | obvious              | AIFS-vs-IFS disagreement       | model divergence              | uncertain bins        | AIFS/IFS                  | no wide spread       | uncalibrated              | `RESEARCH_ONLY`                                         |
| 15 | obvious              | Day0 high crossed              | deterministic bound           | high bins             | high_so_far               | native side          | wrong station             | `HIGH_PRIORITY_STRATEGY`                                |
| 16 | obvious              | Day0 low crossed               | deterministic bound           | low bins              | low_so_far                | native side          | low semantics error       | `HIGH_PRIORITY_STRATEGY`                                |
| 17 | obvious              | Day0 high impossible           | physical bound                | high bin NO           | obs + diurnal             | NO ask               | not truly impossible      | `SHADOW_FIRST`                                          |
| 18 | obvious              | Day0 low impossible            | physical bound                | low bin NO            | obs + regime              | NO ask               | front change              | `SHADOW_FIRST`                                          |
| 19 | obvious              | source-lag arbitrage           | WU/API lag                    | any bin               | source timestamps         | stale quote          | source mismatch           | `SHADOW_FIRST`                                          |
| 20 | obvious              | native NO asymmetry            | separate NO book              | NO token              | token book                | native NO ask        | empty book                | `IMPLEMENTATION_READY_FOR_CLAUDE` instrumentation       |
| 21 | obvious              | depth-aware edge               | liquidity                     | any token             | L2 book                   | walk-to-size         | edge erased               | `IMPLEMENTATION_READY_FOR_CLAUDE`                       |
| 22 | obvious              | FAK/FOK stale sweep            | stale fill race               | any token             | order type + book         | FAK/FOK              | non-fill                  | `SHADOW_FIRST`                                          |
| 23 | high-upside          | threshold gamma                | boundary sensitivity          | near-threshold bins   | obs + forecast            | tight spread         | rounding error            | `SHADOW_FIRST`                                          |
| 24 | high-upside          | local-midnight trap            | local date asymmetry          | intl city bins        | timezone/DST              | quote                | date mismatch             | `IMPLEMENTATION_READY_FOR_CLAUDE` safety                |
| 25 | high-upside          | family vig compression         | relative pricing              | sibling bins          | full family               | basket depth         | incomplete family         | `SHADOW_FIRST`                                          |
| 26 | high-upside          | inventory asymmetry            | one-sided maker               | YES/NO pair           | order book                | both books           | false complement          | `SHADOW_FIRST`                                          |
| 27 | high-upside          | book-hash age                  | stale market                  | all tokens            | hash stream               | fill before update   | race                      | `SHADOW_FIRST`                                          |
| 28 | high-upside          | post-event pre-UMA             | resolution delay              | winning token         | source + UMA              | near-$1 ask          | disputed source           | `SHADOW_FIRST`                                          |
| 29 | high-upside          | station migration arb          | source switch lag             | affected city         | station metadata          | book                 | rules changed             | `RESEARCH_ONLY`                                         |
| 30 | high-upside          | sensor anomaly risk gate       | source integrity              | affected event        | cross-station obs         | no trade / hedge     | false alarm               | `IMPLEMENTATION_READY_FOR_CLAUDE` safety                |
| 31 | high-upside          | regime tail underpricing       | extreme weather               | shoulder YES          | regime tag                | shoulder ask         | tail overfit              | `UNKNOWN_BUT_INTERESTING`                               |
| 32 | high-upside          | same-airmass city lag          | fragmented attention          | city basket           | cluster ID                | multi-city depth     | correlation error         | `RESEARCH_ONLY`                                         |
| 33 | high-upside          | center-shoulder pair           | family relative value         | two bins              | family model              | atomic legs          | legging risk              | `SHADOW_FIRST`                                          |
| 34 | high-upside          | known-winner discount          | liquidity demand              | winning token         | source known              | bid/ask              | settlement ambiguity      | `SHADOW_FIRST`                                          |
| 35 | speculative          | round-number anchoring         | retail psychology             | threshold bins        | price/outcome history     | executable quote     | narrative overfit         | `UNKNOWN_BUT_INTERESTING`                               |
| 36 | speculative          | lottery shoulder bid           | tail psychology               | shoulder              | book + outcome            | native quote         | actually fair             | `UNKNOWN_BUT_INTERESTING`                               |
| 37 | speculative          | holding reward/carry           | market incentives             | held token            | reward status             | exit value           | variable program          | `RESEARCH_ONLY`                                         |
| 38 | speculative          | min-order dust                 | liquidity artifact            | tiny orders           | min_order                 | min feasible         | non-scalable              | `RESEARCH_ONLY`                                         |
| 39 | speculative          | quote hazard model             | refresh prediction            | all books             | hash transitions          | fill race            | noisy                     | `SHADOW_FIRST`                                          |
| 40 | speculative          | no-trade score                 | alpha preservation            | rejected trades       | decision log              | rejected quote       | overfilter                | `IMPLEMENTATION_READY_FOR_CLAUDE`                       |
| 41 | dangerous            | complement-NO trade            | fake price                    | NO token              | none                      | none                 | non-executable            | `DANGEROUS_OR_FALSE_EDGE`                               |
| 42 | dangerous            | mid/last-price edge            | displayed price               | any token             | UI price                  | none                 | quote illusion            | `DANGEROUS_OR_FALSE_EDGE`                               |
| 43 | dangerous            | stale quote no fill            | screenshot edge               | any token             | delayed book              | none                 | cancel race               | `DANGEROUS_OR_FALSE_EDGE`                               |
| 44 | dangerous            | uncapped shoulder sell         | short-tail                    | shoulder              | insufficient              | native NO            | catastrophic tail         | `DANGEROUS_OR_FALSE_EDGE`                               |
| 45 | dangerous            | fallback obs Day0              | wrong source                  | Day0 bin              | non-official obs          | quote                | settlement mismatch       | `DANGEROUS_OR_FALSE_EDGE`                               |
| 46 | dangerous            | post-resolution trade          | impossible window             | resolved market       | venue status              | none                 | trading stopped           | `DANGEROUS_OR_FALSE_EDGE`                               |

---

## 5. Timing Edge Dossier

| Window                    | Information available              | Likely market behavior       | Executable action           | Quote requirement          | Data requirement          | Leakage risk           | Expected half-life | Shadow-test protocol  | Priority             |
| ------------------------- | ---------------------------------- | ---------------------------- | --------------------------- | -------------------------- | ------------------------- | ---------------------- | ------------------ | --------------------- | -------------------- |
| Listing discovery         | contract bins/source first visible | low liquidity, bad anchors   | no live; snapshot           | full token IDs             | Gamma/CLOB event metadata | wrong contract parse   | hours              | log opening books     | High instrumentation |
| Opening first 30–240m     | old priors, few makers             | inertia / wide spread        | only if ask executable      | depth + min order          | listing timestamp         | stale listing          | medium             | opening cohort        | Medium               |
| ECMWF 00z delivery        | new forecast steps                 | book may lag                 | stale-side sweep            | best ask before refresh    | captured_at per step      | using unavailable file | minutes            | release-window logs   | High                 |
| ECMWF 12z delivery        | new forecast steps                 | active US hours maybe faster | same                        | same                       | same                      | same                   | minutes            | compare 00z/12z       | High                 |
| ECMWF 06/18 shorter run   | near-term update                   | less attention               | Day0/near-term only         | tight spreads              | cycle horizon             | lower skill            | minutes-hours      | cycle cohort          | Medium               |
| AIFS/IFS divergence       | model disagreement                 | market follows one model     | shadow only                 | executable both directions | AIFS + IFS                | uncalibrated           | hours              | disagreement log      | Research             |
| Pre-event convergence     | forecast uncertainty shrinks       | price converges              | buy lagging bins            | exit value too             | forecast age              | late liquidity         | hours              | alpha decay           | Medium               |
| Local sunrise low         | low often near fact                | retail may ignore low        | low-bin bound trade         | native NO/YES              | source obs                | local-date error       | 1–4h               | low Day0 cohort       | High                 |
| Midday high               | high approaches fact               | prices update slowly         | high-bin crossed/impossible | fillable quote             | high_so_far               | station lag            | 15–120m            | high Day0 cohort      | High                 |
| Threshold already crossed | fact exists                        | stale quote possible         | buy certain side            | ask/depth/fee              | obs timestamp             | wrong source           | minutes            | fact-vs-quote log     | Highest              |
| Threshold impossible      | fact/physics bound                 | stale optimism               | buy NO                      | native NO ask              | diurnal model             | not deterministic      | minutes-hours      | impossible classifier | High                 |
| Late evening anomaly      | source spike risk                  | market confused              | mostly risk gate            | avoid/hedge                | cross-station             | anomaly false          | variable           | anomaly detector      | Safety               |
| Post local day            | event over, source pending         | liquidity demand             | settlement capture          | near-$1 discount           | official source           | date window            | hours              | known-fact time       | High                 |
| Pre-UMA resolution        | source known, venue open           | discount to $1               | buy winner / exit loser     | status unresolved          | UMA/Gamma status          | challenge              | ~2h if undisputed  | resolution gap log    | High                 |
| Market-maker heartbeat    | book hash unchanged                | stale batch quotes           | FAK/limit sweep             | hash age + depth           | stream snapshots          | quote canceled         | seconds-minutes    | heartbeat latency     | High shadow          |
| No-trade window           | alpha decays, spread tightens?     | tempting but bad             | do not trade                | explicit reject quote      | all                       | none                   | n/a                | rejected outcome log  | Highest evidence     |

---

## 6. Day0MeasuredTemperatureVNext Dossier

### 6.1 Strategic reframing

Day0 must be treated as:

`partial fact`
→ `bounded outcome`
→ `source-lag state`
→ `nowcast residual`
→ `market-staleness detector`
→ `executable quote`
→ `settlement lifecycle`.

Not as:

`forecast probability vs market probability`.

### 6.2 Day0 deterministic-bound engine

Required object: `Day0BoundState`.

Fields:

* `contract_id`
* `city`
* `metric`: `HIGH` or `LOW`
* `target_local_date`
* `settlement_source_id`
* `station_id`
* `rounding_rule`
* `observation_source`
* `observation_time_utc`
* `observation_available_at_utc`
* `provider_reported_time`
* `current_temp`
* `high_so_far`
* `low_so_far`
* `source_match_confidence`
* `causality_status`
* `local_day_elapsed_fraction`
* `remaining_day_window`
* `bound_classification`
* `deterministic_implications_by_bin`
* `review_required_flags`.

Bound classes:

1. `HIGH_THRESHOLD_CROSSED`
   If rounded high_so_far already places high into or above a bin threshold, any lower incompatible bin is impossible; some shoulder/finite implications may be certain.

2. `LOW_THRESHOLD_CROSSED`
   If rounded low_so_far already places low into or below a bin threshold, upper low bins can become impossible.

3. `HIGH_IMPOSSIBLE_DETERMINISTIC`
   Only after local day is over or after a physically/source-backed hard bound. Most “impossible” intraday high cases are probabilistic, not deterministic.

4. `LOW_IMPOSSIBLE_DETERMINISTIC`
   Similar; after temperature has risen, low can still fall again under fronts/radiative cooling. Deterministic only if local-day/time/source logic supports it.

5. `BOUNDED_BUT_UNRESOLVED`
   True Day0 alpha state: observation narrows distribution but does not settle.

6. `SOURCE_LAG_AWARE_FACT`
   Public or API observation shows fact; settlement source likely catches up; official final not yet published.

### 6.3 Day0 probabilistic nowcast engine

Required object: `Day0NowcastDistribution`.

Inputs:

* diurnal curve by city/season
* local sunrise/sunset
* forecast run latest available
* current observation
* high_so_far / low_so_far
* cloud/wind/front regime if available
* city microclimate prior
* source/provider lag model
* uncertainty band
* ensemble residual
* station-specific bias.

Outputs:

* `settlement_probability_by_bin`
* `remaining_day_extrema_distribution`
* `prob_high_will_cross_threshold`
* `prob_low_will_cross_threshold`
* `time_to_alpha_decay`
* `source_lag_adjusted_confidence`.

High/low asymmetry:

* High is often resolved by afternoon but can spike late under advection or sensor anomaly.
* Low may resolve near morning but can reset late evening under cold front.
* Therefore Day0 high/low cannot share one router or one observed-fraction multiplier.

### 6.4 Day0 opportunity detector

Required object: `Day0Opportunity`.

Trigger when all are true:

1. `observed_fact_exists` or `nowcast_edge_exists`
2. settlement source match confidence above threshold
3. quote stale score above threshold
4. executable spread acceptable
5. depth sufficient to intended size
6. fee-adjusted edge positive
7. fill probability above threshold
8. position lifecycle compatible
9. no settlement ambiguity flag
10. no local-date/DST warning.

Opportunity categories:

* `FACT_CROSSED_MARKET_UNAWARE`
* `FACT_IMPOSSIBLE_MARKET_UNAWARE`
* `BOUNDED_NOWCAST_EDGE`
* `SOURCE_LAG_CAPTURE`
* `POST_EVENT_PRE_RESOLUTION`
* `NO_TRADE_STALE_BUT_UNFILLABLE`
* `NO_TRADE_SOURCE_MISMATCH`.

### 6.5 Safety gates

Hard fail-closed gates:

* future observation leakage
* `observation_time > decision_time + tolerance`
* `available_at` unknown for replay
* settlement source mismatch
* station mismatch
* local date mismatch
* DST ambiguity
* high/low metric mismatch
* contract threshold parse ambiguity
* official source lag unknown
* quote timestamp older than allowed unless strategy is explicitly stale-quote and fillable
* market already resolved
* native token unavailable
* min order not satisfied
* wide spread or no depth
* Paris-like sensor anomaly flag without cross-source confirmation.

### 6.6 Day0 replay / shadow protocol

Every Day0 shadow decision must store:

* `decision_time_utc`
* `target_local_date`
* `city_timezone`
* `market_phase`
* `contract_text_hash`
* `settlement_source_id`
* `station_id`
* `observation_source`
* `observation_time`
* `observation_available_at`
* `high_so_far`, `low_so_far`
* `bound_classification`
* `nowcast_distribution`
* `market_book_timestamp`
* `book_hash`
* `YES/NO best bid/ask/depth`
* `fee_rate`
* `hypothetical_order_type`
* `hypothetical_fill_result`
* `no_trade_reason`
* `later official settlement`
* `regret decomposition`.

Regret decomposition:

* forecast/belief error
* observation/source error
* market quote error
* non-fill error
* fee/spread error
* timing/alpha decay error
* settlement ambiguity error.

### 6.7 Claude-ready artifacts

* `src/signal/day0_bound_state.py`
* `src/signal/day0_nowcast_vnext.py`
* `src/signal/day0_opportunity_detector.py`
* `src/analysis/day0_shadow_report.py`
* `src/contracts/observation_availability.py`
* migration: `day0_shadow_decisions`
* tests:

  * crossed high
  * crossed low
  * high/low metric separation
  * DST boundary
  * provider lag
  * future leakage
  * wrong station fail-closed
  * source anomaly flag
  * native NO quote required.

---

## 7. ShoulderStrategyVNext Dossier

### 7.1 Payoff geometry

`open_shoulder` is economically different from `finite_range`.

Finite bin:

* bounded interval
* sibling probabilities redistribute inside known range
* tail miss capped by adjacent bins.

Open shoulder:

* unbounded state region
* tail model dominates
* rare event sample scarcity
* correlated weather regime crash
* source anomaly exposure
* retail lottery demand possible
* market-maker inventory skew possible.

### 7.2 Sell-shoulder vs buy-shoulder asymmetry

**Sell shoulder / buy native NO**

* looks attractive because extreme tail often fails
* may monetize retail lottery overpricing
* but is short-vol / short-tail
* tail event can be cross-city correlated
* should never use normal Kelly.

**Buy shoulder YES**

* usually negative carry if crowd overpays lottery
* may be best edge during extreme regime, heat dome, cold snap, station/source anomaly, forecast model underreaction
* needs regime filter and calibration evidence.

### 7.3 ShoulderStrategyVNext object model

Fields:

* `is_open_shoulder`
* `shoulder_side`: upper/lower
* `metric`: high/low
* `tail_direction`
* `finite_adjacent_bin`
* `tail_probability_raw`
* `tail_probability_calibrated`
* `tail_probability_stressed`
* `tail_regime_tag`
* `retail_lottery_bias_score`
* `extreme_weather_underpricing_score`
* `source_anomaly_score`
* `native_yes_quote`
* `native_no_quote`
* `liquidity_gate`
* `shoulder_family_id`
* `tail_correlation_cluster`
* `max_loss_scenario`
* `kelly_haircut`
* `max_exposure_cap`
* `no_trade_reason`.

### 7.4 Strategy variants

1. **Sell extreme shoulder**
   Verdict: `SHADOW_FIRST`; live only with shoulder-specific cap.

2. **Buy mispriced shoulder**
   Verdict: `UNKNOWN_BUT_INTERESTING`; only during tagged extreme regimes.

3. **Center-vs-shoulder pair**
   Verdict: `SHADOW_FIRST`; requires two-leg fill simulation.

4. **Tail-hedged shoulder basket**
   Example: sell upper shoulder in one city, buy cheaper correlated tail in another or adjacent market. Verdict: `RESEARCH_ONLY`.

5. **Shoulder no-trade gate**
   If shoulder edge exists only at mid/last price or without native NO depth, record no-trade. Verdict: `IMPLEMENTATION_READY_FOR_CLAUDE`.

### 7.5 Kelly/FDR/risk rules

* Separate `hypothesis_family_id = shoulder:{city}:{metric}:{target_date}:{source}:{regime}`.
* Kelly multiplier max e.g. 0.05–0.20 of normal until forward evidence.
* Hard max notional per shoulder side.
* Cluster cap across same weather system.
* No same-direction shoulder sell across multiple cities under one heat dome/cold front.
* Stress test every candidate under:

  * +2σ forecast error
  * station anomaly
  * late-day advection
  * source revision
  * model tail underdispersion
  * correlated city crash.

### 7.6 Settlement-bound interaction

Shoulder strategy becomes safer only when Day0 bound has eliminated tail. Example:

* upper shoulder sell before event: dangerous.
* upper shoulder sell after high impossible with source-matched observation: closer to deterministic Day0 capture.
* lower shoulder sell in low market after low already crossed: may be wrong direction; must isolate metric semantics.

### 7.7 Verdict

Shoulder is not banned. But shoulder live promotion without VNext gates is a hidden portfolio bomb.

---

## 8. MarketAnalysisVNext Requirement Dossier

### 8.1 Core principle

Not all probabilities are the same object.

Current Zeus should evolve from:

`edge = posterior - market`

to:

`strategy-specific expected executable value = settlement_probability × settlement_payoff - fee_adjusted_entry_cost - execution_risk - exit_risk - source_risk - evidence_haircut`.

### 8.2 Required object model

| Object                             | Generated from                                            | Use boundary                  | Pollution path                    | Downstream consumers         | Tests                     |
| ---------------------------------- | --------------------------------------------------------- | ----------------------------- | --------------------------------- | ---------------------------- | ------------------------- |
| `settlement_probability`           | contract semantics + source truth + raw/Day0 distribution | probability token resolves $1 | wrong source/date/rounding        | strategy, settlement capture | source mismatch, rounding |
| `calibrated_belief`                | Platt/empirical calibration                               | model belief                  | stale calibration, bucket leakage | edge scan                    | OOS calibration           |
| `market_prior_estimator`           | market price model / VWMP / historical book               | crowd prior, not executable   | mid/last price illusion           | fusion only                  | prior-vs-executable diff  |
| `executable_entry_cost_yes`        | YES ask + depth + fee + tick                              | buy YES                       | stale/nonfillable ask             | executor/Kelly               | book-walk                 |
| `executable_entry_cost_no`         | native NO ask + depth + fee + tick                        | buy NO                        | complement-NO                     | executor/Kelly               | native token required     |
| `executable_exit_value`            | bid/depth/fee                                             | exit/stop/harvest             | using ask/mid                     | lifecycle                    | exit simulation           |
| `native_token_quote_surface`       | token books                                               | YES/NO asymmetry              | wrong token id                    | all strategies               | token routing             |
| `bid_ask_depth_fee_tick_min_order` | CLOB book + market metadata                               | feasibility                   | missing fee/min order             | risk/execution               | fee/tick/min tests        |
| `quote_freshness`                  | timestamp/hash/ws events                                  | stale vs current              | stale but unfillable              | stale strategies             | hash-age tests            |
| `fill_probability`                 | depth, order type, queue proxy                            | execution confidence          | impossible queue estimate         | sizing                       | FAK/FOK shadow            |
| `liquidity_confidence`             | depth history, spread, size                               | size cap                      | episodic liquidity                | Kelly                        | liquidity cohort          |
| `stale_book_score`                 | info-event age vs book age                                | timing alpha                  | delayed data                      | stale detector               | release/obs windows       |
| `family_completeness`              | event bins/token IDs                                      | basket/FDR                    | missing bin                       | family scanner               | completeness audit        |
| `negative_risk_consistency`        | negRisk metadata + basket prices                          | arb/relative value            | assumed negRisk                   | basket scanner               | metadata gate             |
| `strategy_alpha_decay`             | cohort timing                                             | urgency                       | overfit half-life                 | executor mode                | decay report              |
| `evidence_quality`                 | replay/shadow/live tier                                   | size confidence               | treating shadow as PnL            | Kelly/risk                   | evidence ladder           |
| `no_trade_reason`                  | evaluator rejects                                         | learning                      | not logged                        | reports                      | taxonomy coverage         |
| `hypothesis_family_id`             | strategy/source/city/metric/time                          | FDR/correlation               | too narrow family                 | selection/risk               | family collision          |
| `shadow_experiment_id`             | experiment registry                                       | cohort analysis               | undocumented changes              | reports                      | experiment immutability   |
| `promotion_status`                 | evidence gates                                            | live eligibility              | manual override                   | strategy profile             | fail-closed               |

### 8.3 Provenance rules

Every `MarketAnalysisVNext` decision must carry:

* `contract_semantics_version`
* `settlement_source_version`
* `forecast_source_run`
* `forecast_captured_at`
* `calibration_model_id`
* `market_snapshot_id`
* `book_timestamp`
* `book_hash`
* `fee_snapshot_id`
* `observation_snapshot_id`
* `strategy_profile_version`
* `evidence_tier`
* `decision_time`.

### 8.4 Quote semantics rules

* `YES ask` is buy-YES cost.
* `YES bid` is sell-YES exit value.
* `NO ask` is buy-NO cost.
* `NO bid` is sell-NO exit value.
* `1 - YES` is only a diagnostic complement.
* Displayed UI price is not execution authority.
* Midpoint is not execution authority.
* Last-trade display is not execution authority.
* If spread is wide, displayed value may be misleading; orderbook rules must dominate. ([Polymarket Documentation][2])

### 8.5 Tests required

* native YES/NO token routing
* complement-NO forbidden
* fee changes alter edge
* min-order blocks trade
* tick rounding changes limit price
* stale book score high but fill probability low → no trade
* market prior allowed for fusion but not for execution
* family completeness missing → no basket strategy
* shadow evidence tier cannot unlock live size
* shoulder family separated from finite bins.

---

## 9. Data Scarcity Evidence Ladder

| Layer                              | What it proves                              | What it cannot prove       | Detects false edge       | Data needed           | Leakage avoidance        | Promotion use             | Size confidence    | Claude artifact               |
| ---------------------------------- | ------------------------------------------- | -------------------------- | ------------------------ | --------------------- | ------------------------ | ------------------------- | ------------------ | ----------------------------- |
| 1 Historical weather replay        | settlement semantics and climate/base skill | tradability                | source/date bugs         | final obs             | strict target-date       | sanity only               | none               | `historical_weather_replay`   |
| 2 Forecast hindcast                | model skill vs time                         | quote edge                 | forecast leakage         | archived forecasts    | captured_at gate         | model eligibility         | low                | `forecast_archive_replay`     |
| 3 Market snapshot reconstruction   | rough quote availability                    | queue/fill                 | mid/last illusion        | public snapshots      | snapshot timestamp       | quote feasibility         | low                | `snapshot_reconstructor`      |
| 4 Synthetic order-book stress      | spread/depth fragility                      | true fill                  | fee/spread erased edge   | param grids           | no outcome tuning        | reject fragile strategies | medium-low         | `orderbook_stress_suite`      |
| 5 Shadow decision logging          | decision process quality                    | live PnL                   | all unlogged no-trades   | full decision schema  | immutable log            | required for promotion    | medium             | `shadow_decision_log`         |
| 6 Forward paper trading            | current-market behavior                     | actual execution           | decay/non-fill proxy     | live books            | no backfill changes      | cohort gate               | medium             | `paper_cohort_runner`         |
| 7 No-trade tracking                | alpha preservation                          | opportunity cost fully     | survivorship bias        | rejected decisions    | log every candidate      | improves filters          | high for risk      | `no_trade_ledger`             |
| 8 Negative controls                | spurious strategy signal                    | edge existence             | overfit/leakage          | placebo markets       | randomization locked     | kill bad families         | reduces size       | `negative_control_suite`      |
| 9 Strategy ablation                | contribution of module                      | live robustness            | unnecessary complexity   | shadow cohorts        | pre-registered ablations | model selection           | medium             | `ablation_report`             |
| 10 Bayesian confidence tiers       | small-N disciplined inference               | guaranteed PnL             | overconfidence           | priors + outcomes     | fixed priors             | promotion tier            | explicit cap       | `evidence_tier_model`         |
| 11 Small-N cohort gate             | early failure                               | rare tail risk             | bad first-order strategy | N cohorts             | no cherry-pick           | shadow→paper              | low-medium         | `promotion_gate`              |
| 12 Regret decomposition            | why trade failed                            | counterfactual certainty   | quote vs model errors    | fills/shadow/outcomes | decision-time only       | task prioritization       | medium             | `regret_decomposer`           |
| 13 Execution feasibility scoring   | fill/spread viability                       | price improvement          | non-executable edge      | orderbook stream      | timestamp align          | executor gating           | high for execution | `fill_feasibility_score`      |
| 14 Alpha decay measurement         | timing half-life                            | future stability           | too-slow strategy        | info events/books     | event-time lock          | mode timeout tuning       | medium             | `alpha_decay_meter`           |
| 15 Settlement capture verification | source/venue lag reality                    | all future source behavior | resolution mismatch      | source publish + UMA  | source logs              | settlement strategy gate  | high for capture   | `settlement_capture_verifier` |

Promotion rule:

* **Tier 0:** idea only, no trade.
* **Tier 1:** deterministic semantics pass.
* **Tier 2:** replay pass.
* **Tier 3:** shadow pass with no-trade logging.
* **Tier 4:** paper cohort pass with quote feasibility.
* **Tier 5:** tiny live pilot under hard cap.
* **Tier 6:** limited live with strategy-specific Kelly haircut.
* **Tier 7:** normal live eligible.

No strategy may use normal Kelly before Tier 6.

---

## 10. Hidden Branch / Novel Angle Hunt

|  # | Non-obvious angle                      | Why normal agent misses it      | Reality asymmetry                                  | Possible edge                                                            | Falsification path                  | Claude candidate             |
| -: | -------------------------------------- | ------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------- | ---------------------------- |
|  1 | Station physical/source integrity risk | assumes oracle immutable        | weather station can be anomalous/source can switch | avoid false deterministic trades; maybe trade source-lag only when clean | cross-station anomaly vs settlement | `SourceAnomalyGate`          |
|  2 | Displayed price substitution           | treats UI price as market       | wide spread may show last trade                    | avoid phantom edge                                                       | compare UI/VWMP/book                | `DisplayedPriceRejector`     |
|  3 | Fee convexity around 50%               | ignores fee shape               | fee ∝ p(1-p) for taker markets                     | edge near center can vanish                                              | fee-adjusted EV audit               | `FeeAdjustedEV`              |
|  4 | Book hash as heartbeat clock           | sees stale as binary            | hash transitions reveal maker cadence              | stale fill timing                                                        | event→hash latency cohort           | `BookHeartbeatMeter`         |
|  5 | Low-temperature early fact             | focuses on highs                | low often resolved near sunrise                    | low Day0 capture                                                         | low cohort outcomes                 | `Day0LowBoundEngine`         |
|  6 | Late-evening low reset                 | assumes low fixed after morning | cold front can reset low                           | avoid false impossible                                                   | regime-tag false cases              | `LowResetRiskModel`          |
|  7 | 06/18 ECMWF neglected cycle            | agents focus 00/12              | shorter cycle still useful near-term               | Day0/near-term lag                                                       | 06/18 cohort                        | `ShortCycleWatcher`          |
|  8 | Step-level release, not run-level      | uses one captured_at            | ECMWF products arrive progressively                | trade when relevant step arrives                                         | step availability logs              | `ForecastStepAvailability`   |
|  9 | Native NO book stale while YES fresh   | complement thinking             | separate token books                               | buy NO edge                                                              | native surface logs                 | `NativeNoAsymmetryScanner`   |
| 10 | Sibling bin liquidity holes            | single-bin model                | family fragmented                                  | relative value / no-trade                                                | full family depth                   | `FamilyLiquidityMap`         |
| 11 | Source publication vs venue resolution | stops at event end              | fact known before UMA resolution                   | settlement capture                                                       | known-time vs resolved-time         | `ResolutionWindowScanner`    |
| 12 | Official daily summary revision        | assumes final fixed             | real-time summaries can change                     | source-risk haircut                                                      | revision audit                      | `ObservationRevisionTracker` |
| 13 | Local date vs UTC day                  | timezone forgotten              | daily summaries may use UTC periods                | avoid wrong settlement                                                   | replay timezone cases               | `LocalDateGuard`             |
| 14 | Shoulder as portfolio heat             | bin-level thinking              | open-ended correlated tail                         | cap hidden short-tail                                                    | stress scenarios                    | `ShoulderExposureLedger`     |
| 15 | No-trade reason as signal              | only logs trades                | rejected edges reveal market friction              | improve strategy selection                                               | rejected outcome report             | `NoTradeLedger`              |
| 16 | Market maker inventory pressure        | assumes efficient quotes        | one-sided spread/depth skew                        | infer liquidity imbalance                                                | depth skew cohort                   | `InventorySkewScore`         |
| 17 | Cross-city attention lag               | city markets isolated           | same weather system updates unevenly               | relative value                                                           | cluster forward test                | `WeatherSystemCluster`       |
| 18 | Sensor anomaly as no-trade trigger     | wants alpha from anomalies      | anomaly can reverse source truth                   | prevent catastrophic edge                                                | compare source network              | `SensorAnomalyNoTrade`       |
| 19 | UMA challenge carry                    | ignores resolution mechanics    | dispute can delay redemption                       | settlement discount model                                                | resolved/disputed cohort            | `ResolutionCarryModel`       |
| 20 | Experiment immutability                | agents tweak shadow definitions | small-N overfit                                    | reliable promotion evidence                                              | experiment hash audit               | `ShadowExperimentRegistry`   |

---

## 11. Adversarial Kill List

1. **Backtest leakage**
   Using final daily high/low or forecast files before `captured_at`.

2. **Quote illusion**
   Using mid, VWMP, displayed price, or last trade as executable entry.

3. **Settlement source mismatch**
   WU/IEM/Open-Meteo fallback used as if official.

4. **Native YES/NO mistake**
   Treating `1 - YES` as buy-NO price.

5. **Shoulder tail crash**
   Small-sample shoulder sell “alpha” destroyed by rare heat dome/cold snap.

6. **Kelly overconfidence**
   Probability confidence interpreted as execution/source/portfolio confidence.

7. **FDR undercount**
   Treating correlated sibling bins/cities/strategies as independent discoveries.

8. **Stale quote non-fill**
   Visible stale book disappears before order hits.

9. **No-liquidity edge**
   Huge theoretical edge at size zero.

10. **Crowding / bot race**
    Forecast-release edge consumed by faster agents.

11. **Observed fact unavailable at decision time**
    Replay reads observation that was published later.

12. **Station mismatch**
    Forecast/obs station not contract station.

13. **Official revision**
    Real-time daily summary changes after trade.

14. **Market family incomplete**
    Missing bin, placeholder, or non-negRisk family breaks basket logic.

15. **False diversification**
    Multiple cities under same air mass treated as separate risk.

16. **Source manipulation/anomaly**
    Sensor spike makes “fact” suspect. Strategy must detect/avoid, not exploit by causing anything.

17. **Fee omission**
    Taker fee erases small center-bin edges.

18. **Tick/min-order failure**
    Rounding limit price or min order changes EV.

19. **Exit value ignored**
    Entry edge exists but no exit/settlement path.

20. **Live-lock bypass**
    Strategy prototype promoted without evidence tier.

---

## 12. Claude Implementation Handoff

### Phase 0: Source / evidence instrumentation

**Objective:** make every future strategy auditable.
**Likely files:** `src/contracts/executable_market_snapshot_v2.py`, `src/contracts/execution_price.py`, `src/data/polymarket_client.py`, `src/state/*`, migrations.
**New artifacts:** `ExecutableMarketSnapshotVNext`, `NativeTokenQuoteSurface`, `FeeAdjustedBook`, `BookHashEvent`, `MarketMetadataAudit`.
**Tests:** best ask/bid semantics, native YES/NO routing, fee/tick/min-order, stale timestamp, missing token fail-closed.
**Blast radius:** evaluator/executor input schemas.
**Rollback:** feature flag `MARKET_ANALYSIS_VNEXT_ENABLED=false`.
**Promotion gate:** 7 days shadow snapshots with no schema gaps.
**Unresolved:** exact Gamma/CLOB metadata source for negRisk/weather family completeness.

### Phase 1: Shadow decision logging

**Objective:** record all decisions and no-trades.
**Likely files:** `src/engine/evaluator.py`, `src/strategy/selection_family.py`, `src/reporting/*`, DB migrations.
**New artifacts:** `ShadowDecisionLogVNext`, `NoTradeReason`, `RejectedOpportunityLedger`, `ShadowExperimentRegistry`.
**Tests:** every rejected candidate logged; immutable experiment ID; no future data fields.
**Blast radius:** DB writes / reports.
**Rollback:** dual-write old and new logs.
**Promotion gate:** no missing no-trade reasons for two full market cycles.
**Unresolved:** storage volume for high-frequency orderbook snapshots.

### Phase 2: Day0 modules

**Objective:** convert Day0 into fact-bound + nowcast + stale quote system.
**Likely files:** `src/signal/day0_*`, `src/data/observation_client.py`, `src/engine/evaluator.py`.
**New artifacts:** `Day0BoundState`, `Day0NowcastDistribution`, `Day0OpportunityDetector`, `ObservationAvailabilityRecord`.
**Tests:** crossed high/low, impossible classification, source lag, local date/DST, future leakage, station mismatch.
**Blast radius:** settlement_capture/Day0 routes.
**Rollback:** keep legacy Day0 route and shadow-only VNext.
**Promotion gate:** 30+ Day0 shadow opportunities with correct later settlement/source classification.
**Unresolved:** WU API timestamp reliability by city.

### Phase 3: Shoulder / risk stress modules

**Objective:** isolate shoulder economics and prevent short-tail contamination.
**Likely files:** `src/strategy/candidates/*`, `src/risk_allocator/*`, `src/strategy/kelly.py`, `src/strategy/risk_limits.py`.
**New artifacts:** `ShoulderStrategyVNext`, `ShoulderExposureLedger`, `TailStressScenario`, `WeatherRegimeTag`.
**Tests:** open_shoulder identification, cap enforcement, Kelly haircut, cluster cap, stress failure.
**Blast radius:** risk sizing.
**Rollback:** block all shoulder live profiles.
**Promotion gate:** shoulder-specific shadow report and stress pass.
**Unresolved:** best regime taxonomy.

### Phase 4: MarketAnalysisVNext

**Objective:** separate belief, market prior, executable price, fill, freshness, evidence.
**Likely files:** `src/strategy/market_analysis.py`, new `market_analysis_vnext.py`, `market_analysis_family_scan.py`, `selection_family.py`.
**New artifacts:** `MarketAnalysisVNext`, `ExecutableEdge`, `EvidenceQuality`, `HypothesisFamilyId`.
**Tests:** complement-NO forbidden, market prior not executable, family completeness, stale-but-unfillable no-trade, fee-adjusted edge.
**Blast radius:** candidate selection.
**Rollback:** run VNext shadow alongside legacy.
**Promotion gate:** VNext and legacy reconciliation report, with explained divergences.
**Unresolved:** acceptable fill-probability model without true queue data.

### Phase 5: Replay / evidence ladder

**Objective:** transform data scarcity into falsification infrastructure.
**Likely files:** `src/backtest/*`, `src/analysis/*`, `docs/reference/data_and_replay.md`.
**New artifacts:** `EvidenceLadder`, `RegretDecomposer`, `AlphaDecayMeter`, `ExecutionFeasibilityScore`.
**Tests:** no leakage, fixed experiment config, placebo controls, ablation outputs.
**Blast radius:** analysis/reporting only.
**Rollback:** no live dependency.
**Promotion gate:** first complete evidence report for all active strategies.
**Unresolved:** public market snapshot archive availability.

### Phase 6: Strategy prototypes behind disabled flags

**Objective:** implement candidates as shadow-only.
**Likely files:** `src/strategy/candidates/stale_quote_detector.py`, `liquidity_provision_with_heartbeat.py`, `resolution_window_maker.py`, `neg_risk_basket.py`, `cross_market_correlation_hedge.py`, `weather_event_arbitrage.py`.
**New artifacts:** candidate-specific shadow cohorts.
**Tests:** strategy profile blocks live; required data missing → no-trade.
**Blast radius:** shadow logs.
**Rollback:** disable candidate flags.
**Promotion gate:** candidate-specific evidence tier ≥ 4.
**Unresolved:** which candidates deserve live pilot after shadow.

### Phase 7: Promotion gates / report integration

**Objective:** make live eligibility evidence-driven.
**Likely files:** `src/strategy/strategy_profile.py`, `architecture/strategy_profile_registry.yaml`, `docs/operations/current_strategy_evidence.md`, reporting.
**New artifacts:** `PromotionStatus`, `StrategyEvidenceReport`, `LiveReadinessTribunal`.
**Tests:** no strategy can bypass evidence tier; manual override requires explicit RED/YELLOW gate.
**Blast radius:** live unlock.
**Rollback:** hard lock strategy profile.
**Promotion gate:** reviewed report with source/quote/execution/settlement evidence.
**Unresolved:** human approval workflow.

---

## 13. Final Ranked Action Plan

### 13.1 最该立刻交给 Claude 做的 10 个任务

1. `ExecutableMarketSnapshotVNext`: token-level YES/NO quote, bid/ask/depth/fee/tick/min-order/hash/timestamp.
2. `ShadowDecisionLogVNext`: every candidate, trade, and no-trade.
3. `NoTradeReasonTaxonomy`: source mismatch, quote stale, no depth, fee erased, family incomplete, shoulder risk, etc.
4. `Day0BoundState`: high/low deterministic-bound engine.
5. `ObservationAvailabilityRecord`: `observation_time` vs `available_at` vs `decision_time`.
6. `QuoteFreshnessScore + BookHeartbeatMeter`.
7. `FillFeasibilityScore`: walk book, FOK/FAK simulation, depth-to-size.
8. `ShoulderExposureLedger + ShoulderKellyHaircut`.
9. `HypothesisFamilyIdVNext`: strategy/source/city/metric/family/time/regime.
10. `SettlementCaptureVerifier`: fact-known/source-published/venue-resolved/redeemable timestamps.

### 13.2 最该 shadow 的 10 个策略

1. Day0 threshold crossed stale quote.
2. Day0 impossible threshold native NO.
3. ECMWF 00z release stale book.
4. ECMWF 12z release stale book.
5. Native YES/NO asymmetry.
6. Post-event pre-resolution settlement capture.
7. Market-maker heartbeat stale sweep.
8. Center-vs-shoulder pair.
9. Shoulder sell with regime gates.
10. No-trade alpha preservation cohort.

### 13.3 最值得继续研究的 10 个 speculative edges

1. AIFS-vs-IFS disagreement.
2. Cross-city same-airmass lag.
3. Weather alert / extreme event arbitrage.
4. Negative-risk basket in actual weather market metadata.
5. Round-number threshold anchoring.
6. Retail lottery shoulder demand.
7. Fee convexity edge erosion by bin type.
8. Resolution challenge carry discount.
9. Station/source migration edge.
10. Inventory-skew prediction from book depth.

### 13.4 最危险、最该压住的 10 个 false edges

1. Complement-NO trades.
2. Mid/last-price edge.
3. Shoulder sell without tail cap.
4. Day0 using fallback source as official.
5. Forecast replay without captured_at.
6. Observation replay without available_at.
7. Cross-city Kelly as independent bets.
8. Stale quote without fill feasibility.
9. Settlement capture before source certainty.
10. Basket arbitrage without verified family completeness/negRisk.

### 13.5 最能提升 Zeus 长期策略能力的 5 个架构升级

1. `MarketAnalysisVNext` probability/quote/evidence separation.
2. `EvidenceLadder` with immutable shadow experiments.
3. `Day0MeasuredTemperatureVNext`.
4. `ShoulderStrategyVNext + tail risk ledger`.
5. `Global Family Risk/FDR` across strategy, city, source, metric, weather system, timing window.

---

## 14. Final Tribunal Statement

Zeus 当前策略升级的真正瓶颈不是气象预测准确率，而是 **decision-time truth 与 executable market truth 的分离不足**。Repo 已经比普通 weather bot 成熟很多：它有 settlement semantics、dual-track high/low、calibration、FDR、typed Kelly、riskguard、execution lifecycle、settlement/redeem learning。但 live-money edge 的下一层不在 “再调一个模型”，而在把每一个候选机会拆成：

`事实是否已知`
→ `事实是否是 settlement source 的事实`
→ `市场 quote 是否还没反应`
→ `quote 是否真的可执行`
→ `fill/fee/depth 是否保留 EV`
→ `strategy family 是否已被重复检验`
→ `position 是否能安全进入/退出/settle`
→ `证据是否足以 sizing`.

最大可利用现实不对称是 **Day0 measured-temperature 的事实边界 + source lag + stale executable quote**。这是 weather prediction market 与普通 Bernoulli market 的根本差异：到 Day0，某些 outcomes 不再是 forecast，而是已经发生的物理事实，只是还没有被市场、source page、market maker 或 resolution lifecycle 完全吸收。

最大不能碰的幻觉是 **displayed probability / mid / complement / stale screenshot edge**。Polymarket 是 token-level CLOB，YES/NO 是原生 outcome tokens，order book、fee、tick、min-order、fill/cancel、resolution status 决定真实交易对象。任何不经过 native bid/ask/depth/fee/fill 的 edge，都不能进入 Kelly。

在没有足够实盘数据下，最优升级路线不是小额乱试，而是 **shadow-first evidence manufacturing**：记录所有 trade 和 no-trade，重建 release/observation/quote timing，设计 negative controls，做 synthetic order-book stress，按 Bayesian confidence tiers 推进。Shadow evidence 不是 live PnL 证明，但足以杀掉大多数 false edge，并把少数策略带到可控 live pilot。

下一轮 Claude implementation 应从 `Phase 0/1` 开始：先建 `ExecutableMarketSnapshotVNext` 和 `ShadowDecisionLogVNext`，再做 `Day0BoundState`。策略 prototype 必须全部 disabled/shadow-only。等 Day0 crossed/impossible、native YES/NO、quote freshness、fill feasibility、settlement verification、shoulder stress 这些证据对象稳定后，Zeus 才应该扩大 live strategy catalog。

[1]: https://github.com/fitz-s/zeus "GitHub - fitz-s/zeus · GitHub"
[2]: https://docs.polymarket.com/trading/orderbook "https://docs.polymarket.com/trading/orderbook"
[3]: https://www.ecmwf.int/en/forecasts/datasets/open-data "https://www.ecmwf.int/en/forecasts/datasets/open-data"
[4]: https://docs.polymarket.com/concepts/resolution "https://docs.polymarket.com/concepts/resolution"
[5]: https://github.com/fitz-s/zeus/blob/main/AGENTS.md "zeus/AGENTS.md at main · fitz-s/zeus · GitHub"
[6]: https://raw.githubusercontent.com/fitz-s/zeus/main/docs/reference/zeus_market_settlement_reference.md "https://raw.githubusercontent.com/fitz-s/zeus/main/docs/reference/zeus_market_settlement_reference.md"
[7]: https://docs.polymarket.com/developers/CLOB/introduction "https://docs.polymarket.com/developers/CLOB/introduction"
[8]: https://docs.polymarket.com/concepts/markets-events "https://docs.polymarket.com/concepts/markets-events"
[9]: https://docs.polymarket.com/advanced/neg-risk "https://docs.polymarket.com/advanced/neg-risk"
[10]: https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily "https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily"
[11]: https://www.wsj.com/business/unusual-weather-bets-on-polymarket-spur-french-investigation-b799bec8 "https://www.wsj.com/business/unusual-weather-bets-on-polymarket-spur-french-investigation-b799bec8"
[12]: https://docs.polymarket.com/developers/CLOB/orders/create-order "https://docs.polymarket.com/developers/CLOB/orders/create-order"
