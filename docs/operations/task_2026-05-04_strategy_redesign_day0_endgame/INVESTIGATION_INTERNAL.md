# Internal Investigation — Global timezone coverage of Zeus's strategy schedule

**Date**: 2026-05-04
**Scope**: code-only. External evidence (NWP release tables, Polymarket actual UTC behavior, UMA proposer per-day variance) flagged as EXTERNAL_INVESTIGATION_NEEDED.
**HEAD**: `d0259327e3fd46c3c2e2fc351676a2f887a38d03` (`chore(sizing): remove safety_cap_usd dead code per 2026-05-04 bankroll doctrine [skip-invariant]`)

Task brief lives at `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN.md` and the critic baseline at `CRITIC_REVIEW_R1.md`. The four established facts from the critic review (`LifecyclePhase` enum at `src/state/lifecycle_manager.py:9-19`, `position_current.phase` populated via `src/engine/lifecycle_events.py`, `lead_hours_to_settlement_close` at `src/engine/time_context.py:58-72`, 4-key strategy_key) are taken as preconditions and not re-verified.

---

## Q1: UPDATE_REACTION cron schedule — where is it actually defined?

The cron is defined inside the **long-running daemon** `com.zeus.live-trading` (`~/Library/LaunchAgents/com.zeus.live-trading.plist`, `KeepAlive=false`, `RunAtLoad=true`, no `StartCalendarInterval`) which runs `python -m src.main`. The schedule is built by APScheduler `BlockingScheduler` inside `src/main.py:731-764`.

- `src/main.py:736-740` — `OPENING_HUNT` is registered as an APScheduler `interval` job, parametrised by `discovery["opening_hunt_interval_min"]`.
- `src/main.py:741-747` — `UPDATE_REACTION` is registered as an APScheduler `cron` job (one entry per UTC time in `discovery["update_reaction_times_utc"]`).
- `src/main.py:748-752` — `DAY0_CAPTURE` is also `interval`, parametrised by `discovery["day0_interval_min"]`.

The interval/cron parameters live in `config/settings.json:3-23`:

```
"opening_hunt_interval_min": 15,
"update_reaction_times_utc": ["07:00", "09:00", "19:00", "21:00"],
"day0_interval_min": 15,
"min_hours_to_resolution": 6
```

So the four UPDATE_REACTION fires (07/09/19/21 UTC) confirmed in `STRATEGIES_AND_GAPS.md §3.2` are correct as of HEAD `d0259327`. APScheduler `cron` triggers at the precise UTC h:m, so it is a **hard schedule**, not an "interval that happens to fire." The scheduler runs in the daemon's process timezone — APScheduler's default is system local, but in this codebase no explicit `timezone=` is passed at line 731, so the default is system local **unless** `BlockingScheduler()` is being constructed UTC-aware elsewhere — EXTERNAL_INVESTIGATION_NEEDED to confirm runtime tz; the documented contract treats the four times as UTC.

Per-fire filtering happens in `src/engine/cycle_runtime.py` via `MODE_PARAMS` injected from `src/engine/cycle_runner.py:335-339`:
- `OPENING_HUNT`: `{max_hours_since_open: 24, min_hours_to_resolution: 24}` — fresh markets younger than 24h, ≥24h to settle.
- `UPDATE_REACTION`: `{min_hours_since_open: 24, min_hours_to_resolution: 6}` — older markets, ≥6h to settle.
- `DAY0_CAPTURE`: `{max_hours_to_resolution: 6}` — last 6h.

These filters are applied at `src/engine/cycle_runtime.py:1994-2004` against the per-market UTC scalars `hours_since_open` and `hours_to_resolution` returned from `find_weather_markets`.

## Q2: 51-city timezone distribution

51 cities total, computed at reference UTC `2026-05-04T12:00Z` via stdlib `zoneinfo`:

