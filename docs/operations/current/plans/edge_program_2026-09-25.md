# Edge program: plan of record (2026-09-25)

This is the single entry point after a compaction or a fresh session. It restates the
operator's goals in their own terms, fixes the order they are pursued in, and names the
live number each workstream must move. Detailed plans hang off it; they do not replace it.

## Operator goals (2026-09-17 .. 09-25)
1. **Accuracy.** A probability closer to the real world than the market, for every city,
   every lead date, as a continuous chain recomputed at second resolution.
   - Day0: `H = max(H_confirmed, H_remaining)` and `L = min(L_confirmed, L_remaining)`,
     with hour-to-hour correlation kept and the revision possibility of provisional
     observations kept.
   - Forecast sources and observation sources are distinguished. Each keeps its physical
     attributes: accuracy, point location, publish time, step.
   - Known / unknown is made explicit.
   - Probability jumps on new evidence. There is no jitter without evidence, no wrong time
     splicing, and no decision driven by a wide market spread where we hold no data.
2. **Speed.** A new forecast run or observation reaches the decision in seconds; for
   observations, milliseconds of processing. Candidate levers:
   - early use of a run after content validation;
   - scope-local recompute;
   - network, decode and compute split from short writes;
   - native feed vs API;
   - analytic point probability;
   - incremental information by city × metric × lead × source age.
3. **Reliability.** The system never stalls.
   - A wrong forecast is fixed in the probability, never by banning a market.
   - Every fix is durable, not a one-off recovery.
4. **Storage and data path** (from 09-17). Disk stops growing without bound. Nothing
   valuable is lost. Raw files are kept only while a decode can still need them.

## First-principles order
Speed multiplies whatever q is. The settled-data gap between q and the price locates the
defects to fix in the probability chain; it is never a reason to trade less:

| Slice (scripts/scoreboard_panels.py P1, 2026-09-25) | log loss q | log loss price | q beats price |
|---|---|---|---|
| All settled, n=1316, 849 city-date clusters | 0.890 | 0.551 | 41.6 % |
| September, \|q−p\| < 0.15, n=179 | 0.551 | 0.552 | 49.2 % |
| September, \|q−p\| > 0.50, n=26 | 1.889 | 0.395 | 15.4 % |

The largest defects sit where q disagrees most with the price, so that is where a q
repair pays most. Operator law (2026-09-24, 09-28): a weak q is fixed with better data,
sources or algorithms. It is never answered by shrinking size, pausing entries,
licensing gates, default-deny or banning a market. Every fix is one implementation that
applies to every city, metric, unit and lane it logically covers. The work order is
therefore:

0. **Measurement.** Every workstream declares the live number it moves before it starts.
   It is done only when that number moves on live data the same day and on the next full
   UTC day. Tests passing is necessary, not sufficient. The quota problem was "fixed"
   three times and recurred because this step was missing.
1. **Reliability floor.** When the quota runs out, new model runs freeze for hours each
   day, and accuracy and speed both die with it.
   - End the Open-Meteo re-fetch class at the quota choke point.
   - Cursor proof ledger.
   - Cut throughput.
2. **Accuracy.** Improve the acting q wherever settled out-of-sample data locate a
   defect. Trading continues throughout; the result is measured, not used as a gate.
   - The market-anchored family correction (consult design, 09-27) must actually apply.
     Every revision bump reset its corpus, so trades ran on `SourceIdentityBaseline`.
   - Day0 evidence that post-selection vetoes use today (diurnal nowcast, peak passed)
     moves into the Day0 remaining-path probability; price evidence (ask repricing)
     moves into the ranking cost. Nothing rejects a winner after selection unless the
     fact it was selected on changed.
   - Day0 center bias and the resolver operator feed this.
3. **Coverage.** Families dark on ENS boundary ambiguity come back through
   interval-censored evidence (addendum D2).
4. **Speed**, after 1 lands, because the quota tail dominates latency today:
   - replace the fixed 10-minute consistency wait
     (`src/strategy/live_inference/source_clock_vnext.py:17`) with the first
     content-complete replica for the exact run;
   - native-vs-API anchor per exact run;
   - scope-local recompute.
5. **Incremental information** per city × metric × lead × source age. This needs a clean
   latency baseline to separate "arrived later" from "less informative".
6. **Storage.** Retention by reachability, codec migration, freelist reclaim. It runs in
   parallel, owned separately, and never blocks 1-4.

