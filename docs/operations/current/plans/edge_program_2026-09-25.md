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
Speed multiplies whatever q is. On settled data the acting q is worse than the price:

| Slice (scripts/scoreboard_panels.py P1, 2026-09-25) | log loss q | log loss price | q beats price |
|---|---|---|---|
| All settled, n=1316, 849 city-date clusters | 0.890 | 0.551 | 41.6 % |
| September, \|q−p\| < 0.15, n=179 | 0.551 | 0.552 | 49.2 % |
| September, \|q−p\| > 0.50, n=26 | 1.889 | 0.395 | 15.4 % |

The trades we take are exactly the ones where q disagrees with the price, and there the
market is usually right. The work order is therefore:

0. **Measurement.** Every workstream declares the live number it moves before it starts.
   It is done only when that number moves on live data the same day and on the next full
   UTC day. Tests passing is necessary, not sufficient. The quota problem was "fixed"
   three times and recurred because this step was missing.
1. **Reliability floor.** When the quota runs out, new model runs freeze for hours each
   day, and accuracy and speed both die with it.
   - End the Open-Meteo re-fetch class at the quota choke point.
   - Cursor proof ledger.
   - Cut throughput.
2. **Accuracy gate.** A scope may trade only after its acting q has beaten the price on
   settled out-of-sample data.
   - The market-anchored correction must actually apply. Every revision bump reset its
     corpus, so trades ran on `SourceIdentityBaseline`.
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

Observation → Day0 posterior latency, from source_inventory §4: METAR/WU p50 0.3–0.6
min, HKO 8 min.

## Status census (2026-09-25 17:10Z, live HEAD bbbd62bd4)
LIVE means landed plus a live metric proving it runs.

| Item | Status | Gap |
|---|---|---|
| Source inventory, forecast vs observation | LIVE | `source_inventory_and_probability_race_2026-09-24.md` |
| Day0 max/min remaining-window operator with correlated member paths | PARTIAL | Center bias active in 2 of 24 cells. Resolver operator OFF. |
| Market-anchored correction applied | PARTIAL | Share of corrected trades being measured (census A1) |
| All-cut stall guard | PARTIAL | Reader verdict family-scoped (`14244e742`). No general invariant. |
| Quota re-fetch class | OPEN | Recurs daily. Choke-point guard in flight. |
| Cursor proof ledger | PARTIAL | `26d58cfea`/`8979df299` landed. The same-run re-detection defect is in flight. |
| Dark families (ENS boundary) | IN REVIEW | Branch `claude/agent-ad6df475aede285df` (`43425a013`) |
| 10-minute consistency wait | NOT STARTED | `source_clock_vnext.py:17` |
| Native vs API | NOT STARTED | — |
| Scope-local recompute | PARTIAL | Mechanism exists. Latency not measured post-quota. |
| Analytic point probability | PARTIAL | Day0 analytic path exists. MC remains in confidence matrices. |
| Incremental information | NOT STARTED | Blocked on N2 baseline |
| Jitter / time splicing | PARTIAL | Day0 identity excludes decision time. No global drift metric. |
| Storage | PARTIAL | Trades 199.5 GB (55.7 GB freelist at 09-17), world 102 GB, forecasts 106 GB, 53 GiB free. No installed DB retention job. |

## Workstreams
| Stream | Moves | Acceptance |
|---|---|---|
| Open-Meteo re-fetch guard | N4, N2 | First full UTC day: zero quota aborts, day_count < 8,500 (`openmeteo_refetch_guard_2026-09-25.md`) |
| Cursor proof ledger | N4, N2 | `replacement broad reseed cursor commit` advances productive sources |
| Cut throughput | cut completion, collateral-expired count | Zero `CURRENT_WEALTH_COLLATERAL_EXPIRED` from fold lock hold |
| ENS boundary interval | N3 | Dark families get posteriors. 90 % coverage Wilson LB ≥ 0.80 at n ≥ 30 per metric (`ens_boundary_interval_2026-09-25.md`) |
| Accuracy gate / correction | N1 | Per-scope license on outer folds (`day0_probability_repair_2026-09-25.md` release test) |

## Execution rules
- One owner per stream: one agent, one worktree, one declared number. Commit after each
  passing slice.
- Consult (ChatGPT Pro, weekly budget) goes only to statistical designs on the money path:
  the accuracy gate and license test, and interval-evidence calibration. It is never used
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
Land the ENS boundary interval branch on a GO review. Land the cursor and cut-throughput
slices as they commit. Then start the accuracy gate once census A1 says what share of
trades used a corrected q.
