# Critic Review R2 — PLAN_v2 Day0-as-Endgame strategy redesign

**Verdict**: APPROVED-WITH-CAVEATS
**Date**: 2026-05-04
**Reviewer**: critic-opus
**HEAD**: d0259327e3fd46c3c2e2fc351676a2f887a38d03

## Bottom line

PLAN_v2 substantially closes the R1 REJECT-AND-RESPLIT gap. The two big structural fixes — augment-not-replace `LifecyclePhase` (renamed proposed enum to `MarketPhase`), and drop the per-phase Platt cohort — are correctly executed. Foundational facts (F1 12:00 UTC endDate; D-D APScheduler tz; F11 UTC×Day0 matrix) are confirmed against disk. **However, three of the twelve R1 caveats are documented in §0 but not actually delivered in §6**: C10 (per-table phase handling for `position_lots`/`position_events`) appears nowhere in P0-P9; A4/A8 (Lane 2/Lane 3 trigger mechanism) collapse to "add listener" hand-waves in P6; A9 (P5 multiplier storage) is silent. A confirmed live bug (D-D, P0) sets P0 priority correctly. v2 is shippable on the P0-P5 spine; P6/P7 must be re-spec'd before they leave plan.

## R1-fix verification (per C1-C13)

### C1 [PASS] — augmentation, not greenfield
PLAN_v2.md:16 confirms "v2 must AUGMENT, not invent." §2 (lines 74-121) establishes `MarketPhase` as a separate axis from existing `LifecyclePhase`. §6 P2 (line 305-314) is "MarketPhase enum + per-cycle snapshot (the AUGMENTATION layer)" not greenfield infra. Verified `LifecyclePhase` at `src/state/lifecycle_manager.py:9-19` (10 values intact); `position_current.phase` at `src/state/projection.py:6-8`; `lead_hours_to_settlement_close` at `src/engine/time_context.py:58-72`. C1 fixed.

### C2 [PASS] — naming collision avoided
PLAN_v2.md:17 documents the rename. §2 line 81 declares `class MarketPhase(Enum)`. The R1 collision (`LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` would have shadowed the live enum) is averted. v2 §2.B (lines 96-103) preserves all 10 existing `LifecyclePhase` values. C2 fixed.

### C3 [PASS] — per-phase Platt cohort dropped
PLAN_v2.md:18 (C3 line) + line 374-376 explicit exclusion: `v1-P4 per-phase Platt cohort — DROPPED (lead_days already a Platt regressor)`. Verified `lead_days` is a Platt input at `src/calibration/platt.py:1-5` ("Lead_days is NOT a bucket dimension — it's a Platt input. This triples positive samples per bucket (45→135) vs the 72-bucket approach."). Plan §10 line 447 confirms in non-goals. C3 fixed.

### C4 [PASS] — CHECK-constraint migration is BLOCKING
PLAN_v2.md:19 + §6 P8 (lines 358-365) classify CHECK migration as `Risk: BLOCKING`. Plan §10 line 448 confirms P0-P9 do NOT touch CHECK constraints; P8 is contingent. Verified live CHECK constraints at `src/state/db.py:663-669` (decision_chain) and `src/state/db.py:759-764` (strategy_health), both with hardcoded 4-key sets. C4 fixed.

### C5 [PASS] — phase-from-decision_time invariant
§6 P2 lines 309-310: "phase computed from cycle's decision_time, NEVER from wall-clock at point-of-use (per critic R1 C5)". §8 T1 (line 415): "Phase-from-decision_time stability: ... sees the SAME phase for every candidate of the same market, even if `T_0` straddles a city's local midnight." This is the right relationship test. C5 fixed.

### C6 [PASS] — Day0Router targeting
§6 P3 (lines 316-321) targets EXACTLY the 5 sites (`cycle_runner.py:318, 428` + `evaluator.py:931, 943, 955`). Verified all 5 anchors resolve at HEAD via `grep -n DAY0_CAPTURE`. P3 also adds `cycle_runtime.py:2083` (the observation-fetch gate) which I count as a SIXTH site — not 5. The plan calls it out as a separate bullet (line 320: "cycle_runtime.py:2083 fetch_day0_observation gate") which is correct but the bullet count drift from "5 sites" to 5+1 should be reconciled in §6 prose. Minor. C6 fixed with caveat.