| UTC offset | Count | Cities |
|---|---:|---|
| -7 | 3 | Los Angeles, San Francisco, Seattle |
| -6 | 2 | Denver, Mexico City |
| -5 | 5 | Austin, Chicago, Dallas, Houston, Panama City |
| -4 | 4 | Atlanta, Miami, NYC, Toronto |
| -3 | 2 | Buenos Aires, Sao Paulo |
| +1 | 2 | Lagos, London |
| +2 | 7 | Amsterdam, Cape Town, Madrid, Milan, Munich, Paris, Warsaw |
| +3 | 6 | Ankara, Helsinki, Istanbul, Jeddah, Moscow, Tel Aviv |
| +5 | 1 | Karachi |
| +5.5 | 1 | Lucknow |
| +7 | 1 | Jakarta |
| +8 | 12 | Beijing, Chengdu, Chongqing, Guangzhou, Hong Kong, Kuala Lumpur, Manila, Shanghai, Shenzhen, Singapore, Taipei, Wuhan |
| +9 | 3 | Busan, Seoul, Tokyo |
| +12 | 2 | Auckland, Wellington |

- **Min offset**: −7.0 (US-Pacific in DST).
- **Max offset**: +12.0 (NZ).
- **Spread**: 19.0 hours.
- **Median bucket**: +2 / +3 (European clump).
- **Modal bucket**: +8 (12 cities — China/SEA clump).

There is NO IANA gap of 5+ hours in the 51-city footprint that would create an empty "global UTC dead-zone" — see Q5.

## Q3: Per-city Phase-A entry window vs UTC clock

Phase-A (UPDATE_REACTION-eligible per `MODE_PARAMS`) requires `hours_since_open ≥ 24` AND `hours_to_resolution ≥ 6`. Both quantities are derived **from Polymarket Gamma fields** at `src/data/market_scanner.py:996-1011, 1049-1056`:
- `hours_to_resolution = (event.endDate - now_utc) / 3600` — purely UTC-based (line 1000).
- `hours_since_open = (now_utc - event.createdAt) / 3600` — purely UTC-based (line 1054).

Polymarket market open UTC time = `createdAt`, which is **set by Polymarket's market-issuance pipeline at the moment they list the event**. Whether that issuance is itself locked to a per-city local time is EXTERNAL_INVESTIGATION_NEEDED — code only sees the UTC string. From `docs/runbooks/settlement_mismatch_triage.md:148` and `docs/reports/legacy_reference_settlement_source_provenance.md:139-142`, **`endDate` ≠ target_date**: `endDate = target_date + 1 day` (market closes the day after the weather observation date). This is a **silent UTC-vs-city-local skew** that Zeus already exhibits but does not exploit for per-city tiling.

The Phase-A entry-eligible window is therefore not "per-city" in code today — it is a single global UTC predicate `(endDate - now_utc) ∈ (6h, ∞)` AND `(now_utc - createdAt) ≥ 24h`. Cities only differ in what `endDate` Polymarket assigns, not in the gate semantics.

## Q4: Per-city Phase-B (Day0) window vs UTC clock

The `STRATEGIES_AND_GAPS.md §3.2` claim of "UMA 10:00 UTC" has **NO source-code anchor**. There is no constant `RESOLUTION_UTC_HOUR`, `UMA_SETTLE_UTC`, or equivalent in `src/`. The `10:00 UTC` figure appears only in `docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md:156` and was already weakened in `docs/operations/task_2026-05-02_oracle_lifecycle/PLAN_v2.md:209` ("UMA proposer may submit non-10:00 UTC"). DOCUMENTATION DRIFT FLAGGED.

What the code actually uses for Day0 is `lead_hours_to_settlement_close` at `src/engine/time_context.py:58-72` — and its semantics are **city-local end-of-target_date**, not UMA UTC settle:

```python
target_end_local = datetime.combine(target_day, time.min, tzinfo=tz) + timedelta(days=1)
reference_local = reference.astimezone(tz)
delta = target_end_local - reference_local
return delta.total_seconds() / 3600.0
```

