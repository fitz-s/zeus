# Known Gaps — Venus Evolution Worklist

**NOTE:** Closed entries moved to docs/to-do-list/known_gaps_archive.md on 2026-05-01 (per 2026-04-30 recheck)
**Location note:** Active known gaps moved from `docs/operations/known_gaps.md` to this to-do-list surface on 2026-05-02.
**Last main-aligned:** 2026-05-08 (closed stale headings and small blockers
moved to archive; active blockers realigned; `climate_zone` left as future work)

每个 gap 是一个 belief-reality mismatch。每个 gap 的终态：变成 antibody（test/type/code）→ FIXED。
如果一个 gap 包含 "proposed antibody"，下一步就是实现它。

**Active surface**: this file lists OPEN, PARTIALLY FIXED,
STALE-UNVERIFIED, and residual-bearing MITIGATED gaps that still demand
attention.

**Antibody archive** (closed FIXED/CLOSED entries — immune-system record of
what we made impossible): `docs/to-do-list/known_gaps_archive.md`. Reference
when a similar pattern resurfaces; do not re-open without proof the antibody
failed.

---

## CRITICAL: SQLite 单写者锁导致 live daemon 崩溃 (2026-05-04)

**Status:** PARTIALLY FIXED — Wave35 prevents
`rebuild_calibration_pairs_v2.py` and `refit_platt_v2.py` write mode from
defaulting to the canonical shared `zeus-world.db`; they now require an explicit
isolated `--db` target and reject the live shared world DB before opening a
write connection. Full calibration DB physical isolation and promotion tooling
remain open architecture work.
**First observed:** 2026-05-04
**Root cause:** SQLite 单写者模型。WAL 模式允许并发读，但同一时刻只有一个写者可以持有写锁。`rebuild_calibration_pairs_v2.py` 跑了 30+ 分钟写事务，任何需要写锁的操作（包括 `init_schema()`）都会等待或超时崩溃。

**Severity by scenario:**

| 场景 | 风险 |
|------|------|
| 重建脚本在 live trading 时段运行 | **CRITICAL** — live daemon 无法重启 |
| 重建脚本在盘前运行但超时 | **HIGH** — 开盘时仍锁定 |
| 正常数据采集（非重建） | LOW — 短事务，WAL 通常够用 |

**Immediate mitigation:** 不要在 live 时段（或盘前 2h 内）运行 `rebuild_calibration_pairs_v2.py`。操作员手动调度。

**Structural fixes (options):**
1. **写锁 timeout + 快速失败**：rebuild 脚本持有锁超过 N 秒时主动放锁，live daemon 设置短 `timeout` 而不是无限等待——最小成本，治标。
2. **DB 物理隔离**：calibration DB（重建写入）与 live trading DB（zeus.db）分开——从根本上消除锁竞争，是 `project_zeus_isolation_design.md` 已记录的结构决策方向。
3. **事务分片**：rebuild 脚本改为分批 commit（每 N 城市一个事务），降低单次持锁时长——降低概率，但不消除竞争。

**Proposed antibody:** DB 物理隔离（option 2）是唯一能使"rebuild 时 live daemon 崩溃"这一错误类别不可能发生的结构决策。Options 1/3 是降险措施。
**Antibody deployed (2026-05-08 Wave35):** rebuild/refit bulk calibration
writers no longer silently target `state/zeus-world.db` in write mode. Dry-run
read-only inspection can still use canonical world truth, but `--no-dry-run`
requires `--db <isolated staging DB>` and refuses the canonical shared world DB.
This closes the accidental default live-lock path for those two scripts; it does
not authorize rebuild/refit execution, live DB mutation, promotion, migration,
or live unlock.
**Blocks:** live daemon 稳定性；任何需要在 live 时段运行 rebuild 的操作。

---

## CRITICAL: Full-flow live audit (2026-04-28)

**Status:** OPEN; read-only audit record.
**Audit scope:** weather contract semantics -> source truth -> forecast signal
-> calibration -> edge -> execution -> holding/monitoring -> exit -> settlement
-> learning/observability.
**Audit posture:** Read-only gap register. Each entry is an open belief-reality mismatch; resolution requires code/test/type antibody.

### 2026-05-08 active gap realignment

This section replaces the older 2026-04-30 overlay plus stale `[OPEN]`
headings. Resolved headings were moved to
`docs/to-do-list/known_gaps_archive.md` under
`2026-05-08 — Active known-gaps realignment closures`.

**Out of this wave:** `s3 — climate_zone field missing from config/cities.json`
is future calibration taxonomy work. Keep it visible, but do not spend this
wave on it.

**Closed entries:** moved to the archive section named above. Do not treat the
removed headings as active blockers unless new current-code evidence proves the
antibody failed.

**Remaining small blockers to investigate/repair first:**

