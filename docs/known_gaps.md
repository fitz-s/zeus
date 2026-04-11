# Known Gaps — Venus Evolution Worklist

每个 gap 是一个 belief-reality mismatch。每个 gap 的终态：变成 antibody（test/type/code）→ FIXED。
如果一个 gap 包含 "proposed antibody"，下一步就是实现它。

---

## CRITICAL: DST / Timezone

### [OPEN] Historical diurnal aggregates still need DST-safe rebuild cleanup
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

### [FIXED] astype(int) hardcoded in bootstrap/day0/core rounding paths → SettlementSemantics.round_values() (2026-03-31)
**Location:** `src/strategy/market_analysis.py:146,193` + `src/signal/day0_signal.py:73,82`
**Problem:** `np.round(noised).astype(int)` 强制整数四舍五入。如果 °C 城市的 settlement precision 是 0.1°C，所有概率计算都是错的。即使当前 °C 也是 integer settlement，这个 hardcode 意味着如果 Polymarket 改 precision，系统不会自动适应。
**Impact:** 5 个 °C 城市（London, Paris, Seoul, Shanghai, Tokyo）的 bootstrap CI 和 Day0 signal。
**Antibody deployed:** settlement rounding now lives in `SettlementSemantics.round_values()`, and core paths use semantics-aware rounding instead of integer coercion shortcuts. `test_no_hardcoded_integer_rounding_for_celsius` now passes.

### [FIXED] BOUNDARY_WINDOW=0.5 hardcoded → unit-aware (2026-03-31, Sonnet)
**Location:** `src/signal/ensemble_signal.py:44`
**Problem:** `boundary_sensitivity()` 用 ±0.5° 窗口衡量 "多少 members 在 bin 边界附近"。对 °F integer bins 正确（半个 degree），对 °C 可能 scale 不对。
**Impact:** Boundary sensitivity 影响 edge 计算的 confidence。
**Proposed antibody:** `BOUNDARY_WINDOW = 0.5 if unit == "F" else 0.28`（和 instrument noise sigma 同 scale）

### [FIXED] Platt calibration is bin-width-aware (2026-03-31)
**Location:** `src/calibration/platt.py`
**Problem:** Platt 的 A/B/C 参数对 5°F bin 和 1°C point bin 使用相同系数。但 p_raw 的 scale 不同：5°F bin 的 p_raw ≈ 5× 1°C bin 的 p_raw（更多 members 落入更宽的 bin）。
**Impact:** °C 城市的 Platt calibration 可能 miscalibrated。
**Context:** 实际 Polymarket bin 结构（从 zeus.db 验证 2026-03-31）：°F = 2°F range bins（40-41, 42-43, ...），°C = 1°C point bins（10°C, 11°C, ...）。所以 p_raw scale 差异是 2:1 不是 5:1。Platt 的 A 参数会部分补偿，但如果 training data 混合了 °F 和 °C（calibration bucket = cluster×season，cluster 混合 °F 和 °C 城市），calibration 有问题。
**Antibody deployed:** Platt fit/predict now consume width-aware normalized inputs through the calibration chain; replay, monitor, bootstrap, and refit paths were updated to use the same input space.

### [FIXED] Bin had no unit field → `Bin.unit` added (2026-03-31)
### [FIXED] Bin had no width property → `Bin.width` + `Bin.settlement_values` added (2026-03-31)
### [FIXED] °C cities got °F SettlementSemantics → `SettlementSemantics.for_city()` (2026-03-31)
### [FIXED] Bin.unit not propagated to creation sites → evaluator + monitor_refresh updated (2026-03-31)

---

## CRITICAL: Exit/Entry Epistemic Asymmetry

### [FIXED] MC count: monitor=1000, entry=5000 → both 5000 (2026-03-31)