i.e. hours from `now_utc` to `(target_date + 1 day)` boundary in `city.timezone`. So the Day0 phase transition (`hours_to_settlement <= 6.0` at `src/engine/cycle_runtime.py:1506`) uses **per-city local time**, not a global UTC clock. This already exists in code today.

For each city, the Day0 UTC window = last 6 city-local hours of `target_date`, i.e. local `[18:00, 24:00)`, mapped to UTC `[18 − offset, 24 − offset) mod 24`:

| Offset bucket | Day0 UTC window | Cities |
|---|---|---|
| −7 | 01:00–07:00 | LA, SF, Seattle |
| −6 | 00:00–06:00 | Denver, Mexico City |
| −5 | 23:00–05:00 (wrap) | Austin, Chicago, Dallas, Houston, Panama City |
| −4 | 22:00–04:00 (wrap) | Atlanta, Miami, NYC, Toronto |
| −3 | 21:00–03:00 (wrap) | Buenos Aires, Sao Paulo |
| +1 | 17:00–23:00 | Lagos, London |
| +2 | 16:00–22:00 | Amsterdam, Cape Town, Madrid, Milan, Munich, Paris, Warsaw |
| +3 | 15:00–21:00 | Ankara, Helsinki, Istanbul, Jeddah, Moscow, Tel Aviv |
| +5 | 13:00–19:00 | Karachi |
| +5.5 | 12:30–18:30 | Lucknow |
| +7 | 11:00–17:00 | Jakarta |
| +8 | 10:00–16:00 | 12 cities (China/SEA) |
| +9 | 09:00–15:00 | Busan, Seoul, Tokyo |
| +12 | 06:00–12:00 | Auckland, Wellington |

UMA-fixed-UTC would imply all 51 cities clump into a single 6h UTC window. Code does **NOT** clump them — they spread across all 24 UTC hours, with peak concentration at UTC 15-18 (European-evening = Asian-late-night = US-pre-midnight overlap).

## Q5: Global coverage matrix

For each UTC hour, count of cities in Day0 (city-local 18:00–24:00) vs Phase-A-eligible (otherwise) — Phase-A "Other" column is empty by construction because every non-Day0 city is, in steady-state, Phase-A-eligible (markets exist year-round, lead ≥ 24h is satisfied for non-Day0 markets).

| UTC | Day0 cities | Phase-A cities | Vacuum |
|---:|---:|---:|---:|
|  0 | 13 | 38 | 0 |
|  1 | 16 | 35 | 0 |
|  2 | 16 | 35 | 0 |
|  3 | 14 | 37 | 0 |
|  4 | 10 | 41 | 0 |
|  5 |  5 | 46 | 0 |
|  6 |  5 | 46 | 0 |
|  7 |  2 | 49 | 0 |
|  8 |  2 | 49 | 0 |
|  9 |  5 | 46 | 0 |
| 10 | 17 | 34 | 0 |
| 11 | 18 | 33 | 0 |
| 12 | 16 | 35 | 0 |
| 13 | 18 | 33 | 0 |
| 14 | 18 | 33 | 0 |
| 15 | 21 | 30 | 0 |
| 16 | 16 | 35 | 0 |
| 17 | 17 | 34 | 0 |
| 18 | 17 | 34 | 0 |
| 19 | 15 | 36 | 0 |
| 20 | 15 | 36 | 0 |
| 21 | 11 | 40 | 0 |
| 22 |  8 | 43 | 0 |
| 23 | 11 | 40 | 0 |

**Empirical refutation of the §3.2 "20h vacuum"**: there is **no UTC hour at which all 51 cities are in "otherwise"**. Min Day0 cities = 2 (UTC 07-08); max = 21 (UTC 15). Some city is in Phase-B-Day0 every single hour of the day. The "20h vacuum" is a per-city framing artifact (a single city has only 6h/day in Day0), never a global property. Conversely, at UTC 07-08 (peak documented UPDATE_REACTION fires), only 2 cities are Day0-eligible — meaning the cron is firing UPDATE_REACTION at exactly the time when the scheduler's *opportunity surface* is most heavily Phase-A-leaning, not a coincidence but an artifact of the EU-AM/US-pre-AM forecast-release alignment.

