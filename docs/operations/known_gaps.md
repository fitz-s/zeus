# Known Gaps — Venus Evolution Worklist

每个 gap 是一个 belief-reality mismatch。每个 gap 的终态：变成 antibody（test/type/code）→ FIXED。
如果一个 gap 包含 "proposed antibody"，下一步就是实现它。

**Active surface**: this file lists OPEN, MITIGATED, PARTIALLY FIXED, and
STALE-UNVERIFIED gaps that still demand attention.

**Antibody archive** (closed FIXED/CLOSED entries — immune-system record of
what we made impossible): `docs/operations/known_gaps_archive.md`. Reference
when a similar pattern resurfaces; do not re-open without proof the antibody
failed.

---

## ANTI-RABBIT-HOLE: upstream-Polymarket scope limits (READ FIRST)

### [STRUCTURAL — NOT A BUG] Polymarket LOW market series starts 2026-04-15
**Status:** documented; do not chase.
**Audit date:** 2026-04-28 (gamma-api.polymarket.com live probe).
**Fact:** Polymarket did NOT offer LOW (mn2t6 / "lowest temperature") weather markets before 2026-04-15. First closed LOW event resolved 2026-04-15. Coverage is 8 cities only: London, Seoul, NYC, Tokyo, Shanghai, Paris, Miami, Hong Kong.
**Reality numbers:** 48 closed LOW events / 18 active. Date range 2026-04-15..2026-04-29. HIGH (max temp) market series predates LOW by ~2 years.
**Implication:**
- `state/zeus-world.db::settlements` LOW rows will never exceed ~50 historical + ~8/day going forward
- LOW Platt training MUST use `observations.low_temp` (42,749 rows / 51 cities / 2023-12-27..2026-04-19) as canonical ground truth — NOT `settlements` LOW
- Absence of LOW settlement rows for (city, date) tuples outside the 8-city × post-2026-04-15 scope is structural, not a backfill miss
**Do NOT:**
- Write retro-scrapers for pre-2026-04-15 dates
- Open quarantine reactivation tickets for cities outside the 8-city set
- Search archives expecting historical LOW market truth to exist
- Block on this gap when training LOW calibration; use observations.low_temp
**Antibody:** `architecture/fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15` (severity=critical)
**Proof artifacts:**
- `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`
- `docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json`
**Invalidation:** only a fresh gamma-api probe with HTTP-evidence showing LOW events with endDate < 2026-04-15 OR coverage beyond 8 cities may relax this.

---

## CRITICAL: DST / Timezone

### [OPEN — NOT LIVE-CERTIFIED] Historical diurnal aggregates still need DST-safe rebuild cleanup
**Certification status:** This gap blocks live math certification. The DST historical rebuild has NOT been executed and historical data derived from pre-fix aggregates is NOT certified for promotion. See `architecture/data_rebuild_topology.yaml` → `dst_historical_rebuild`.
**Location:** `scripts/etl_hourly_observations.py`, `scripts/etl_diurnal_curves.py`, `src/signal/diurnal.py`
**Problem:** The old London 2025-03-30 hour=1 evidence is stale. ETL/runtime is now partially DST-aware, but historical `diurnal_curves` materializations may still need to be rebuilt from true zone-aware local timestamps.
**Runtime mismatch:** `get_current_local_hour()` in `diurnal.py` already uses `ZoneInfo` and is DST-aware. The remaining risk is stale pre-fix aggregates/backfill, not the runtime clock itself.
**Impact:** Day0 `diurnal_peak_confidence` can still drift if old hourly/diurnal tables remain in circulation. NYC (EDT/EST), Chicago (CDT/CST), London (BST/GMT), Paris (CEST/CET) should be revalidated after rebuild; Tokyo, Seoul, Shanghai remain safe (no DST).
**Proposed antibody:**
1. Verify every ETL/backfill path derives `obs_hour` from zone-aware local timestamps.
2. Rebuild historical `hourly_observations` / `diurnal_curves` materializations from the corrected path.
3. Keep `test_diurnal_curves_hour_is_dst_aware` (or equivalent) to guard spring-forward/fall-back behavior.
**Cities affected:** DST cities only until the historical rebuild is proven clean.

---

## CRITICAL: Instrument Model