### C7 [PASS] — relationship-test floor is 6
§8 (lines 411-426) lists exactly 6 invariants T1-T6. T1 covers C5 race; T2 covers axis-coordination; T3 covers boundary; T4 covers F1 endDate UTC; T5 covers two-clock fix; T6 covers default-preservation. C7 fixed.

### C8 [PASS] — position-creation-time gate for P5
§6 P5 (lines 341-342): "Migration: existing positions (created pre-P5) retain original Kelly; new positions use phase-aware (per critic R1 C8)". §7 OD7 (line 394) confirms the policy as operator-decision-required. C8 fixed.

### C9 [PASS] — §7 split into operator/critic/impl
§7 has three subsections at lines 386, 396, 403. OD1-OD7 are operator-decisions; CD1-CD4 are critic-decidable; ID1-ID3 are impl-time. C9 fixed.

### C10 [FAIL] — `position_lots`, `position_events` per-table phase handling NOT delivered
PLAN_v2.md:25 claims "v2 enumerates per-table phase-handling." §6 (P0-P9) does NOT mention `position_lots` or `position_events` anywhere. `grep -n "position_lots\|position_events" PLAN_v2.md` returns ONLY line 25 (the §0 promise). Verified disk: `src/state/db.py:258-281` defines `position_lots` with its OWN CHECK constraint on a `state` enum of 7 values (`OPTIMISTIC_EXPOSURE, CONFIRMED_EXPOSURE, EXIT_PENDING, ECONOMICALLY_CLOSED_OPTIMISTIC, ECONOMICALLY_CLOSED_CONFIRMED, SETTLED, QUARANTINED`) — distinct axis from `LifecyclePhase`. `position_lots` does NOT have a `phase` column (verified via `PRAGMA table_info` — only `state`). The plan needs a §6 sub-section explicitly stating: position_lots phase handling = NONE (lots are per-event records, not per-position-state); position_events phase handling = (already includes `phase_after` per `src/state/projection.py:61, 82`). Without this, v3 implementer will guess. **This is the single largest unfixed R1 caveat.**

### C11 [PASS] — dormant pairs documented
§5 lines 222-235 list all 6 keys: `settlement_capture, center_buy, opening_inertia` (active); `shoulder_sell` (boot-eligible 0.0×); `shoulder_buy, center_sell` (dormant 0.0×). Verified disk: `src/strategy/kelly.py:71-73` confirms all three Kelly 0.0 dormants (`shoulder_sell, shoulder_buy, center_sell`). `src/control/control_plane.py:316`: `_LIVE_ALLOWED_STRATEGIES = {"settlement_capture", "center_buy", "opening_inertia"}`. C11 fixed.

### C12 [PASS] — `Last reused/audited:` header
PLAN_v2.md:4 has `**Last reused/audited**: 2026-05-04`. C12 fixed.

### C13 (renamed C12 in source) — same as C12, PASS.

**R1-fix scorecard: 11 PASS, 1 FAIL (C10), 0 partial.** C10 is the load-bearing miss.

---

## §9 attack vectors (A1-A10) + new (A11-A16)

### A1 F1 generality [PASS]
Spot-checked 6 additional cities via Gamma API on 2026-05-04: Chengdu, Cape Town, Mexico City, Helsinki, Jakarta, Warsaw. Every single city returns `endDate=2026-05-04T12:00:00Z` (or 05-05 12:00 for next-day) and `createdAt=2026-05-02T04:05-04:07Z`. Combined with INVESTIGATION_EXTERNAL Q3's 7 cities = 13 cities verified, 13/13 conformant to the F1 pattern. F1 generality holds at the population scale needed for v2 to ship.