1. **Calibration/live-lock proof path:** active lock remains the TIGGE
   00z/12z asymmetry plus missing staged live-smoke evidence. Repair order:
   prove current Platt/calibration promotion state, add or verify staged-smoke
   evidence, and keep uncalibrated strategies blocked.
   **2026-05-08 read-only rebuild-authority note:** the T1E sentinel repair
   does not by itself prove current Platt math wrong or require an immediate
   emergency rebuild. Read-only inspection of the authoritative
   `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db` found
   active `platt_models_v2` rows and existing `validated_calibration_transfers`
   rows, but zero `calibration_pairs_v2_rebuild_complete:*` sentinel rows in
   `zeus_meta`. Therefore existing models are not automatically invalidated by
   this code repair, but any future live refit, calibration-transfer OOS
   authority, or promotion-grade proof under the new gate requires a controlled
   daemon-locked/offline rebuild that writes the completion sentinel first.
2. **Source/settlement current-fact refresh:** HK broad HKO caution, WU website
   daily-summary vs API-hourly mismatch, Taipei historical source transitions,
   and DST historical rebuild live-certification remain active until fresh
   source/data evidence closes or narrows them.
   **Data-layer handling rule (2026-05-08):** do not code-patch these into
   apparent truth. Each item needs an offline/current-fact packet with source
   evidence, target-date scope, isolated DB or read-only proof, checksums or row
   counts, dry-run output, and rollback/non-promotion plan before any canonical
   data mutation, rebuild, refetch, settlement relabel, calibration refit, or
   learning/report promotion.
3. **RED direct venue side-effect SLA:** current `cycle_runner` records durable
   RED intent and uses normal execution seams; direct in-cycle venue
   `cancel_order()` / sweep submission is not implemented and requires a
   live-side-effect packet with explicit operator-go.
4. **D4 entry/exit evidence symmetry:** Wave31 closes the live-execution
   invariant: weak statistical exits now fail closed before exit intent. Residual
   work is strategy-quality, not live-submit permission: implement stronger
   exit-side evidence instead of holding/reviewing weak triggers.
5. **D3 execution-cost residual:** Kelly boundary now requires typed
   `ExecutionPrice`. Wave32 closes the venue-confirmed full-open fill cost cap
   in portfolio read models. Wave36 closes the pending-entry residual where
   submitted/model economics could populate open position `entry_price`,
   `cost_basis_usd`, effective portfolio exposure, or `position_current`
   read-model economics before fill authority. Wave41 closes the reconcile
   residual where generic exchange `price` could become realized `fill_price`.
   Wave42 closes the harvester residual where corrected-marked positions
   without fill authority could fall back to legacy settlement P&L. Remaining
   work is typed cost-basis continuity across deeper read-model, replay/report,
   and learning boundaries.
6. **Low-priority cleanup/unknowns:** Wave38 removes future
   `hourly_observations` constructibility and scheduling. Existing DB-file
   physical residue, if present, remains a data-layer cleanup requiring a
   migration/backup/rollback packet. Wave39 verified malformed `solar_daily`
   rootpage degrade behavior. Wave40 refreshed Open-Meteo quota evidence:
   Zeus has process-local 429/quota guards, but not a workspace-wide quota
   authority. ACP router status is archived/out-of-scope for Zeus code.

**Remaining work classification after Waves 35-42:**

| Item | Current class | Why it is not a small code cleanup |
| --- | --- | --- |
| Calibration DB physical isolation / promotion tooling | architecture + data-layer | Needs storage topology, promotion authority, dry-run/rollback, and no live DB mutation without operator-go. |
| RED direct venue side-effect SLA | live side-effect | Would call venue/account mutation paths; requires explicit operator-go, fake/live dry-run evidence, and rollback/block-state design. |
| DST historical diurnal rebuild certification | data rebuild | Requires offline historical rebuild/certification from zone-aware timestamps; no canonical backfill/relabel authorized here. |
| HK/WU/Taipei settlement-source mismatches | external source/current-fact audit + data-layer | Must be resolved from external settlement-source evidence per date before any row can be relabeled, learned from, or promoted. |
| D3 remaining typed execution-cost continuity | partial architecture/source | Waves 41-42 closed two active contamination paths; residual work is deeper read-model/replay/report/learning continuity and any external venue fee/liquidity-role evidence. |
| D4 stronger exit evidence | strategy/evidence architecture | Wave31 makes weak exits fail closed; building symmetric exit evidence requires new external-reality-valid evidence, not a one-line fix. |
| s4 precision-weight continuity | schema/data-layer | Requires `precision_weight` schema/read/write path, rebuild/refit cohorting, and compatibility policy for legacy rows. |
| Physical `hourly_observations` residue | destructive DB cleanup | Source constructibility is gone; physical drop still needs DB inventory, backup, dry-run SQL, and rollback. |
| Open-Meteo quota contention | workspace-boundary operational gap | Zeus core fetch paths use a process-local tracker; quota/cooldown is still not a shared workspace authority, and source-health probing can spend quota outside that tracker. |