### [FIXED] Exit uses CI-aware conservative edge instead of raw point estimate (2026-03-31)
**Location:** `src/execution/exit_triggers.py` — `_evaluate_buy_yes_exit()`, `_evaluate_buy_no_exit()`
**Problem:** Exit 判断 `forward_edge < threshold`。但 forward_edge 是 point estimate。Entry 用 bootstrap CI 量化 edge uncertainty。Exit 应该用 `ci_lower_of_forward_edge < threshold`——只在 edge 统计显著地负时才触发 exit。
**Impact:** Near-threshold positions 因 MC noise 产生 false EDGE_REVERSAL，每次 burn spread $0.30-0.50。
**Antibody deployed:** monitor path now emits coherent confidence bands around current forward edge, and exit paths use a conservative lower-bound evidence edge for reversal logic. `test_exit_uses_ci_not_raw_edge` now passes.

### [FIXED] hours_since_open hardcoded to 48.0 → computed from position.entered_at (2026-03-31, Sonnet)
**Location:** `src/engine/monitor_refresh.py` — `_refresh_ens_member_counting()` 和 `_refresh_day0_observation()` 都传 `hours_since_open=48.0` 给 `compute_alpha()`
**Problem:** Alpha 不随实际持仓时间衰减。一个持仓 2 小时的 position 和持仓 48 小时的 position 拿到相同的 alpha 权重。
**Proposed antibody:** 从 `position.entered_at` 计算实际 hours since open。传给 `compute_alpha()`。

### [FIXED] MODEL_DIVERGENCE_PANIC threshold → two-tier 0.20/0.30 (2026-04-06, math audit)
**Location:** `src/execution/exit_triggers.py` — divergence score threshold
**Evidence (Fitz, 2026-03-31):**
- LA 64-65°F buy_no: exit NO=0.780 → 实时NO=0.825 (+0.045，方向有利但退出后继续涨) → **退早了**
- LA 66-67°F buy_no: exit NO=0.655 → 实时NO=0.705 (+0.050，方向有利但退出后继续涨) → **退早了**
- SF 64-65°F buy_no: exit NO=0.750 → 实时NO=0.765 (+0.015，基本持平，合理)
- CHI ≥48°F buy_no: exit NO=0.0115 → 市场YES=98.9%，模型可能本来就在错误方向
**Problem:** 3/4 笔 divergence exit 退出后市场向模型预测方向移动——说明是模型超前于市场，不是真正的 panic。0.15 threshold 触发了 false exit，把本来可以持有到结算赚钱的仓位提前震出。
**Impact:** LA 两笔各亏损约 $0.045-0.050 未实现盈利。如果持有到结算且市场继续向有利方向走，可以多赚。
**Fix:** divergence threshold 应提高到 0.25-0.30，或改为：只在 divergence score 超过 threshold **且** market direction 持续3个周期都向不利方向移动时才触发。
**Verification needed:** 等 Apr 1/2 市场结算后对比 divergence exits 和实际结算结果。

---

## CRITICAL: Day0 Signal Quality

### [FIXED] Day0 连续衰减函数 — observation_weight() implemented (2026-03-31, Sonnet)
**Location:** `src/signal/day0_signal.py` — `obs_dominates()` 在 80% threshold 处 binary switch
**Problem:** Day0（<6h to settlement）应该逐渐从 ENS-dominated 过渡到 observation-dominated。当前：要么 100% ENS weight，要么 `obs_dominates()` at 80% 翻转。没有中间态。
**正确设计（from spec，未实现）：** `weight_obs = f(hours_since_sunrise, diurnal_peak_confidence, n_observations)`——连续函数，从 0（完全 ENS）到 1（完全 observation）平滑过渡。
**关键 domain knowledge：**
- 温度日变化遵循 diurnal cycle：日出后升温，14:00-16:00 local 达到 peak，之后降温
- 如果当前已经过了 diurnal peak（`diurnal_peak_confidence > 0.7`），观测到的 high 就是 "almost final"
- 如果还没过 peak，ENS forecast for remaining hours 仍然重要
- 极端 diurnal pattern（傍晚温度 < 凌晨）可能导致 WU 在 peak 后重新调整 high
**Impact:** 所有 Day0 交易的 signal quality。Day0 应该是 highest-confidence strategy（observation = fact），但 binary switch 让它 suboptimal。
**Test:** `test_day0_observation_weight_increases_monotonically` now passes.