All entries antibody-closed (Bin.unit / SettlementSemantics.for_city / Platt
bin-width-aware / astype(int) → SettlementSemantics.round_values, etc.). See
`known_gaps_archive.md` → "CRITICAL: Instrument Model".

---

## CRITICAL: Exit/Entry Epistemic Asymmetry

Instrument-level antibodies all closed (MC count parity / CI-aware exit /
hours_since_open / MODEL_DIVERGENCE_PANIC threshold). See
`known_gaps_archive.md` → "CRITICAL: Exit/Entry Epistemic Asymmetry".

The structural relationship gap remains OPEN as **D4** under "MEDIUM-CRITICAL:
Cross-Layer Epistemic Fragmentation" below.

---

## CRITICAL: Day0 Signal Quality

All entries antibody-closed (continuous observation_weight / continuous
post-peak sigma decay). See `known_gaps_archive.md` → "CRITICAL: Day0 Signal
Quality".

---

## MEDIUM: Data Confidence

### [STALE-UNVERIFIED] Open-Meteo quota contention is workspace-wide, not Zeus-only
**Location:** Zeus + `51 source data` + Rainstorm-era ingestion loops
**Problem (filed 2026-04-03):** Workspace has shared data agents that can cause `429 Too Many Requests` on Open-Meteo, causing Zeus to misdiagnose quota issues.
**Status (2026-04-06):** All recent Open-Meteo API calls in the log show `HTTP/1.1 200 OK` with no 429 errors. Harvester ran successfully (`settlements_found=141`) but created 0 pairs — the failure mode appears to be Stage-2 bootstrap, not quota exhaustion. This gap may be less active than initially feared.
**Proposed antibody:** 建立 workspace-wide quota coordination：至少要有共享计数 / cooldown / update watermark，或者明确调度隔离，让 Zeus 的交易路径优先于后台数据 agent。

(2 FIXED entries on persistence_anomaly + 2 CLOSED 2026-04-15 entries on
alpha_overrides / harvester bias correction archived to
`known_gaps_archive.md` → "MEDIUM: Data Confidence".)

---

## CRITICAL: Settlement Source Mismatch (2026-04-16 smoke test)

### [OPEN] HK: SettlementSemantics uses WMO half-up, but PM resolution uses floor (bin containment)
**Location:** `src/contracts/settlement_semantics.py` → `for_city()` → non-WU path
**Problem:** PM HK description says: "resolve to the temperature range that **contains** the highest temperature... temperatures in Celsius to **one decimal place**." HKO Daily Extract returns 0.1°C precision (e.g., 27.8°C). PM maps 27.8 into "27°C" bin via floor containment: 27 ≤ 27.8 < 28. Our `SettlementSemantics` uses `precision=1.0` + `rounding_rule="wmo_half_up"`, giving `floor(27.8+0.5)=28` — wrong bin.
**Evidence:** Floor fixes 3/3 HKO-period mismatches (03-18, 03-24, 03-29) with 0 regressions against 16 total HK PM markets. All 11 existing matches preserved under floor.
**Impact:** HK is the only city with decimal-precision raw values (all WU cities return integers where floor=WMO). This is an architecture-level change: modifying `SettlementSemantics.for_city()` for HKO rounding affects the probability chain (ENS → noise → settlement rounding → bin assignment).
**Fix scope:** Change `rounding_rule` to `"floor"` for `settlement_source_type == "hko"` in `SettlementSemantics.for_city()`. Requires system constitution review since WMO half-up is stated as universal law in AGENTS.md line 49 and line 117.
**Blocked by:** System constitution review — AGENTS.md says "Settlement: WMO asymmetric half-up rounding" as universal. HKO is an exception where PM uses containment semantics instead.

### [OPEN] HK 03-13, 03-14: unresolved HKO source/audit mismatch; no WU ICAO route
**Problem:** Earlier packet language claimed a WU/VHHH Airport route. Operator correction 2026-04-28 supersedes that: Hong Kong has no WU ICAO route in Zeus. We have HKO Observatory data and the two early dates remain unresolved source/audit mismatches until fresh operator-approved primary-source evidence proves the settlement source.
**Impact:** 2 mismatches. Do not resolve by adding HK WU/VHHH/`wu_icao` aliases; keep quarantined/fail-closed pending HKO-specific audit evidence.