### 2026-05-08 broad open-gap exploration record

Read-only exploration after the small-blocker repair wave narrows the remaining
work as follows:

1. **D4 exit evidence hard gate: live-execution path closed by Wave31.** Entry
   `DecisionEvidence` exists and is persisted for new canonical entries.
   `cycle_runtime` now blocks `EDGE_REVERSAL`, `BUY_NO_EDGE_EXIT`, and
   `BUY_NO_NEAR_EXIT` before monitor result/executable intent when entry
   evidence is missing or the current exit burden is weaker. Residual: build an
   external-reality-valid stronger exit evidence contract rather than relying on
   hold/review for weak statistical triggers.
2. **D3 execution-cost continuity: active contamination paths narrowed.** Kelly and final submit
   seams now use typed executable-price/snapshot guards, and fill authority
   fields exist. Wave32 closed the active read-model case where a full-open
   venue-confirmed fill cost could be capped by `position_current` projection
   cost; partial-exit open slices remain proportional to remaining shares.
   Wave36 closed the pending-entry no-fill case: submitted limit/model price and
   target notional now remain submitted/target fields and no longer become
   effective open economics in `materialize_position`, `PortfolioState`, or the
   DB status/loader views. Wave41 closed the exchange-reconcile case where a
   generic exchange `price` could be promoted into `venue_trade_facts.fill_price`.
   Wave42 closed the harvester case where corrected-marked positions without
   fill authority could settle through legacy `size_usd / entry_price` P&L.
   Residual: the cost object still does not survive as one typed lineage through
   read-model, replay/report, and learning consumers, and some diagnostic paths
   still reason from bare `entry_price` floats when corrected fill/executable
   cost authority is absent.
3. **Calibration proof path remains certification-blocked, not emergency
   invalidated.** Active Platt rows exist, but future live refit,
   calibration-transfer OOS promotion, or promotion-grade proof must consume the
   new complete rebuild sentinel. The `s4` calibration weighting LAW antibody
   suite is now split: Wave37 closes the safe code/config/static subset, while
   row-level `precision_weight` antibodies remain blocked on a schema/data
   packet. `s7` n_mc=10000 rebuild remains a future purity/certification
   rebuild, ideally after `s8` vectorization.
4. **Source/settlement current facts are partially refreshed but not closed.**
   A read-only `watch_source_contract.py --json --compact-alerts --report-only`
   probe on 2026-05-08 returned `authority=VERIFIED`, `status=OK`, 168 checked
   configured active temperature markets, and zero WARN/ALERT source-contract
   drift events. This narrows active market station drift only; it does not close
   HK 03-13/03-14 HKO audit mismatch, WU website daily-summary vs API-hourly
   historical mismatches, Taipei per-date source transitions, or DST historical
   rebuild certification.
   Required data-layer follow-up:
   - HK/HKO: collect authoritative HKO settlement-source evidence for the exact
     affected target dates, compare against persisted settlement/observation
     rows, and keep any mismatch quarantined until an operator-approved offline
     relabel/rebuild packet exists.
   - WU daily-summary vs API hourly: prove which WU surface Polymarket used for
     each affected market and date before using API-hourly maxima/minima as
     settlement or learning authority. If the website daily summary differs,
     legacy rows must be cohort-tagged or rebuilt offline, not silently reused.
   - Taipei: build per-date source-role evidence across the historical
     transition window before any transfer, calibration, or settlement-learning
     rows can become promotion-grade.
   - DST historical aggregates: certify rebuild from zone-aware timestamps for
     affected DST cities before promotion-grade Day0/diurnal calibration claims.
     Runtime clock correctness alone is insufficient proof that historical
     materializations are clean.
5. **RED direct side-effect remains operator-go scope.** RED/force-exit now marks
   active positions for sweep and can emit durable CANCEL proxy commands before
   normal command recovery. It still deliberately does not call venue
   `cancel_order()` / submit sells directly inside `_execute_force_exit_sweep`;
   that belongs to a live-side-effect packet with dry-run and rollback evidence.
6. **Do not treat deep architecture gaps as cleanup.** Harvester Stage-2
   canonical learning is architecture work. Wave38 closed the low-risk
   `hourly_observations` runtime compatibility cleanup at source/schema/test
   level; physical DB-file deletion remains data-layer work and is tracked
   separately below. Wave39 verified malformed `solar_daily` rootpage degrade
   behavior without attempting physical DB repair.

### Money-path coverage verdict