### [FIXED] Day0 post-peak sigma → continuous decay base*(1-peak_confidence*0.5) (2026-03-31, Sonnet)
**Location:** `src/signal/day0_signal.py` — `if diurnal_peak_confidence > 0.7: sigma /= 2`
**Problem:** Instrument noise sigma 在 peak confidence 0.7 处突然减半。应该是连续的：`sigma = base_sigma * (1 - peak_confidence * decay_factor)`
**Proposed antibody:** 连续 sigma 衰减函数，不需要 threshold。

---

## MEDIUM: Data Confidence

### [FIXED] persistence_anomaly — threshold 10→30, confidence scaling 10-30%, 3-day window (2026-03-31, Sonnet)
**Location:** `src/engine/monitor_refresh.py` — `_check_persistence_anomaly()`
**Problem:** 30% alpha discount 建立在 n=10 样本上。Statistical rule of thumb：frequency estimate 需要 expected count in category > 5。对 frequency=0.05，n=100 给 expected count=5。n=10 给 expected count=0.5。
**Context:** `temp_persistence` 表只有 552 行，按 city×season×delta_bucket 分桶。每个桶的实际样本量可能是个位数。
**Proposed antibody:** 提高 minimum n_samples 到 30。或：discount 大小应该 scale with confidence（n=10 → discount 10%, n=50 → discount 30%）。
**Test:** `test_persistence_discount_requires_adequate_samples` (currently XFAIL)

### [FIXED] persistence_anomaly 只看昨天 → 3-day average window (2026-03-31, Sonnet)
**Location:** 同上
**Problem:** 只查 `target_date - 1 day` 的 settlement。如果连续多天都有异常温度变化，只看一天不够。
**Proposed antibody:** 查最近 3 天的 settlements，取 average delta。

### [OPEN] alpha_overrides 只有伦敦验证为盈利
**Context:** 数学验证显示只有 London 的 alpha override 预期盈利。其他城市的 override 可能 negative EV。
**Proposed antibody:** 需要 per-city alpha override validation。如果验证不通过，disable override for that city。

### [OPEN] Harvester 不知道 bias correction 是否开启
**Location:** `src/execution/harvester.py` — `harvest_settlement()` 生成 calibration pairs
**Problem:** Harvester 生产的 calibration pairs 的 p_raw 可能和 live signal 不一致（如果 live 开了 bias correction 但 harvester 没有）。
**Proposed antibody:** Harvester 生成 pairs 时应该使用和 evaluator 相同的 bias correction 设置。或在 calibration_pairs 里记录 `bias_corrected: bool`。

### [STALE-UNVERIFIED] Open-Meteo quota contention is workspace-wide, not Zeus-only
**Location:** Zeus + `51 source data` + Rainstorm-era ingestion loops
**Problem (filed 2026-04-03):** Workspace has shared data agents that can cause `429 Too Many Requests` on Open-Meteo, causing Zeus to misdiagnose quota issues.
**Status (2026-04-06):** All recent Open-Meteo API calls in the log show `HTTP/1.1 200 OK` with no 429 errors. Harvester ran successfully (`settlements_found=141`) but created 0 pairs — the failure mode appears to be Stage-2 bootstrap, not quota exhaustion. This gap may be less active than initially feared.
**Proposed antibody:** 建立 workspace-wide quota coordination：至少要有共享计数 / cooldown / update watermark，或者明确调度隔离，让 Zeus 的交易路径优先于后台数据 agent。

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

### [FIXED] Healthcheck assumptions validation now succeeds in the active Python env (2026-04-03)
**Location:** `zeus/scripts/healthcheck.py`
**Problem:** Running healthcheck under `/opt/homebrew/bin/python3` previously depended on `numpy` being present; in this session the selected Python env now has numpy available and healthcheck completes with `assumptions_valid: true`.
**Impact:** Heartbeat classification is no longer blocked by a missing-numpy validation path in the active env.
**Antibody deployed:** Verified `python3` resolves to `/opt/homebrew/bin/python3` and `import numpy` succeeds (`2.4.2`), so the healthcheck assumptions gate now passes in the current runtime.