### A2 F11 method DST audit [PASS]
Computed `observed_target_day_fraction` for May 2026 (NH summer time, SH winter time):
- London (BST UTC+1): trade-close at 13:00 BST → 13/24 = 54.2% (plan claims ~54%) ✓
- LA (PDT UTC-7): trade-close at 05:00 PDT → 5/24 = 20.8% (plan claims ~21%) ✓
- Wellington (NZST UTC+12 — winter, NOT NZDT): trade-close at 00:00 NZST = exactly end-of-target-day → 24/24 = 100% (plan claims ~100%) ✓
- Sydney (AEST UTC+10 — winter): trade-close at 22:00 AEST → 22/24 = 91.7% (plan does not claim Sydney; consistent with table) ✓

Plan §3 line 167-172 table is DST-aware. F11 matrix in INVESTIGATION_INTERNAL Q5 used `zoneinfo` which respects DST automatically. A2 PASS.

### A3 D-D severity (LIVE BUG verification) [FAIL — bug confirmed live]
`src/main.py:731` reads `scheduler = BlockingScheduler()` — verified no `timezone=` kwarg. Probed default tz via `from apscheduler.schedulers.blocking import BlockingScheduler; print(BlockingScheduler().timezone)` on this host — returns `America/Chicago`. The launchd plist `com.zeus.live-trading.plist` does not set `TZ=UTC`. The four UPDATE_REACTION cron strings `"07:00", "09:00", "19:00", "21:00"` from `config/settings.json` are therefore parsed as CDT (UTC-5 in May 2026 DST), meaning the cron actually fires at:
- 07:00 CDT = 12:00 UTC
- 09:00 CDT = 14:00 UTC
- 19:00 CDT = 00:00 UTC (next day)
- 21:00 CDT = 02:00 UTC (next day)

This means the documented "07/09/19/21 UTC = NWP-release alignment" claim in `STRATEGIES_AND_GAPS.md` is OFF by 5 hours on every fire. UPDATE_REACTION at 12:00 UTC catches Polymarket endDate-close moment for that day's markets (probably useful by accident). UPDATE_REACTION at 14:00 UTC sits in GFS 12z production window (probably useful by accident). UPDATE_REACTION at 00:00 / 02:00 UTC fires during ECMWF 18z and GFS 18z production — *these two crons are firing during NWP production cycles, not after release*, which is empirically the wrong time. **D-D is a confirmed live timing bug, not "potential."** P0 priority is correct. The fix at §6 P0 (`timezone=ZoneInfo("UTC")`) will materially change live behavior; OD1 needs operator awareness that the cron schedule has been wrong for some indeterminate period. **Critical caveat: P0 should not just "add timezone= kwarg"; it must include a doc + commit-body note explaining that crons WILL move 5h to the named UTC times after the fix, and operator should verify whether the post-fix UTC schedule is still optimal vs. the accidentally-CDT schedule.**

### A4 Lane 2 endDate detection cadence [FAIL]
§6 P6 (lines 344-349) reads "Add Polymarket createdAt event listener (Lane 2) / Add Polymarket endDate event listener (Lane 2)". This is the entirety of the trigger-mechanism specification. NO mechanism is named: no on-chain event subscription, no polling cadence, no Polygon RPC binding, no Gamma API webhook, no fallback if event source flaps. Per INVESTIGATION_INTERNAL Q3: existing detection is `_ACTIVE_EVENTS_TTL = 300.0` (5-min Gamma cache) at `src/data/market_scanner.py:139`. If "Lane 2" is just "raise the OPENING_HUNT cadence to 1-min around 04:04 UTC and 12:00 UTC", that's a different design than "event listener". v3 must specify: (a) is this a REST polling tighter window, (b) on-chain Polygon `MarketCreated` / `OrderFilled` event subscription, or (c) a static cron at the documented UTC times? Each has different reliability/operational profiles. P6 cannot ship without a concrete mechanism. **REQUIRES v3 amendment in P6.**

