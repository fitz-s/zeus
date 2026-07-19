# Universe coverage audit — how much of the Polymarket weather universe Zeus prices and enters

Date: 2026-07-19. Window: last 14 days (2026-07-05 → 2026-07-19) unless noted. All DB reads `sqlite3 -readonly`, all timestamps UTC.

## 0. Table-freshness map (read this first — it changes every other answer)

The team-lead brief named `market_topology_state`, `executable_market_snapshots`, `market_price_history`, `opportunity_fact` in **`state/zeus-world.db`**. Checking those directly:

```
sqlite3 -readonly state/zeus-world.db "SELECT MIN(recorded_at),MAX(recorded_at),COUNT(*) FROM opportunity_fact"        -- 0 rows
sqlite3 -readonly state/zeus-world.db "SELECT MIN(captured_at),MAX(captured_at),COUNT(*) FROM executable_market_snapshots"  -- 0 rows
sqlite3 -readonly state/zeus-world.db "SELECT MIN(recorded_at),MAX(recorded_at),COUNT(*) FROM market_price_history"    -- 0 rows
sqlite3 -readonly state/zeus-world.db "SELECT MIN(recorded_at),MAX(recorded_at),COUNT(*) FROM market_topology_state"   -- 2026-05-19 → 2026-05-28, 3938 rows (stale, 8 weeks dead)
```

All four are empty or dead in `zeus-world.db`. The live copies are in **`state/zeus_trades.db`** instead:

| table | zeus-world.db | zeus_trades.db |
|---|---|---|
| `executable_market_snapshots` | 0 rows | 10,216,667 rows, **2026-05-15 → 2026-07-19 (live)** |
| `market_price_history` | 0 rows | 622,649 rows, dead since 2026-05-28 |
| `opportunity_fact` | 0 rows | 38,555 rows, dead since 2026-05-28 |
| `market_topology_state` | 3,938 rows, dead since 2026-05-28 | 6,490 rows, dead since 2026-05-19 |

The candidate/decision architecture moved to a certificate model that neither team briefed table captures: `decision_certificates` (zeus-world.db, 1.34M rows, **live through 2026-07-19**) plus `selection_family_fact` / `selection_hypothesis_fact` (18,351 families / 354,360 hypotheses in the 14-day window, live) plus the append-only order-lifecycle aggregate `edli_live_order_events` (15,259 rows, live). `opportunity_fact`, `market_price_history`, and `market_topology_state` are pre-certificate-architecture tables nobody dropped. This report uses the live tables; every number below cites its source table.

## 1. Universe inventory (14 days)

`config/cities.json` configures **54 cities**. Querying the live snapshot table for every Gamma `event_slug` captured in the window (`executable_market_snapshots`, grouped and parsed as `{highest|lowest}-temperature-in-{city}-on-{date}`):

```sql
SELECT event_slug, COUNT(*), MIN(captured_at), MAX(captured_at)
FROM executable_market_snapshots
WHERE captured_at >= '2026-07-05'
GROUP BY event_slug;                      -- 915 distinct slugs, 49 distinct cities
```

**49 of 54 configured cities actually have a Polymarket weather market in the window.** Five never do, in this window or ever (checked against all-time `market_topology_state`, which has zero rows for them going back to 2026-05-19): **Auckland, Jakarta, Jinan, Lagos, Zhengzhou**. Jakarta had 33 historical rows through 2026-05-28 then stopped; the other four have never matched. No code-level exclusion exists (`rg` over `src/` for these names turns up nothing but an unrelated comment) — Polymarket simply doesn't appear to run daily-temperature markets for them, or lists them too rarely to have been caught in 65 days of scanning. This is upstream of Zeus and not a lever.