### [STALE-UNVERIFIED] CycleRunner fails on malformed `solar_daily` schema rootpage
**Location:** `zeus/state/zeus.db` / the day0 capture path that reads `solar_daily`
**Problem (filed 2026-04-02):** The paper cycle failed with `malformed database schema (solar_daily) - invalid rootpage`. The monitor path was reading a broken SQLite object and the cycle aborted instead of degrading cleanly.
**Status (2026-04-06):** The latest `opening_hunt` cycles completed without this error appearing in the log. Not confirmed fixed — may have been intermittent or masked by a different cycle mode. Requires a deliberate `day0_capture` run to verify.
**Proposed antibody:** Add an explicit schema/integrity check before day0 capture and fail closed with a structured error (plus a repair/migration path) instead of letting SQLite rootpage corruption surface mid-cycle.

### [STALE-UNVERIFIED] Exit authority still runs with incomplete monitor context on some day0 positions
**Location:** `src/engine/cycle_runner.py`, `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`
**Problem:** Current cycle logs show `INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)` for several day0 positions. The cycle continues, but exit authority is evaluating with partially missing freshness context.
**Live evidence (2026-04-06):** 4 positions (`dab0ddb6-e7f`, `e6f0d01d-2a3`, `19a7116d-36c`, `511c16a6-27d`) repeatedly triggered this warning in the 14:30 and 15:00 cycles.
**Live evidence (2026-04-09):** 3 positions (`52280711-260`, `b33ff595-3cb`, `c25e2bfe-769`) still triggering `INCOMPLETE_EXIT_CONTEXT` in day0_capture cycle. Still not confirmed fixed.
**Proposed antibody:** Make `fresh_prob_is_fresh` a required field for exit evaluation; if it is missing, mark the position as `exit_context_incomplete` and skip exit authority until the next full monitor refresh.

### [OPEN] strategy_tracker can report profit that is not reconstructible from durable DB truth
**Location:** `src/state/strategy_tracker.py`, `zeus/state/strategy_tracker-paper.json`, `zeus/state/positions-paper.json`, `zeus/state/zeus.db`
**Problem:** `strategy_tracker-paper.json` currently reports `opening_inertia` cumulative PnL of `+247.83`, but the authoritative current-regime cash ledger in `positions-paper.json` only reflects `opening_inertia` realized PnL of `-2.21`. Several large positive `opening_inertia` trades in the tracker (for example `f4e0d2a6-b8a`, `b2086cca-a1a`, `836270b8-2cc`, `8d9071fa-fab`, `eebdb911-99e`, `16a62cac-696`) are not reconstructible from `trade_decisions` or `position_events` in the current DB snapshot.
**Impact:** A non-authoritative attribution surface can be mistaken for wallet truth, creating a false belief that paper PnL is much higher than the bankroll snapshot actually shows.
**Proposed antibody:** Rebuild tracker summaries only from durable settlement/exit events or stamp every non-DB-backed trade with explicit archival provenance; add a reconciliation test that tracker PnL must be derivable from durable event truth (or explicitly marked as legacy/archive-only).

---

## 2026-04-03 — edge-reversal follow-up triage

### [OPEN] Paper positions have no token_id → chain_state=unknown → stale_legacy_fallback → RiskGuard RED
**Location:** `src/execution/executor.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py`
**Problem (filed 2026-04-10):** 12 paper positions entered April 7 with no token_id. All have `chain_state="unknown"`, `token_id=""`. Canonical DB projection returns non-ok status → `load_portfolio()` falls back to stale JSON → RiskGuard sees broken portfolio → RED → all new entries blocked since April 7.
**Evidence (2026-04-10):** `load_portfolio falling back to JSON because canonical projection is unavailable: stale_legacy_fallback` in both zeus-paper.log and riskguard.err. 12 positions in `positions-paper.json` with empty token_id. No new trades in cycle logs since April 7 despite active April 11 markets.
**Impact:** Zero new trades for 3 days. Polymarket has 47 active April 11 markets with prices, but system cannot enter due to RED block.
**Proposed antibody:** Add a canonical projection preflight in `load_portfolio()` that explicitly checks position chain state — if > N positions have `chain_state=unknown`, mark projection as `degraded` instead of `ok`, and require explicit handling rather than silent fallback.