### [OPEN] WU cities (SZ/Seoul/SP/KL/etc.): API max(hourly) ≠ website daily summary high
**Problem:** PM resolves from WU website daily summary page (e.g., `wunderground.com/history/daily/cn/shenzhen/ZGSZ`). We compute `max(hourly_temp_C)` from WU v1 API. These are different values. Tested on 10 SZ mismatch dates: neither floor(F→C) nor WMO(F→C) from API hourly data explains PM values (1/10 and 3/10 respectively). Additionally, the WU API returns obs from "Lau Fau Shan" (HK station) for ZGSZ, while PM reads the Bao'an Airport page.
**Impact:** ~19 mismatches across SZ(10), Seoul(5), SP(2), KL(1), Chengdu(1).
**Fix:** Need to either scrape the WU website daily summary or find the XHR API endpoint that the WU Angular SPA uses to load daily summary data.

### [OPEN] Taipei: PM switched resolution source 3 times
**Problem:** PM used CWA (03-16~03-22) → NOAA Taiwan Taoyuan Intl Airport (03-23~04-04) → WU/RCSS Taipei Songshan Airport (04-05+). We only have WU/RCSS data for all dates. Gaps of 1-5°C on 16 mismatch dates confirm wrong source.
**Impact:** 16 mismatches. Need per-date source routing or historical data from CWA and NOAA for the affected periods.

---

## Polymarket Bin Structure (verified from zeus.db, 2026-03-31)

**这是 ground truth，来自实际市场数据，不是 spec：**

### °F 城市（Atlanta 示例）
```
40-41°F, 42-43°F, 44-45°F, 46-47°F, 48-49°F, 50-51°F, 52-53°F, 54-55°F, 56-57°F
+ shoulder: X°F or below, X°F or higher
```
每个 center bin = 2°F range，覆盖 2 个 integer settlement 值。
每个 market 约 9 个 center bins + 2 shoulder bins。

### °C 城市（London 示例）
```
9°C, 10°C, 11°C, 12°C, 13°C, 14°C, 15°C
+ shoulder: X°C or below, X°C or higher
```
每个 center bin = 1°C point bin，覆盖 1 个 integer settlement 值。
每个 market 约 7-10 个 center bins + 2 shoulder bins。

### Settlement Chain
```
Atmosphere → NWP model → ASOS sensor (0.1°C precision) → METAR report → 
WU display (integer °F for US, integer °C for international) → Polymarket settlement
```

---

## Module Relationship Map（从这个 session 的 deep reading 中提取）

### Entry Path
```
market_scanner → evaluator → EnsembleSignal.p_raw_vector(bins, n_mc=5000)
                           → Platt calibrate → MarketAnalysis.find_edges()
                           → FDR filter → Kelly sizing → risk limits
                           → executor → Position(env=mode, unit=city.unit)
```

### Monitor Path
```
cycle_runner._execute_monitoring_phase()
  → monitor_refresh.refresh_position(conn, clob, pos)
    → _refresh_ens_member_counting() OR _refresh_day0_observation()
      → EnsembleSignal.p_raw_vector(single_bin, n_mc=5000)  [was 1000, fixed]
      → Platt calibrate → compute alpha → p_posterior
      → EdgeContext(forward_edge, p_market, confidence_band_*)
  → exit_triggers.evaluate_exit_triggers(pos, edge_ctx)
    → EDGE_REVERSAL / BUY_NO_EDGE_EXIT / SETTLEMENT_IMMINENT / etc.
  → exit_lifecycle.execute_exit(portfolio, pos, reason, price, paper_mode, clob)
    → paper: close_position() directly
    → live: place_sell_order() → check fill → retry/backoff
```

### Key Cross-Module Relationships
1. **Entry 和 monitor 必须用相同的 MC count** — FIXED (both 5000)
2. **Entry 和 monitor 必须用相同的 SettlementSemantics** — FIXED (for_city)
3. **Entry uses bootstrap CI, monitor now emits coherent conservative bounds for exit logic** — PARTIALLY CLOSED
4. **Entry and monitor both use real hours_since_open semantics** — FIXED
5. **Evaluator 传 Bin.unit，monitor_refresh 传 Bin.unit** — FIXED (both use position.unit)
6. **Harvester 和 evaluator 的 bias correction 设置不同步** — OPEN gap
7. **Canonical settlement payload path is authoritative** — FIXED (canonical path landed; no stale OPEN claim remains)
8. **`status_summary` runtime truth is lane-specific and enum-normalized** — FIXED (no mixed `ChainState.UNKNOWN` vs `unknown` truth)