## Scoreboard (the four numbers)
| # | Number | Source | Baseline 2026-09-25 |
|---|---|---|---|
| N1 | Settled log loss q vs price, per scope and per \|q−p\| bucket | `scripts/scoreboard_panels.py --panel P1` | 0.890 vs 0.551 |
| N2 | Forecast availability → first live posterior using it, p50/p90/p99 per source | source_inventory §4 query | 15.8 / 114.6 / 308.9 min (09-19..24) |
| N3 | Venue families with no live posterior, target ≥ today | forecasts DB, `runtime_layer='live'` | 28 of 290 |
| N4 | Open-Meteo day_count and first `source_clock_quota_abort` time | `state/openmeteo_quota.json`, ingest log | 9,000 crossed at 11:56Z / 05:22Z / 16:05Z on 09-23/24/25 |

| N5 | Share of auction cuts ending SELECTED or NO_TRADE, per hour | `tier0_auction_cut.status` in `state/zeus_trades.db` | 09-28: 100 of 1,692 (6 %) |
| N6 | SELECTED cuts that become an ENTRY venue command, per hour | `tier0_auction_cut.actuation_outcome` | 09-29 10-22Z: 35 of 199 |

Observation → Day0 posterior latency, from source_inventory §4: METAR/WU p50 0.3–0.6
min, HKO 8 min.

## Status census (2026-09-30 02:30Z, live HEAD 31b086c2a)
LIVE means landed plus a live metric proving it runs.

| Item | Status | Evidence / gap |
|---|---|---|
| Quota re-fetch class | LIVE | 09-27..29 no aborts. Leaks left: archive_hourly, health probe, ncep_nbm, ifs9 anchor. |
| Selector/validator = one predicate | LIVE | Expected growth `dd8012bd8`; size + ask repricing `c28484d17`; same-token cooldown `82f88fa9d`. Structural antibodies in `tests/solve/test_solver_properties.py`. |
| Day0 diurnal evidence in q | LIVE | `e18722b30`: mixture applied in every Day0 producer, veto deleted; walk-forward dLL -0.058 [-0.070,-0.046]. Legacy Day0Router deleted `9ce365b71`. |
| Family-scoped faults by construction | LIVE | `e8e74d8eb` `family_fault_tag`; one Wilson bound `f3ea99ecd`. |
| One cut-cancel predicate + wake retirement | LIVE | `77ef6b7cd`: cancel_source/stage on every cut; backlog 50.7k -> ~31k and falling (500/min). |
| Actuation outcome persisted | LIVE | `21ea8c372`: tier0_auction_cut.actuation_outcome/_reason. |
| Recovery never starved | LIVE | `8e2fdab1c`/`7e5869935`/`a9eebec93`/`cb4ab1c5d`/`d9dd60470`: one venue-terminal fill law for guard, preflight and reducer; full sweep cannot be starved; per-pass interrupt scope. |
| Venue access (geoblock) | LIVE | `31b086c2a`: host-wide VenueAccessState; 09-29 14:32-17:03Z 403 window was host egress via VPN (`utun10`), operator network decision pending. |
| Book depth never fabricated | LIVE | `761c62b11`: producer keeps ladder on same touch; unknown depth read from venue first. |
| Storage retention | LIVE | Forecast `64e64935a` (queue files + posterior MC arrays); trades `3025ab504` (executable_market_snapshots ~42.5 GB). File bytes need a separate VACUUM. |
| position_events growth 0.75 GB/day | OPEN | 94% is MONITOR_REFRESHED.day0_monitor_probability_receipt (~42 KB/row); fix at the writer. |
| SELECTED -> ENTRY conversion | OPEN | 09-29 funnel: 51/199 MARKET_AUTHORITY_SUPERSEDED (maker witness vs moved book); measure from actuation_outcome now. |
| Market-anchored family correction | TOOLING | Refit on exact q_raw from the complete-capture corpus once labels accrue. |
| 10-minute consistency wait / native vs API / scope-local recompute | NOT STARTED | Speed work after conversion is measured. |
| Incremental information | NOT STARTED | Needs N2 latency baseline. |