### A5 east-west asymmetry math [PARTIAL FAIL]
§5 line 285 formula `final_kelly_mult *= max(0.3, observed_target_day_fraction)` defines `observed_target_day_fraction = (hours_observed_so_far) / 24`. Per F7 (Wunderground 15-min refresh per `INVESTIGATION_EXTERNAL.md:153-156`), observation is *not* uniformly available throughout the day. For LA at decision_time = 12:00 UTC = 05:00 PDT, "5 hours of target-day observed" assumes Wunderground daily-summary captures 00:00 PDT to 05:00 PDT contiguously — but Wunderground daily summary publishes AFTER local midnight (per F7 + INVESTIGATION_EXTERNAL.md:155 "COOP daily max/min available 'in near real-time' but no published-after-day-end schedule given"). The hours-observed-so-far metric should be `min(hours_since_target_local_midnight, hours_data_actually_published)` — not raw clock fraction. The plan formula is approximately right for cities with always-on hourly METAR (most ICAO airport stations do publish hourly), but the MAX-temp signal that determines settlement is gated by daily-summary availability, which is post-midnight-local. **For UTC-7 cities at 12:00 UTC, "5 hours observed" includes only the wee-hours pre-dawn (00:00-05:00 PDT), which contributes near-zero information to predicted high temperature** — the high is realized 13-15:00 PDT, post-trade-close. Plan's 0.3× floor for LA is generous. The right floor might be 0.0 (or operator-gated entry refusal — see A10/OD3) since pre-dawn observation is informationally degenerate for a max-temp resolution. **REQUIRES v3 amendment**: clarify whether `observed_target_day_fraction` is a clock fraction or an information fraction (informationally weighted by hour-of-day diurnal contribution to max-temp). Or accept clock-fraction with operator-known caveat.

### A6 P5 calibration recalibration [PARTIAL FAIL]
§6 P5 says "Replace STRATEGY_KELLY_MULTIPLIERS[key] with [(key, phase)]" but `calibration_pairs_v2` has no phase column (verified `src/state/schema/v2_schema.py:267-298` per R1 A2). Platt fit happens against a single corpus that already encodes lead_days as a continuous regressor. P5's NEW Kelly multipliers `(center_buy, SETTLEMENT_DAY)=0.5` and `(opening_inertia, SETTLEMENT_DAY)=0.0` (defense) effectively introduce a phase-dependent post-Platt scaling. This is mathematically valid (Platt produces a calibrated probability; Kelly multiplier is a separate sizing scaler) — but the plan should explicitly note that **Platt does not need rebuilding for P5**, because the (key, phase) multiplier acts AFTER calibration. R1 C3 dropped the in-Platt cohort split; the new P5 multiplier is an out-of-Platt scaler. Plan should add this clarification in §6 P5. Without it, an implementer might re-trigger a Platt rebuild they don't need. **REQUIRES v3 amendment**: P5 explicitly documents "Kelly multiplier is post-calibration; no Platt rebuild required."

### A7 P3-P4 ordering dependency [PASS — but with caveat]
§6 line 380 sequencing: "P0 → P1 → P2 → (P3, P4 in parallel) → P5". I do not see a hidden dependency where D-A's two-clock unification (P4) MUST precede D-B's mode→phase migration (P3). They can run in parallel because:
- P3 changes 5 dispatch sites from `discovery_mode == "day0_capture"` to `market_phase == SETTLEMENT_DAY`. The `market_phase` value comes from P2's snapshot, which already exists at P3 entry.
- P4 changes the CANDIDATE-FILTER clock at `cycle_runtime.py:2003` from UTC `endDate − now` to `MarketPhase`-derived. Also depends on P2's snapshot.

Both depend on P2. Neither depends on the other. **Caveat**: §6 P3 line 320 includes `cycle_runtime.py:2083 fetch_day0_observation gate` which IS a candidate-side site and overlaps with P4's territory. If both P3 and P4 simultaneously edit this region of `cycle_runtime.py`, merge-conflict risk is HIGH. v3 should specify which packet owns line 2083 (recommend: P3 owns dispatch site, P4 owns filter site, but they must not overlap on the same line range). A7 PASS with merge-coordination caveat.