---

## Tooling / Operator Health

### [STALE-UNVERIFIED] CycleRunner fails on malformed `solar_daily` schema rootpage
**Location:** `zeus/state/zeus.db` / the day0 capture path that reads `solar_daily`
**Problem (filed 2026-04-02):** The paper cycle failed with `malformed database schema (solar_daily) - invalid rootpage`. The monitor path was reading a broken SQLite object and the cycle aborted instead of degrading cleanly.
**Status (2026-04-06):** The latest `opening_hunt` cycles completed without this error appearing in the log. Not confirmed fixed — may have been intermittent or masked by a different cycle mode. Requires a deliberate `day0_capture` run to verify.
**Proposed antibody:** Add an explicit schema/integrity check before day0 capture and fail closed with a structured error (plus a repair/migration path) instead of letting SQLite rootpage corruption surface mid-cycle.

### [OPEN] strategy_tracker can report profit that is not reconstructible from durable DB truth
**Location:** `src/state/strategy_tracker.py`, `zeus/state/strategy_tracker-paper.json`, `zeus/state/positions-paper.json`, `zeus/state/zeus.db`
**Problem:** `strategy_tracker-paper.json` currently reports `opening_inertia` cumulative PnL of `+247.83`, but the authoritative current-regime cash ledger in `positions-paper.json` only reflects `opening_inertia` realized PnL of `-2.21`. Several large positive `opening_inertia` trades in the tracker (for example `f4e0d2a6-b8a`, `b2086cca-a1a`, `836270b8-2cc`, `8d9071fa-fab`, `eebdb911-99e`, `16a62cac-696`) are not reconstructible from `trade_decisions` or `position_events` in the current DB snapshot.
**Impact:** A non-authoritative attribution surface can be mistaken for wallet truth, creating a false belief that paper PnL is much higher than the bankroll snapshot actually shows.
**Proposed antibody:** Rebuild tracker summaries only from durable settlement/exit events or stamp every non-DB-backed trade with explicit archival provenance; add a reconciliation test that tracker PnL must be derivable from durable event truth (or explicitly marked as legacy/archive-only).

(2 FIXED entries on Healthcheck assumptions + Day0 stale probability waiver
archived to `known_gaps_archive.md` → "Tooling / Operator Health".)

---

## 2026-04-03 — edge-reversal follow-up triage

### [OPEN] Paper positions have no token_id → chain_state=unknown → stale_legacy_fallback → RiskGuard RED
**Location:** `src/execution/executor.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py`
**Problem (filed 2026-04-10):** 12 paper positions entered April 7 with no token_id. All have `chain_state="unknown"`, `token_id=""`. Canonical DB projection returns non-ok status → `load_portfolio()` falls back to stale JSON → RiskGuard sees broken portfolio → RED → all new entries blocked since April 7.
**Evidence (2026-04-10):** `load_portfolio falling back to JSON because canonical projection is unavailable: stale_legacy_fallback` in both zeus-paper.log and riskguard.err. 12 positions in `positions-paper.json` with empty token_id. No new trades in cycle logs since April 7 despite active April 11 markets.
**Impact:** Zero new trades for 3 days. Polymarket has 47 active April 11 markets with prices, but system cannot enter due to RED block.
**Proposed antibody:** Add a canonical projection preflight in `load_portfolio()` that explicitly checks position chain state — if > N positions have `chain_state=unknown`, mark projection as `degraded` instead of `ok`, and require explicit handling rather than silent fallback.

### [MITIGATED] Missing monitor-to-exit chain escalates before settlement (2026-04-13)
**Location:** `src/engine/cycle_runtime.py`, `src/engine/monitor_refresh.py`
**Problem:** A subset of positions reach settlement with only lifecycle + settlement events and no intermediate monitor/reversal chain, so `EDGE_REVERSAL` never has a chance to fire.
**Impact:** The system cannot protect itself from fast-moving divergence if the monitor phase does not create an actual executable exit path.
**Antibody deployed:** `execute_monitoring_phase()` now records `monitor_chain_missing` when a settlement-sensitive position cannot form a usable monitor-to-exit chain because refresh failed or exit authority returned `INCOMPLETE_EXIT_CONTEXT`. Refresh failures now produce a `MonitorResult` instead of disappearing from the cycle artifact, and `status_summary` projects `cycle_monitor_chain_missing:<count>` as infrastructure RED.
**Residual:** This is operator-visible cycle escalation, not durable lifetime proof. DB projection/schema support for monitor counts or a durable monitor evidence spine remains a separate package.