**The bigger structural fact: of the 49 cities with any market, only 8 ever have a LOW-temperature market** in the 14-day window — Hong Kong, London, Miami, NYC, Paris, Seoul, Shanghai, Tokyo. The other 41 (Amsterdam, Ankara, Atlanta, Austin, Beijing, … Wuhan) show **zero `lowest-temperature-in-*` slugs across all 16 days scanned**, only `highest-temperature-in-*`. `cities.json` marks most of those 41 `"weighted_low_calibration_eligible": true` — the config anticipates LOW markets that Polymarket does not currently list. This is the real shape of the tradeable universe: **~49 HIGH families + 8 LOW families ≈ 57 addressable city×metric families/day**, not the ~98 a 49×2 assumption would imply. Table below is per-city high/low day-counts from the same query (`high_days`/`low_days` = distinct target dates seen in 16 calendar days of scan coverage):

```
hong-kong  high=17 low=17   london  high=14 low=16   miami  high=17 low=16
nyc        high=16 low=16   paris   high=14 low=16   seoul  high=15 low=15
shanghai   high=15 low=15   tokyo   high=15 low=15
<41 other cities>  high=15–17 low=0
```

## 2. Funnel: seen → priced → candidate → submitted → filled

Three different granularities exist across the certificate/selection/order-aggregate tables; each is cited separately rather than force-fit into one number.

**Selection lane** (`selection_family_fact` + `selection_hypothesis_fact`, zeus-world.db, 14d):

```sql
SELECT COUNT(DISTINCT family_id) FROM selection_family_fact WHERE created_at >= '2026-07-05';        -- 18,351 families
-- per-family bin coverage and hypothesis counts (see §5)
SELECT rejection_stage, COUNT(*) FROM selection_hypothesis_fact h
  JOIN selection_family_fact f ON f.family_id=h.family_id
  WHERE f.created_at>='2026-07-05' GROUP BY rejection_stage;
```
354,360 hypotheses tested → 27,103 passed prefilter (7.6%) → 11,666 selected post-FDR (3.3% of tested, 43% of prefilter-passed). Rejection stages: `QKERNEL_PREFILTER_REJECTED` 319,996, `QKERNEL_NOT_SELECTED` 15,437, `DIRECTION_LAW_REJECTED` 7,261. Of the 11,666 selected hypotheses, **11,448 (98%) are `buy_no`** — expected, since most bins in an 11-bin distribution are individually unlikely; only 218 are `buy_yes`. Only **2** selected hypotheses in the whole window clear edge > 0.85 (near-certain / dead-bin-class edge) — the tail-bin free-money case is rare in this data, not a large uncaptured pool (see §5).

**Live order lane** (`edli_live_order_events`, zeus-world.db, aggregate append log, 14d):

```sql
SELECT event_type, COUNT(*) FROM edli_live_order_events WHERE occurred_at>='2026-07-05' GROUP BY event_type;
```
346 `DecisionProofAccepted` (candidates that reached the live decision-proof stage) → 346 `SubmitPlanBuilt`/`PreSubmitRevalidated`/`LiveCapReserved`/`ExecutionCommandCreated` → **204 `SubmitRejected`** (59% of the 346 die at the final pre-submit gate) → 160 `VenueSubmitAttempted` → 120 `VenueSubmitAcknowledged` → 147 `UserTradeObserved` (fills; count includes fills of orders acked before the window). `position_events` shows **140** `ENTRY_ORDER_FILLED` rows in the same window, consistent with the aggregate log.

**The dominant drop is between "statistically selected" (11,666 hypotheses / 18,351 families) and "reaches the live order pipeline at all" (346)** — roughly 33x. `no_trade_events` (zeus-world.db; this table itself stopped being written 2026-07-14, so only a 5-day slice: 2026-07-09→14, 2952 rows) shows the reasons operating in that gap: `strategy_economic_floor` 1058, `confidence_band_insufficient` 379, `ultra_low_price_not_authorized` 373, `model_conflict` 336, `mutually_exclusive_family_dedup` 315, `crosscheck_unavailable` 141, `already_held_same_token` 88. These are downstream-of-FDR portfolio/risk/liquidity gates (economic floor, dedup within a family, cross-model disagreement veto), not universe-coverage gaps — flagged here because the question asked where families drop out, but they belong to a different investigation (risk-gate tightness), not city/market coverage.

