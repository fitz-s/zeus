Verdict/recommendation — medium confidence: concentrate the $1,200 book on maker-first BUY-NO against over-priced central/modal bins, with day-0 nowcast stale-quote trades as the second sleeve and coherence arb only opportunistically; do not concentrate on long YES ranges, far-tail NO, or the WU-warm directional bias.

I could not read any uploaded source artifact for your settlement-graded sample; file search returned no matching files. I therefore treat the n=360 / ~11k bin observations, σ≈1.3–1.8°C, modal realized 22–25%, market modal ≈35%, far-NO win-rate ≈98.5%, and WU-cold center bias as provided measurements, not independently reproduced. Public sources do confirm the key mechanics: Polymarket temperature event pages use Wunderground and whole-degree resolution for these global temperature-bin markets, and the Chicago page says revisions count only until the first datapoint for the following date is published. 
Polymarket
 Polymarket’s own docs say displayed prices are not necessarily executable because you pay the ask when buying, which is critical for every EV estimate below. 
Polymarket Documentation
 Current Polymarket fee docs list Weather at fee-rate 0.05, maker fee 0, maker rebate 25%, and the fee formula fee = C × feeRate × p × (1-p). 
Polymarket Documentation

The most important correction: if “sell NO” is literal, it is the wrong side. In CTF, YES pays $1 if the event occurs and NO pays $1 if it does not; every binary market has exactly those two tokens. 
Polymarket Documentation
 If favorite YES is over-priced at 35% while true q≈23%, the positive trade is buy NO at about 65%, not sell NO. Literal sell-NO is long the favorite and has roughly −12¢/contract EV before fees under q=0.23. Implement the strategy as “buy NO / synthetic short YES on overpriced bin,” not as “sell NO.”

Core math, using current Weather fee docs and your modal measurement: for a buy-YES token at executable ask aY, EV_Y = q - aY - fee(aY). For buy-NO at executable ask aN, EV_N = (1-q) - aN - fee(aN). With Weather fee rate 0.05, fee(0.65)=0.05×0.65×0.35=1.1375¢, matching your “~2% of cash paid” assumption for a 65¢ NO. If modal true q is 0.22–0.25 and NO ask is 0.65, after-cost EV is about +8.9¢ to +11.9¢ per contract, payoff variance is q(1-q)=0.172–0.188, EV/variance is 0.47–0.69, and full-Kelly cost allocation is an unsafe-looking 26–35% before haircutting for model error, correlated weather, liquidity, and settlement risk. Use fractional Kelly only.

Ranked edge table
Rank	Candidate edge	Mechanism and other side	After-cost EV and EV/variance	Confirming data	Failure modes
1	[HIGH] Central/modal overpricing: BUY NO on over-priced favorite	The market appears to price a one-bin point forecast too sharply. With σ≈1.5°C and 1°C bins, a centered modal bin is only about 26% under a Normal; your WU-calibrated realized modal frequency is 22–25%, while market modal YES is ≈35%. The other side is retail/favorite-long traders or NO sellers anchoring to a deterministic forecast.	At q=0.23, askNO=0.65, Weather fee=1.14¢: EV≈0.77−0.65−0.0114=+10.9¢; variance≈0.177; EV/variance≈0.61. At q=0.25, EV≈+8.9¢; EV/variance≈0.47. This survives fees if executable NO ask is below about 1−q−fee, or roughly below 74¢ for q=0.25.	Verify locally: replay settlement-graded quote snapshots using executable L2 NO asks, not UI probability; bucket by lead time, city, high/low, modal price, and clip size. Require realized modal frequency conditional on tradeable modal price ≈35% to stay below ~33–34%.	Strongest case against it: the 35% market price may be right conditional on day-0 observations, station microclimate, WU quirks, or quote-time information absent from the ensemble. It also fails if your measured “market price” is midpoint/last rather than executable ask; Polymarket warns you pay the ask, not the displayed midpoint. 
Polymarket Documentation