### [PARTIALLY FIXED] EDGE_REVERSAL — hard divergence kill-switch at 0.30 added (2026-04-06, math audit)
**Location:** `src/state/portfolio.py`, `src/execution/exit_triggers.py`
**Problem:** Reversal requires two negative confirmations plus an EV gate, so a position can become clearly wrong in settlement truth without ever tripping runtime reversal.
**Impact:** The system may hold losers through large adverse moves when the market changes quickly but not persistently enough for the current confirmation rule.
**Proposed antibody:** Keep the conservative reversal path, but add a separate hard divergence kill-switch (single-shot on extreme divergence / velocity) for high-confidence failures.

### [MITIGATED] Harvester Stage-2 DB shape preflight prevents noisy canonical-bootstrap failures (2026-04-13)
**Location:** `src/execution/harvester.py` / runtime `position_events` helpers
**Problem:** Recent log tails show repeated harvester errors stating that legacy runtime `position_events` helpers do not support canonically bootstrapped databases. The Stage-2 bootstrap path is still being exercised at runtime even though the helper contract cannot handle the current DB shape.
**Live evidence (2026-04-06):** Harvester ran at 12:47–12:55 CDT and produced `settlements_found=141, pairs_created=0, positions_settled=0`. It found settlements but generated zero calibration pairs — consistent with Stage-2 helpers failing on canonically bootstrapped DB. Gamma API fetch also timed out during this run (`WARNING: Gamma API fetch failed: The read operation timed out`).
**Impact:** Harvester cycles can fail noisily and skip settlement/pair creation work, leaving the runtime path partially broken even when the daemon and RiskGuard are alive.
**Antibody deployed:** `run_harvester()` now runs a Stage-2 DB-shape preflight after settled events are fetched and before per-event learning work starts. If runtime support tables are missing, it returns `stage2_status='skipped_db_shape_preflight'` with missing trade/shared table lists and skips only Stage-2 snapshot/calibration/refit work; event parsing and settlement handling still run. Legacy `decision_log` settlement-record storage degrades when that table is absent instead of crashing the cycle.
**Residual:** This is a structured skip, not a migration. It does not create calibration pairs on canonical-only bootstrap DBs, rebuild `p_raw_json`, or replace legacy Stage-2 helpers with a fully canonical learning path.

### [OPEN] ACP router fallback chain is recovering after failure, not stabilizing before dispatch
**Source:** `evolution/router-audit/2026-04-08-router-audit.md`
**Problem:** The current router can classify `auth`, `timeout`, and `network` failures, but dispatch still happens before allowlist/auth/timeout hard prechecks. Result: the fallback chain keeps switching to another failure surface instead of a known-good surface.
**Impact:** Window-level timeout clusters, invalid auth tokens, and Discord gateway/network failures can cascade across the routing stack.
**Proposed antibody:** Add a deterministic pre-dispatch gate for allowlist/auth/timeout, then run semantic routing only over candidates that already passed preflight.