## Q6: lead_hours_to_settlement_close — actual semantics

`src/engine/time_context.py:58-72` returns hours-to-end-of-target_date in `city.timezone`, NOT hours-to-UMA-UTC. The function:

```python
target_end_local = datetime.combine(target_day, time.min, tzinfo=tz) + timedelta(days=1)
```

— builds the 24:00 local boundary (= 00:00 of `target_date+1` in `city.timezone`), then subtracts UTC `now`. So semantics = city-local end-of-day.

It is consulted in TWO places:
1. `src/engine/cycle_runtime.py:1501-1505` — to drive the runtime Day0 phase transition (`hours_to_settlement <= 6.0` at `:1506` triggers `enter_day0_window_runtime_state` at `:1509`).
2. `src/engine/cycle_runtime.py:1719` — in the monitor-failed branch, for diagnostics only.

The candidate filter at `src/engine/cycle_runtime.py:2003` still uses `market["hours_to_resolution"]` — the **UTC-derived `endDate − now_utc`** from `src/data/market_scanner.py:1000`. So there is a **two-clock split**:
- Phase **transition** (active → day0_window in `position_current.phase`): city-local end-of-target_date.
- Phase **gate for entry candidates** (DAY0_CAPTURE filter): UTC `endDate` (which is `target_date + 1 day`).

These two clocks are skewed by `(24 − city.offset)` hours. For UTC+8 Beijing, the runtime phase flips into `day0_window` at UTC 10:00 of `target_date` (city-local 18:00), but the DAY0_CAPTURE candidate filter `hours_to_resolution < 6` against `endDate = target_date+1` UTC midnight only opens the entry window at UTC 18:00 of `target_date` (city-local 02:00 of `target_date+1`). FLAGGED as a structural inconsistency — the same "Day0" lifecycle word means two different windows in two parts of the runtime.

## Q7: 5 DAY0_CAPTURE branch sites in code

Re-reading the five branches the critic enumerated:

| Site | What it does |
|---|---|
| `src/engine/cycle_runner.py:318` | `_classify_edge_source(mode, edge)` — if `mode == DAY0_CAPTURE` returns the literal string `"settlement_capture"`. Pure mode-to-edge_source mapping, no observation fetch. |
| `src/engine/cycle_runner.py:428` | Freshness-degraded short-circuit — if mid-run freshness verdict says `day0_capture_disabled` and `mode == DAY0_CAPTURE`, the cycle returns early with `skipped=True`. Mode-coupled, not phase-coupled. |
| `src/engine/evaluator.py:931` | `_edge_source_for(candidate, edge)` — if `candidate.discovery_mode == "day0_capture"` returns `"settlement_capture"`. Mirror of `cycle_runner._classify_edge_source` for evaluator-internal calls. |
| `src/engine/evaluator.py:943` | `_strategy_key_for(candidate, edge)` — if `candidate.discovery_mode == "day0_capture"` returns `"settlement_capture"` (the `strategy_key` written to DB). |
| `src/engine/evaluator.py:955` | `_strategy_key_for_hypothesis(candidate, hypothesis)` — same logic for Day0 family-hypothesis path (hypothesis-replay). |

The Day0-observation **fetch** is at `src/engine/cycle_runtime.py:2080-2085` — gated on `mode == deps.DiscoveryMode.DAY0_CAPTURE`, NOT on `position.phase`. The evaluator gate at `src/engine/evaluator.py:1403` (`is_day0_mode = candidate.discovery_mode == "day0_capture"`) and the rejection at `:1416-1425` also pivot on `discovery_mode`, not phase.

**All five branch sites are coupled to `discovery_mode` (the cron-injected `DiscoveryMode` enum), not to `position_current.phase`.** Day0 observations are only ever fetched when the cron fired with mode=DAY0_CAPTURE; a market that is in city-local Day0 but encountered via UPDATE_REACTION cron will not get a Day0 observation fetched.