## Workstreams
| Stream | Moves | Acceptance |
|---|---|---|
| Open-Meteo re-fetch guard | N4, N2 | First full UTC day: zero quota aborts, day_count < 8,500 (`openmeteo_refetch_guard_2026-09-25.md`) |
| Cursor proof ledger | N4, N2 | `replacement broad reseed cursor commit` advances productive sources |
| Cut throughput | cut completion, collateral-expired count | Zero `CURRENT_WEALTH_COLLATERAL_EXPIRED` from fold lock hold |
| ENS boundary interval | N3 | Dark families get posteriors. 90 % coverage Wilson LB ≥ 0.80 at n ≥ 30 per metric (`ens_boundary_interval_2026-09-25.md`) |
| Accuracy / correction | N1 | Settled log loss moves toward and below the price per scope, walk-forward, cluster = city-day |
| Cut completion | N5 | Cuts reach SELECTED or NO_TRADE; one shared selector/validator predicate; one cancel predicate |

## Execution rules
- One owner per stream: one agent, one worktree, one declared number. Commit after each
  passing slice.
- Consult (ChatGPT Pro, weekly budget) goes only to statistical designs on the money path:
  the family-level q correction and its walk-forward validation, and interval-evidence
  calibration. It is never used
  for mechanical fixes.
- Luna (`model: haiku`, separate quota) does census, attribution, grep and trace work,
  code review, and baseline-vs-after failure-set diffs. Sonnet does bounded
  implementation. Opus does multi-file money-path changes.
- Landing sequence:
  1. independent review;
  2. failure-set diff on `git archive` exports;
  3. strip AI trailers;
  4. `git push origin HEAD:live`;
  5. bare `git -C /Users/leofitz/zeus pull --ff-only`;
  6. `scripts/deploy_live.py restart all`;
  7. read the declared number.

## Next action
1. Read the actuation_outcome funnel per hour; attack the largest non-fact-change bucket.
2. position_events writer: store the Day0 monitor receipt by hash + key fields, not 42 KB per row.
3. Refit the market-anchored family correction on exact q_raw.
4. Speed: consistency wait, native vs API, scope-local recompute.

Landing law (09-29 lesson): pull and restart are one step; a pulled but not restarted
runtime diff arms `deployment_freshness_mismatch` and blocks every submit.

## Diagnostic 2026-09-27: located q defects (offline, `scripts/fit_candidate_calibration.py`)
These numbers are inputs to the q repair list. Under operator law they never justify
shrinking, pausing, gating or default-deny.
Artifacts are in the session scratchpad: `release_price_only_fde1cd8a9b0a00d1.json`, `release_proxy_q_c5c7ebf7f91fd16a.json`, and `release_proxy_q_overround_le_1.3_6cccb3967e314ea3.json`. Scope: 817 city-days, 6,583 decision instants, target dates 08-26..09-23. Uncertainty is 3-date-block with simultaneous bounds.

**Findings:**
- **Defect: acting q carries almost no weight beyond a coherent price.**
  - Pooled blend weight w ≈ 0.01 [0.005, 0.045] in every metric×lane on coherent books. Unpooled w is 0 in most cells.
  - The upper bound of the loss slope at w = 0 is positive everywhere.
  - Out of sample the blend is +0.0006 nats worse than the market, and every |q−p| band fails.
- **LOW markets are not probabilities.** The median YES-ask sum is 3.5 and only 8% of books are coherent. E[y − ask] is −0.17 (Day0) and −0.22 (forecast). The positive w in LOW only reflects incoherent asks.
- **No price-only edge after cost.** E[y − held_ask − fee] ≤ 0 in every metric × lane × P band. Nine HIGH cells are significantly negative. The flat-normalized-P anomaly (+0.048 at P 0.30–0.50) does not survive the executable ask.
- **No fresh-evidence window.** In HIGH, the blend is significantly harmful less than 5 minutes after a q change (+0.025) and at 12–24 h to local day end (+0.020). Every other window is null.
- **Evidence too thin to fit per scope.** Every release verdict is INSUFFICIENT_EVIDENCE: only 13 testable dates, below the floor of 14. The reason is that 09-19 and 09-22 have no rows, because only winner-producing cuts are persisted.

**Limits.** q is a proxy: the latest prior posterior. On rows where the decision's own q_raw exists, |proxy − own| exceeds 0.15 for 12%. The population is winner-only cuts, and P is the ask vector because a mid vector is unavailable.

**Repair levers.** Information the market lacks (earlier and denser observations, Day0 remaining-path evidence now used only as vetoes, station-level bias), execution below fair value (maker), and the family-level correction refit on exact decision-time q_raw from the complete-capture corpus. Each is proven on untouched outcomes.