(5 FIXED entries on settlement CI guard / buy-yes proxy / settlement won
ambiguity / control-plane gate drift / LA Gamma Milan / Heartbeat cron RED
suppression archived to `known_gaps_archive.md` → "2026-04-03 —
edge-reversal follow-up triage".)

---

## MEDIUM-CRITICAL: Cross-Layer Epistemic Fragmentation (D1–D6)

Six design gaps identified at the signal→strategy→execution boundary. The signal layer's high hit rate does not compose into profit because each cross-layer handoff loses the semantic that makes the upstream number meaningful. These are architecture-level gaps requiring typed contracts at module boundaries (INV-12 territory).

### [MITIGATED] D1 — Alpha consumers declare EV compatibility (2026-04-13)
**Location:** `src/strategy/market_fusion.py` — `compute_alpha()`
**Problem:** α adjustments (spread, lead time, freshness, model agreement) are validated against Brier score. But profit requires EV > cost. Brier-optimization converges Zeus toward market consensus, which drives edge → 0. The optimization target (accuracy) conflicts with the business objective (profit).
**Impact:** Systematic edge compression. Alpha tuning that improves calibration accuracy simultaneously destroys the trading edge.
**Antibody deployed:** `compute_alpha()` returns `AlphaDecision(optimization_target='risk_cap')`; active entry and monitor consumers call `value_for_consumer('ev')` before using α. Invalid alpha targets now fail construction, and a Brier-target alpha fails closed before Kelly sizing instead of silently flowing into EV decisions.
**Residual:** α is still a conservative risk-cap blend, not an EV-optimized sweep. Closing D1 fully requires deriving and validating an EV-target alpha policy, not just preventing target mismatch.

### [MITIGATED] D2 — Tail alpha scale is explicit calibration treatment (2026-04-13)
**Location:** `src/strategy/market_fusion.py` — tail alpha scaling
**Problem:** `TAIL_ALPHA_SCALE=0.5` scales α toward market on tail bins, directly halving the edge that buy_no depends on (retail lottery-effect overpricing of shoulder bins). The scaling serves calibration accuracy (Brier) but destroys the structural edge that Strategy B (Shoulder Bin Sell) exploits.
**Impact:** Strategy B's primary edge source is systematically attenuated by a calibration-serving parameter.
**Antibody deployed:** `alpha_for_bin()` now routes tail scaling through `DEFAULT_TAIL_TREATMENT = TailTreatment(scale_factor=TAIL_ALPHA_SCALE, serves='calibration_accuracy', ...)` instead of applying a naked constant. Provenance also states this is calibration-serving, not buy_no P&L validated.
**Residual:** Behavior is unchanged and still may attenuate buy_no structural edge. Closing D2 requires a profit-validated tail policy, likely direction/objective-aware, with buy_no P&L evidence.

### [OPEN] D3 — Entry price must remain typed through execution economics
**Location:** `src/strategy/market_analysis.py` — `BinEdge.entry_price`
**Problem:** `BinEdge.entry_price = p_market[i]` (implied probability from mid-price), but actual execution price = ask + taker fee (5%) + slippage. Kelly sizing uses the implied probability as the cost basis, systematically oversizing positions because the real cost is higher.
**Impact:** Every Kelly-sized position is larger than it should be. The magnitude depends on spread width and fee structure.
**Mitigation deployed (2026-04-13):** `evaluator.py` wraps entry price as `ExecutionPrice`, queries token-specific CLOB fee rate when available, and computes `polymarket_fee(p) = fee_rate × p × (1-p)` before Kelly. The default settings now make the fee-adjusted path authoritative (`EXECUTION_PRICE_SHADOW=true`) instead of leaving the old bare-price path in control.
**Remaining antibody:** Remove or harden the rollback seam, carry typed execution cost beyond evaluator, and connect market-specific tick size, neg-risk, and realized fill/slippage reconciliation.

### [OPEN] D4 — Entry-exit epistemic asymmetry (CRITICAL)
**Location:** `src/engine/evaluator.py` (entry), `src/execution/exit_triggers.py` (exit)
**Problem:** Entry requires BH FDR α=0.10 + bootstrap CI + `ci_lower > 0` — high statistical burden. Exit requires only 2-cycle confirmation — low statistical burden. The system admits edges cautiously but exits aggressively, killing true edges via noise before they mature.
**Cross-reference:** Several specific manifestations of this asymmetry are tracked in the "Exit/Entry Epistemic Asymmetry" section above (MC count mismatch [FIXED], CI-aware exit [FIXED], hours_since_open [FIXED], divergence threshold [FIXED]). This gap tracks the *structural* asymmetry: entry and exit should share a symmetric `DecisionEvidence` contract with comparable statistical burden.
**Proposed antibody:** Entry and exit share the same `DecisionEvidence` contract type with symmetric statistical burden. Exit reversal requires bootstrap-grade evidence, not just 2 consecutive point-estimate checks.

(D5 / D6 / Day0-canonical-event closed entries archived to
`known_gaps_archive.md` → "MEDIUM-CRITICAL: Cross-Layer Epistemic
Fragmentation (D1–D6)".)