### [OPEN] Entry sizing is too permissive on reconstructed day0 windows with weak CI
**Location:** `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`, `src/state/portfolio.py`
**Problem:** Day0 / `update_reaction` entries can still be sized aggressively even when `ci_lower == ci_upper == 0`, `fill_quality = 0`, and the decision is reconstructed rather than directly observed.
**Impact:** The system can allocate oversized capital to weakly-supported extreme bins, producing large settlement losses before any runtime reversal has a chance to intervene.
**Proposed antibody:** Add a hard size cap or rejection gate for reconstructed day0 entries with missing / degenerate CI, and require a nonzero confidence band before allowing full-size execution.

### [OPEN] Some positions never develop a usable monitor-to-exit chain before settlement
**Location:** `src/engine/cycle_runtime.py`, `src/engine/monitor_refresh.py`
**Problem:** A subset of positions reach settlement with only lifecycle + settlement events and no intermediate monitor/reversal chain, so `EDGE_REVERSAL` never has a chance to fire.
**Impact:** The system cannot protect itself from fast-moving divergence if the monitor phase does not create an actual executable exit path.
**Proposed antibody:** Require at least one post-entry monitor scan before settlement-sensitive positions are allowed to age out; if monitoring does not occur, flag the position as `monitor_chain_missing` and escalate.

### [OPEN] Exit evaluation hard-fails when best_bid / exit context is missing
**Location:** `src/state/portfolio.py`, `src/execution/exit_triggers.py`
**Problem:** `best_bid is None` currently yields `INCOMPLETE_EXIT_CONTEXT` rather than a conservative fallback.
**Impact:** Thin books or incomplete market snapshots can suppress exits entirely, even when the live edge has clearly reversed.
**Proposed antibody:** Add a degraded fallback exit path (mid / last-trade / conservative proxy) and only hard-fail when all market evidence sources are unavailable.

### [PARTIALLY FIXED] EDGE_REVERSAL — hard divergence kill-switch at 0.30 added (2026-04-06, math audit)
**Location:** `src/state/portfolio.py`, `src/execution/exit_triggers.py`
**Problem:** Reversal requires two negative confirmations plus an EV gate, so a position can become clearly wrong in settlement truth without ever tripping runtime reversal.
**Impact:** The system may hold losers through large adverse moves when the market changes quickly but not persistently enough for the current confirmation rule.
**Proposed antibody:** Keep the conservative reversal path, but add a separate hard divergence kill-switch (single-shot on extreme divergence / velocity) for high-confidence failures.

### [FIXED] Settlement `won` ambiguity split into explicit semantic fields (2026-04-06)
**Location:** `src/state/db.py`, `src/engine/lifecycle_events.py`, `src/state/decision_chain.py`
**Problem:** Settlement records stored `won=true` alongside negative PnL, conflating market-bin correctness with trade profitability.
**Fix (2026-04-06):**
- Bumped `CANONICAL_POSITION_SETTLED_CONTRACT_VERSION` to `v2`
- Added `market_bin_won` to canonical payload: `True` iff position's bin matched winning bin (market direction)
- Added `position_profitable` to canonical payload: `True` iff realized PnL > 0 (actual profit)
- `won` kept for backward compat; normalization falls back to deriving from `won` when new fields absent
- `decision_chain.py` normalization updated to read v2 fields and compute from `pnl > 0` for v1 records
- Tests updated to reflect `v2` contract version (malformed v1 fixture tests left unchanged)
**Note:** `direction_correct` deferred — can be derived from `market_bin_won + direction + entry_price` when needed; current `market_bin_won` / `position_profitable` split resolves the core ambiguity described in this gap.

### [FIXED] Control-plane gate drift was a stale summary projection, now cleared (2026-04-06)
**Location:** `zeus/state/control_plane-paper.json`, `zeus/state/status_summary-paper.json`, status renderer
**Problem (historical):** The rendered paper summary previously exposed a stale duplicate gate field for `opening_inertia` that disagreed with the control plane.
**Resolution:** The current paper snapshot no longer shows that split-brain state. `status_summary-paper.json` no longer presents the old stale `strategy_summary.gated` conflict in the latest snapshot, and the canonical gate authority remains the control plane.
**Follow-up:** Keep the renderer deriving any future gate projections from the control plane only, and add a regression test if the duplicate field returns.