2	[HIGH] Day-0 nowcast / partially realized extreme	Daily high/low is a running extreme. Once current station observations rule out lower bins or make one bin nearly locked, stale resting orders become mispriced. Polymarket pages show these markets can remain open until formal resolution, even after the event date has passed. 
Polymarket
 The other side is stale liquidity or traders using forecasts but not the latest station/WU/METAR path.	Not proven by your sample, but structurally strongest per variance when present: if true P=0.95 and YES ask=0.80, after Weather fee EV≈+14.2¢, variance≈0.0475, EV/variance≈3.0. If buying NO on an impossible bin at 80¢ with true NO P≈0.99, EV/variance is also very high.	Verify locally: join WU hourly history, METAR/SYNOP, current running max/min, and L2 books at 5–15 minute cadence; replay from local noon through WU next-day first datapoint; measure fillable stale edge after fees and partial-fill slippage.	Boundary risk is large: WU may lag, revise before the next-day datapoint, or differ from METAR; late sun, frontal passage, airport siting, and timezone/date handling can change the extreme. Competition may erase stale orders quickly.
3	[MEDIUM-HIGH] Same-bin and full-set coherence arbitrage	Same-bin: buy YES_i and NO_i if askY+askN+fees<1; it pays $1 regardless. Full-set: buy all YES outcomes if Σ askY+fees<1; exactly one YES wins. Multi-outcome events are containers for related mutually exclusive markets, and CTF tokens are fully collateralized. 
Polymarket Documentation
 
Polymarket Documentation
 The other side is inconsistent one-sided books or stale makers.	If executable, variance is settlement/operational only; mathematical payoff variance is zero. EV is exactly 1−cost for same-bin/full-YES. Current Chicago example did not show positive complement arb on the two liquid central bins: 45¢ YES + 58¢ NO and 52¢ YES + 49¢ NO are already above 1 before fees. 
Polymarket
	Verify locally: scan every active temp event for YES+NO<1 and ΣYES<1, using depth sufficient for $5/$25/$50 clips, current fee params from getClobMarketInfo, and tick size. Polymarket exposes per-market fee and tick metadata via public client methods. 
Polymarket Documentation
	Capacity is probably tiny, and sequential multi-leg execution creates partial-fill risk. If neg-risk conversion is enabled, a NO can convert atomically into YES tokens for other outcomes, but temperature events’ negRisk flag must be verified locally; Polymarket docs describe the mechanism, not that every temp event has it. 
GitHub

4	[MEDIUM] Central-range NO basket / overround basket	Buy NO on several over-priced central bins. For selected set S with k bins, payoff is k−1 if one selected bin wins and k otherwise. EV is k − q_S − ΣaskNO − fees; if askNO≈1−marketYES, EV is approximately ΣmarketYES_S − q_S − fees. The other side is the same point-forecast overconcentration, but expressed structurally.	Example: market central YES sum 0.75, true q_S 0.63, three NO legs priced 0.65/0.80/0.80: cost≈2.277, expected payoff≈2.37, EV≈+9.3¢ per basket, variance≈0.233, EV/variance≈0.40. That is lower EV/variance and much lower ROI than single modal-NO unless adjacent bins are also over-priced.	Verify locally: estimate realized coverage of top-2/top-3 market bins versus their executable YES sum; compare basket NO EV to best single-leg NO under identical clips.	Adding fair or under-priced shoulders dilutes the modal edge. Multi-leg books are one-sided and shallow; the basket may not fill at the displayed structure.
5	[MEDIUM] Maker-first liquidity provision around your fair q	Post NO bids against over-priced central bins and cancel aggressively on forecast/obs updates. Maker pays zero fees and Weather maker rebate is listed at 25%, while takers pay the fee curve. 
Polymarket Documentation
 The other side is urgent takers and UI-driven retail flow.	On the modal-NO example, maker at 65¢ avoids the 1.14¢ taker fee and may add about 0.28¢ rebate if eligible, improving EV by roughly 1.4¢/contract versus taking. But this is not a standalone edge; it is execution alpha layered on ranks 1–2.	Verify locally: fill-rate and adverse-selection study by quote distance, time-to-settlement, city, and update windows; include cancel latency and queue position.	Makers get filled when wrong unless quotes are pulled immediately. Polymarket’s market-maker docs explicitly recommend canceling stale quotes when market conditions change. 