## Q8: Existing phase machinery — used or shelved?

`LifecyclePhase.DAY0_WINDOW` (src/state/lifecycle_manager.py:12) is consulted in:
- `src/state/lifecycle_manager.py` — enum definition + LEGAL_LIFECYCLE_FOLDS table (lines 34-86).
- `src/engine/lifecycle_events.py:9, 18, 217, 255, 291` — projection writer; `phase_for_runtime_position` derives the canonical phase from runtime state, then `update_trade_lifecycle` persists `position_current.phase`.
- `src/execution/harvester.py:300, 308, 1746` — settlement dual-write skips when `position_current.phase` is already terminal (read-only consumer).

NO read-site in `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`, or `src/engine/evaluator.py` dispatches strategy on `position_current.phase`. The phase column is **populated** correctly (write-side wired) but never **read** as a strategy router. All strategy dispatch flows through `discovery_mode` (cron-injected at `src/main.py:737, 744, 749`).

This is the asymmetry: phase is a side-effect of runtime, not a driver. The runtime correctly transitions `active → day0_window` based on city-local time at `src/engine/cycle_runtime.py:1506-1513`, but no candidate-side, evaluator-side, or strategy_key-side code consumes that fact.

## Q9: Existing per-city-tz triggers

None. Every scheduler trigger in `src/main.py:731-764` is global UTC:
- `OPENING_HUNT` — interval (15 min, no tz).
- `UPDATE_REACTION` — APScheduler `cron` at four UTC times (`update_reaction_times_utc`).
- `DAY0_CAPTURE` — interval (15 min, no tz).
- `harvester` (line 753) — interval (1 hr).
- `heartbeat` (line 754), `venue_heartbeat` (line 757) — interval.

`cron/jobs.json` (the OpenClaw cron at `~/.openclaw/cron/jobs.json`) governs the OpenClaw outer-host scheduler and is unrelated to Zeus's APScheduler — Zeus runs as a standalone launchd `com.zeus.live-trading` daemon and consumes nothing from `cron/jobs.json`. No Zeus-side per-city-tz trigger exists.

## Q10: Polymarket open-time discovery latency

Discovery cadence:
- `src/data/market_scanner.py:139` — `_ACTIVE_EVENTS_TTL = 300.0` (5-min cache).
- `src/data/market_scanner.py:783-846` — `_get_active_events()` returns the cached event list from the most recent Gamma fetch; cache invalidates every 5 minutes.
- `src/engine/cycle_runner.py:444-448` — `_clear_active_events_cache()` is called at the START of each cycle, so each scheduler fire forces a fresh Gamma `_fetch_events_by_tags()` (`src/data/market_scanner.py:886`).

So at every OPENING_HUNT fire (every 15 min), Zeus refetches Gamma. **Worst-case discovery latency for a newly-opened Polymarket market = 15 minutes** (one OPENING_HUNT cycle interval). There is no "sub-15-min first-look" guarantee — the system is interval-polled, not webhook-driven.

The OPENING_HUNT filter at `src/engine/cycle_runner.py:336` requires `max_hours_since_open: 24` AND `min_hours_to_resolution: 24`. So a market is only OPENING_HUNT-eligible during its first 24h of life AND only if `endDate` is ≥ 48h away (since `endDate = target_date + 1 day`, the market must be for `target_date ≥ today + 1 day`).

EXTERNAL_INVESTIGATION_NEEDED: when does Polymarket actually issue these markets (their `createdAt` timestamp distribution)? If they batch-list at e.g. 14:00 UTC daily, the OPENING_HUNT window for any given market is `[createdAt, createdAt + 24h]` UTC, which is itself a fixed UTC window per cohort, not per-city.

---

## Bottom-line conclusions

### 1. Is the §3.2 "20h vacuum" empirically supported or refuted?