| Money path segment | Current verdict | Primary blockers |
|---|---|---|
| Contract semantics | PARTIAL | Paris is closed; HK/WU/Taipei/DST source semantics still need fresh current-fact evidence |
| Source truth | PARTIAL | current source validity must be refreshed before live claims; HK and Taipei remain caution paths |
| Forecast signal | PARTIAL | local finite/vector gates are closed; live trading still needs current source/data evidence and promotion-grade calibration evidence |
| Calibration | BLOCKED | TIGGE cycle asymmetry and calibration promotion evidence remain live-lock blockers |
| Edge construction | PARTIAL | local gate repairs are closed; live economics still require staged P&L after fees/slippage/fill drag |
| Execution intent | PARTIAL | entry and held-exit executable snapshot paths are locally wired/fail-closed; live evidence still needs staged scan -> snapshot -> command insertion |
| Venue submission | PARTIAL | normal live path is bound to U1/final SDK submission provenance; legacy compatibility helpers are not deploy evidence |
| Risk/control | PARTIAL | RED/ORANGE local behavior is repaired; direct venue cancel/sweep from RED itself is a separate operator-go SLA |
| Fill/holding | PARTIAL | command finality and optimistic-vs-confirmed drift journal separation are locally repaired; live proof still belongs to staged evidence |
| Monitoring/exit | PARTIAL | D4 live-execution invariant closed by Wave31; residual strategy-quality work is stronger exit evidence. Whale-toxicity remains orderbook-adjacent pressure, not true all-market print-sweep detection |
| Settlement/learning | PARTIAL | harvester lineage repairs are local; live harvester flag and production writes remain operator-gated |
| Observability | PARTIAL | readiness projections are non-authority; staged-live-smoke evidence is still missing |

### [OPEN P1] RED direct venue side-effect SLA remains outside `cycle_runner`

**Location:** `src/engine/cycle_runner.py::_execute_force_exit_sweep`,
`src/execution/command_recovery.py`, `tests/test_riskguard_red_durable_cmd.py`.
**Problem:** Effective RED now triggers the local sweep path, but the cycle
remains side-effect-free: it records durable cancel/sweep intent and relies on
the normal command/execution seams. It does not itself call venue
`cancel_order()` or submit sell orders in the RED cycle.
**Impact:** If operator law requires bounded immediate venue side effects from
RED itself, Zeus can look locally compliant while pending orders or active
exposure still wait for downstream execution workers.
**Repair boundary:** This is not a docs-only repair. Direct venue mutation needs
explicit operator-go, dry-run evidence, rollback plan, and a fake-venue
relationship test proving RED produces exactly one cancel/sell action or a
visible `RED_SWEEP_BLOCKED` state per eligible exposure.

### Current small-blocker repair plan

1. **Investigate before code:** use bounded read-only slices for source truth,
   calibration/rebuild, execution/risk, and low-priority cleanup. Do not touch
   `climate_zone` in this wave.
2. **Patch only non-live-mutating blockers first:** Wave31 closed the D4
   hard-gate live-execution invariant. Waves41-42 narrowed D3 execution-cost
   continuity at the reconcile and harvester seams. RED direct venue side
   effects are not eligible without operator-go for live-side-effect work.
3. **Verification per blocker:** each repair needs a relationship test or static
   semantic check at the boundary it claims to protect. Function-local tests are
   insufficient for these gaps.
4. **Downstream sweep:** after each repair, search monitor, exit, settlement,
   replay, report/export, learning/calibration, legacy/fallback paths for the
   old meaning before marking the gap closed.
5. **Do not use readiness green as deploy proof:** `live_readiness_check.py`
   gates are necessary but not sufficient; staged-live-smoke evidence and
   operator live-money deploy-go remain separate.

### Websearch policy for this audit family

Use websearch only for current external facts that can change outside the repo:
Gamma active markets/resolutionSource, Polymarket CLOB/WS semantics, fee/order
rules, WU/HKO endpoint behavior, and current provider availability. Do not use
websearch to override canonical DB truth, local architecture law, or historical
packet evidence without recording the conflict and treating it as a new audit
finding.

---

## ANTI-RABBIT-HOLE: upstream-Polymarket scope limits (READ FIRST)

No active remediation items remain in this section. The Polymarket LOW market
series structural boundary is archived in `docs/to-do-list/known_gaps_archive.md`.

---

## CRITICAL: DST / Timezone

### [OPEN — NOT LIVE-CERTIFIED] Historical diurnal aggregates still need DST-safe rebuild cleanup
**Certification status:** This gap blocks live math certification. The DST historical rebuild has NOT been executed and historical data derived from pre-fix aggregates is NOT certified for promotion. See `architecture/data_rebuild_topology.yaml` → `dst_historical_rebuild`.
**Location:** `scripts/etl_diurnal_curves.py`, `src/signal/diurnal.py`
**Problem:** The old London 2025-03-30 hour=1 evidence is stale. ETL/runtime is now partially DST-aware, but historical `diurnal_curves` materializations may still need to be rebuilt from true zone-aware local timestamps.
**Runtime mismatch:** `get_current_local_hour()` in `diurnal.py` already uses `ZoneInfo` and is DST-aware. The remaining risk is stale pre-fix aggregates/backfill, not the runtime clock itself.
**Impact:** Day0 `diurnal_peak_confidence` can still drift if old diurnal materializations remain in circulation. NYC (EDT/EST), Chicago (CDT/CST), London (BST/GMT), Paris (CEST/CET) should be revalidated after rebuild; Tokyo, Seoul, Shanghai remain safe (no DST).
**Proposed antibody:**
1. Verify every remaining ETL/backfill path derives local-hour inputs from zone-aware local timestamps.
2. Rebuild historical `diurnal_curves` materializations from the corrected path.
3. Keep `test_diurnal_curves_hour_is_dst_aware` (or equivalent) to guard spring-forward/fall-back behavior.
**Cities affected:** DST cities only until the historical rebuild is proven clean.

