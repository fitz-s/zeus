# Strategy Redesign v3.1 — Day0-as-Endgame + Global-Tiled Scheduling

**Created**: 2026-05-04
**Last reused/audited**: 2026-05-04 (v3.1 amendments — critic R3+R4 fixes on PR #53)
**Authority basis**: operator directive 2026-05-04 + critic-opus REJECT-AND-RESPLIT verdict on PLAN.md (R1) + critic-opus APPROVED-WITH-CAVEATS verdict on PLAN_v2 (R2) + critic-opus APPROVED-WITH-CAVEATS on PR #53 P2 stages 1-3 (R3) + critic-opus APPROVED-WITH-CAVEATS on PR #53 P3 (R4) + INVESTIGATION_INTERNAL.md (code-grounded) + INVESTIGATION_EXTERNAL.md (NWP / UMA / Polymarket primary sources)
**Status**: PLAN-v3.1 — P0+P1 merged into main via PR #51 (e62710e6); P2 stages 1-3 + P3 D-B mode→phase migration shipped on PR #53 (flag-OFF default, byte-equal preservation). P4 + P5 in follow-up PRs. P6/P7 REJECT pending mechanism re-spec.
**Branch / PR history**: PR #51 (P0+P1 merged); PR #53 (P2+P3 in flight); P4+P5 follow-up.
**Supersedes**: `PLAN.md` (R1, REJECT-AND-RESPLIT). PLAN.md is kept on disk as historical evidence; do not implement from it. **NOTE**: this file was renamed from `PLAN_v2.md` to `PLAN_v3.md` in commit fixing critic R4 ATTACK 8 — version label drift. All §6 packet citations should use the new filename.

## §0.0 UTC-strict execution directive (operator 2026-05-04, post-R2)

> "所有的执行时间都需要严格统一用UTC，我们的交易系统遍布全球，必须采用同一个时间语义在不同时区的表达"

**Hard constraint applied to v3 throughout**:
- The scheduler (APScheduler) ALWAYS operates in UTC. `timezone=ZoneInfo("UTC")` is non-negotiable (P0).
- All cron times are UTC literal. No per-tz scheduler instances.
- City-local concepts (e.g., "end of target_local_date in city.timezone") are COMPUTED into UTC anchors at decision-time; the scheduler only ever sees UTC.
- Per-city Lane 1 triggers (P7) are NOT per-tz scheduler operation. They are APScheduler `date` jobs per (city, target_date), each one firing at a UTC instant derived from city.tz arithmetic done at job-scheduling time.
- All log timestamps, all DB columns, all event-bus messages: UTC. City-local rendering happens only at the user-facing display layer, never internally.
- Why: 51-city × all-timezone trading system needs ONE time semantic. Mixing tz invites correctness bugs at every cross-module boundary (e.g., D-A two-clock split in v2 §4 was exactly this class of bug).

This directive REPLACES any v2 wording that suggested per-city-tz scheduler operation. P7 below is rewritten accordingly.

## §0.1 Changelog v2 → v3 (per critic R2's 10 amendments)

1. **C10 fix** — §6 P2 now enumerates `position_lots` (NO phase column needed; separate state enum axis) and `position_events` (already has `phase_after`; P2 does not change it).
2. **A4+A8 fix** — §6 P6 replaces "add listener" with concrete trigger mechanisms (Polymarket: Gamma API tightened polling; NWP: ingest-pipeline marker file watch).
3. **A9 fix** — §6 P5 specifies multiplier stored at open-time on `decision_chain.kelly_multiplier_used` (already a column); recomputed at exit only if explicitly re-evaluating sizing.
4. **A6 fix** — §6 P5 explicitly notes "Kelly multiplier is post-calibration; no Platt rebuild required."
5. **A14 fix** — §6 P2 specifies atomic per-market multi-position phase transition wrapped in single SAVEPOINT; §8 adds T7 invariant test for no partial-write.
6. **A15 fix** — §6 P7 specifies APScheduler `ThreadPoolExecutor(max_workers=64)` with per-tz coalescing across 14 distinct UTC offsets (not 51 individual schedules).
7. **A16 fix** — §6 P7 specifies `misfire_grace_time = 24*3600` for per-city date jobs + startup catch-up routine that re-fires missed transitions for positions still in target_local_date < 24h.
8. **D-D / A3 caveat** — §6 P0 commit body explicitly notes the cron WILL shift 5h post-fix; operator must verify whether 07/09/19/21 UTC is actually optimal vs. the accidentally-CDT schedule of the past N weeks.
9. **A5+A12+A10/OD3 fix** — §3 clarifies `observed_target_day_fraction` is *clock-time* approximation (informationally degraded for west-of-UTC-5 cities); §7 OD3 documents the regime where option B (hard gate) is correct.
10. **C6 minor fix** — §6 P3 prose updated to "5+1 sites" (5 strategy-dispatch + 1 observation-fetch gate at `cycle_runtime.py:2083`).

### v3 → v3.1 amendments (post-critic-R3 on PR #53, 2026-05-04)

11. **R3 ATTACK 8 fix** — surface Polymarket `startDate`/`endDate` onto the parent market dict from `_parse_event` so `market_phase_from_market_dict` consumes Gamma's explicit timestamps; F1 12:00-UTC is now a real fallback, not the only path. Implemented at `src/data/market_scanner.py::_parse_event` return dict.
12. **R3 ATTACK 6 fix** — rename `_require_utc` → `_require_zero_utc_offset` in `src/strategy/market_phase.py` to honestly reflect the looseness (zero-offset zones like `Europe/London` winter accepted). No production-path change today; future-agent footgun closed.
13. **R3 ATTACK 1 fix** — §6 P2 amended to name `probability_trace_fact.market_phase` (the actual chosen column) instead of the conceptual `decision_chain.market_phase`, with rationale tied to §6.P9 cohort attribution.
14. **R3 ATTACK 2 fix** — §2 boundary anchor wording updated from "city-local end-of-target_date − 24h" to "city-local 00:00 of target_local_date (start-of-target_date local)" to match the DST-correct implementation in `settlement_day_entry_utc`.
15. **R3 ATTACK 5 fix** — A14 SAVEPOINT atomicity DEFERRED-TO-P3 in §6 with rationale recorded inline (P2 ships read-only tag; transition mechanism only materializes with P3 D-B dispatch flip). T7 moves from §8-promise to §8-with-P3 explicit.

---

## §0 What v1 got wrong (consolidated)

### From critic R1 (12 caveats — verbatim short form)

C1. P0 was greenfield, but `LifecyclePhase` enum + `position_current.phase` column + `lead_hours_to_settlement_close()` + `LEGAL_LIFECYCLE_FOLDS` already exist. v2 must AUGMENT, not invent.
C2. v1's proposed `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` collides with the live `LifecyclePhase`. v2 renames to **`MarketPhase`** (function of target_date + city.tz, distinct axis from per-position `LifecyclePhase`).
C3. v1 §5 L2 + §6 P4 (per-phase Platt cohort) is *strictly worse* than the existing design — `lead_days` is already a continuous Platt input regressor (`src/calibration/platt.py:1-10`). v2 DROPS P4 entirely.
C4. v1 mis-classified CHECK-constraint risk as HIGH; it's BLOCKING. 4-key strategy_key set is hardcoded in 2 SQLite CHECK constraints (`src/state/db.py:663-669`, `:759-764`) + 6 Python constants + 46 test files. v2 includes a dedicated migration packet.
C5. v1 had no phase-from-decision_time invariant. v2 makes phase a cycle-snapshot, never wall-clock-derived at point-of-use.
C6. v1 §5 L3 mischaracterized Day0Router as posterior-fusion weight shift. It is metric dispatch (`src/signal/day0_router.py:49-86`). The actual coupling-to-mode is 5 specific `DAY0_CAPTURE` call sites. v2 targets those 5, not a generalized fusion refactor.
C7. v1 had no relationship-test floor. v2 enumerates 6 minimum invariants (§8).
C8. v1 P2 was unsafe — would re-evaluate exit logic on existing positions. v2 gates new exit logic by position-creation-time.
C9. v1 §7 mixed operator-decisions with critic/impl-decisions. v2 splits them.
C10. v1 omitted `position_lots` and `position_events`. v2 enumerates per-table phase-handling.
C11. v1 §2.3 omitted dormants `shoulder_buy` and `center_sell` from current taxonomy. v2 corrects: 6 currently registered (4 active + 2 dormant) → reframed taxonomy.
C12. v1 lacked the mandatory `Last reused/audited:` header. v2 has it.

### From operator (2026-05-04)

O1. **§3.2 "20h vacuum" framing is dead.** With 51 cities tiled across timezones, no global stagnation hour exists. Empirically refuted by INVESTIGATION_INTERNAL Q5 — UTC×Day0 matrix shows 2-21 cities in Day0 at any given UTC hour. The "vacuum" was a single-city projection.

### From external investigation

E1. **Polymarket weather endDate = 12:00 UTC of target date for ALL cities** (verified across 7 cities via Gamma API). NOT the doc-claimed "10:00 UTC". NOT city-local.
E2. **Polymarket creates markets at ~04:04 UTC, T-2 days before target.** Uniform global batch.
E3. **Settlement ≠ endDate.** endDate is trading cutoff. Actual UMA OO settlement happens hours later (variable, gated by Wunderground daily-summary lag + 2h UMA liveness).
E4. **East-west epistemic asymmetry from 12:00 UTC fixed end:**
   - Wellington (UTC+12): trading closes ~midnight NZST → near-complete day observation pre-close
   - London (UTC+1): closes ~13:00 BST → afternoon peak unobserved at trade-close
   - LA (UTC-7): closes ~05:00 PDT → ENTIRE target day unobserved at trade-close

### From internal investigation (4 drifts to fix before strategy work)

D-A. **Two-clock split**: `cycle_runtime.py:1506` uses city-local end-of-target-date for phase transition; `cycle_runtime.py:2003` uses UTC `endDate − now` for candidate filter. Skewed by `(24 − city.offset)` hours. For UTC+8, the DAY0_CAPTURE entry window opens 2h AFTER city-local target_date already ended.
D-B. **Mode-coupling, not phase-coupling**: 5 DAY0_CAPTURE branches (`cycle_runner.py:318, 428`; `evaluator.py:931, 943, 955`) gate behavior on `discovery_mode`, not on `position_current.phase` (which is populated but never read for dispatch).
D-C. **UMA "10:00 UTC" mythology**: appears in `STRATEGIES_AND_GAPS.md:156` and old plan docs but has NO source-code anchor. Actual: Polymarket endDate 12:00 UTC + variable UMA settlement lag.
D-D. **APScheduler timezone bug (POTENTIAL LIVE INCIDENT)**: `src/main.py:731` may not pass `timezone=ZoneInfo("UTC")` kwarg. If the macOS host TZ is not UTC, the "07/09/19/21 UTC" cron times in `STRATEGIES_AND_GAPS.md` and `config/settings.json` actually fire at host-local times. Verify before everything else.

---

## §1 Anchored facts (from investigations — load-bearing for v2)

| ID | Fact | Source |
|---|---|---|
| F1 | Polymarket weather market `endDate = 12:00 UTC` of target date, ALL 51 cities | INVESTIGATION_EXTERNAL.md Q3 (Gamma API verified ×7) |
| F2 | Polymarket `createdAt ≈ 04:04 UTC`, T-2 days before target. startDate 20-70min later (warm-up). | EXTERNAL Q3 |
| F3 | UMA OO Polymarket-default liveness = 7200s (2h); dispute path 48-72h via DVM | EXTERNAL Q2 |
| F4 | UMA settlement is variable: Wunderground daily-summary lag + 2h liveness; observed 14h after endDate for London 2026-05-03 | EXTERNAL Q2 |
| F5 | Polymarket resolution source = Wunderground per-city ICAO airport stations | EXTERNAL Q3 |
| F6 | NWP releases (likely cron anchors): ECMWF Open Data 00z/12z S3 release ~5-7h after run; GFS 06z/18z release lagged similarly | EXTERNAL Q1 |
| F7 | NWS METAR ~20min lag from MADIS; WU airport-station refresh ~15min | EXTERNAL Q5 |
| F8 | 51-city UTC offset spread: -8 (LA, SF, Seattle) to +13 (Wellington, Auckland) | INTERNAL Q2 + `config/cities.json` |
| F9 | Existing `LifecyclePhase` enum has DAY0_WINDOW (per-position phase) | INTERNAL Q8, `src/state/lifecycle_manager.py:9-19` |
| F10 | Existing `lead_hours_to_settlement_close()` returns hours-to-end-of-target_date in city.timezone | INTERNAL Q6, `src/engine/time_context.py:58-72` |
| F11 | At every UTC hour, 2-21 cities are in their city-local Day0 window. Min 2 (UTC 07-08), max 21 (UTC 15) | INTERNAL Q5 |
| F12 | 5 DAY0_CAPTURE-mode branches in code (cycle_runner.py + evaluator.py) — these are the refactor targets | INTERNAL Q7 |
| F13 | 4-key strategy_key set hardcoded in 2 SQLite CHECK constraints + 6 Python constants + 46 test files | Critic R1 A3 |
| F14 | Platt fit uses lead_days as continuous regressor, NOT bucket dimension | Critic R1 A2, `src/calibration/platt.py:1-10` |
| F15 | APScheduler may lack explicit timezone= kwarg → host-tz cron drift risk | INTERNAL Drift D, `src/main.py:731` |

---

## §2 The reframing — two orthogonal axes

PLAN v1 conflated two distinct concepts. v2 separates them explicitly:

### Axis A: `MarketPhase` (NEW — function of target_date + city.tz, market-time)

```python
class MarketPhase(Enum):
    PRE_TRADING       = "pre_trading"        # before Polymarket opens (T-2 days before target)
    PRE_SETTLEMENT_DAY = "pre_settlement_day" # opened, but >24h to local end-of-target-date
    SETTLEMENT_DAY    = "settlement_day"      # within 24h of local end-of-target-date
    POST_TRADING      = "post_trading"        # after Polymarket endDate (12:00 UTC of target_date)
    RESOLVED          = "resolved"            # UMA settlement complete
```

`MarketPhase` is computed from (target_local_date, city.timezone, decision_time_utc, polymarket_endDate). It is the same for every position on the same market.

**Boundary anchors** (locked from F1, F10):
- `PRE_TRADING → PRE_SETTLEMENT_DAY` at Polymarket `startDate` (~T-2 days + 20-70min warm-up)
- `PRE_SETTLEMENT_DAY → SETTLEMENT_DAY` at city-local 00:00 of `target_local_date` (i.e., start-of-target_date local; equivalent to "24h before end-of-target_date" on non-DST days, but stays local-calendar-correct on DST-transition days where the older "end−24h" formulation silently shifted by ±1h). Implemented at `src/strategy/market_phase.py::settlement_day_entry_utc`. Locked from operator framing "当地市场 0 点前的 24 个小时" + critic R3 ATTACK 2 (PR #53). [v3 amendment 8]
- `SETTLEMENT_DAY → POST_TRADING` at Polymarket endDate = 12:00 UTC of target_date
- `POST_TRADING → RESOLVED` at UMA proposePrice settlement (variable, ~14h+ after endDate)

### Axis B: `LifecyclePhase` (EXISTING — per-position state machine, position-time)

Already in code at `src/state/lifecycle_manager.py:9-19`:
```python
LifecyclePhase: PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT,
               ECONOMICALLY_CLOSED, SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN
```

The relationship between the two axes:
- A position's `LifecyclePhase.DAY0_WINDOW` is set **when** the market enters `MarketPhase.SETTLEMENT_DAY` AND the position is `ACTIVE`. (One-to-many: one market's transition triggers DAY0_WINDOW for every active position on it.)
- A position can be `LifecyclePhase.PENDING_EXIT` while market is still `MarketPhase.SETTLEMENT_DAY` (intra-Day0 exit decision).
- `LifecyclePhase.ECONOMICALLY_CLOSED` ⇔ `MarketPhase.POST_TRADING` for that position (modulo voids).

This separation is critical because:
- The CRON / DiscoveryMode confusion was conflating MarketPhase (which day part) with strategy class (what to do).
- The "Day0 是终章" insight is that the **strategy's role** changes between MarketPhase.PRE_SETTLEMENT_DAY (entry-dominant) and MarketPhase.SETTLEMENT_DAY (terminal-posture-dominant).

### What `DiscoveryMode` becomes

DiscoveryMode is currently used for two purposes:
1. **Cycle-level cron scheduling** (when to scan Polymarket). This survives — interval/cron is an orthogonal scheduling concern.
2. **Strategy dispatch** (what to do given the candidate). This MIGRATES from DiscoveryMode to MarketPhase + LifecyclePhase.

After the refactor, `DiscoveryMode` is a *cron label* (which scheduled job fired) and nothing else. Strategy dispatch reads `MarketPhase(market) + LifecyclePhase(position-if-any)` and decides.

---

## §3 Global-tiled scheduling (replaces §3.2 "20h vacuum")

### What's wrong today (grounded in F11, F12, F15)

- OPENING_HUNT and DAY0_CAPTURE: 15-min interval, UTC-clock-anchored. Equally serves all 51 cities (no per-tz preference).
- UPDATE_REACTION: 4 fixed UTC cron times (07/09/19/21). Each fire scans **all 51 cities' candidates** but at UTC times that align with at most ~12 cities' useful local-tz NWP-post-release window.
- The "20h vacuum" complaint: in any one city, UPDATE_REACTION fires only 4× per day, leaving 20h gap **for that city's UPDATE_REACTION strategy**. Globally there is no vacuum (F11), but locally each city sees a 20h gap on this specific strategy class.

### What's structurally needed (three-lane)

**Lane 1: Per-city-tz observation/posture trigger.** Fire at city-local times tied to:
- Day0 entry (24h before city-local end-of-target_date) — emits SETTLEMENT_DAY transition for active positions
- Hourly intra-Day0 observation refresh — picks up Wunderground 15-min refresh per city
- Per-city target_date midnight — POST_TRADING / RESOLVED transitions

Lane 1 is **per-city × per-event**, not global cron. APScheduler `date` jobs scheduled when a market opens, not as recurring cron.

**Lane 2: Global UTC liquidity / market-event triggers.** Fire on:
- Polymarket createdAt window (~04:00-05:00 UTC daily, T-2 days before target_date) — discovers new markets
- Polymarket endDate (12:00 UTC daily) — "trading closes" event for every market settling that day
- UMA settlement events (variable post-endDate) — RESOLVED transitions

Lane 2 is **global UTC × per-market-event**, fired by ingest-side observation of Polymarket/UMA on-chain events.

**Lane 3: Global NWP-release triggers.** Fire when each NWP cycle becomes available (per F6):
- ECMWF Open Data 00z release (~05:00-07:00 UTC)
- GFS 06z release
- ECMWF Open Data 12z release
- GFS 18z release
- ICON / GEFS as supplementary

Lane 3 is **global UTC × per-NWP-cycle**, fired by ingest pipeline confirmation that the new model run is downloaded. This subsumes the "07/09/19/21 UTC" UPDATE_REACTION cron with a more honest trigger (the NWP arrival event itself, not an arbitrary UTC time).

### Why this kills "20h vacuum" framing entirely

Per-city Lane 1 ensures every city has its own timing. Lane 2 ensures global market events are caught. Lane 3 anchors NWP-derived edges to the real release event, not an arbitrary UTC clock. There is no "vacuum" because no single lane is supposed to cover everything.

### East-west epistemic asymmetry (from F4)

12:00 UTC fixed endDate creates per-longitude asymmetry:

| City | UTC offset | trade-close local time | observed-target-day fraction at trade-close |
|---|---|---|---|
| Wellington | +12 | 24:00 NZST | ~100% (trade-close at city-local midnight = end-of-target-day) |
| Tokyo | +9 | 21:00 JST | ~88% |
| Singapore | +8 | 20:00 SGT | ~83% |
| London | +1 | 13:00 BST | ~54% |
| NYC | -4 | 08:00 EDT | ~33% |
| LA | -7 | 05:00 PDT | ~21% |

A SETTLEMENT_DAY entry on Wellington has access to *near-complete* day-of observation. The same SETTLEMENT_DAY entry on LA has access to *almost no* day-of observation — it's structurally more like a PRE_SETTLEMENT_DAY entry that happens to be late.

**Implication**: SETTLEMENT_DAY entry strategies should down-weight their Kelly multiplier *as a function of `observed_target_day_fraction`*, not uniformly. The current `STRATEGY_KELLY_MULTIPLIERS["settlement_capture"] = 1.0` is empirically wrong — settlement_capture for LA at trade-close has near-zero observation backing, while settlement_capture for Wellington has near-full observation backing. They should not get the same multiplier.

---

## §4 Drifts to fix BEFORE strategy work

These four drifts are *currently latent or live bugs*. Strategy redesign that ignores them ships on a broken foundation.

### D-D first: APScheduler timezone (LIVE BUG candidate)

`src/main.py:731` schedule construction. Read code. If APScheduler lacks `timezone=` kwarg, the "07/09/19/21 UTC" cron actually fires at host-local times. On a CDT host (UTC-5), this means the cron actually fires at 12/14/00/02 UTC — not what the docs claim.

**Fix**: explicit `timezone=ZoneInfo("UTC")` (or whichever tz is the design intent, but UTC is what docs assume).
**Risk**: LOW (one-line fix).
**Verification**: log first fire after restart, confirm UTC alignment.

### D-A: Two-clock split

`cycle_runtime.py:1506` (city-local) vs `:2003` (UTC endDate-now). Pick one, propagate.

Per F1 (Polymarket endDate is uniformly 12:00 UTC), the **candidate filter** is correctly UTC-anchored — but the city-local Day0 transition trigger at `:1506` and the UTC endDate filter at `:2003` should agree on what "Day0" means.

**Fix**: define `MarketPhase` (per §2) as the single source of truth. `cycle_runtime.py:2003` filters by `MarketPhase ≠ PRE_TRADING AND MarketPhase ≠ POST_TRADING`. `cycle_runtime.py:1506` triggers `LifecyclePhase` transition when `MarketPhase` enters `SETTLEMENT_DAY`.
**Risk**: MED (touches candidate filter — verify no candidate count regression).

### D-B: Mode → phase migration

5 DAY0_CAPTURE branches at `cycle_runner.py:318, 428`; `evaluator.py:931, 943, 955`. Each gates behavior on `discovery_mode == DAY0_CAPTURE`. Migrate each to `market_phase == SETTLEMENT_DAY`.

**Fix**: per-branch refactor + relationship test.
**Risk**: MED (5 sites, each independently testable).

### D-C: UMA 10:00 UTC mythology purge

Replace doc claims. Code has no anchor on 10:00 UTC.

**Fix**: docs-only. Update `STRATEGIES_AND_GAPS.md`, AGENTS.md sections, runbook. Anchor on F1 (Polymarket endDate = 12:00 UTC) + F4 (settlement variable, ~14h post-endDate per observed evidence).
**Risk**: LOW (docs only).

---

## §5 Strategy taxonomy (revised — measured, not 4→10 explosion)

PLAN v1 proposed 10 keys. v2 retreats: introduce the new taxonomy as an *additional axis* (MarketPhase), not as new strategy_keys, until each new key has an evidence cohort + reporting capacity to back it.

### Current taxonomy (corrected per critic R1 A14)

```
Active (in _LIVE_ALLOWED_STRATEGIES, positive Kelly):
  - settlement_capture  Kelly 1.0×
  - center_buy          Kelly 1.0×
  - opening_inertia     Kelly 0.5×

Registered but Kelly 0.0× (boot-eligible, not runtime-live):
  - shoulder_sell       Kelly 0.0× (in LIVE_SAFE_STRATEGIES but excluded from _LIVE_ALLOWED_STRATEGIES)

Registered as dormant (Kelly 0.0×, never produced by classifier):
  - shoulder_buy        Kelly 0.0×
  - center_sell         Kelly 0.0×
```

**6 keys exist today** (4 active or boot-eligible + 2 dormant), not 4.

### v2 proposed taxonomy (targeted, not exhaustive)

Keep the 6 existing keys. Add **MarketPhase** as a per-decision tag in evidence/reporting. Decisions:

```
For each existing strategy_key, the (strategy_key, MarketPhase) pair becomes the
attribution unit. E.g.:
  (settlement_capture, SETTLEMENT_DAY) — Day0 entry on settlement-day
  (settlement_capture, PRE_SETTLEMENT_DAY) — currently rare (Day0 cutover routes
     these to settlement_capture only when hours_to_resolution<6, but per D-A
     two-clock split, this can happen mid-cycle straddling)

  (opening_inertia, PRE_SETTLEMENT_DAY) — fresh-market opening_hunt
  (opening_inertia, SETTLEMENT_DAY)     — should not occur post-fix (markets
     <24h since open AND <24h to settle is contradictory)

  (center_buy, PRE_SETTLEMENT_DAY) — UPDATE_REACTION center, normal
  (center_buy, SETTLEMENT_DAY)     — Day0-time center buys (rare, currently
     produced only when UPDATE_REACTION fires within 24h of settlement)
```

This is a **minimum-disruption** redesign: no new strategy_keys, no CHECK constraint changes, no DB CHECK migration. Just an additional MarketPhase tag on every decision, queryable for attribution.

If after one trading week the (strategy_key, MarketPhase) data shows distinct EV / Brier / Kelly profiles per pair, **then** new strategy_keys can be split out per pair. Until then, 6 keys × 4 phases = 24 attribution buckets as the data layer.

### Kelly multiplier becomes phase-aware (no new keys)

Replace `STRATEGY_KELLY_MULTIPLIERS[strategy_key]` with `STRATEGY_KELLY_MULTIPLIERS[(strategy_key, market_phase)]` (or equivalent function).

Initial values (all conservative; operator-tunable):

```
(settlement_capture, SETTLEMENT_DAY) = 1.0   # currently 1.0
(settlement_capture, PRE_SETTLEMENT_DAY) = 0.0  # should not occur (defense)
(center_buy, PRE_SETTLEMENT_DAY)      = 1.0   # currently 1.0
(center_buy, SETTLEMENT_DAY)          = 0.5   # NEW — Day0-time entry penalty
(opening_inertia, PRE_SETTLEMENT_DAY) = 0.5   # currently 0.5
(opening_inertia, SETTLEMENT_DAY)     = 0.0   # contradictory window (defense)
(shoulder_sell, *)                    = 0.0   # already 0.0
(shoulder_buy, *)                     = 0.0   # already 0.0
(center_sell, *)                      = 0.0   # already 0.0
```

**East-west asymmetry (per §3 last subsection)** is captured by an additional `observed_target_day_fraction` factor for SETTLEMENT_DAY:
```
final_kelly_mult *= max(0.3, observed_target_day_fraction)
```
where `observed_target_day_fraction = (hours_observed_so_far) / 24` at decision_time. LA SETTLEMENT_DAY entries get ~0.21× of nominal multiplier; Wellington entries get ~1.0×.

---

## §6 Implementation packets (corrected — each is its own PR)

```
P0  D-D APScheduler timezone fix (LIVE BUG triage)
    - Add explicit timezone=ZoneInfo("UTC") to APScheduler at src/main.py:731
    - Verify cron times log at expected UTC after restart
    - Risk: LOW (one-line code change) but BEHAVIORAL impact significant
    - Tests: 1 unit test asserting timezone= kwarg present and equals UTC

    !! CRITIC R2 A3 CONFIRMED LIVE: probed BlockingScheduler().timezone on host
       — returns America/Chicago. Cron has been firing at CDT for unknown N
       weeks. The "07/09/19/21 UTC" labels in STRATEGIES_AND_GAPS.md are wrong:
       actual fires are at 12/14/00/02 UTC (5h late vs documented).

    Commit body MUST contain (operator confirmation gate):
       "POST-FIX CRON SHIFT: with timezone=UTC, the four UPDATE_REACTION cron
        times (07:00, 09:00, 19:00, 21:00) will now fire at literal UTC. Until
        this fix, they have been firing at CDT (12/14/00/02 UTC) due to host-tz
        default. Operator should review whether the post-fix UTC schedule is
        actually optimal (07/09/19/21 UTC was the design intent and aligns with
        ECMWF Open Data 00z/12z + GFS 06z/18z release windows per
        INVESTIGATION_EXTERNAL Q1) — the accidentally-CDT schedule was hitting
        Polymarket endDate close (12:00 UTC) and GFS production windows
        (00:00/02:00 UTC) instead. Approve UTC restoration OR explicitly
        re-pick the four cron times before merge."

P1  D-C docs purge of UMA 10:00 UTC mythology
    - Update STRATEGIES_AND_GAPS.md + AGENTS.md + runbook
    - Anchor on F1+F3+F4 (Polymarket endDate 12:00 UTC, UMA settlement variable)
    - Risk: LOW
    - Tests: none (docs only)

P2  MarketPhase enum + per-cycle snapshot (the AUGMENTATION layer)
    - Define MarketPhase enum (5 values per §2)
    - Implement market_phase_for_decision(market, decision_time, city) helper
    - Plumb decision_time through EVERY phase-derivation site
    - Critical invariant: phase computed from cycle's decision_time, NEVER from
      wall-clock at point-of-use (per critic R1 C5)
    - Relationship test: midnight-straddle decision sees stable phase across cycle
    - Tag every Decision / EdgeDecision / candidate snapshot with market_phase
    - Per-table phase handling (per critic R2 C10):
       * position_current.phase   — already populated; P2 does NOT change writer.
                                    market_phase is a candidate/decision tag,
                                    not a position-row column.
       * position_lots.state      — separate state-machine axis (7-value enum
                                    OPTIMISTIC_EXPOSURE..QUARANTINED at
                                    src/state/db.py:258-281). NO phase column
                                    needed; lots are per-event records.
       * position_events.phase_after — already populated by build_position_current_projection
                                    at src/state/projection.py:61, 82. P2 does
                                    NOT change this writer.
       * probability_trace_fact.market_phase — NEW column added by P2 stage 3
                                    (one schema change, additive, default
                                    NULL for legacy rows + ALTER migration).
                                    Chosen over a separate decision_chain
                                    table because probability_trace_fact is
                                    the existing per-decision lineage spine
                                    (carries decision_id, strategy_key,
                                    discovery_mode) and aligns with §6.P9
                                    cohort attribution. Implemented at
                                    src/state/db.py + writer
                                    log_probability_trace_fact.
                                    [v3 amendment 9 — critic R3 ATTACK 1]
       * candidate snapshot       — NEW market_phase tag in evidence/reporting.
    - Per-market multi-position write atomicity (per critic R2 A14)
      DEFERRED-TO-P3 [v3 amendment 10 — critic R3 ATTACK 5]:
       * When a market transitions MarketPhase (e.g., to SETTLEMENT_DAY), all
         active positions on that market must transition LifecyclePhase in a
         single SAVEPOINT. Use upsert_position_current() inside an explicit
         BEGIN/SAVEPOINT/RELEASE block.
       * §8 T7 invariant test asserts no partial-write state.
       * RATIONALE FOR DEFERRAL: P2 ships MarketPhase as an
         observability/cohort tag only — it is read-only with respect to
         position_current. The "multi-position transition" surface that A14
         worried about does not yet exist; it materializes only when P3
         (D-B mode→phase migration) flips dispatch onto market_phase.
         Implementing SAVEPOINT atomicity in P2 would be scaffolding without
         a caller. The SAVEPOINT-wrapped transition + T7 land with P3 in the
         same PR.
    - Risk: MED (touches every layer, but additive — no removal)
    - Tests: §8 floor (relationship invariants T1-T6 land with P2; T7 lands
      with P3 SAVEPOINT atomicity)

P3  D-B mode→phase migration (5+1 sites; per critic R2 C6)
    - 5 strategy-dispatch sites:
       * cycle_runner.py:318    DAY0 freshness short-circuit  → MarketPhase.SETTLEMENT_DAY
       * cycle_runner.py:428    DAY0 disable on STALE         → MarketPhase.SETTLEMENT_DAY
       * evaluator.py:931, 943, 955  strategy_key dispatch    → market_phase or LifecyclePhase
    - +1 observation-fetch gate site:
       * cycle_runtime.py:2083  fetch_day0_observation gate   → market_phase == SETTLEMENT_DAY
       (separately classified because it gates ingest, not strategy)
    - Per-site refactor + relationship test that mode-default behavior is preserved
    - Merge-coordination caveat (per critic R2 A7): cycle_runtime.py:2083 (P3 site)
      and cycle_runtime.py:2003 (P4 site) are in the same file; coordinate ranges
      to avoid merge conflict. P3 owns line 2083 (dispatch); P4 owns line 2003 (filter).
    - Risk: MED (5+1 sites, each independently testable; default behavior stays
      until explicitly flag-flipped)
    - Tests: 6 site-specific relationship tests (one per site)

P4  D-A two-clock unification
    - cycle_runtime.py:1506 + :2003 unified through MarketPhase
    - Adopt F1's UTC endDate (12:00 UTC) as the SETTLEMENT_DAY → POST_TRADING
      anchor; SETTLEMENT_DAY entry boundary (24h before) is still city-local
      end-of-target-date because that's where useful observation accumulates
    - Risk: MED (candidate count regression possible if filter widens or narrows)
    - Tests: relationship test that candidate count at any UTC hour matches
      INTERNAL Q5 expectation (2-21 cities in SETTLEMENT_DAY)

P5  Phase-aware Kelly multiplier
    - Replace STRATEGY_KELLY_MULTIPLIERS[key] with [(key, phase)] (function or dict)
    - Apply observed_target_day_fraction factor for SETTLEMENT_DAY
    - Multiplier storage policy (per critic R2 A9):
       * Multiplier RESOLVED at open-time in the evaluator (function call:
         strategy_kelly_multiplier_for(strategy_key, market_phase, city, decision_time))
       * Resolved value STORED on `decision_chain.kelly_multiplier_used`
         (column already exists per src/engine/evaluator.py:206).
       * At exit-sizing or recompute, USE the stored value — do NOT re-resolve.
       * This makes P5 phase-transition-stable: a position opened at
         PRE_SETTLEMENT_DAY with multiplier 1.0× retains 1.0× even after market
         transitions to SETTLEMENT_DAY mid-flight. Honors C8 promise.
    - Calibration relationship (per critic R2 A6):
       * Kelly multiplier is POST-CALIBRATION sizing scaler. Platt calibration
         operates on (p_raw, lead_days) → p_calibrated; Kelly multiplier scales
         the f* derived from p_calibrated. P5 introduces NO Platt rebuild; the
         calibration_pairs_v2 corpus is unchanged.
       * Relationship test: P5 deploy with no Platt artifact change produces
         expected sizes (deterministic on synthetic decisions).
    - Risk: MED (changes live sizing)
    - Tests: every (key, phase) returns expected multiplier; SETTLEMENT_DAY for LA
      vs Wellington at same decision_time produces different sizes; stored
      multiplier survives phase transition.
    - Migration: existing positions (created pre-P5) retain original Kelly; new
      positions use phase-aware (per critic R1 C8). Operator OD7 confirms.

P6  Lane 2 + Lane 3 scheduling refactor (mechanism re-spec per critic R2 A4+A8)
    - Lane 2 (Polymarket events): NOT on-chain RPC sub. Use existing Gamma API
      polling at `src/data/market_scanner.py` with two cadences:
       * Around 04:00-05:00 UTC daily (T-2 day open window): tighten to 1-min
         polling interval (vs current 5-min `_ACTIVE_EVENTS_TTL = 300.0`).
         Detects new createdAt at ~04:04 UTC reliably within ≤1 min.
       * Around 11:55-12:05 UTC daily (target-day endDate window): tighten to
         30-sec polling. Detects MarketPhase.POST_TRADING transition reliably.
       * Outside these windows: revert to 5-min default polling.
       * Mechanism: time-window-aware polling cadence in market_scanner; no
         new IPC; same Gamma API.
    - Lane 3 (NWP releases): file-watcher on ingest-pipeline marker files.
       * `src/ingest_main.py` already runs ECMWF Open Data + GFS + ECMWF
         oper TIGGE ingest jobs that write artifacts under `state/ingest_status/`.
       * Add a marker-file convention: each ingest job writes
         `state/ingest_status/<source>-<cycle>-<release_utc>.READY` on
         successful completion.
       * Strategy daemon polls this directory at 1-min cadence; on new file,
         fires a phase-A re-evaluation cycle for affected metric/source.
       * Mechanism: filesystem polling (simple, restart-resilient, no IPC).
       * Fallback: if no marker file appears within a known release window
         (per F6: ECMWF 00z by 09:00 UTC, GFS 06z by 09:00 UTC, etc.), fire
         a fallback re-eval at the window-end timestamp regardless.
    - Risk: HIGH (changes cron semantics)
    - Tests:
       * per-NWP-cycle simulation that the marker-file trigger fires at each release
       * per-Polymarket-event simulation that endDate window catches the
         12:00 UTC transition within 30s
       * fallback test: no marker file produced ⇒ fallback re-eval still fires

P7  Lane 1 per-(city, target_date) UTC-anchored triggers (UTC-strict per §0.0)
    - APScheduler operates in UTC ONLY (no per-tz scheduler instance).
    - For each (city, target_date) under active markets, schedule an APScheduler
      `date` job whose `run_date_utc` is derived by:
         end_of_target_local_date_utc = (
             datetime.combine(target_date + timedelta(days=1), time(0,0,0),
                              tzinfo=ZoneInfo(city.timezone))
             .astimezone(timezone.utc)
         )
         settlement_day_entry_utc = end_of_target_local_date_utc - timedelta(hours=24)
       The scheduler sees UTC; the per-city-tz arithmetic happens at job-creation.
    - Hourly intra-Day0 observation refresh: APScheduler `interval` job per city,
      `start_date_utc = settlement_day_entry_utc`, `end_date_utc = end_of_target_local_date_utc`,
      hours=1. UTC anchors throughout.
    - Worker pool sizing (per critic R2 A15): APScheduler executor configured
      with `ThreadPoolExecutor(max_workers=64)`. 64 > 51 cities × any concurrent
      hourly refresh + 4 NWP-anchor cron jobs + buffer.
    - Per-tz coalescing NOT used (would re-introduce tz semantics into scheduler;
      operator §0.0 forbids). Each city gets its own UTC-anchored job.
    - Missed-trigger recovery (per critic R2 A16):
       * `misfire_grace_time = 24*3600` for per-city `date` jobs (24h tolerance).
       * Startup catch-up routine in src/main.py: on daemon boot, scan
         position_current for active positions where target_local_date in
         city.tz is within 24h of utcnow() and the SETTLEMENT_DAY transition
         marker is missing. Force-fire the transition for those positions.
       * Belt-and-suspenders: every cycle (15-min interval) reasserts
         MarketPhase from decision_time → if any market silently crossed the
         24h-before-end-of-target-date boundary without a date-job fire, the
         cycle still computes the correct phase. (This means per-city date jobs
         are an OPTIMIZATION for tighter timing, not the sole source of truth.)
    - Risk: HIGH (51-city × N-target-date matrix of date-jobs; UTC-only)
    - Tests:
       * per-city date-job fires at expected UTC instant computed from city.tz
       * 64-worker executor handles 51 simultaneous fires without queueing
       * misfire_grace_time of 24h tolerates daemon restart within window
       * startup catch-up routine fires for active positions whose markets
         are already in SETTLEMENT_DAY at boot
       * cycle-level reassertion catches a market that missed its date-job

P8  CHECK-constraint migration (BLOCKING for any new strategy_key)
    - Currently NOT triggered: v2 §5 keeps 6 existing keys
    - Documented here as the ONE plan element that must be coordinated if/when
      operator approves a new strategy_key (e.g., post-data review of (key,
      phase) attribution)
    - Migration steps: drop+recreate decision_chain, drop+recreate strategy_health,
      update 6 Python constants, update 46 test files in lockstep
    - Risk: BLOCKING

P9  Reporting / attribution per (key, phase)
    - Extend edge_observation + attribution_drift to track (strategy_key,
      market_phase) cohorts
    - Dashboard / weekly report by phase
    - Risk: LOW (additive)
    - Tests: cohort SQL produces correct counts on synthetic data

EXPLICITLY EXCLUDED from v2 (rejected from v1):
  v1-P4 per-phase Platt cohort  — DROPPED (lead_days already a Platt regressor)
  v1-P0 greenfield phase enum   — REPLACED by P2 MarketPhase augmentation
  v1-P1+P2 strategy_key 4→10    — REPLACED by P5 phase-aware multiplier on existing keys
```

**Sequencing rule**: P0 → P1 → P2 → (P3, P4 in parallel) → P5 → (P6, P7 in parallel) → P9. P8 only triggered by explicit operator decision after P9 produces ≥2 weeks of (key, phase) attribution data.

---

## §7 Open decisions — split

### Operator-decision-required (BLOCKING for P0-P5 implementation)

OD1. **D-D first?** Confirm P0 APScheduler timezone fix runs ahead of all strategy work. If host-tz is currently non-UTC and scheduler is silently running on host-tz, this is a live bug; fix first.
OD2. **MarketPhase enum naming** — `MarketPhase`/`SettlementPhase`/`MarketDayPhase`? Pick one in this PR.
OD3. **East-west asymmetry — embed in Kelly or in entry-gate?** Option A: Kelly down-weight via observed_target_day_fraction (P5). Option B: harder gate that refuses SETTLEMENT_DAY entries below 0.5 fraction. v2 default = A (Kelly). Operator confirms.
OD4. **shoulder_sell promotion path** — currently in LIVE_SAFE_STRATEGIES but excluded from _LIVE_ALLOWED_STRATEGIES. Is the v2 plan to promote shoulder_sell now (independently of MarketPhase work) or keep it deferred?
OD5. **Lane 2 + Lane 3 scheduling refactor — same release as P0-P5 or separate?** Recommend separate (P6 = its own PR after P5 stable).
OD6. **shoulder_buy + center_sell dormant pair activation** — per critic R1 C11, these exist at Kelly 0.0× and never fire. Plan to keep dormant or wire after P9 attribution shows cohort gaps?
OD7. **Position-creation-time gate for P5** — confirm new Kelly multipliers apply only to positions opened post-P5-merge. Existing positions retain old multipliers until exit.

### Critic-decidable (resolved in critic R2 review of this PLAN_v2)

CD1. P2's "phase from decision_time" invariant — adequate as proposed, or needs additional thread-safety / cycle-snapshot guarantee?
CD2. P5's `max(0.3, observed_target_day_fraction)` floor — empirically defensible? Or should it be 0.5? Or no floor at all?
CD3. P3's 5-site refactor coverage — are there branches I missed beyond the 5 INTERNAL Q7 identified?
CD4. v2 §5 "keep 6 keys, add phase tag" minimum-disruption — does this avoid the BLOCKING CHECK-constraint risk fully, or is there a downstream surface I missed?

### Implementation-time (no decision needed now)

ID1. APScheduler restart resilience for Lane 1's 51 per-city schedules
ID2. Performance budget for `market_phase_for_decision` if called per-candidate-per-cycle (cache vs recompute)
ID3. Reporting cohort SQL aggregation patterns

---

## §8 Relationship-test floor (minimum 6 invariants for P2 merge)

These tests are non-negotiable before P2 lands:

T1. **Phase-from-decision_time stability**: a cycle starting at `decision_time = T_0` and processing 50 candidates over 30s wall clock sees the SAME phase for every candidate of the same market, even if `T_0` straddles a city's local midnight. (Pins critic R1 C5.)

T2. **Phase-vs-LifecyclePhase consistency**: every active position whose market is `MarketPhase.SETTLEMENT_DAY` has `LifecyclePhase ∈ {DAY0_WINDOW, PENDING_EXIT}`. (Pins F9 + axis-A/B coordination.)

T3. **MarketPhase boundaries**: `MarketPhase.PRE_SETTLEMENT_DAY` for London 2026-05-08 high temp at decision_time `2026-05-07 23:00 UTC` (= 24:00 BST = exactly city-local end-of-target-date − 24h) — boundary inclusive or exclusive? Test pins the chosen rule.

T4. **endDate UTC anchor**: `MarketPhase.POST_TRADING` for any city's market at any UTC time ≥ 12:00 UTC of target_date. Verifies F1.

T5. **Candidate filter consistency post-D-A fix**: at `decision_time = T`, the count of candidates passing `min_hours_to_resolution` filter equals the count of cities in `MarketPhase ∈ {PRE_SETTLEMENT_DAY, SETTLEMENT_DAY}` per INTERNAL Q5 matrix.

T6. **Mode-default preservation post-D-B**: with no flag flipped, every site's behavior is byte-equal to pre-D-B (no live regression). The phase-coupled path is reachable only via explicit phase-aware refactor flag (or post-P3 default flip).

T7. **Per-market multi-position SAVEPOINT atomicity** [LANDS WITH P3 — see §6.P2 deferral note + §0.1 amendment 15]: when a market transitions MarketPhase, all its active positions are updated in a single SAVEPOINT. Test asserts that a synthetic mid-transition crash leaves zero partially-updated positions (every position either has the new phase or the old, never the new on some and old on others).

---

## §9 Critic R2 attack vectors (predicted; for the next critic pass to attack)

A1. **F1 generality**: 7-city Gamma API verification doesn't prove the 12:00 UTC endDate holds for all 51 cities. Spot-check at least 5 more cities in critic R2.
A2. **F11 method**: INTERNAL Q5 matrix derived from `config/cities.json` timezone offsets + an estimate of "in Day0 window". Does the matrix correctly account for DST boundaries on each city's timezone in May 2026?
A3. **D-D severity**: is APScheduler timezone bug confirmed live? P0 needs verification (run `python -c "import apscheduler; ..."` against current main.py).
A4. **§3 Lane 2 stability**: Polymarket endDate event listener — how does Zeus reliably detect 12:00 UTC market closes? On-chain event subscription, polling, or static-cron? If polling, what's the cadence?
A5. **§5 east-west asymmetry math**: `observed_target_day_fraction` formula assumes uniform observation availability. Wunderground refresh cadence (F7) is 15min. What's the actual fraction observable at decision_time for an LA-vs-Wellington pair?
A6. **P5 Kelly re-calibration**: changing per-(key, phase) multipliers without rebuilding `calibration_pairs_v2` runs Platt against the OLD prior. Is that acceptable for the first deploy, or does P5 require a calibration cohort recalibration?
A7. **P3-P4 ordering**: D-A and D-B are listed in parallel. Are there ordering dependencies? E.g., does D-B's mode→phase migration require D-A's two-clock unification first?
A8. **P6 NWP-release-trigger fragility**: NWP releases are not always punctual. ECMWF Open Data 12z can slip 30+ min. Lane 3 needs robust "release detected" not "release expected at time T".
A9. **§5 phase-aware Kelly migration policy**: §6 P5 says "existing positions retain original multiplier". Implementation detail: is multiplier stored per-position-row or recomputed at sizing-time? Stored = stable; recomputed = re-evaluation risk.
A10. **§7 OD3 alternative rejection**: I default to "Kelly down-weight" (option A) for east-west asymmetry. Is option B (hard gate at 0.5 fraction) ever the right call? When?

---

## §10 Non-goals

- v2 does NOT propose new strategy_keys today. The 6 existing keys (4 active + 2 dormant) stay; (key, phase) becomes the attribution unit.
- v2 does NOT touch calibration_pairs_v2 schema. Per-phase Platt cohort split is rejected (critic R1 C3).
- v2 does NOT change CHECK constraints in P0-P9. P8 (CHECK-constraint migration) is documented as a *contingent* future packet, only triggered if operator approves new keys post-attribution.
- v2 does NOT touch the bankroll truth chain (settled in PR #46).
- v2 does NOT touch the entry-forecast contract (PR #47) or activation gating (PR #49). Those are orthogonal.
- v2 does NOT propose §3.4 PRICE_DRIFT_REACTION or §3.2 MIDDLE_STATE_HUNT as separate modes — those are subsumed under Lane 2/3 event-driven triggers if shipped at all.

---

## §11 Success criteria

This plan succeeds when:

1. critic-opus R2 returns APPROVED-WITH-CAVEATS or APPROVED. All caveats addressed in v3 (in-place edit) or marked non-blocker with operator concurrence.
2. Operator approves §7 OD1-OD7 in writing (commit body).
3. P0-P9 each ship in their own PR with §8 relationship tests green.
4. Final state: 6 strategy_keys × 4 MarketPhase = 24 attribution buckets in evidence/reporting; phase-aware Kelly; per-city-tz Lane 1 triggers active; UMA / Polymarket events properly handled in Lane 2; APScheduler tz bug fixed; D-A/B/C/D drifts closed.
5. After ≥2 weeks of live attribution data, operator decides whether to promote any (strategy_key, MarketPhase) pair to its own first-class strategy_key (triggering P8).

---

## Cross-references

- v1 (superseded): `PLAN.md` in this directory
- Critic v1 verdict: `CRITIC_REVIEW_R1.md` in this directory
- Internal investigation: `INVESTIGATION_INTERNAL.md` in this directory
- External investigation: `INVESTIGATION_EXTERNAL.md` in this directory
- Originating directive: operator chat 2026-05-04
- Adjacent contracts: PR #46 (bankroll), PR #47 (entry-forecast contract), PR #49 (activation evidence gating)
- Subsumed gap doc: `docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md` (§3.1-3.6 reframed within v2 P2-P7)
- Subsumed plan: `docs/operations/task_2026-05-02_strategy_update_execution_plan/PLAN.md` (Stage 5+ work recast into P0-P9)