### A8 NWP-release-trigger fragility [FAIL]
§6 P6 lines 344-349 do NOT specify a release-detection mechanism. ECMWF Open Data S3 release "available between 7 and 9 hours after the forecast starting date and time" (per INVESTIGATION_EXTERNAL Q1). That's a 2-hour window per cycle. Plan needs one of: (a) S3 ListBucket polling at e.g. 1-min cadence within the 7-9h post-cycle window, (b) AWS S3 Event Notification subscription, (c) `ecmwf-opendata` client library polling. Plan says nothing. The §3 line 155 description "fired by ingest pipeline confirmation that the new model run is downloaded" implies (c) but provides no Zeus-side hook. EXISTING infrastructure: `src/ingest_main.py` references `ecmwf_open_data` ingest job (per `grep -l ecmwf_open_data src/`). v3 P6 must specify how Zeus's strategy scheduler observes the ingest pipeline's release-confirmation signal: shared DB table watch? File-based marker? IPC? **REQUIRES v3 amendment.**

### A9 P5 multiplier storage [FAIL]
§6 P5 (lines 335-342) does NOT specify whether the multiplier is stored per-position-row or recomputed at sizing-time. This is consequential per R1 C8: a position opened pre-P5 retains its OLD multiplier for the position's lifetime. If multiplier is RECOMPUTED at sizing-time from `(strategy_key, current_market_phase)`, then a position opened at MarketPhase=PRE_SETTLEMENT_DAY transitioning to MarketPhase=SETTLEMENT_DAY will have its multiplier change mid-flight — *exactly the rollback issue R1 C8 was trying to prevent*. The right design: **store the multiplier as a position-row column at OPEN time**; at exit-sizing, use the stored value. This requires a schema change to `position_current` (or `decision_chain`). v3 P5 must specify. **REQUIRES v3 amendment.**