**Zero families ever priced**: none among the 49 cities with a market — every city that has any Polymarket weather market also shows up in `selection_family_fact` in the 14-day window (cross-checked against §1's city list; the only cities missing from `selection_family_fact` are the same 5 — Auckland, Jakarta, Jinan, Lagos, Zhengzhou — that have no market at all).

## 3. Day0 lane coverage

`src/engine/day0_admission.py:52-117` (`day0_live_admission_rejection_reason`) is the documented pre-submit gate for `DAY0_EXTREME_UPDATED` candidates: 8 ordered checks, first-failing-wins. Two findings, one structural bug and one architectural mismatch.

**Finding A — the city/metric allowlist gates are dead code at the only call site.** `src/engine/event_reactor_adapter.py:16000-16029` constructs:
```python
city_allowlist=frozenset(
    {str(actionable_payload.get("city") or event_payload.get("city") or "")}
),
```
while `ctx.city` two lines above (`_h2_city`, line 15965) is built from the **identical expression**. `ctx.city not in ctx.city_allowlist` (`day0_admission.py:61-62`) can therefore never be true — `DAY0_CITY_NOT_ALLOWLISTED` is unconstructable from this path. `metric_allowlist` isn't passed at all, so it falls back to the dataclass default `frozenset({"high","low"})` (`day0_admission.py:47`) — also all-permissive. `git log -L 16025,16028` shows this has been the shape since the gate was introduced (`0c88ed45b fix(live): gate fragile day0 submits`); it isn't a regression, it was never wired as a real stage allowlist. Whatever "stage rollout" control the operator believes exists here does not run through this code path.

**Finding B — the real city-scoping gate lives one layer up and is exposure-circular, not source-type-driven.** `src/events/reactor.py:4900-5008` (`_Day0LiveFamilyAdmission`) computes `scan_cities` from two inputs: `market_families` (any city×date×metric present in `market_events` for the *current local day only*, broad) unioned with `exposure_families` (cities with an open REST order or an already-held position — `_open_rest_family_rows_for_refresh`, `_held_position_families`). `src/events/reactor.py:7359-7369` passes this `scan_cities` set into `Day0ExtremeUpdatedTrigger`, which only emits `DAY0_EXTREME_UPDATED` events for cities in that set (`src/events/triggers/day0_extreme_updated.py:269-270`: `if self._scan_cities == (): return []`, and the `city IN (...)` clause built from it at line 273-278). So a city can only enter the Day0 fast lane once it *already* has exposure through the non-Day0 path — new cities can't bootstrap into Day0 coverage on their own.

**Observed 14-day footprint** (`decision_certificates`, `certificate_type='ActionableTradeCertificate'`, `event_type='DAY0_EXTREME_UPDATED'`): only **6 of the 49 actively-priced cities** produced any Day0 candidate — Hong Kong (2), London (4), Milan (4), Moscow (5), Paris (62), Seoul (1) — 78 candidates total, none showing `submitted=1` at cert-creation time (that field is a cert-time snapshot, not the outcome — see below). Cross-referencing the 39 distinct `final_intent_id`s from these against `edli_live_order_events` shows real submit activity did occur: 46 `SubmitRejected`, 35 `VenueSubmitAttempted`, 20 `VenueSubmitAcknowledged`, 24 `UserTradeObserved` (fills) — so the Day0 lane is not fully inert, just narrow.

**Source-type answer to "which allowlist expansions are justified by wu_icao vs hko/noaa/cwa":** the source-type distinction (`_METAR_NATIVE_SOURCE_TYPES = {"wu_icao"}`, `day0_admission.py:22`) only requires a fast-obs source when `settlement_source_type == "wu_icao"`; hko and noaa bypass that specific check entirely (`day0_admission.py:69`) — not because they have a wired fast lane, but because the gate simply doesn't apply to them. `src/data/day0_fast_obs.py:177-208` confirms: the free METAR fast lane (aviationweather.gov) is wired for all 50 `wu_icao` cities; HKO's own fast lane is "SPEC'd, not wired"; NOAA cities (Istanbul, Moscow, Tel Aviv) explicitly return `None` ("day0 families for them are not WU-settled"). Given that, the observed 6-city footprint (4 wu_icao + hko + noaa) already mixes source types — **source type is not what's limiting Day0 to 6 cities**; Finding B's exposure-circularity is. Of the 50 wu_icao cities with a wired fast lane, 44 show zero Day0 activity in 14 days purely because they haven't separately built exposure yet. Expanding `scan_cities` to the full `market_families` set (dropping the exposure-gated union, or seeding it from the already-broad `market_families` alone) is the concrete, source-type-blind fix that would unlock Day0 fast-lane pricing for the other ~44 wu_icao cities without touching gates 3-8.

**`edli_no_submit_receipts`** (zeus-world.db) — the other no-submit ledger the brief named — stopped being written 2026-06-29 (14,610 rows total, all before that date); it predates the certificate architecture and has nothing for the 14-day window. Its historical `reason` field is dominated by a generic `event_bound_final_intent_no_submit` (62,671 of ~63k rows) with only a long tail of granular `SUBMIT_ABORTED_*`/`EDLI_LOCKED_OPPORTUNITY_*` reasons — it was never a fine-grained Day0-reason ledger even when live.

## 4. Time coverage / entry lead time

Joined `position_events` (`ENTRY_ORDER_FILLED`, 14d, 140 rows) against `executable_market_snapshots` first-capture time per `condition_id` (indexed lookup via `idx_snapshots_condition_captured`, zeus_trades.db):

```
target_date − first_quote_seen (days):  mean 1.60, median 1.66, min −0.07, max 1.71
entry_time  − first_quote_seen (hours): mean 25.15, median 22.78, min 0.04, max 61.13
```

Markets first appear in the snapshot stream roughly **1.6–1.7 days (≈38–41h) before `target_date`** — this looks like Polymarket's own listing lead time, a ceiling Zeus doesn't control. Zeus's median entry lands **~23 hours after first quote**, i.e. it typically consumes more than half of the available pre-settlement window before entering, leaving a median of only ~15-18 hours of runway. This is consistent with "not systematically catching the earliest, lowest-competition hours of a freshly-listed market" — though with only 140 fills in the window and no separate control group for what edge looked like in hour 1 vs hour 23, this report can state the lag but not price the lost edge.

## 5. Bin coverage

`selection_hypothesis_fact.range_label` distinct-count per family, 14d (zeus-world.db, 18,351 families):

```
mean bins tested = 10.87 / 11,  median = 11,  distribution: 11 bins→17,884 families (97.5%), 10→157, 1→103, {2..9}→190
```

**The engine evaluates essentially all bins, not just near-center ones** — this refutes the premise that bin coverage itself is where opportunity leaks. The absorbing-boundary mask that would matter for "does it take the free NO on an already-impossible bin" is real and correctly implemented (`src/engine/evaluator.py:2463-2510`, `_edli_day0_mask_for_analysis`): once an observed running high/low rules a bin out, its posterior mass is forced to exactly 0 and a runtime assertion (`_assert_day0_mask_consistent_with_observation`, line 2519) fails closed if any masked bin still carries mass. That said, of the 11,666 post-FDR-selected hypotheses in the window, **only 2 clear edge > 0.85** (the near-certain / dead-bin signature) — so whatever free-money tail-bin mispricing exists on Polymarket is empirically rare in this 14-day sample, not a large uncaptured class.

## 6. Multi-market same-truth arbitrage (HIGH+LOW joint exposure)

Mechanism, from code: `src/engine/global_single_order_auction.py:1-33` — a "pure cross-family coordinator for one current executable order" that joins every currently-prepared family's candidate simplex into **one auction** and picks a single terminal-wealth-maximizing winner (`select_global_single_order`) across the whole live candidate universe per cycle. This already prevents double-committing capital across simultaneously-live opportunities system-wide.

But the family key is `(city, target_date, metric)` (`src/data/replacement_cycle_advance_trigger._substrate_refresh_family_key`, referenced at `src/events/reactor.py:4954` and `4983`) — **HIGH and LOW for the same city+date are separate families**, each with an independently-computed probability simplex. The single global auction picks one winner across all families by terminal wealth, but it does not model the physical coupling between a city's HIGH and LOW outcome on the same day (e.g., an extreme HIGH observation constrains the plausible diurnal range and thus the LOW distribution too). Two concrete consequences: (1) no joint-distribution pricing exploits the correlation between a city's HIGH and LOW bins even when both are in the tradeable set (only 8 cities have both — §1); (2) because only **one** order is selected per auction cycle, two independently-legitimate +EV opportunities in the same city's HIGH and LOW markets on the same cycle compete for the single winner slot rather than both executing — the loser waits for the next cycle. This is a real, code-confirmed "treats markets independently" finding, scoped narrowly: it can only matter for the 8 dual-metric cities, and only when both metrics have a live opportunity in the same auction cycle.

---

## Ranked uncovered-opportunity classes

1. **Day0 fast lane restricted to 6 of 49 priceable cities by an exposure-circular gate, not by source type** (§3, Finding B). 44 of 50 wu_icao cities have a wired free METAR fast lane (`day0_fast_obs.py`) but never get scanned for Day0 events because `scan_cities` only admits cities that already have exposure. This is the most concrete, source-type-blind, code-level fix available: broaden `scan_cities` to the full `market_families` set. Dollar magnitude not computable from 14-day Day0 activity alone (78 candidates, ~$242 blocked kelly in-sample) — the lever is in the ~44x city-count expansion, not in scaling today's tiny observed flow.
2. **Dead city/metric allowlist gate at the Day0 admission boundary** (§3, Finding A) — `DAY0_CITY_NOT_ALLOWLISTED`/`DAY0_METRIC_NOT_IN_STAGE` can never fire from the only call site; whatever staged-rollout control was intended isn't running. Low urgency by itself (gate 2's downstream exposure gate is the actual limiter), but worth fixing so a future staged rollout isn't silently a no-op.
3. **41 of 49 tradeable cities have no LOW-temperature market on Polymarket at all** (§1) — largely a Polymarket product-offering fact rather than a Zeus gap, but it means the addressable universe is ~57 city×metric families/day, not ~98; sizing "coverage %" against 98 overstates the miss.
4. **Median entry lag of ~23h into a ~38-41h market window** (§4) — plausible but unpriced; would need a controlled comparison of edge-at-first-quote vs edge-at-entry to turn into a dollar number.
5. **HIGH/LOW joint-truth arbitrage across the 8 dual-metric cities** (§6) — real mechanism gap, narrowly scoped (8 cities, only when both metrics fire the same cycle); likely small in absolute dollars given how few cities qualify.
6. **Tail-bin "impossible bin still priced" mispricing** (§5) — mechanism is correctly implemented and the opportunity is empirically rare (2 near-certain hypotheses in 14 days); not a material uncaptured class right now.