### [OPEN] Harvester Stage-2 bootstrap path is not runtime-ready against canonically bootstrapped databases
**Location:** `src/execution/harvester.py` / runtime `position_events` helpers
**Problem:** Recent log tails show repeated harvester errors stating that legacy runtime `position_events` helpers do not support canonically bootstrapped databases. The Stage-2 bootstrap path is still being exercised at runtime even though the helper contract cannot handle the current DB shape.
**Live evidence (2026-04-06):** Harvester ran at 12:47–12:55 CDT and produced `settlements_found=141, pairs_created=0, positions_settled=0`. It found settlements but generated zero calibration pairs — consistent with Stage-2 helpers failing on canonically bootstrapped DB. Gamma API fetch also timed out during this run (`WARNING: Gamma API fetch failed: The read operation timed out`).
**Impact:** Harvester cycles can fail noisily and skip settlement/pair creation work, leaving the runtime path partially broken even when the daemon and RiskGuard are alive.
**Proposed antibody:** Add an explicit runtime-DB shape gate before Stage-2 harvester work starts, and route canonically bootstrapped databases through the migrated helper path only. Add a regression test that the legacy helper is rejected with a structured preflight error instead of spamming runtime logs.

### [OPEN] Los Angeles Gamma markets can be mislabeled / sourced from Milan
**Location:** Gamma API market discovery / `market_scanner` LA path
**Problem:** Current audit evidence shows the Los Angeles market title / data source can resolve to Milan temperature data instead of LA weather truth.
**Impact:** LA bin construction can be anchored to the wrong city, which contaminates signal, entry sizing, and any downstream settlement comparison.
**Proposed antibody:** Add an explicit city-to-market sanity check in discovery so LA cannot bind to non-LA titles or station metadata; fail closed and record the anomaly when title/location mismatch is detected.

### [OPEN] ACP router fallback chain is recovering after failure, not stabilizing before dispatch
**Source:** `evolution/router-audit/2026-04-08-router-audit.md`
**Problem:** The current router can classify `auth`, `timeout`, and `network` failures, but dispatch still happens before allowlist/auth/timeout hard prechecks. Result: the fallback chain keeps switching to another failure surface instead of a known-good surface.
**Impact:** Window-level timeout clusters, invalid auth tokens, and Discord gateway/network failures can cascade across the routing stack.
**Proposed antibody:** Add a deterministic pre-dispatch gate for allowlist/auth/timeout, then run semantic routing only over candidates that already passed preflight.

### [FIXED] Heartbeat cron silently suppressed RED because delivery mode was `none`
**Location:** `/Users/leofitz/.openclaw/cron/jobs.json` (`zeus-heartbeat-001`)
**Problem:** The heartbeat cron job was configured with `delivery.mode = none`, so unhealthy runs could complete without announcing anything to Discord.
**Impact:** A stale RiskGuard / RED healthcheck could run silently, leaving the operator without the expected immediate warning.
**Fix (2026-04-05):** Switched `zeus-heartbeat-001` to `delivery.mode = announce` and tightened the payload so unhealthy runs must emit a concise alert, while healthy runs must return `NO_REPLY`.

---

## MEDIUM-CRITICAL: Cross-Layer Epistemic Fragmentation (D1–D6)

Six design gaps identified at the signal→strategy→execution boundary. The signal layer's high hit rate does not compose into profit because each cross-layer handoff loses the semantic that makes the upstream number meaningful. These are architecture-level gaps requiring typed contracts at module boundaries (INV-12 territory).