### A10 OD3 east-west asymmetry option [PARTIAL FAIL]
Plan §7 OD3 (line 390) defaults to option A (Kelly down-weight). Per A5 above, for cities west of UTC-5 the observation-to-info ratio at trade-close is so degraded that a Kelly multiplier of 0.21× is still trading on essentially-no-information. Option B (hard gate refusing SETTLEMENT_DAY entries below 0.5 fraction) would refuse LA, NYC, Sao Paulo SETTLEMENT_DAY entries entirely. The right answer is plausibly **mode-dependent**: keep option A for SHOULDER fade strategies (where center-of-distribution is the bet, not the max), and switch to option B for SETTLEMENT_DAY-specific strategies where direction-of-the-day signal is load-bearing. Plan §7 OD3 dismisses option B without showing the regime where B wins. **REQUIRES v3 amendment**: at minimum, document the alternate regime where option B is the right call (or explicitly state operator decision is to take A even at LA's 0.3× floor).

### A11 startDate variability across cities [PASS]
Spot-checked 13 cities (7 from external + 6 my add). Range of `startDate − createdAt` lag observed:
- Singapore: 04:24 − 04:04 = 20 min
- London: 04:55 − 04:03 = 52 min
- Tokyo: 05:10 − 04:04 = 66 min
- Wellington: 05:16 − 04:04 = 72 min

So 20-72min spread (plan says "20-70min" — close enough). The variance is small and the `MarketPhase` boundary `PRE_TRADING → PRE_SETTLEMENT_DAY` at startDate is not a load-bearing strategy signal — by startDate, the market has just opened and there's no positional capacity yet. The 50-min spread does not break invariants. A11 PASS.

### A12 0.3× LA SETTLEMENT_DAY floor positivity [REGIME-DEPENDENT — PARTIAL FAIL]
Plan §5 line 285 floor `max(0.3, observed_target_day_fraction)` produces 0.3× for LA at 12:00 UTC trade-close. Whether this is positive-EV depends on whether the OUR signal (Zeus's calibrated probability) at that moment is more accurate than the market's price even with near-zero observation. For SETTLEMENT_DAY entries west of UTC-X, Zeus's signal is dominated by NWP forecast skill rather than observation — and per F14 / `src/calibration/platt.py:1-10`, Platt is calibrated on `lead_days` continuously, so a zero-observation entry is just the high-lead-days region of the existing calibration. The 0.3× floor is defensible IF Platt is well-calibrated at lead_days≈0 hours-of-observation — which is a specific empirical question the plan does not gate on. **REQUIRES v3 amendment OR operator OD3 decision**: gate 0.3× floor on "Platt calibration evidence at lead<24h with observation_fraction<0.3 shows Brier < market-price baseline". Without this, 0.3× LA SETTLEMENT_DAY is hopeful, not evidence-grounded.

### A13 startDate variance breaking invariants [PASS]
The 50-min startDate variance falls entirely within `MarketPhase.PRE_TRADING → PRE_SETTLEMENT_DAY` boundary. Since strategy work is gated by `LifecyclePhase=ACTIVE` (not entered until first fill), the startDate boundary affects only the candidate-discoverability window, not position-lifecycle. No invariant breaks. A13 PASS.

### A14 multi-position-per-market atomicity [FAIL]
A market can have multiple positions (different bins / different bin-widths). When market enters `MarketPhase.SETTLEMENT_DAY`, the plan says "all active positions on that market transition to LifecyclePhase.DAY0_WINDOW" (§2 line 106-107). The writer for `position_current.phase` is `upsert_position_current()` at `src/state/projection.py:87-125` — per-position-row UPSERT. Plan §6 P3+P4 do not specify whether the per-market transition is atomic: if cycle starts at T_0, processes 5 positions on one market, and the cycle wall-clock takes 30s, are all 5 positions written with the SAME phase snapshot, or could a partial-failure leave 3 in DAY0_WINDOW and 2 still in ACTIVE? §6 P2 line 311 ("decision_time, NEVER from wall-clock at point-of-use") covers the read-side stability. It does NOT cover the write-side atomicity. Plan §8 T2 ("every active position whose market is MarketPhase.SETTLEMENT_DAY has LifecyclePhase ∈ {DAY0_WINDOW, PENDING_EXIT}") asserts the post-condition but doesn't pin a transactional invariant. **REQUIRES v3 amendment**: P2 or P4 must specify whether per-market multi-position phase transition is wrapped in a single SAVEPOINT, and a relationship test should assert no partial state.

### A15 51-city APScheduler scaling [FAIL]
§6 P7 (lines 351-356) acknowledges "51 schedules; APScheduler max-jobs limits + restart resilience" risk but does not solve it. APScheduler's `BlockingScheduler` default executor is `ThreadPoolExecutor(max_workers=10)`. Plan does not specify whether P7 will (a) increase max_workers to ≥51, (b) coalesce per-city triggers into per-tz-bucket triggers (14 distinct UTC offsets per `INVESTIGATION_INTERNAL Q2` — much more tractable than 51), or (c) use a different executor. Per `src/main.py:731` no executor config is currently provided. With current P0-fixed scheduler at default 10 workers, P7's 51 schedules would queue under load. **REQUIRES v3 amendment**: P7 must specify worker pool size + per-tz coalescing strategy. ID1 ("APScheduler restart resilience for Lane 1's 51 per-city schedules") at line 405 is correctly classified impl-time but the worker-count question is design-time, not impl-time.

### A16 Lane 1 missed-trigger recovery [FAIL]
§6 P7 says "per-city schedule for SETTLEMENT_DAY entry transition (24h before local end-of-target-date)" — APScheduler `date` job per market per city. If the daemon restarts during the 24h window, will the date-job re-fire? APScheduler `date` triggers are one-shot; if missed, they're missed. The `misfire_grace_time` config at `src/main.py:736-764` is currently unset (default 1 hour). For a 24h-window per-city trigger, a daemon restart > 1h after the SETTLEMENT_DAY entry transition will SKIP the trigger entirely. Per F11 the daemon could restart during a peak-Day0 hour (UTC 15: 21 cities in Day0). Plan does not address restart resilience beyond ID1 deferral. **REQUIRES v3 amendment**: P7 must specify either (a) `misfire_grace_time = 24*3600` for per-city date jobs, (b) a startup catch-up routine that detects positions in target_local_date < 24h and force-fires the transition, or (c) a heartbeat-based recurring tick that reasserts SETTLEMENT_DAY transition from scratch on every cycle. Option (c) is cheapest; (b) is most correct.

---

## Caveats requiring v3 plan amendment

1. **C10 (largest miss)**: §6 must add a per-table phase-handling subsection. `position_lots` has its own state enum on a separate axis; clarify NO `phase` column needed there. `position_events` already has `phase_after`; clarify P2 does not change it. Without this, v3 implementer guesses.
2. **A4 + A8**: P6 trigger-mechanism vacuum. Specify Polymarket createdAt/endDate detection (RPC sub vs polling vs cron) and NWP-release detection (ingest-pipeline marker vs S3 event sub vs poll). Each is a concrete design choice. P6 cannot leave plan-stage without these.
3. **A9**: P5 multiplier-storage policy. State explicitly: stored-at-open-time on a position-row column, recomputed at exit-sizing using stored value. Add the schema-add task.
4. **A6**: P5 explicitly note "Kelly multiplier is post-calibration; no Platt rebuild required." Prevents wasted recalibration.
5. **A14**: P2 or P4 specify atomic write of per-market multi-position phase transition (single SAVEPOINT). Add §8 invariant test for no partial-write.
6. **A15**: P7 specify worker pool size (≥51 or per-tz-coalesced) + APScheduler executor config.
7. **A16**: P7 specify missed-trigger recovery: `misfire_grace_time` ≥ 24h OR a force-fire startup routine.
8. **D-D / A3 caveat**: P0 commit body must note the cron WILL shift 5h to true UTC after fix — operator should verify whether 07/09/19/21 UTC is actually optimal vs. the accidentally-CDT-shifted schedule of the past N days/weeks.
9. **A5 + A12 + A10/OD3**: settle whether `observed_target_day_fraction` is clock-time or information-time. Either accept clock-time approximation with operator awareness, or weight by diurnal max-temp contribution. Document the regime where option B (hard gate) wins.
10. **C6 minor**: §6 P3 prose still says "5 sites" while listing 6 (5 + cycle_runtime.py:2083). Reconcile bullet count.

---

## Bottom line for operator

**Start P0 immediately.** D-D APScheduler tz fix is a confirmed live bug (host tz = America/Chicago, scheduler runs in CDT, the 07/09/19/21 UTC cron is firing 5h shifted). P0 is one-line and unblocks honest discussion of the cron schedule going forward. Critical: P0 commit body must alert that post-fix cron will SHIFT, so operator can verify whether the post-fix UTC schedule is actually optimal.

**P1 (docs purge), P2 (MarketPhase enum), P3 (mode→phase migration on 5+1 sites), P4 (two-clock fix), P5 (phase-aware Kelly with stored multiplier) are shippable** with the listed v3 amendments addressed in-place. P5 is mid-risk — please confirm OD3 and OD7 in writing before P5 PR opens.

**P6 (Lane 2 + Lane 3 scheduling) and P7 (Lane 1 per-city triggers) are NOT shippable in current form.** Trigger mechanisms (A4/A8), worker pool sizing (A15), and missed-trigger recovery (A16) are all unspecified. v3 must redesign these packets before they leave plan stage. This is consistent with §6 line 380's recommendation that P6/P7 ship in their own PRs after P5.

**P8 (CHECK-constraint migration) correctly classified BLOCKING and gated on operator decision after P9 attribution data**. No action needed now.

The plan's R1-fix execution is strong (11/12 caveats fixed; 1 documented but not delivered). The §9 attack-vector exposure is honest — plan author predicted A1-A10 and the new A11-A16 mostly ratify the predictions. **APPROVED-WITH-CAVEATS** for P0-P5 spine; **REJECT** P6/P7 in current form pending v3 mechanism specification.