Not investigated here (belongs to a different lane, flagged for the parallel `inv-nosubmit-gates` / risk-gate investigation): the `strategy_economic_floor` / `confidence_band_insufficient` / `mutually_exclusive_family_dedup` reasons that account for most of the 33x drop between FDR-selected hypotheses and live order attempts (§2) are downstream portfolio/risk gates, not universe-coverage gates — they determine how much of an already-seen opportunity gets acted on, not whether Zeus ever saw it.

## ERRATUM (2026-07-19, post-review)

Lever #1 (Day0 `scan_cities` exposure-circularity, §3 Finding B) is **historical, fixed by commit `11aa75e29` "perf(day0): bound market admission scan" (2026-07-16)**. At current HEAD, `_Day0LiveFamilyAdmission` admits `market_families ∪ exposure_families` where `market_families` is built from *all* current-local-day `market_events` rows (`reactor.py:4922-4955`) — not exposure-gated. Live logs show `admitted_families=57` every cycle today, i.e. the full universe. My 14-day sample (07-05→07-19) straddled the fix, so the observed 6-city Day0 footprint was dominated by pre-fix days and does not describe current behavior. Treat §3 Finding B and the "biggest lever" framing in my reply as stale.

Still valid: the tautological `city_allowlist` at `event_reactor_adapter.py:~16026` (Finding A — separate from the fixed gate, still a live dead-code gate), the LOW-market scarcity (8/49 cities, §1), and the ~23h median entry lag after first quote (§4).