### [OPEN] D1 — Alpha blending optimization target misalignment (CRITICAL)
**Location:** `src/strategy/market_fusion.py` — `compute_alpha()`
**Problem:** α adjustments (spread, lead time, freshness, model agreement) are validated against Brier score. But profit requires EV > cost. Brier-optimization converges Zeus toward market consensus, which drives edge → 0. The optimization target (accuracy) conflicts with the business objective (profit).
**Impact:** Systematic edge compression. Alpha tuning that improves calibration accuracy simultaneously destroys the trading edge.
**Proposed antibody:** α outputs become a typed `AlphaDecision(value, optimization_target, evidence_basis, ci_bound)` so downstream consumers know whether the number was optimized for accuracy or EV. INV-12 enforcement.

### [OPEN] D2 — TAIL_ALPHA_SCALE breaks buy_no primary edge source (CRITICAL)
**Location:** `src/strategy/market_fusion.py` — tail alpha scaling
**Problem:** `TAIL_ALPHA_SCALE=0.5` scales α toward market on tail bins, directly halving the edge that buy_no depends on (retail lottery-effect overpricing of shoulder bins). The scaling serves calibration accuracy (Brier) but destroys the structural edge that Strategy B (Shoulder Bin Sell) exploits.
**Impact:** Strategy B's primary edge source is systematically attenuated by a calibration-serving parameter.
**Proposed antibody:** `TailTreatment` contract that declares whether it serves calibration OR profit. Cannot serve both without explicit tradeoff documentation.

### [OPEN] D3 — Entry price uses implied probability, not VWMP+fee (SEVERE)
**Location:** `src/strategy/market_analysis.py` — `BinEdge.entry_price`
**Problem:** `BinEdge.entry_price = p_market[i]` (implied probability from mid-price), but actual execution price = ask + taker fee (5%) + slippage. Kelly sizing uses the implied probability as the cost basis, systematically oversizing positions because the real cost is higher.
**Impact:** Every Kelly-sized position is larger than it should be. The magnitude depends on spread width and fee structure.
**Proposed antibody:** `ExecutionPrice(vwmp_or_ask, fee_deducted=True/False, currency)` wrapper at the Kelly boundary. Kelly must receive fee-adjusted cost, not implied probability.

### [OPEN] D4 — Entry-exit epistemic asymmetry (CRITICAL)
**Location:** `src/engine/evaluator.py` (entry), `src/execution/exit_triggers.py` (exit)
**Problem:** Entry requires BH FDR α=0.10 + bootstrap CI + `ci_lower > 0` — high statistical burden. Exit requires only 2-cycle confirmation — low statistical burden. The system admits edges cautiously but exits aggressively, killing true edges via noise before they mature.
**Cross-reference:** Several specific manifestations of this asymmetry are tracked in the "Exit/Entry Epistemic Asymmetry" section above (MC count mismatch [FIXED], CI-aware exit [FIXED], hours_since_open [FIXED], divergence threshold [FIXED]). This gap tracks the *structural* asymmetry: entry and exit should share a symmetric `DecisionEvidence` contract with comparable statistical burden.
**Proposed antibody:** Entry and exit share the same `DecisionEvidence` contract type with symmetric statistical burden. Exit reversal requires bootstrap-grade evidence, not just 2 consecutive point-estimate checks.

### [OPEN] D5 — Vig smearing distorts edge signal (MEDIUM)
**Location:** `src/strategy/market_fusion.py` — probability blending
**Problem:** `p_market` includes vig (~0.95–1.05 total probability across bins). Blending model probability with vig-contaminated market probability, then normalizing, smears the vig bias into the posterior. The posterior systematically inherits a vig-driven bias that is not a real probability signal.
**Proposed antibody:** Vig normalization before blend, under a declared `VigTreatment` contract that makes the vig handling explicit.

### [OPEN] D6 — EV hold calculation ignores funding/correlation cost (MEDIUM)
**Location:** `src/strategy/kelly.py` — hold value calculation
**Problem:** `net_hold = shares × p_posterior` assumes free carry. Ignores opportunity cost of locked capital and correlation crowding across simultaneous positions. When holding multiple positions, capital is not fungible, but the sizing treats it as if each position is independent.
**Proposed antibody:** `HoldValue` contract declaring included costs; minimum = fee + time-cost-to-settlement. Correlation-adjusted portfolio heat already partially addresses this through the Kelly cascade drawdown factor, but the hold value itself remains naïve.