**Refuted as a global property.** From the code-derived UTC×Day0 matrix in Q5:
- Every UTC hour has between 2 and 21 cities in their city-local Day0 window.
- No UTC hour has all 51 cities outside their Phase-A AND Day0 windows.
- The "20h vacuum" is a single-city projection (one city has 18h/day not in Day0), incorrectly aggregated as a global statement.

The operator's pushback is empirically correct: with 51 cities × 19h offset spread, no global stagnation exists. What §3.2 actually describes is the gap between four UPDATE_REACTION cron fires from a single-city perspective.

### 2. What's the actual structural problem with current scheduling vs 51-city tiled coverage?

Three structural problems, in order of severity:

(a) **Two-clock split for Day0** (Q6 finding). The runtime phase transition into `day0_window` at `cycle_runtime.py:1506` uses city-local end-of-day. The DAY0_CAPTURE candidate-filter at `cycle_runtime.py:2003` uses UTC `endDate − now`. These differ by ~`24 − city.offset` hours. For UTC+8 cities, the position-phase says "Day0" 8h before the candidate-filter accepts the market for fresh entry. Same word, two windows.

(b) **Mode-coupling, not phase-coupling, of strategy dispatch** (Q7-Q8). All 5 DAY0_CAPTURE branches in `cycle_runner.py:318, 428` and `evaluator.py:931, 943, 955`, plus the observation-fetch gate at `cycle_runtime.py:2083` and the evaluator entrance at `evaluator.py:1403, 1416`, pivot on the cron-injected `discovery_mode` enum. `position_current.phase` is populated but never read. A market in city-local Day0 encountered via the UPDATE_REACTION cron at UTC 07/09/19/21 will be classified `update_reaction` and skip the Day0 observation fetch — the cron-mode label overrides the truth-about-the-market.

(c) **UTC-anchored UPDATE_REACTION schedule misses 50% of city-local forecast windows.** The four UTC times 07/09/19/21 align with European-morning and European-evening forecast updates, which are the right times for European cities. For UTC+8 cities (12 of 51), UTC 07/09 = local 15:00/17:00 (afternoon) and UTC 19/21 = local 03:00/05:00 (pre-dawn). The cron fires at the correct UTC moment for ECMWF/GFS publication, not the correct city-local moment for entry-quality. EXTERNAL_INVESTIGATION_NEEDED on whether NWP release schedules are UTC-locked (likely yes, since ECMWF runs are 00/06/12/18 UTC); if so, the UTC anchor is correct for forecast-update reaction but wrong for per-city pre-Day0 entry timing.

### 3. What in the existing phase machinery is reusable for a per-city-tz redesign vs what must be added?

**Already present (reusable as-is)**:
- `LifecyclePhase` enum at `src/state/lifecycle_manager.py:9-19` with `DAY0_WINDOW`.
- `LEGAL_LIFECYCLE_FOLDS` transition table at `:34-86`.
- `position_current.phase` column populated via `src/engine/lifecycle_events.py`.
- City-local end-of-target-date helpers `lead_hours_to_date_start`, `lead_hours_to_settlement_close` at `src/engine/time_context.py:32-72`.
- Day0 phase transition trigger at `src/engine/cycle_runtime.py:1506-1513` (already runs city-local).
- City-tz inventory in `config/cities.json` (51 cities, 14 distinct UTC offsets).

**Must be added**:
- Phase-coupled (not mode-coupled) candidate filter and observation-fetch gate. Replace `mode == DiscoveryMode.DAY0_CAPTURE` at `cycle_runtime.py:2083` and `evaluator.py:1403, 1416` with a check on the candidate's projected lifecycle phase, derived from `lead_hours_to_settlement_close(target_date, city.timezone, now_utc) <= 6`.
- Phase-coupled strategy_key at `src/engine/evaluator.py:931, 943, 955` and `src/engine/cycle_runner.py:318` — replace `discovery_mode == "day0_capture"` with the same phase derivation.
- A unified Day0 clock — eliminate the two-clock split (Q6) by deriving `hours_to_resolution` for the candidate filter from the same city-local `lead_hours_to_settlement_close`, not from Polymarket UTC `endDate`.
- A scheduler trigger that fires every 15 min globally but evaluates per-city Day0 eligibility on each fire. The current 15-min DAY0_CAPTURE interval is structurally fine — the missing piece is the per-city eligibility check inside the cycle, not a per-city scheduler.