Polymarket Documentation

6	[LOW-MEDIUM] Far-tail NO	Far bins almost never win, and your sample says far-NO win rate ≈98.5%. The other side is lottery-ticket YES buyers or tail hedgers.	At true NO P=0.985, buying NO at 98¢ has EV≈0.985−0.980−0.001=+0.4¢, variance≈0.0148, EV/variance≈0.27. At 99¢ it is negative after fees. At 97¢ it is attractive, but those quotes are unlikely to be continuously available.	Verify locally: threshold scanner for far NO asks ≤97.5–98.0¢ with depth; condition on open-ended catch-all bins separately.	Your own measurement says the model still under-disperses far tails, so tail NO has hidden crash risk. The books often show no usable NO liquidity or near-99¢ pricing; the Chicago page shows several tail rows with tiny YES prices and missing/near-empty NO books. 
Polymarket

7	[LOW] WU-cold center bias directional trade	If μ* is 0.3–0.4σ cold versus WU, warmer-side bins should be under-priced relative to your raw ensemble. The other side is traders using non-WU realizations or generic station forecasts.	I would not allocate a standalone sleeve. A 0.3σ shift can matter near bin boundaries, but after fees and thin books it should be absorbed into calibrated q_i, not traded as “always warmer.”	Verify locally: city/station fixed effects versus WU only, out-of-sample by month and weather regime; compare to OpenMeteo and official station data.	Your context says the bias collapses to ~0.08σ versus OpenMeteo, so it is source-fragile. A settlement-source mapping error can flip it.
8	[REJECT as primary] Buy 2–3 contiguous YES range	The intuitive thesis is “true σ spans 3–4 bins, so buy the spread.” The problem is that the range usually includes the over-priced modal bin; your measured non-modal near bins are fair to slightly negative after cost, so the modal overpricing dominates.	EV for YES range is q_S−ΣaskYES−fees. Using the same stylized central sum above, q_S≈0.63 and market sum≈0.75, so EV is about −12¢ before fees.	Verify locally: only allow YES range if executable range ask sum is below calibrated q_S by at least fees + 3–5¢ safety margin.	This becomes a trade only when the market under-prices the whole range, not merely when it over-prices the single favorite. Your measurements argue against that being continuous.
Answer to the operator’s thesis

The thesis is directionally right but too narrow and possibly mislabeled. The measured edge is not “favorite-NO is the only edge”; it is that the market is overpricing central probability mass relative to a WU-calibrated settlement distribution. The best single expression is BUY NO on the modal favorite, but the same mechanism can appear as a central NO basket or a coherence structure. The thesis ignores day-0 nowcast, which may have better Kelly characteristics when stale quotes exist, but I cannot rank it above modal-NO without a local replay.

The strongest competing explanation is that the market’s 35% modal price is informed and your ensemble misses station-day information. I reject that as the base case only because your provided settlement-graded data says the modal actually realized 22–25% over 360 families. The gap is too large to explain by fees: at NO ask 65¢, the break-even modal YES probability is still about 33–34%. The smallest fact that would change my verdict is a local replay showing that executable modal NO asks were usually 73–75¢ rather than ~65¢, or that quote-time-conditioned modal realization was ≥33%.

Recommended $1,200 deployment

Run one scanner, not separate discretionary theses. For each city/day/high-low family, compute WU-calibrated q_i, executable YES/NO asks, fee curve, depth, and current observation state.

Allocate roughly 60–70% of risk budget to rank-1 modal/central BUY-NO trades, maker-first. Taker only when after-fee EV is at least 8¢/contract at your intended clip; maker bids can use a 5–6¢ threshold because they avoid taker fee and may earn rebate. Keep ordinary clips around $25–$75, cap one city/day at about $100, and cap one correlated regional weather system/day at $200–$250. The raw full-Kelly numbers are much too high for a small book because effective n is lower than 360 once weather correlation, WU station quirks, and quote selection are included.