---

## CRITICAL: Instrument Model

All entries antibody-closed (Bin.unit / SettlementSemantics.for_city / Platt
bin-width-aware / astype(int) → SettlementSemantics.round_values, etc.). See
`docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Instrument Model".

---

## CRITICAL: Exit/Entry Epistemic Asymmetry

Instrument-level antibodies all closed (MC count parity / CI-aware exit /
hours_since_open / MODEL_DIVERGENCE_PANIC threshold). See
`docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Exit/Entry Epistemic Asymmetry".

The structural relationship gap is now **PARTIAL** as **D4** under
"MEDIUM-CRITICAL: Cross-Layer Epistemic Fragmentation" below: live execution
fails closed, but stronger statistical exit evidence remains future work.

---

## CRITICAL: Day0 Signal Quality

All entries antibody-closed (continuous observation_weight / continuous
post-peak sigma decay). See `docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Day0 Signal
Quality".

---

## MEDIUM: Data Confidence

### [OPEN — WORKSPACE-BOUNDARY] Open-Meteo quota/cooldown is process-local, not workspace-authoritative
**Location:** `src/data/openmeteo_quota.py`,
`src/data/openmeteo_client.py`, `src/data/ensemble_client.py`,
`src/data/observation_client.py`, `src/data/source_health_probe.py`;
external co-tenants such as `51 source data` / Rainstorm-era ingestion loops
remain outside this repository.
**Problem (filed 2026-04-03, refreshed 2026-05-08):** Open-Meteo quota and
429 cooldown are real provider/account/IP/workspace constraints, but Zeus
currently models them as process-local state. `OpenMeteoQuotaTracker` keeps
`_count` and `_blocked_until` in memory per Python process. Core Zeus fetchers
consult that tracker, but another daemon/process/workspace can spend the same
provider quota without updating this process, and `source_health_probe` performs
a direct `httpx.get()` archive probe outside the shared tracker path.
**Current evidence:** `openmeteo_client.fetch()` checks
`quota_tracker.can_call()`, records successful calls, and engages cooldown on
HTTP 429. `ensemble_client` and `observation_client` also use
`quota_tracker`. `tests/test_runtime_guards.py` covers warning, hard block,
reset, and 429 cooldown behavior for one tracker instance. No current repo
evidence proves a persisted/shared quota ledger across Zeus processes or
non-Zeus co-tenants.
**Object-meaning failure:** downstream trading/source-health consumers may
treat "Open-Meteo quota available" as a Zeus-local boolean, while the real
object is a shared external quota/cooldown interval. That changes source
authority and time-validity meaning across process/workspace boundaries.
**Current live-money posture:** not a direct order/execution bug. It can still
corrupt source-health diagnosis or cause unnecessary degraded/fallback states if
co-tenants burn quota or trigger a provider cooldown that Zeus did not observe.
**Required antibody:** a workspace-authoritative quota/cooldown ledger or lease
with UTC-day reset, endpoint class, retry-after/cooldown interval, and atomic
cross-process updates. `source_health_probe` must either consume the shared
client/ledger or run under an explicit small probe budget. Relationship tests
must prove two independent tracker instances share count/cooldown state and
that health probing cannot bypass the ledger.
**Repair boundary:** do not implement this as an in-memory tweak. A real fix
needs a small runtime-governance design for where the shared ledger lives
(`state/` DB vs lockfile vs daemon-local service), crash recovery semantics,
and whether non-Zeus co-tenants can or must participate. No live external
traffic or provider-load test is authorized by this gap entry.

(2 FIXED entries on persistence_anomaly + 2 CLOSED 2026-04-15 entries on
alpha_overrides / harvester bias correction archived to
`docs/to-do-list/known_gaps_archive.md` → "MEDIUM: Data Confidence".)

---

## CRITICAL: Settlement Source Mismatch (2026-04-16 smoke test)

HKO rounding/containment is closed in code and archived in
`docs/to-do-list/known_gaps_archive.md`. The remaining HK issue is source/audit
authority for specific historical dates, not rounding arithmetic.

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
  → exit_lifecycle.execute_exit(portfolio, pos, reason, price, legacy_env, clob)
    → legacy diagnostic: close_position() directly
    → live: place_sell_order() → check fill → retry/backoff
```

### Key Cross-Module Relationships