The redesign is a **phase-driven dispatch refactor**, not a scheduler refactor. The four UTC UPDATE_REACTION fires can stay (they align with NWP UTC release times); what changes is that the cron mode no longer overrides the candidate's true lifecycle phase.

---

## Forensic appendix

**Drift A — UMA 10:00 UTC has no source-code anchor.**
Cited in `docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md:156` as "(UMA 10:00 UTC)" without a code citation. Already weakened in `docs/operations/task_2026-05-02_oracle_lifecycle/PLAN_v2.md:209`: "UMA proposer may submit non-10:00 UTC. bridge needs retry/timestamp-align." Also weakened in `docs/operations/tigge_daemon_integration.md:42`: "the prior documented 'TIGGE posts by 10:00 UTC' claim was wrong." There is no `RESOLUTION_UTC_HOUR`, `UMA_SETTLE_UTC`, or hour-specific constant in `src/`. The Day0 boundary in code is *city-local end-of-target-date*, not UMA UTC.

**Drift B — `endDate` vs `target_date`.**
Per `docs/runbooks/settlement_mismatch_triage.md:148` and `docs/reports/legacy_reference_settlement_source_provenance.md:139-142`, Gamma `endDate = target_date + 1 day`. The candidate filter's `hours_to_resolution = endDate − now_utc` (`src/data/market_scanner.py:1000`) therefore measures hours-to-market-close, not hours-to-target-date-end-in-city-local. For a UTC+8 Beijing market with `target_date = 2026-05-08`, `endDate = 2026-05-09T00:00Z` = Beijing 2026-05-09T08:00. The DAY0_CAPTURE filter (`hours_to_resolution < 6`) opens the entry window at UTC 2026-05-08T18:00 = Beijing 2026-05-09T02:00 — i.e., **two hours after city-local target_date has already ended**. For Asian cities the DAY0_CAPTURE window is mostly a post-resolution window, not a pre-resolution observation-capture window. This is a deeper bug than the §3.2 vacuum framing implies.

**Drift C — `discovery_mode` is the de-facto strategy router, not phase.**
The runtime contract at `architecture/runtime_modes.yaml:1-29` states modes are *parameters to one CycleRunner path*, but the DiscoveryMode enum value flows through to:
- `MarketCandidate.discovery_mode` (string) — sticky on the candidate object.
- `_strategy_key_for(candidate, edge)` — DB write of `strategy_key`.
- `_edge_source_for(...)` — DB write of `edge_source`.
- `is_day0_mode` gate inside the evaluator — controls whether observation fetch is attempted.

This makes `discovery_mode` the canonical strategy axis. `position_current.phase` is a parallel column populated by `lifecycle_events.py` but never consulted. The redesign requires either (a) renaming `discovery_mode` to `position_phase_at_decision` and re-deriving from `lead_hours_to_settlement_close`, or (b) keeping `discovery_mode` as a cron-side input and gating dispatch on the phase column instead. Either way the structural decision is *one* refactor, not five.

**Drift D — APScheduler timezone is implicit.**
`src/main.py:731` — `BlockingScheduler()` has no `timezone=` kwarg. APScheduler's default is system local. The launchd daemon `com.zeus.live-trading.plist` does not set a `TZ` env var. The four UPDATE_REACTION times are written as bare strings `"07:00"` etc. in `config/settings.json`, parsed at `src/main.py:742` as `int(h), int(m)`. EXTERNAL_INVESTIGATION_NEEDED on what tz APScheduler actually runs in — likely UTC if the macOS host is UTC, but if the host is America/Chicago (which `/etc/localtime` may be on Fitz's macOS), the four times are CDT and the §3.2 documented "07/09/19/21 UTC" is wrong. This needs a runtime probe; from code alone it's underspecified.