Reserve 20–30% for day-0 nowcast stale quotes. Only trade when the observation-constrained probability is extreme enough to clear ask + fee + at least 5¢ safety margin. This sleeve should use FAK/FOK-style execution and tight cancellation because the edge is perishable.

Reserve up to 10% for zero-variance or near-zero-variance coherence scans. Treat them as fill-and-forget only when all legs can be executed safely; otherwise, skip. Do not chase long YES ranges or far-tail NO merely to stay active.

Operational findings to fix before scaling

[BLOCKER] order-side semantics — CTF YES/NO payoff mapping — literal “sell NO” is long the favorite and flips the edge negative — concrete fix: rename and test the strategy as buy_no_on_overpriced_yes; verify locally: unit test with q=0.23, askNO=0.65 must produce positive EV for BUY NO and negative EV for literal sell-NO. 
Polymarket Documentation

[HIGH] executable-price basis — Polymarket UI probability can be midpoint/last, while buys execute at ask — impact: backtests using displayed odds can manufacture false edge — concrete fix: replay L2 order books and depth at intended clip; verify locally: compare modal edge using displayed price, best ask, and VWAP for $25/$50/$100. 
Polymarket Documentation

[HIGH] per-market fee parameters — fees are applied at match time and can be queried per market — impact: assuming a flat 2% cost can mis-rank 35¢ YES, 65¢ NO, and 98¢ NO trades — concrete fix: use getClobMarketInfo(conditionID) fee data and fee = C×r×p×(1-p); verify locally: assert each temp market’s feesEnabled, fee-rate, tick, and min size before scoring. 
Polymarket Documentation

[MEDIUM] settlement boundary/source — WU finalization, first next-day datapoint, whole-degree bins, station mapping, and inclusive bounds are part of the payoff — impact: a one-degree or wrong-station error dominates expected edge — concrete fix: build a settlement mirror for each ICAO/WU URL and exact bin-label parser; verify locally: reproduce resolved outcomes for all 2026-06-08..06-15 families from WU only. 
Polymarket

[MEDIUM] execution ordering and rollback — Polymarket orders can be matched, live, delayed/unmatched, mined/confirmed/failed; partial fills cannot be canceled, only the unfilled portion — impact: multi-leg baskets and coherence arbs have legging risk — concrete fix: use FOK/FAK for arbitrage legs, reserve capital per open order, and reconcile fills before submitting dependent legs; verify locally: simulate partial-fill recovery for same-bin and range baskets. 
Polymarket Documentation

[LOW] source confusion — Polymarket US Weather FAQs describe NWS CLI settlement for Polymarket US contracts, while the global temperature-bin pages I read use Wunderground — impact: mixing sources can create fake WU bias or wrong settlements — concrete fix: route by platform/entity and market rules page, not generic “weather” docs; verify locally: assert every traded market has a Wunderground resolution URL before using the WU calibration. 
Polymarket
 
Polymarket

Highest-value local checks

First, run a tradeable-quote modal-NO replay: for every family in the measured sample, use the actual best NO ask and VWAP at $25/$50/$100, exact fee params, and final WU settlement. This determines whether the measured 35% modal overpricing was harvestable.

Second, run a time-stratified confound test: split modal-NO EV by lead time, local hour, market opened age, and whether the day’s extreme had already been partly realized. This is the direct test of “market had info our ensemble lacked.”

Third, run a day-0 nowcast replay: maintain current running high/low from WU/METAR and score stale quotes from local noon to next-day first datapoint. This is the one alternative that could outrank favorite-NO on EV/variance.

Fourth, run a coherence scanner across all active temperature events. Polymarket’s temperature browse page showed hundreds of active temperature markets and substantial volume at the time accessed, so even rare small incoherences may be worth harvesting. 
Polymarket

Load-bearing assumptions: your settlement-graded sample is correctly mapped to WU, the quoted 35% modal price is close to executable NO≈65¢ after spread/depth, the calibrated σ remains stable out of sample, and the book can cancel maker quotes before forecast/observation updates make them stale. If any one of those fails, reduce size immediately and let only zero-variance coherence or verified nowcast trades through.