Historical closed relationships are in `known_gaps_archive.md`. Active
cross-module relationship work remaining in this file is limited to:

1. D3 typed execution-cost meaning surviving evaluator -> Kelly -> fill/exit/
   replay/report boundaries.
2. D4 stronger exit evidence construction after Wave31 hard gate.
3. Source/settlement current-fact boundaries for HK, WU daily-summary, Taipei,
   and DST historical rebuild certification.

---

## Tooling / Operator Health

All current closed entries for strategy_tracker JSON authority, Healthcheck
assumptions, Day0 stale probability waiver, and malformed `solar_daily`
rootpage degrade behavior are archived in
`docs/to-do-list/known_gaps_archive.md` → "Tooling / Operator Health".

---

## 2026-04-03 — edge-reversal follow-up triage

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

ACP router fallback-chain work is closed for Zeus scope and archived in
`docs/to-do-list/known_gaps_archive.md`; no `src/` or `tests/` Zeus consumer was
found in the 2026-05-08 realignment.

(5 FIXED entries on settlement CI guard / buy-yes proxy / settlement won
ambiguity / control-plane gate drift / LA Gamma Milan / Heartbeat cron RED
suppression archived to `docs/to-do-list/known_gaps_archive.md` → "2026-04-03 —
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
**Mitigation deployed (2026-04-13; DSA-09 cleanup 2026-04-29):** `evaluator.py` wraps entry price as `ExecutionPrice`, queries token-specific CLOB fee rate when available, and computes `polymarket_fee(p) = fee_rate × p × (1-p)` before Kelly. The fee-adjusted path is now unconditional; the stale `EXECUTION_PRICE_SHADOW` rollback flag was removed from `settings.json` after the shadow-off branch was deleted.
**Mitigation deployed (2026-05-08 Wave32):** `src/state/db.py` and `src/state/portfolio.py` now preserve venue-confirmed full-open fill cost above `position_current` projection/target cost while keeping partial-exit open slices reduced by remaining share ratio.
**Mitigation deployed (2026-05-08 Wave41):** `src/execution/exchange_reconcile.py`
no longer treats a generic exchange `price` field as realized fill authority
when appending missing linkable trade facts. Only explicit fill-price fields
(`avgPrice`, `avg_price`, `fillPrice`, `fill_price`) can populate
`venue_trade_facts.fill_price`; confirmed/matched/mined trades with only
generic `price` now produce an `exchange_trade_missing_fill_economics` finding
and do not trigger fill-finality events.
**Mitigation deployed (2026-05-08 Wave42):** `src/execution/harvester.py`
now rejects settlement P&L economics when a position carries corrected
executable economics markers but lacks fill-derived authority. Legacy fallback
remains available only for unclassified legacy rows; corrected rows fail closed
instead of reusing ambiguous `size_usd / entry_price` economics.
**Remaining antibody:** Carry typed execution cost beyond evaluator, and connect
market-specific tick size, neg-risk, realized fill/slippage reconciliation, and
persisted fee/liquidity-role authority through read-model, replay, report, and
learning consumers.

### [PARTIAL] D4 — Entry-exit epistemic asymmetry (LIVE-EXECUTION MITIGATED)
**Location:** `src/engine/evaluator.py` (entry), `src/execution/exit_triggers.py` (exit)
**Problem:** Entry requires BH FDR α=0.10 + bootstrap CI + `ci_lower > 0` — high statistical burden. Legacy statistical exit triggers used only 2-cycle confirmation — low statistical burden.
**Mitigation deployed (2026-05-08 Wave31):** `src/engine/cycle_runtime.py` now gates `EDGE_REVERSAL`, `BUY_NO_EDGE_EXIT`, and `BUY_NO_NEAR_EXIT` before `MonitorResult`, `build_exit_intent()`, or `execute_exit()`. Missing entry evidence or weaker exit evidence degrades to hold/review; non-statistical force-majeure exits are not blocked by D4.
**Residual antibody:** Build an exit-side evidence contract with external-reality-valid statistical authority so ordinary statistical exits can proceed on evidence that is symmetric with entry, instead of relying on the fail-closed hold/review path.

(D5 / D6 / Day0-canonical-event closed entries archived to
`docs/to-do-list/known_gaps_archive.md` → "MEDIUM-CRITICAL: Cross-Layer Epistemic
Fragmentation (D1–D6)".)

---

## Calibration improvement backlog (recovered from session 59195a96)

Items explicitly queued as future/open in session 59195a96, not in prior gap register. Non-blocking to live trading in current state. Source: `.claude/tasks/59195a96-*/3.json`, `4.json`, `6.json`, `7.json`, `8.json`.

### [DEFERRED — FUTURE] s3 — climate_zone field missing from config/cities.json

**Authority:** LAW 6 in `docs/reference/zeus_calibration_weighting_authority.md`.
**Problem:** Current city config still lacks `climate_zone`. This is future
calibration taxonomy work, not part of the 2026-05-08 small-blocker wave.
**Next step:** Propose mapping for all current cities → operator review before
writing to config. Do not write the field without operator sign-off on the
taxonomy.
**Blocks:** s6 (PoC v6 cluster-level α tuning).

### [PARTIAL — DATA-LAYER BLOCKED] s4 — Calibration weighting LAW antibodies

**Authority:** `docs/reference/zeus_calibration_weighting_authority.md`.
**Problem:** The original backlog framed this as "add 11 tests", but Wave37
found two distinct object classes: safe code/config/static antibodies that can
be enforced now, and row-level calibration-weight semantics that require schema,
ingest, rebuild, refit, historical-row cohorting, and promotion-policy work.

**Wave37 safe-subset antibodies deployed:** `tests/test_calibration_weighting_laws.py`
now protects:

- `test_per_city_weighting_eligibility`: every `config/cities.json` city has
  explicit `weighted_low_calibration_eligible`; PoC-v5 LAW2 opt-out cities are
  false (`Jakarta`, `Busan`, `Hong Kong`, `NYC`, `Houston`, `Chicago`,
  `Guangzhou`, `Beijing`) and all others true.
- `test_no_temp_delta_magnitude_weighting_in_production`: targeted production
  calibration/strategy sources must not introduce LAW3-disallowed
  temperature-delta magnitude weighting.
- `test_rebuild_n_mc_default_bounded`: offline calibration-pair rebuild default
  is `calibration_batch_rebuild_n_mc() == 1000`, separate from runtime
  `ensemble_n_mc() == 10000`; explicit CLI override remains allowed.
- `test_rebuild_uses_per_city_metric_savepoint_not_outer_monolith`: rebuild
  code must keep per-city/metric savepoints and must not restore an outer
  monolithic rebuild savepoint.
- `test_no_per_city_alpha_tuning_in_production`: targeted production
  calibration/strategy sources must not introduce per-city alpha tuning.

Existing antibodies also cover the runtime side of LAW4:
`tests/test_runtime_n_mc_floor.py` and `tests/test_evaluator_explicit_n_mc.py`
pin runtime MC floor and explicit evaluator/monitor n_mc threading.

**Still open — data-layer packet required:** do not attempt to green-test these
without an approved schema/data plan:

- `test_calibration_weight_continuity`: current `ensemble_snapshots_v2` and
  `calibration_pairs_v2` still use binary `training_allowed`; there is no
  persisted `precision_weight REAL CHECK (0 <= precision_weight <= 1)`.
- `test_weight_floor_nonzero_for_ambig_only`: current LOW ingest can still
  collapse `boundary_ambiguous=True` to `training_allowed=False`; the future
  fix must compute a continuous floor (`WEIGHT_FLOOR = 0.05`) when causality,
  horizon, and member completeness are satisfied.
- `test_high_track_unaffected_by_low_law`: cannot be asserted as
  `precision_weight=1` for HIGH until the precision-weight field exists and
  HIGH rows are explicitly cohort-tagged under that schema.

**Required future packet:** add `precision_weight` to the snapshot/pair training
authority path; define compatibility semantics for legacy `training_allowed`
rows; update `snapshot_ingest_contract`, rebuild selection, Platt fitting
sample weights, refit/promotion gates, replay/report/learning cohorting, and
data-quality assertions; run only against an isolated DB with dry-run row
counts/checksums and a rollback/non-promotion plan. No canonical DB migration,
backfill, refit, promotion, relabel, or learning/report promotion is authorized
by the Wave37 safe subset.

**Deferred by operator scope:** `test_climate_zone_present` and
`test_cluster_alpha_map_finite` remain under s3/s6. The user explicitly kept
`climate_zone` as future taxonomy work; do not add it without operator review.

### [OPEN — blocked by s3] s6 — PoC v6 cluster-level α tuning

**Location:** `_poc_weighted_platt_2026-04-28/poc_v6_cluster_alpha.py` (to be created).
**Problem:** Current Platt calibration uses a single global α. Climate-zone partitioning may improve aggregate Brier score and reduce per-zone miscalibration.
**Scope:** 4-zone α grid search using `climate_zone` partition from s3. Compare aggregate Brier vs B_uniform baseline + per-zone Brier. Run on rebuilt `calibration_pairs_v2`.
**Blocked by:** s3 (climate_zone field in config/cities.json).

### [DEFERRED] s7 — Re-rebuild calibration_pairs_v2 at n_mc=10000

**Problem:** Current `calibration_pairs_v2` was built with `n_mc=5000` (training time budget) but `p_raw_vector_from_maxes` at runtime uses `n_mc=10000`. This creates a ~10⁻³σ Platt fit asymmetry. Undetectable in practice but technically impure: the Platt model was fitted on a slightly different distribution than the one it scores at runtime.
**2026-05-08 assessment:** This remains a future purity/certification rebuild,
not an immediate repair triggered by the T1E sentinel patch. The new sentinel
gate changes promotion authority: a future rebuild/refit or OOS transfer
promotion must produce and consume a complete `calibration_pairs_v2_rebuild_complete`
sentinel, but the absence of that sentinel does not silently relabel existing
active Platt rows as mathematically wrong. Wave37 separated the ordinary batch
rebuild default from runtime precision: default rebuilds now use n_mc=1000 per
LAW4, while this future symmetry rebuild must pass explicit `--n-mc 10000` and
downstream `--rebuild-n-mc 10000` evidence if the operator chooses to certify
runtime-training MC symmetry.
**Cost:** ~32 hours at n_mc=10000 with current Python loop. Feasible only after s8 (vectorize MC loop, 10-100× speedup).
**Prerequisite:** s8 must land first to make the cost feasible.
**Defer until:** live deployment is stable and s8 is complete.

### [DEFERRED] s8 — Vectorize p_raw_vector_from_maxes MC loop

**Location:** `src/signal/ensemble_signal.py:215`
**Problem:** `p_raw_vector_from_maxes` uses a Python `for _ in range(n_mc)` loop. At n_mc=10000 and 416K snapshots, a full calibration_pairs_v2 rebuild takes ~32 hours. Vectorizing to `(n_mc, n_members)` numpy broadcast would give 10-100× speedup.
**Required antibody:** Equivalence test (bit-precise result match vs current loop output) + p_raw_vector regression suite before deploying the vectorized version.
**Not deployment-blocking.** Unblocks s7.

---

## Deferred items (from open_work_items.md, consolidated 2026-05-01)

### [DEFERRED] Two-System Independence Phase 4

Revisit due ~2026-06-26 (8 weeks from Phase 3 completion 2026-05-01).
Divergent strategy restart-policy needs may require a process split — escalate
to architect when the window opens.

### [LOW] Backfill script unification

18 `scripts/backfill_*.py` scripts remain ad-hoc. No live trading impact.
Scope a unification package when convenient.

### [OPEN — DATA-LAYER APPROVAL REQUIRED] Physical `hourly_observations` residue may remain in existing DB files

**Status:** OPEN only for physical canonical-DB cleanup. Wave38 removes future
constructibility and all repo-level scheduler/writer/schema/view paths, but it
does not mutate existing `state/zeus-world.db` files.
**Location:** Existing SQLite files may still contain
`hourly_observations` and/or `v_evidence_hourly_observations` if they were
initialized before Wave38. Source constructibility was removed from
`src/state/db.py`; `scripts/etl_hourly_observations.py` was deleted; linter/tests
now block reintroduction.
**Why this remains:** Dropping live/world DB tables or views is a destructive
data-layer operation and requires explicit operator approval, DB inventory,
dry-run SQL, backup path, rollback path, and proof no open evidence/audit packet
needs the historical rows. This wave intentionally did not run `DROP TABLE`,
`DROP VIEW`, migrations, backfills, or canonical DB writes.
**Current live-money impact:** No known live trading, calibration, monitor,
exit, settlement, replay, or learning path can create or consume the legacy
object after Wave38. Residual risk is artifact confusion if an operator or
future tool manually inspects an old DB file and treats the legacy table/view as
current hourly truth.
**Required future packet:** `OPERATOR_DECISION_REQUIRED` data-cleanup packet
with:
1. inventory query over every target DB path, read-only first;
2. dependent packet/evidence audit for any remaining manual references;
3. dry-run `DROP VIEW IF EXISTS v_evidence_hourly_observations; DROP TABLE IF EXISTS hourly_observations;`;
4. backup/rollback procedure and post-migration schema assertions;
5. no promotion of legacy rows into `observation_instants_v2`, reports, replay,
   calibration, or learning authority.

---

### [OPEN — K1 FOLLOWUPS DEFERRED] K1-broken hardcoded paths in operator scripts

**Status:** OPEN — deferred from K1 P3 followups (2026-05-14) per PLAN §4.5.
**Authority:** docs/operations/task_2026-05-14_k1_followups/PLAN.md §4.5
**Affected files:**
- `scripts/healthcheck.py` — K1-broken: uses hardcoded world.db path for tables that moved to forecasts.db
- `scripts/verify_truth_surfaces.py` — K1-broken: world.db path for forecast-class tables
- `scripts/venus_sensing_report.py` — K1-broken: world.db path for forecast-class tables
**Why deferred:** Status quo is already broken today (pre-P3); bundling the
fixes triples P3 scope without adding new live-money risk. These are operator
diagnostic scripts, not runtime daemon paths.
**Required fix:** Update each script to open `get_forecasts_connection()` for
observations/forecast-class table queries, and `get_world_connection()` for
world-class queries. Follow the same pattern as `hole_scanner.py:566-580`
(the P3 fix committed 2026-05-14).
**Live-money impact:** LOW — these are read-only diagnostic scripts; they
produce wrong/empty output but do not affect trading, risk, or settlement.
