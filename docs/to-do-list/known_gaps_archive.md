# Known Gaps — Antibody Archive

This file is the immune-system record: gaps that were resolved by code, type,
or test antibodies, kept for traceability when a similar pattern resurfaces.
Active gaps live in `docs/to-do-list/known_gaps.md`.

Each entry preserves its original `[FIXED]` / `[CLOSED]` status tag and the
antibody description. Do not re-open an entry without proof the antibody
failed in current code.

Archive cutover: 2026-04-26.

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

### [CLOSED — 2026-04-15] alpha_overrides 只有伦敦验证为盈利
**Context:** 数学验证显示只有 London 的 alpha override 预期盈利。其他城市的 override 可能 negative EV。
**Resolution:** `compute_alpha` 已将 override 机制标记为废弃，override 表 0 rows。Override 路径不再活跃，不需要 per-city validation。如果未来需要恢复，须作为新 packet 重新评估。

### [CLOSED — 2026-04-15] Harvester 不知道 bias correction 是否开启
**Location:** `src/execution/harvester.py` — `harvest_settlement()` 生成 calibration pairs
**Resolution:** Full lineage fix:
1. `ensemble_snapshots` schema now has `bias_corrected INTEGER` column (code-level migration in `init_schema`)
2. `_store_snapshot_p_raw()` in evaluator.py persists `ens.bias_corrected` at snapshot-write time (decision-time truth)
3. `get_snapshot_context()` returns `bias_corrected` from snapshot
4. Production caller passes snapshot's `bias_corrected` to `harvest_settlement()`
5. `harvest_settlement()` also accepts explicit `bias_corrected` param; falls back to `settings.bias_correction_enabled` when None (for pre-migration snapshots without the column)
6. `decision_group_id` computed upfront from `source_model_version` + `forecast_issue_time` (also from snapshot)
Tests: `test_bias_corrected_persisted_through_harvest`, `test_bias_corrected_fallback_reads_settings`

---

## Tooling / Operator Health

### [FIXED] Healthcheck assumptions validation now succeeds in the active Python env (2026-04-03)
**Location:** `zeus/scripts/healthcheck.py`
**Problem:** Running healthcheck under `/opt/homebrew/bin/python3` previously depended on `numpy` being present; in this session the selected Python env now has numpy available and healthcheck completes with `assumptions_valid: true`.
**Impact:** Heartbeat classification is no longer blocked by a missing-numpy validation path in the active env.
**Antibody deployed:** Verified `python3` resolves to `/opt/homebrew/bin/python3` and `import numpy` succeeds (`2.4.2`), so the healthcheck assumptions gate now passes in the current runtime.

### [FIXED] Day0 stale probability no longer blocks exit authority (2026-04-13)
**Location:** `src/engine/cycle_runner.py`, `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`
**Problem:** Current cycle logs show `INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)` for several day0 positions. The cycle continues, but exit authority is evaluating with partially missing freshness context.
**Live evidence (2026-04-06):** 4 positions (`dab0ddb6-e7f`, `e6f0d01d-2a3`, `19a7116d-36c`, `511c16a6-27d`) repeatedly triggered this warning in the 14:30 and 15:00 cycles.
**Live evidence (2026-04-09):** 3 positions (`52280711-260`, `b33ff595-3cb`, `c25e2bfe-769`) still triggered `INCOMPLETE_EXIT_CONTEXT` in day0_capture cycle.
**Antibody deployed:** `ExitContext.missing_authority_fields()` now waives stale `fresh_prob_is_fresh` only for `day0_active=True`; `evaluate_exit()` keeps audit markers (`day0_stale_prob_authority_waived`, `stale_prob_substitution`) instead of pretending stale probability is fresh. Non-day0 stale probability still fails closed. Covered by `tests/test_day0_exit_gate.py` and `tests/test_live_safety_invariants.py`.
**Residual:** If fresh live logs still show this exact missing field for day0 positions, the likely defect is upstream state classification not reaching `day0_window`, not the freshness waiver itself.

---

## 2026-04-03 — edge-reversal follow-up triage

### [FIXED] Settlement-sensitive entries fail closed on degenerate CI (2026-04-13)
**Location:** `src/engine/evaluator.py`
**Problem:** Day0 / `update_reaction` entries can still be sized aggressively even when `ci_lower == ci_upper == 0`, `fill_quality = 0`, and the decision is reconstructed rather than directly observed.
**Impact:** The system can allocate oversized capital to weakly-supported extreme bins, producing large settlement losses before any runtime reversal has a chance to intervene.
**Antibody deployed:** `evaluate_candidate()` rejects settlement-sensitive entry modes (`day0_capture`, `update_reaction`) before Kelly when the confidence band is missing, non-finite, has `ci_lower <= 0`, or has `ci_upper <= ci_lower`. The rejection is recorded as `EDGE_INSUFFICIENT` with `confidence_band_guard`; `opening_hunt` is unchanged in this narrow packet.
**Residual:** This does not rebuild historical reconstructed decisions or add provenance for deterministic/high-quality zero-width CI. Supporting such a case safely would need a larger provenance/schema packet.

### [FIXED] Buy-yes exit uses degraded proxy when best_bid is missing (2026-04-13)
**Location:** `src/state/portfolio.py`
**Problem:** `best_bid is None` currently yields `INCOMPLETE_EXIT_CONTEXT` rather than a conservative fallback.
**Impact:** Thin books or incomplete market snapshots can suppress exits entirely, even when the live edge has clearly reversed.
**Antibody deployed:** `Position.evaluate_exit()` keeps `ExitContext.best_bid=None` for audit truth, but buy-yes exit evaluation now uses a degraded EV-gate proxy from fresh `current_market_price - 0.01` when `best_bid` is unavailable. It records `best_bid_unavailable`, `best_bid_proxy_from_current_market_price`, and `best_bid_proxy_tick_discount`; if current market price is missing or stale, exit authority still fails closed.
**Residual:** The proxy is not chain-proven sell-side liquidity. Use the audit markers to separate degraded exits from exits based on a real best bid.

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
**Location:** `zeus/state/control_plane-legacy.json`, `zeus/state/status_summary-legacy.json`, status renderer
**Problem (historical):** The rendered legacy diagnostic summary previously exposed a stale duplicate gate field for `opening_inertia` that disagreed with the control plane.
**Resolution:** The current legacy snapshot no longer shows that split-brain state. `status_summary-legacy.json` no longer presents the old stale `strategy_summary.gated` conflict in the latest snapshot, and the canonical gate authority remains the control plane.
**Follow-up:** Keep the renderer deriving any future gate projections from the control plane only, and add a regression test if the duplicate field returns.

### [FIXED] Los Angeles Gamma discovery rejects explicit Milan conflicts (2026-04-13)
**Location:** Gamma API market discovery / `market_scanner` LA path
**Problem:** Current audit evidence shows the Los Angeles market title / data source can resolve to Milan temperature data instead of LA weather truth.
**Impact:** LA bin construction can be anchored to the wrong city, which contaminates signal, entry sizing, and any downstream settlement comparison.
**Antibody deployed:** `market_scanner._parse_event()` now rejects Gamma events when event or market text/station metadata explicitly references a different configured city than the matched event city. LA events with Milan/Milano/LIMC/Milan Malpensa evidence fail closed before outcomes are returned; valid LA/KLAX metadata still parses.
**Residual:** This only catches explicit text/station conflicts on the `find_weather_markets()` discovery path. Existing monitor helpers (`get_current_yes_price`, `get_sibling_outcomes`) and harvester closed-event polling still need their own source-attestation package if they must defend against the same class of malformed Gamma payload. If Gamma omits metadata or supplies self-consistent but false LA metadata, external source attestation is still required.

### [FIXED] Heartbeat cron silently suppressed RED because delivery mode was `none`
**Location:** `/Users/leofitz/.openclaw/cron/jobs.json` (`zeus-heartbeat-001`)
**Problem:** The heartbeat cron job was configured with `delivery.mode = none`, so unhealthy runs could complete without announcing anything to Discord.
**Impact:** A stale RiskGuard / RED healthcheck could run silently, leaving the operator without the expected immediate warning.
**Fix (2026-04-05):** Switched `zeus-heartbeat-001` to `delivery.mode = announce` and tightened the payload so unhealthy runs must emit a concise alert, while healthy runs must return `NO_REPLY`.

---

## MEDIUM-CRITICAL: Cross-Layer Epistemic Fragmentation (D1–D6)

### [CLOSED] D5 — Sparse-monitor vig treatment with typed impute provenance (2026-04-24, T6.3 Option C)
**Location:** `src/strategy/market_fusion.py` + `src/contracts/vig_treatment.py`
**Problem (historical):** `p_market` includes vig (~0.95–1.05 total probability across bins). Blending model probability with vig-contaminated market probability, then normalizing, smears the vig bias into the posterior. Separately, sparse monitor vectors (zeros for non-held bins) diluted the held-bin posterior OR (post-B086 2026-04-19) were left as raw zeros which underweighted the held bin in blend.
**Antibody deployed (2026-04-13):** `compute_posterior()` constructs `VigTreatment.from_raw(p_market)` and blends against `clean_prices` before final posterior normalization when `p_market` looks like a complete market-family vector.
**Antibody deployed (2026-04-24, T6.3 Option C, commit `6f53ef2`):** `VigTreatment.from_raw(p_market, *, sibling_snapshot=None, imputation_source="none")` gains impute path — when raw market has zero bins and a sibling_snapshot (e.g., p_cal) is supplied, zeros are filled from the sibling and the record carries `imputed_bins: tuple` + `imputation_source: Literal["none","sibling_market","p_cal_fallback"]`. The sparse branch of `compute_posterior` now routes through this with `imputation_source="p_cal_fallback"`. Silent revival of pre-B086 policy is structurally impossible — typed-visible on the VigTreatment record. Archive supersedence recorded at `docs/archives/local_scratch/2026-04-19/zeus_data_improve_bug_audit_100_resolved.md:17` (local-only; durable record in `docs/archives/packets/task_2026-04-23_midstream_remediation/T6_receipt.json` + `src/contracts/vig_treatment.py` module docstring).
**Category immunity**: 1.0/1.0. Downstream auditors reading the posterior record can distinguish market-derived bins from model-prior fills via `imputation_source`.
**Remaining operator decision**: once `sibling_market` source wiring lands (T6.4-phase3 style slice threading real cross-market snapshots), flip caller from `p_cal_fallback` to `sibling_market`.

### [CLOSED] D6 — Exit EV gate routes through HoldValue with fee + time + correlation crowding (2026-04-24, T6.4 + phase2)
**Location:** `src/state/portfolio.py`, `src/contracts/hold_value.py`
**Problem (historical):** `net_hold = shares × p_posterior` assumes free carry. Ignores opportunity cost of locked capital and correlation crowding across simultaneous positions.
**Antibody deployed (2026-04-13):** Active and legacy exit EV gates routed hold EV through `HoldValue.compute(fee=0, time=0)` — free-carry arithmetic made explicit.
**Antibody deployed (2026-04-24, T6.4 minimal + hardening, commits `96fd850` + `6b4455f` + `4edd4c8`):** `HoldValue.compute_with_exit_costs(shares, current_p_posterior, best_bid, hours_to_settlement, fee_rate, daily_hurdle_rate, correlation_crowding=0.0)` factory. `fee_cost = shares × polymarket_fee(best_bid, fee_rate)` reuses existing Polymarket formula; `time_cost = capital_locked × (hours/24) × daily_hurdle_rate` with `hours=None` soft-collapse (breadcrumb `hold_value_hours_unknown_time_cost_zero` on the exit decision when authority gap). Wired into 4 EV-gate sites (`_buy_yes_exit` Day0 + Layer-4, `_buy_no_exit` Day0 + Layer-4 — buy_no previously bypassed HoldValue entirely; T6.4 brought it through the contract for parity). Feature-flag-gated via `feature_flags.HOLD_VALUE_EXIT_COSTS` (default OFF, preserves pre-T6.4 zero-cost behavior until operator flip after T6.3-followup-1 replay audit).
**Antibody deployed (2026-04-24, T6.4-phase2, commit `ebdfb2d`):** `ExitContext` gains `portfolio_positions: tuple = ()` + `bankroll: Optional[float] = None` (populated by `cycle_runtime._build_exit_context` from `PortfolioState.positions` with self-excluded via `trade_id` filter). `_compute_exit_correlation_crowding(this_cluster, portfolio_positions, bankroll, shares, best_bid, crowding_rate)` uses `src/strategy/correlation.py::get_correlation` to compute `rate × exposure_ratio × shares × best_bid` dollar crowding cost. Wired through `correlation_crowding=` kwarg on all 4 factory calls; applied_validations breadcrumb `hold_value_correlation_crowding_applied` fires when cost > 0. `exit.correlation_crowding_rate` default 0.0 (no-op-safe — phase2 shipping does NOT alter live behavior until operator config flip). Bounds [0, 0.1] via getter to catch misconfig.
**Category immunity**: 1.0/1.0 when `exit.correlation_crowding_rate > 0` (fee + time + correlation all wired). Silent D6 bypass is structurally impossible: fee/time flag-gate flips are operator-audited via T6.3-followup-1; correlation activation is a config flip rather than code change.
**Remaining operator decision**: T6.4 pre-flag-flip checklist — re-verify polymarket_fee formula against live Polymarket docs + decide replay-receipt code-enforcement (file-presence assertion inside `hold_value_exit_costs_enabled()`) vs operator-governance. Both documented in `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` §"T6.4 pre-flag-flip operator checklist".

### [CLOSED] Day0 transition emits durable canonical position_events row (2026-04-24, Day0-canonical-event slice)
**Location:** `src/engine/lifecycle_events.py` + `src/engine/cycle_runtime.py` + `architecture/2026_04_02_architecture_kernel.sql`
**Problem (historical):** `cycle_runtime.execute_monitoring_phase` day0 transition at L620+ updated `position_current.phase` via `update_trade_lifecycle` but did NOT emit a canonical `position_events` row. `tests/test_live_safety_invariants.py::test_day0_transition_emits_durable_lifecycle_event` was skipped `OBSOLETE_PENDING_FEATURE` (T1.c-followup L875) because there was no event to assert against.
**Antibody deployed (2026-04-24, commits `8de2290` + `4d546ee`):** Added `build_day0_window_entered_canonical_write(position, *, day0_entered_at, sequence_no, previous_phase, source_module)` single-event builder emitting `DAY0_WINDOW_ENTERED` event_type (new entry in `position_events.event_type` CHECK constraint) with `phase_before=active` / `phase_after=day0_window` and payload carrying `day0_entered_at`. `cycle_runtime._emit_day0_window_entered_canonical_if_available` wires the builder after the successful persist at the day0 transition site (queries `MAX(sequence_no)` and increments per the `fill_tracker._mark_entry_filled` pattern). Added `_ensure_day0_window_entered_event_type` legacy-DB migration in `src/state/ledger.py` that rebuilds the table with the expanded CHECK when DAY0_WINDOW_ENTERED is absent. L875 test un-skipped and passing end-to-end.
**Category immunity**: lifecycle authority gains a typed event for day0 transitions. Audit tools reading `position_events` can distinguish the transition from generic MONITOR_REFRESHED or raw `position_current` phase flips.
**Remaining operator action**: production DB migration via `init_schema(conn)` daemon restart — pre-migration snapshot + SHA-256 sidecar following REOPEN-2 pattern. See `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` §"Day0-canonical-event production DB migration".

---

## 2026-04-30 — Closed Live-Alpha Runtime and Legacy-Authority Gaps

Entries here were removed from the active gap register because, at the
2026-04-30 archive cutover, their deployed antibodies made the original failure
mode non-active, or because the item was a structural false-positive boundary
rather than remediation work.

### [CLOSED — 2026-04-30; archived from MITIGATED] Legacy fill polling no longer treats `MATCHED` as a fill terminal
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, code and tests
enforced CONFIRMED-only success finality; the remaining note was a future
typed-order-finality extension, not an active blocker.

**Location:** `src/execution/fill_tracker.py`, `src/execution/exit_lifecycle.py`.
**Original problem:** Legacy polling set `FILL_STATUSES = {"FILLED", "MATCHED"}`.
Polymarket trade lifecycle treats `MATCHED` as non-terminal; `CONFIRMED` is the
successful terminal.
**Antibody deployed:** Entry and exit polling now use `CONFIRMED` as the only
success terminal. `MATCHED`/`FILLED` entry payloads record venue facts and
optimistic exposure only; they do not set `entry_fill_verified`, `entered_at`, or
canonical entry-fill truth. Exit polling leaves `MATCHED`/`FILLED` pending.
Stale deps cannot remove `CONFIRMED` from the fill-status set.
**Evidence:** `src/execution/fill_tracker.py::FILL_STATUSES`,
`_fill_statuses()`, `_record_optimistic_entry_observed()`;
`src/execution/exit_lifecycle.py::FILL_STATUSES`;
`tests/test_live_safety_invariants.py::test_confirmed_fill_survives_stale_deps_fill_statuses`,
`test_legacy_polling_matched_maps_numeric_live_runtime_id_to_optimistic_lot`,
and `test_pending_exit_filled_status_does_not_economically_close`.
**Residual:** If a future adapter proves an order-level status is irreversible
fill finality before trade `CONFIRMED`, it needs a typed order-finality contract
instead of reusing `MATCHED`/`FILLED` as generic success terminals.

### [CLOSED — 2026-04-30; archived from MITIGATED] Exit lifecycle no longer economically closes on non-final `MATCHED`/`FILLED`
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, code and tests
kept non-CONFIRMED exit observations pending; the remaining note was a future
adapter-contract extension, not an active blocker.

**Location:** `src/execution/exit_lifecycle.py::FILL_STATUSES`,
`src/execution/exit_lifecycle.py::_check_order_fill`,
`src/execution/exit_lifecycle.py::check_pending_exits`,
`src/execution/exit_lifecycle.py::_execute_live_exit`.
**Original problem:** Exit lifecycle defined `FILL_STATUSES = {'MATCHED',
'FILLED'}` and used that set in both immediate post-submit fill checks and
later `check_pending_exits()`. A `MATCHED` order/trade status could call
`compute_economic_close()` before trade `CONFIRMED`.
**Antibody deployed:** Exit lifecycle now defines `FILL_STATUSES =
frozenset({"CONFIRMED"})`; `MATCHED` and `FILLED` are explicit non-final
observations and leave the exit pending.
**Evidence:** `src/execution/exit_lifecycle.py::FILL_STATUSES`,
`tests/test_live_safety_invariants.py::test_pending_exit_filled_status_does_not_economically_close`,
`test_pending_exit_matched_status_does_not_economically_close`, and
`test_deferred_confirmed_fill_logs_last_monitor_best_bid`.
**Residual:** If a future adapter proves a non-`CONFIRMED` order string is
irreversible fill finality, add a typed order-finality source instead of
widening this raw status set.

### [CLOSED — 2026-04-30; archived from MITIGATED] Command recovery no longer turns non-final `MINED`/`FILLED` into `FILL_CONFIRMED`
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, the recovery path
mapped only CONFIRMED to fill finality; future adapter finality proof was not an
active raw-status gap.

**Location:** `src/execution/command_recovery.py::_reconcile_row`,
`src/state/venue_command_repo.py::append_event`,
`docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`.
**Original problem:** The command recovery loop treated a recovered
`SUBMIT_UNKNOWN_SIDE_EFFECT` order response with venue status in
`{"FILLED", "MINED", "CONFIRMED"}` as `FILL_CONFIRMED`, collapsing non-final
venue observations into confirmed command truth.
**Antibody deployed:** Recovery now emits `FILL_CONFIRMED` only for
`CONFIRMED`; `FILLED`, `MATCHED`, `MINED`, `PARTIAL`, `PARTIALLY_MATCHED`, and
`PARTIALLY_FILLED` emit `PARTIAL_FILL_OBSERVED`.
**Evidence:** `src/execution/command_recovery.py::_reconcile_row`,
`tests/test_command_recovery.py::test_unknown_side_effect_nonconfirmed_status_stays_partial_not_fill_finality`,
and `test_unknown_side_effect_confirmed_reaches_fill_finality`.
**Residual:** A future adapter may add typed order-finality proof, but raw
`MINED`/`FILLED` recovery responses no longer advance to `FILLED`.

### [CLOSED — STRUCTURAL 2026-04-30] Polymarket LOW market series starts 2026-04-15
**Archived from active register:** 2026-04-30.
**Archive rationale:** At the 2026-04-30 archive cutover, this was an
anti-rabbit-hole false-positive boundary backed by fatal-misread and packet
evidence, not an active remediation item.
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
- `docs/archives/packets/task_2026-04-28_settlements_low_backfill/plan.md`
- `docs/archives/packets/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json`
**Invalidation:** only a fresh gamma-api probe with HTTP-evidence showing LOW events with endDate < 2026-04-15 OR coverage beyond 8 cities may relax this.

### [CLOSED — 2026-04-30; archived from MITIGATED] strategy_tracker no longer reports JSON PnL as independent truth
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, runtime strategy
tracking was DB/event-derived and no longer wrote or trusted JSON projection
authority; legacy JSON references were fixture/history hygiene.
**Location:** `src/state/strategy_tracker.py`, legacy `strategy_tracker-*.json` artifacts.
**Original problem:** `strategy_tracker-legacy.json` could report `opening_inertia`
cumulative PnL that was not reconstructible from durable DB/event truth.
**Antibody deployed:** `StrategyTracker` is now a no-write canonical projection:
`record_*` methods are no-op compatibility shims, `save_tracker()` does not
write disk, `load_tracker()` ignores legacy files, and `summary()` derives from
`query_authoritative_settlement_rows()` / `position_events`.
**Evidence:** `src/state/strategy_tracker.py` module contract and no-op
`record_*` / `save_tracker()` implementations.
**Residual:** Legacy `strategy_tracker-legacy.json` references in tests or
historical artifacts remain fixture/archive surfaces. They should not be used as
runtime or wallet truth.

### [CLOSED — 2026-04-30; archived from MITIGATED] Legacy JSON fallback no longer becomes portfolio authority
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, portfolio loading
was DB-first and degraded instead of promoting JSON; residual references were
fixtures/history, not live authority.
**Location:** `src/state/portfolio.py::load_portfolio`,
`src/engine/cycle_runtime.py`, `src/riskguard/riskguard.py`.
**Original problem (filed 2026-04-10):** Legacy positions with missing token ids
could make canonical projection non-authoritative, after which
`load_portfolio()` could fall back to stale JSON and let RiskGuard reason over a
broken legacy portfolio as if it were current truth.
**Antibody deployed:** `load_portfolio()` is DB-first. If the DB connection or
projection is not authoritative, it returns a degraded `PortfolioState`
(`authority="unverified"`, `portfolio_loader_degraded=True`) and suppresses new
entries; it does not promote deprecated JSON into authority. Chain-only
quarantine evidence can still be rehydrated explicitly.
**Evidence:** `src/state/portfolio.py::load_portfolio`,
`tests/test_runtime_guards.py::test_load_portfolio_db_connection_failure_ignores_corrupt_json_and_degrades`,
`test_load_portfolio_treats_empty_projection_as_canonical_despite_legacy_json`,
and `tests/test_truth_layer.py::test_load_portfolio_rejects_deprecated_state_file`.
**Residual:** `positions-legacy.json` remains in legacy fixtures/history and
truth-surface stale-status tests. Those references are not live portfolio
authority and can be cleaned in a separate test/docs hygiene packet.

### [CLOSED — 2026-04-30] LOW non-Day0 monitor uses LOW probability chain
**Archived from active register:** 2026-04-30.
**Original problem:** `monitor_refresh._refresh_ens_member_counting()` resolved a
LOW position's calibrator metric but constructed `EnsembleSignal` with the
implicit HIGH default, so held LOW positions could compute raw probability from
daily maxima.
**Antibody deployed:** `monitor_refresh` now resolves `MetricIdentity` once and
passes `temperature_metric=temperature_metric` into `EnsembleSignal`, while the
calibrator receives the same metric string.
**Evidence:** `src/engine/monitor_refresh.py` metric-resolution and
`EnsembleSignal(... temperature_metric=...)` callsite; LOW monitor regression
coverage in `tests/test_phase9c_gate_f_prep.py`.
**Residual:** None for the original LOW-vs-HIGH monitor-chain break. Exit
partial-fill exposure and whale detector gaps remain separately active.

### [CLOSED — 2026-04-30] LOW Day0 handles open shoulders and rich observation context
**Archived from active register:** 2026-04-30.
**Original problem:** LOW Day0 probability converted `None` shoulder bounds with
`float(None)` and dropped rich HIGH-path fields such as settlement rounding,
observation source/time, and temporal context.
**Antibody deployed:** `Day0LowNowcastSignal` treats open shoulders as
`-inf/+inf`, applies injected settlement rounding, and preserves observation
source/time plus temporal context through `Day0SignalInputs` and the router.
**Evidence:** `src/signal/day0_low_nowcast_signal.py`,
`src/signal/day0_router.py`, and `tests/test_phase6_day0_split.py` covering
open shoulders, rounding injection, and rich observation context.
**Residual:** Day0 source-role/station authorization is still active under the
separate Day0 observation routing gap.

### [CLOSED — 2026-04-30] Entry intent carries executable snapshot facts
**Archived from active register:** 2026-04-30.
**Original problem:** The runtime call to `create_execution_intent()` did not
pass `executable_snapshot_id`, min tick, min order, or neg-risk facts even though
the command repository required them.
**Antibody deployed:** `cycle_runtime` extracts required executable snapshot
fields, captures a fresh entry snapshot when missing, blocks on missing/capture
failure, and passes snapshot id/min tick/min order/neg-risk into
`create_execution_intent()`.
**Evidence:** `src/engine/cycle_runtime.py` snapshot-field extraction/capture and
intent callsite; `src/data/market_scanner.py::capture_executable_market_snapshot`;
`tests/test_runtime_guards.py::test_entry_intent_receives_executable_snapshot_fields`,
`test_live_entry_snapshot_capture_failure_blocks_before_intent`, and
`test_live_entry_captures_and_commits_snapshot_before_executor`.
**Residual:** Exit snapshot refresh is still tracked by the executable snapshot
producer/refresher residual.

### [CLOSED — 2026-04-30] Market scan authority is gated before entry discovery
**Archived from active register:** 2026-04-30.
**Original problem:** `find_weather_markets()` exposed bare market dicts while
scanner authority (`VERIFIED`, `STALE`, `EMPTY_FALLBACK`, `NEVER_FETCHED`) was
lost before entry evaluation.
**Antibody deployed:** Runtime reads `get_last_scan_authority()`, records forward
substrate/availability facts, and blocks discovery before evaluator on non-
`VERIFIED` scan authority.
**Evidence:** `src/engine/cycle_runtime.py::_market_scan_authority()` and the
pre-evaluator block; `tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator`,
`test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator`, and
`test_discovery_phase_blocks_unverified_market_scan_authority_before_evaluator`.
**Residual:** Current-source validity and Day0 observation routing remain
separate source-truth gaps.

### [CLOSED — 2026-04-30] Open-Meteo ENS snapshot failure no longer produces tradeable empty snapshot id
**Archived from active register:** 2026-04-30.
**Original problem:** Open-Meteo-shaped ENS payloads could lack issue/valid times,
`_store_ens_snapshot()` could return `""`, and evaluation could continue without
a decision snapshot id.
**Antibody deployed:** Entry forecast evidence now requires explicit source,
role, authority, issue/valid/fetch/available facts; Open-Meteo no longer fakes
issue time from valid time; failed snapshot persistence rejects the candidate
instead of continuing with an empty id.
**Evidence:** `src/engine/evaluator.py::_entry_forecast_evidence_errors`,
`_store_ens_snapshot` failure handling, and probability-vector validation;
`tests/test_runtime_guards.py::test_openmeteo_degraded_forecast_fallback_blocks_entry_before_vector`,
`test_openmeteo_parse_keeps_first_valid_time_and_does_not_fake_issue_time`,
`test_store_ens_snapshot_links_openmeteo_valid_time_without_faking_issue_time`,
`test_store_ens_snapshot_refuses_legacy_id_collision_without_p_raw_corruption`,
and `test_store_ens_snapshot_refuses_v2_conflict_without_legacy_fallback`.
**Residual:** Live Open-Meteo as entry-primary still requires valid issue-time and
role evidence; missing evidence is a no-trade, not a silent empty-snapshot trade.

### [CLOSED — 2026-04-30] Gamma closed/non-accepting child markets are filtered before outcome extraction
**Archived from active register:** 2026-04-30.
**Original problem:** `_extract_outcomes()` included every child market with
parseable tokens/prices even if Gamma marked the child closed, inactive,
non-accepting, or orderbook-disabled.
**Antibody deployed:** `_market_child_is_tradable()` filters explicit
non-tradable child markets before token/price extraction while preserving
tradable children with status facts.
**Evidence:** `src/data/market_scanner.py::_extract_outcomes` and
`_market_child_is_tradable`; `tests/test_k1_slice_d.py::test_extract_outcomes_filters_untradable_gamma_children`.
**Residual:** Reduced-family statistical treatment for partially closed market
families remains a policy question, but closed children no longer enter the
outcome vector.

### [CLOSED — 2026-04-30] v2 row-count observability prefers world truth over trade shadow
**Archived from active register:** 2026-04-30.
**Original problem:** Status summary used unqualified v2 table row-count queries,
so empty trade shadow tables could hide populated attached world tables.
**Antibody deployed:** `_get_v2_row_counts()` now has table-specific schema
preference and bounded row-count probes, preferring attached `world` for world
v2 tables and respecting present empty world tables as authority.
**Evidence:** `src/observability/status_summary.py::_V2_ROW_COUNT_SCHEMA_PREFERENCE`
and `_get_v2_row_counts`; `tests/test_phase10b_dt_seam_cleanup.py::TestRCPV2RowCountSensor`
world-vs-main and missing-table coverage.
**Residual:** Status summaries remain observability projections, not authority
for live deploy.

### [CLOSED — 2026-04-30] VWMP-derived entry limits are quantized to executable snapshot tick
**Archived from active register:** 2026-04-30.
**Original problem:** Entry price planning could produce fractional VWMP-derived
limits such as `0.313333...`, which then failed the executable snapshot tick gate.
**Antibody deployed:** `create_execution_intent()` aligns BUY limit prices down
to `executable_snapshot_min_tick_size` before constructing the `ExecutionIntent`;
live runtime now requires the snapshot min-tick field before intent creation.
**Evidence:** `src/execution/executor.py::_align_buy_limit_price_to_tick`,
`create_execution_intent`; `src/engine/cycle_runtime.py` snapshot-field gate;
executor/runtime tests covering snapshot-threaded intent and repriced limit
submission.
**Residual:** Entry `max_slippage` enforcement is closed separately on
2026-04-30.

### [CLOSED — 2026-04-30] Entry max-slippage budget is enforced before command persistence
**Archived from active register:** 2026-04-30.
**Original problem:** `ExecutionIntent.max_slippage` was typed as
`SlippageBps`, but dynamic entry repricing still used an independent 5% best-ask
jump window. A best ask outside the 200 bps budget could become the submitted
limit before command persistence.
**Antibody deployed:** `create_execution_intent()` now evaluates adverse
slippage against the executable quote reference and rejects repriced limits above
the 200 bps budget. Runtime executable-snapshot repricing records the reference
price, applied bps, and whether the best ask was blocked by the budget before
threading the intent.
**Evidence:** `src/execution/executor.py::create_execution_intent`,
`src/engine/cycle_runtime.py::_reprice_decision_from_executable_snapshot`,
`tests/test_executor.py::TestExecutor::test_create_execution_intent_rejects_reprice_above_slippage_budget`,
`tests/test_executor_command_split.py::test_create_execution_intent_rejects_reprice_above_max_slippage`,
and `tests/test_runtime_guards.py::test_live_reprice_binds_intent_limit_when_dynamic_gap_would_not_jump`.
**Residual:** This closes configured entry slippage-budget enforcement. It does
not prove live alpha or solve separate live fee evidence / realized execution
attribution gaps.

### [CLOSED — 2026-04-30] Weather multi-bin buy_no/shoulder-sell hypotheses have native-NO executable reachability
**Archived from active register:** 2026-04-30.
**Original problem:** Full-family FDR could select multi-bin `buy_no` hypotheses
while `MarketAnalysis.find_edges()` produced no executable `BinEdge` for those
multi-bin native-NO trades.
**Antibody deployed:** Native NO quote evidence is threaded through family scan,
edge materialization, evaluator quote acquisition, snapshot capture, and
selected-token runtime routing; live enablement remains default-off.
**Evidence:** `tests/test_fdr.py`, `tests/test_runtime_guards.py`,
`tests/test_executable_market_snapshot_v2.py`, `tests/test_bootstrap_symmetry.py`,
`tests/test_executor.py`, and `tests/test_exit_safety.py` coverage named in the
original closeout.
**Residual:** This does not promote Shoulder Bin Sell to live alpha or prove P&L;
it only closes the structural reachability gap.
### [OPEN P1] Day0 observation path can bypass settlement-source routing

**Location:** `src/data/observation_client.py::get_current_observation` and
`_fetch_wu_observation`; `config/cities.json` Hong Kong and Paris rows.
**Problem:** Day0 observation fetch tries WU geocode timeseries for every city
before checking settlement source type. A read-only probe on 2026-04-28 showed
Hong Kong, whose config is `settlement_source_type="hko"` with `wu_station=null`,
returning WU `obs_id=VHHH`. The same probe showed Paris returning WU
`obs_id=LFPG`, while current Polymarket Paris markets resolve on `LFPB`.
**Impact:** Day0 high/low observation can be anchored to the wrong physical
station even when settlement semantics correctly say HKO or the live market
source says LFPB. This directly affects Day0 p_raw, shoulder capture, monitor
refresh, and exit decisions.
**False-positive boundary:** London probe returned `obs_id=EGLC`, matching its
WU config. The issue is source-routed cities and any city where geocode-nearest
station differs from the contract station.
**2026-04-30 recheck:** Entry evaluation now has an
`OBSERVATION_SOURCE_UNAUTHORIZED` gate for executable Day0 entries and only
allows `wu_api` for `settlement_source_type="wu_icao"`. That is a partial
entry-side guard, not a full fix. A local no-network monkeypatch still showed
`get_current_observation()` calling the WU geocode endpoint for a Hong Kong
`settlement_source_type="hko"` city and returning `source="wu_api"`, and
`Day0ObservationContext` still carries no `obs_id`/station field. The Day0
monitor refresh path consumes `_fetch_day0_observation()` directly and does not
apply the evaluator's source-policy rejection before building `Day0SignalInputs`.
**Proposed remediation:**
1. Route Day0 observation by `settlement_source_type`, not by generic provider
   priority.
2. For WU cities, require returned `obs_id` to match the contract station or a
   dated approved station map.
3. For HKO, skip WU/IEM entirely and use an HKO-native current observation path;
   if HKO current data is unavailable, fail closed for Day0 HK entries.
4. Persist `obs_id`/source station in `Day0ObservationContext` and
   probability-trace facts.
**Acceptance evidence:** HK Day0 never returns VHHH/WU as settlement observation;
Paris Day0 only returns LFPB after the Paris source decision; mismatched obs_id
produces structured no-trade.


### [MITIGATED 2026-04-30; RESIDUAL P3] Whale-toxicity uses orderbook-adjacent pressure, not all-market prints

**Location:** `src/engine/monitor_refresh.py::refresh_position`,
`src/state/portfolio.py::Position.evaluate_exit`, and
`src/execution/exit_triggers.py::evaluate_exit_triggers`.
**Original problem:** Exit logic had a `WHALE_TOXICITY` immediate-exit branch
and the execution module advertised adjacent-bin sweep detection, but monitor
refresh always initialized `pos.last_monitor_whale_toxicity = None`. The
portfolio exit path therefore recorded only `whale_toxicity_unavailable`.
**Current behavior:** Monitor refresh now computes a narrow orderbook-adjacent
pressure detector for held YES positions. It requires VERIFIED sibling-bin
metadata and fresh adjacent CLOB top-book facts, compares adjacent YES pressure
against the held bin with a 5c recent-surge margin or stricter 15c static
pressure fallback, and requires visible bid notional at least as large as the
position notional floor. `buy_no` positions are marked not-applicable rather
than falsely treating adjacent YES buying as toxic.
**False-positive boundary:** This is not a true all-market trade-print whale
sweep detector. Zeus's current V2 adapter exposes `get_trades`, and Polymarket
documents that surface as authenticated account trade history. Public
market-trade event methods are separate and not wired into this repo path, so
Zeus does not claim print-level whale detection without a market-stream
producer. Missing Gamma/CLOB facts leave the field `None`; clear facts set it
to `False`; strong adjacent pressure sets it to `True`.
**Evidence:** `tests/test_lifecycle.py::TestMonitorWhaleToxicity` covers toxic,
clear, unverified-scan unknown, and `buy_no` not-applicable cases. Selected
monitor-to-exit runtime seams prove `whale_toxicity` still flows through
`ExitContext` without corrupting Day0 monitor validations.
**Residual:** If the operator wants a literal "whale swept adjacent bin" claim,
add a dedicated market-level trade/websocket feed with threshold/lookback
validation. That is an enhancement beyond the current orderbook-pressure safety
gate, not a blocker for the current non-Paris local code path.


### [MITIGATED 2026-04-30; RESIDUAL P2] Entry partial fills preserve filled exposure after remainder cancel

**Location:** `src/execution/fill_tracker.py::_check_entry_fill`,
`_record_partial_entry_observed`.
**Original problem:** A `PARTIAL` entry could remain `pending_tracked`; after the
remainder timed out and cancellation succeeded, the entire local position could
be voided as `UNFILLED_ORDER`, losing already-filled shares.
**Antibody deployed:** `PARTIAL` now records filled shares, fill price, and cost
basis without marking the entry active or verified. If the remainder is
cancelled or expires after observed fill, the position stays `pending_tracked`
with `partial_remainder_cancelled` status instead of being voided or promoted to
success before `CONFIRMED`.
**Evidence:** `tests/test_live_safety_invariants.py::test_partial_remainder_cancel_preserves_filled_exposure`.
**Residual:** Rich command-fact semantics for partial->failed/retrying and a
confirmed optimistic-vs-final partial ledger remain a separate packet. The
specific void-after-cancel exposure-loss bug is no longer an active open gap.


### [MITIGATED 2026-04-30; RESIDUAL P1] Live fee-rate `base_fee` shape is parsed, but unit/provenance semantics remain unresolved

**Location:** `src/data/polymarket_client.py::get_fee_rate`,
`src/engine/evaluator.py::_fee_rate_for_token`,
`src/engine/evaluator.py::_size_at_execution_price_boundary`.
**2026-04-30 recheck:** `PolymarketClient.get_fee_rate()` now accepts
`base_fee`, `baseFee`, `fee_rate_bps`, `feeRateBps`, `feeRate`, `fee_rate`,
`takerFeeRate`, and `taker_fee_rate`, and `tests/test_v2_adapter.py` covers the
current `base_fee` shape. The original parser-shape blocker is therefore
mitigated. The entry remains active because the code returns the raw numeric
`base_fee` as the `polymarket_fee()` coefficient, while the documented formula
expects a fractional fee-rate coefficient; raw response capture and conversion
basis are still not persisted into decision evidence.
**Original problem:** The live evaluator asks the CLOB client for a token-specific fee
rate before Kelly sizing. `PolymarketClient.get_fee_rate()` calls
`https://clob.polymarket.com/fee-rate?token_id=...` but only accepts fields such
as `feeRate`, `fee_rate`, `takerFeeRate`, or `taker_fee_rate`. Current official
Polymarket documentation and a live read-only weather-token request show the
endpoint returning `{"base_fee": <integer>}`. Because `base_fee` is not parsed,
the client raises `RuntimeError`, `_fee_rate_for_token()` converts that into
`FeeRateUnavailableError`, and evaluator rejects the candidate at
`EXECUTION_PRICE_UNAVAILABLE`.
**Read-only reproduction:** On 2026-04-29, querying a current Paris weather YES
token returned HTTP 200 with `{"base_fee":1000}` from `/fee-rate`. Calling
`PolymarketClient().get_fee_rate(token)` on the same token produced
`RuntimeError: Fee-rate response missing feeSchedule.feeRate ...`.
**Residual impact:** Even after market discovery, signal, calibration, and
executable snapshot gates are repaired, live entry sizing can still be wrong if
the current CLOB `base_fee` integer is interpreted as the wrong
`polymarket_fee()` coefficient. This is now a unit/provenance compatibility
blocker, not a parser-shape blocker or alpha/model limitation.
**False-positive boundary:** If runtime injects a test/dummy `clob` without
`get_fee_rate`, evaluator falls back to `FEE_RATE_WEATHER`. The live path uses
the real client method, so unknown fee response units must be converted or fail
closed with evidence.
**Proposed remediation:**
1. Parse `base_fee` from `/fee-rate` and convert it to the exact fee-rate unit
   expected by `polymarket_fee()`, with an explicit test against official docs and
   live fixture JSON.
2. Decide whether sizing should use category reality-contract fallback only when
   fee-rate endpoint is unavailable, or fail closed when endpoint returns an
   unknown shape.
3. Store raw fee response, converted fee rate, and conversion basis in decision
   evidence/executable snapshot facts.
4. Add tests for `{"base_fee": 1000}`, legacy `feeRate`, disabled-fee shape, and
   malformed response.
**Acceptance evidence:** A current weather token's `/fee-rate` response parses
without exception, the converted value matches the intended Polymarket fee
formula units, and evaluator no longer rejects otherwise-valid candidates at
`EXECUTION_PRICE_UNAVAILABLE` solely because the response field is `base_fee`.




---

## [CLOSED — 2026-05-04] Day0 capture mode contradictory resolution-hour filters

**Original finding:** `MODE_PARAMS[DAY0_CAPTURE] = {"max_hours_to_resolution": 6}` had no `min_hours_to_resolution`. Scanner default `min_hours_to_resolution=6.0` dropped all markets with `hours_to_resolution < 6`; runtime second-stage kept only `hours_to_resolution < 6`. Practical intersection: empty — DAY0_CAPTURE could never discover any candidates.

**Antibody (landed in `cycle_runtime.py::execute_discovery_phase`):**
```python
min_hours_to_resolution = params.get("min_hours_to_resolution")
if min_hours_to_resolution is None:
    min_hours_to_resolution = 0 if "max_hours_to_resolution" in params else 6
markets = deps.find_weather_markets(min_hours_to_resolution=min_hours_to_resolution)
```
Presence of `max_hours_to_resolution` in params → scanner receives `min_hours_to_resolution=0` → <6h markets survive scanner filter.

**Verified:** 2026-05-04 by reading `cycle_runtime.py:1997-2001` on main (`cd882ee9`+). Gap was stale; no code change needed.

---

## Full-flow live audit — Source resolution subcomponents

### [CLOSED P1 — 2026-05-03] Paris config uses LFPG while current markets resolve on LFPB

**Location:** `config/cities.json` Paris `wu_station` and
`settlement_source`.
**Problem:** Fresh Gamma daily-temperature probe for 2026-04-28..2026-04-30
showed Paris HIGH/LOW resolutionSource as WU Bonneuil-en-France `LFPB`, while
production config still points Paris at `LFPG` / Charles de Gaulle. A broader
read-only active-event probe found `146` active daily-temperature events and
`6` station mismatches; all `6` were Paris HIGH/LOW Apr 28-30 with
`LFPB` vs `LFPG`. Existing LOW backfill evidence also records `LFPB`. A later
read-only source-boundary sweep found observed Paris HIGH contracts resolving
on `LFPG` through 2026-04-18 and on `LFPB` from 2026-04-19 onward. Paris LOW
slugs were not observable for 2026-04-15..2026-04-22 in that sweep; the first
observable Paris LOW event was 2026-04-23 and resolved on `LFPB`.
**Impact:** Paris observation, model calibration, signal generation, and
settlement rebuild can use a different station than the market contract. This
is a contract/source truth mismatch, not a modeling error.
**False-positive boundary:** Both WU pages currently respond. The issue is not
endpoint liveness; it is which WU station the active Polymarket contract names.
The same active-event probe did not find non-Paris station mismatches among
recognized configured cities.
**Resolution (2026-05-01 decision + 2026-05-03 execution):**
1. `config/cities.json` updated 2026-05-01: `wu_station: LFPB`,
   `airport_name: Paris-Le Bourget Airport`.
   Authority: `architecture/paris_station_resolution_2026-05-01.yaml`.
2. LFPG legacy observation rows deleted by backfill `--replace-station-mismatch`.
   762 LFPB obs rows backfilled (2024-01-01 → 2026-01-31).
3. LFPG-derived calibration_pairs_v2 rows (747,150 QUARANTINED) deleted.
   New LFPB pairs rebuilt over full historical window 2024-01-01→2026-05-01.
4. Platt models refitted on LFPB pairs (all 8 buckets VERIFIED+active).
5. Source-contract quarantine released via `state/source_contract_quarantine.json`.
   Paris removed from `_source_contract_pending_conversions` in `cities.json`.
6. `architecture/paris_station_resolution_2026-05-01.yaml` marked APPLIED.
**Verification:** `verify_ready.py` passed with Paris markets in ready list.
**Evidence:** `docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results/E8_audit/11_paris_resync_log.md`
**Resolved by:** Operator 2026-05-03 execution; all repairs verified.

---

## Discovery-mode window closure

### [CLOSED — 2026-05-04] Day0 capture mode has contradictory resolution-hour filters

**Closed:** 2026-05-04. Gap was valid when filed (original audit showed `MODE_PARAMS[DAY0_CAPTURE]` had no `min_hours_to_resolution`). Subsequent session added compensating logic in `cycle_runtime.py::execute_discovery_phase` (lines 1997-2001):
```python
min_hours_to_resolution = params.get("min_hours_to_resolution")
if min_hours_to_resolution is None:
    min_hours_to_resolution = 0 if "max_hours_to_resolution" in params else 6
markets = deps.find_weather_markets(min_hours_to_resolution=min_hours_to_resolution)
```
DAY0_CAPTURE has `max_hours_to_resolution` → `min_hours_to_resolution=0` is passed to scanner → <6h markets are not dropped at scanner boundary. The fix is present in current main.
**Resolved by:** Compensating logic present in main branch (upstream commit); closure verified 2026-05-04.

---

## Execution/venue settlement structural closures (T1+T2+PR#61)

### [CLOSED — 2026-05-05] V2 submit path still uses compatibility envelope

**Location:** `src/venue/polymarket_v2_adapter.py::place_limit_order` and
`_create_compat_submission_envelope`.
**Original problem:** The V2 adapter creates placeholder market identity with legacy markers.
**Structural closure:** T1F phase added `envelope.assert_live_submit_bound()` at adapter.py:319 which raises ValueError on compat envelope. Additionally, `submit_limit_order()` (compat shim) explicitly returns `_rejected_submit_result()` with error_code="COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE" at line 571. The compat envelope is structurally quarantined from the live path.
**Resolved by:** T1F (commit 0468a9ad); closure verified 2026-05-05.

---

### [CLOSED — 2026-05-05] Harvester can rebrand live decision p_raw as TIGGE training data

**Location:** `src/execution/harvester.py::maybe_write_learning_pair`,
`src/execution/harvester.py::harvest_settlement`.
**Original problem:** Live decision p_raw could be stored as TIGGE training rows via source rebranding.
**Structural closure:** T1C wraps `harvest_settlement()` in `maybe_write_learning_pair()` wrapper with T1C-LEARNING-AUTHORITY-GATE: gate refuses unless `snapshot_training_allowed=True`. Live p_raw (source_model_version='live_v1') cannot pass the wrapper. Caller must explicitly set training flag; default-False semantics prevent silent rebranding.
**Resolved by:** T1C (commit 72e58e3a); closure verified via test_openmeteo_p_raw_lineage_does_not_write_tigge_training_pair.

---

### [CLOSED — 2026-05-05] Final SDK submission envelope is not persisted after CLOB submit

**Location:** `src/execution/executor.py::_persist_final_submission_envelope_payload`,
`src/state/venue_command_repo.py::insert_submission_envelope`.
**Original problem:** SDK-produced envelope was transient; no durable append-only persistence.
**Structural closure:** T1G audit (sdk_envelope_path_audit.md) verified 7 VERIFIED_PERSISTS sites where `_persist_final_submission_envelope_payload()` is called (lines 1609, 2291, and 5 vault paths). SDK response flows into `insert_submission_envelope()`. Persistent, linked, and audited.
**Resolved by:** T1G (commit 3bcd8778); closure verified via sdk_envelope_path_audit.md + test_final_sdk_envelope_persistence.

---

### [CLOSED — 2026-05-05] Harvester live settlement write is HIGH-only for LOW markets

**Location:** `src/execution/harvester.py::_write_settlement_truth`,
`src/execution/harvester.py::_lookup_settlement_obs`, `src/execution/harvester.py::run_harvester`.
**Original problem:** Settlement path only handled HIGH metric; LOW markets would be written as HIGH.
**Structural closure:** T1C updated `_write_settlement_truth()` to accept `temperature_metric` parameter. `_lookup_settlement_obs()` also accepts `temperature_metric` and routes via `_metric_identity_for()`. Metric-aware observation lookup and settlement routing are now in place. HIGH and LOW markets resolve with correct observation fields.
**Resolved by:** T1C (commits 72e58e3a + calibration-transfer path); closure verified via test_harvester_settlement_redeem.

---

### [CLOSED — 2026-05-05] Settlement observation lookup ignores authority, station, and metric identity

**Location:** `src/execution/harvester.py::_lookup_settlement_obs`.
**Original problem:** Query did not enforce VERIFIED authority, station match, or metric field presence.
**Structural closure:** T1C+T1BD added explicit checks: `authority == 'VERIFIED'`, `station_id` match via `_station_matches_city()`, and metric_field via `metric_identity.observation_field`. All three constraints are now enforced in the query loop. Quarantined observations are filtered.
**Resolved by:** T1C (commit 72e58e3a) + T1BD authority freeze; closure verified via test_harvester_learning_authority + test_chain_reconciliation_corrected_guard.

---

### [CLOSED — 2026-05-05] Calibration maturity edge-threshold multiplier is dead on the live path

**Location:** `src/engine/evaluator.py::evaluate_candidate`.
**Original problem:** The multiplier was declared in manager.py but never applied before FDR/edge selection.
**Structural closure:** PR #61 added explicit `maturity_multiplier = edge_threshold_multiplier(cal_level)` calculation at evaluator.py line ~1625. If `cal_level >= 4`, entry is blocked at `CALIBRATION_IMMATURE` stage BEFORE edge/FDR selection. Multiplier is also passed to `compute_alpha()`. Previously dead gate now enforces maturity constraints.
**Resolved by:** PR #61 (commit cf177799); closure verified via test_evaluator_phase_beta_wiring + test_market_analysis_transfer_uncertainty.

---

### [CLOSED — 2026-05-05] Settled pending-exit exposure can be skipped indefinitely

**Location:** `src/execution/harvester.py::_settle_positions`.
**Original problem:** Positions in `pending_exit` were skipped unless `exit_state == "backoff_exhausted"`.
**Structural closure:** T1C updated skip condition logic: `pending_exit_at_settlement = state_name == "pending_exit"` now ALLOWS settlement if in that state (not blanket skip). Settlement logic walks through all non-terminal states including `pending_exit`. Additionally, P6 phase-check via `position_current` DB table added for idempotency.
**Resolved by:** T1C (commit 72e58e3a); closure verified via test_settle_positions_uses_enqueue_redeem.

---

### [CLOSED — 2026-05-05] Filled-command idempotency collision can rematerialize without order id

**Location:** `src/execution/executor.py::_orderresult_from_existing`.
**Original problem:** FILLED collision path set `external_order_id` but not `order_id`, causing silent order-id loss on recovery.
**Structural closure:** T2G updated both ACKED and FILLED branches in `_orderresult_from_existing()` to set `order_id=existing.venue_order_id` in addition to `external_order_id`. Order ID is now preserved through idempotency collision recovery.
**Resolved by:** T2G (commit ca48d7f9); closure verified via test_settle_positions_uses_enqueue_redeem + test_harvester_settlement_redeem.

---

### [CLOSED — 2026-05-05] Exit partial fills do not reduce local position exposure

**Location:** `src/execution/exit_lifecycle.py::check_pending_exits`,
`src/execution/exit_lifecycle.py::_apply_partial_exit_fill`.
**Original problem:** PARTIAL exit status was observed but not materialized; local shares remained unchanged.
**Structural closure:** T1C added `_apply_partial_exit_fill()` helper which now triggers on PARTIAL status, reducing `position.effective_shares`, updating `cost_basis_usd` with remaining-ratio adjustment, and marking `exit_state='sell_pending'`. Remaining unsold shares are queued for retry. Partial fills now reduce local exposure immediately.
**Resolved by:** T1C (commit 72e58e3a); closure verified via test_exit_safety.py + test_lifecycle.py partial-fill coverage.

---

## 2026-05-08 — Active known-gaps realignment closures

These entries were still visible as active or contradictory notes in
`docs/to-do-list/known_gaps.md`. The 2026-05-08 realignment rechecked them
against current source/tests/docs and moved the closed items here so the active
worklist only carries live gaps, residuals, deferred work, or unknowns.

### [CLOSED — 2026-05-08] Production executable snapshot producer/refresher exists for entry and held exits

**Original active heading:** `[OPEN P1] No production executable snapshot producer/refresher was found`.
**Original problem:** Earlier static inventory found snapshot gates but no live
producer/refresher for production entry or held-position exit paths.
**Structural closure:** Entry snapshot capture/threading exists, and held exits
can reuse a fresh snapshot or capture a fresh VERIFIED Gamma + CLOB executable
snapshot before sell intent creation through `exit_lifecycle`.
**Evidence:** `src/execution/exit_lifecycle.py` captures exit snapshots when no
fresh row exists; `src/data/market_scanner.py` owns executable snapshot capture;
the active worklist now keeps only staged dry-run evidence as a deploy proof,
not a code-path missing-producer gap.
**Residual:** live unlock still needs staged scan -> snapshot -> command
evidence, but that is deploy proof, not an active missing-code-path blocker.

### [CLOSED — 2026-05-08] Effective RED now triggers sweep even without `force_exit_review`

**Original active heading:** `[OPEN P1] Fail-closed RED causes do not trigger force-exit sweep`.
**Original problem:** Earlier cycle gating appeared to sweep only when
`get_force_exit_review()` was true, leaving infrastructure RED states as
entry-block-only.
**Structural closure:** Current `cycle_runner` triggers the local RED sweep on
effective `risk_level == RiskLevel.RED`, including stale/no-row/DB-error RED
paths that do not set the daily-loss force-exit flag.
**Evidence:** `src/engine/cycle_runner.py` RED branch checks effective risk
level; `tests/test_riskguard_red_durable_cmd.py` covers RED sweep behavior when
`force_exit_review` is false.
**Residual:** direct venue cancel/sell side-effect SLA remains active in
`known_gaps.md`; this closure only covers local RED sweep invocation.

### [CLOSED — 2026-05-08] ORANGE has a favorable-exit path distinct from YELLOW

**Original active heading:** `[OPEN P2] ORANGE risk currently behaves like entry-block-only YELLOW`.
**Original problem:** ORANGE appeared to block entries while leaving held
positions to ordinary monitor logic, making it indistinguishable from YELLOW.
**Structural closure:** Current runtime has `_orange_favorable_exit_decision`
and invokes it during monitoring, with tests covering favorable and unfavorable
cases.
**Evidence:** `src/engine/cycle_runtime.py::_orange_favorable_exit_decision`;
`tests/test_runtime_guards.py` ORANGE favorable-exit coverage.

### [CLOSED — 2026-05-08] Collateral preflight rejects stale snapshots

**Original active heading:** `[OPEN P2] Collateral preflight accepts arbitrarily stale snapshots`.
**Original problem:** Buy/sell preflight could pass against numerically
sufficient but stale account truth.
**Structural closure:** `CollateralLedger` buy/sell preflight now asserts
snapshot freshness and raises on stale or future timestamps before command or
SDK boundaries.
**Evidence:** `src/state/collateral_ledger.py::_assert_snapshot_fresh` is called
from both buy and sell preflights.

### [CLOSED — 2026-05-08] ENS local-day non-finite values fail closed before posterior/edge construction

**Original active heading:** `[OPEN P1] ENS local-day NaNs can pass validation and create false posterior edges`.
**Original problem:** NaNs inside the selected local-day slice could propagate
into all-zero probabilities and false posterior edges.
**Structural closure:** Local-day values and member extrema are now checked for
finite values before p_raw/posterior use.
**Evidence:** `src/signal/ensemble_signal.py` finite checks around local-day
values and member extrema; model/vector tests remain the regression antibody.

### [CLOSED — 2026-05-08] Day0 stale/epoch observations cannot silently become tradeable fresh `p_raw`

**Original active heading:** `[OPEN P1] Day0 stale/epoch observations can still produce tradeable p_raw`.
**Original problem:** WU epoch timestamps and sparse/stale provider samples
could degrade freshness while still producing tradeable Day0 probabilities.
**Structural closure:** Executable Day0 observations are source-bound, WU epoch
timestamps parse as fresh when current, and stale or coverage-invalid Day0
observations block entry or degrade monitor refresh to stale/read-only evidence.
**Evidence:** Day0 runtime observation context tests and live-safety invariant
tests cover fresh epoch parsing, stale entry blocking, and monitor degradation.

### [CLOSED — 2026-05-08] HK HKO settlement rounding uses containment/truncation, not WMO half-up

**Original active heading:** `[OPEN] HK: SettlementSemantics uses WMO half-up, but PM resolution uses floor`.
**Original problem:** HKO raw values are decimal Celsius and Polymarket bin
containment maps values such as 27.8°C into the 27°C bin, unlike WMO half-up.
**Structural closure:** HKO settlement semantics now use the HKO-specific
`oracle_truncate` rounding rule instead of generic WMO half-up.
**Evidence:** `src/contracts/settlement_semantics.py` selects
`rounding_rule="oracle_truncate"` for HKO; `tests/test_hk_settlement_floor_rounding.py`
covers HK containment examples.
**Residual:** HK 03-13/03-14 source-audit evidence remains active; do not add a
WU/VHHH alias to close that separate source-authority gap.

### [CLOSED — 2026-05-08] Paris LFPG/LFPB source mismatch is no longer an active blocker

**Original stale note:** active overlay text still said Paris was excluded/open.
**Original problem:** Paris markets transitioned from LFPG to LFPB and source
config/backfill/calibration needed conversion.
**Structural closure:** The conversion is already archived as closed: Paris
config uses LFPB, LFPG legacy rows were removed/quarantined as applicable,
LFPB rows were backfilled, and Platt models were refit.
**Evidence:** `docs/operations/current_source_validity.md` records the completed
Paris conversion; the closed record lives above as
`[CLOSED P1 — 2026-05-03] Paris config uses LFPG while current markets resolve on LFPB`.

### [CLOSED — 2026-05-08] ACP router fallback chain is out of Zeus runtime scope

**Original active heading:** `[OPEN] ACP router fallback chain is recovering after failure, not stabilizing before dispatch`.
**Original problem:** An OpenClaw/ACP routing stack could dispatch before
allowlist/auth/timeout prechecks.
**Closure:** Repo search found only docs/packet references and no Zeus `src/` or
`tests/` consumer. This is not a Zeus live-money runtime gap.
**Disposition:** Keep any real ACP repair in its owning routing repo; do not
carry it in Zeus `known_gaps.md`.

### [CLOSED — 2026-05-08] T1E rebuilt calibration scope now carries an exact completion sentinel

**Original active heading:** `T1E C-3 — rebuild_calibration_pairs_v2 partial-commit semantic (LOW)`.
**Original problem:** `scripts/rebuild_calibration_pairs_v2.py` commits each
`(city, metric)` bucket independently. An interrupted rebuild could leave
already-committed buckets beside missing buckets, and a downstream opener had no
authoritative exact-scope marker distinguishing complete from partial rebuilt DB
state.
**Structural closure:** live rebuild writes now overwrite the exact rebuild
scope in `zeus_meta` to `in_progress` before the first bucket commit and mark it
`complete` only after all post-write gates pass. `assert_rebuild_complete_sentinel()`
fails closed when the exact-scope sentinel is absent or still in-progress.
Live `refit_platt_v2.py` checks that sentinel before it can promote
`calibration_pairs_v2` rows into `platt_models_v2`; dry-run remains
non-promoting inspection. Narrow refit scopes may use a covering all-scope
complete sentinel, but any overlapping `in_progress` sentinel blocks promotion
before exact or covering completion is accepted. The refit/rebuild consumer also
validates sentinel payload metric identity, bin source, scope, and `n_mc` against
the key it is consuming, so a contaminated `zeus_meta` row cannot authorize a
different rebuilt object by carrying only `status="complete"`.
Post-critic hardening extends the same completion proof to calibration-transfer
OOS evidence: non-dry-run `evaluate_calibration_transfer_oos.py` skips
cross-domain writes when the target route lacks a complete rebuild sentinel,
and `evaluate_calibration_transfer_policy_with_evidence()` refuses to turn an
existing `validated_calibration_transfers` row into `LIVE_ELIGIBLE` unless the
target cohort's `calibration_pairs_v2` source is still covered by a complete
rebuild sentinel. Any overlapping in-progress sentinel fails closed so an old
covering complete row cannot mask a current partial rebuild.
**Evidence:** `scripts/rebuild_calibration_pairs_v2.py` sentinel helpers,
`scripts/refit_platt_v2.py` live-refit gate, and
`tests/test_rebuild_live_sentinel.py` relationship tests prove
in-progress-before-bucket, no complete marker after validation failure,
fail-closed consumer behavior, resolved `n_mc` provenance, covering-scope
semantics, payload self-consistency, overlapping-in-progress blocking, and no
Platt model promotion from an incomplete source scope.
`tests/test_evaluate_calibration_transfer_oos.py` and
`tests/test_calibration_transfer_policy_with_evidence.py` prove the OOS writer
and reader cannot materialize live-eligible transfer evidence from a partial or
unproven target rebuild scope.
**Residual:** This does not authorize running rebuild during live trading or
swap a rebuilt DB into live truth. It only makes partial rebuilt scope detection
machine-checkable.

### [CLOSED — 2026-05-08] M5 position drift separates confirmed journal truth from optimistic exposure

**Original active heading:** `[MITIGATED 2026-04-30; RESIDUAL P2] M5 exchange reconciliation no longer promotes non-final trades to filled commands`.
**Original residual:** command finality was repaired, but
`_journal_positions_by_token()` still counted `MATCHED`, `MINED`, and
`CONFIRMED` together in the drift comparison surface, allowing an optimistic
trade fact to be treated as the same position object as confirmed journal truth.
**Structural closure:** position drift now builds separate confirmed and
optimistic journals. Exchange position comparison uses only latest
`CONFIRMED` trade facts; evidence separately reports `confirmed_journal_size`,
back-compatible `journal_size`, `optimistic_journal_size`, and both evidence
classes.
**Evidence:** `src/execution/exchange_reconcile.py::_record_position_drift_findings`
and `tests/test_exchange_reconcile.py::test_position_drift_compares_exchange_to_confirmed_not_optimistic`.
The economic-drift lifecycle test also proves a blocked confirmed transition can
emit both the trade-lifecycle finding and a confirmed-journal position drift
instead of silently accepting the optimistic MATCHED exposure as final truth.
**Residual:** This is read-only reconciliation evidence. It does not mutate
venue state or promote optimistic lots to confirmed positions.

### [CLOSED — 2026-05-08] `hourly_observations` compatibility surface is no longer constructible or scheduled

**Original active heading:** `[LOW] hourly_observations is dead — schedule deletion`.
**Original problem:** `hourly_observations` was a lossy local-hour compatibility
table populated by `scripts/etl_hourly_observations.py` from
`observation_instants_current`, with an evidence view
`v_evidence_hourly_observations`. Runtime consumers had already moved to
`observation_instants_v2`/`observation_instants_current`, but schema init,
ingest/backfill hooks, tests, and linter exceptions still preserved the stale
object as if it were a valid evidence surface.
**Structural closure:** Wave38 removes the table/view DDL from `src/state/db.py`,
deletes `scripts/etl_hourly_observations.py`, removes ingest/backfill/assumption
hooks, removes manifest/topology references to the deleted writer, and converts
the semantic linter from compatibility-allowlist mode to fail-closed
no-reintroduction mode for both `hourly_observations` and
`v_evidence_hourly_observations`.
**Evidence:** `tests/test_architecture_contracts.py` now asserts `init_schema()`
does not create the table or evidence view. `tests/test_semantic_linter.py`
proves deleted table/view reads/DDL and the old writer path are rejected, and
that runtime `src/`/`scripts/` code does not reintroduce the strings outside the
linter itself. `tests/test_structural_linter.py` asserts the deleted ETL script
stays absent. `tests/test_world_writer_boundary.py` removes the writer from the
world-DB write allowlist.
**Residual:** Physical legacy tables/views may still exist inside already
initialized DB files. Dropping those is a destructive data-layer operation and
is tracked as an active `OPERATOR_DECISION_REQUIRED` known gap with dry-run,
backup, rollback, and dependency-audit requirements.

### [CLOSED — 2026-05-08] `solar_daily` malformed rootpage degrades Day0 authority

**Original active heading:** `[STALE-UNVERIFIED] CycleRunner fails on malformed solar_daily schema rootpage`.
**Original problem:** A legacy diagnostic cycle had seen SQLite raise
`malformed database schema (solar_daily) - invalid rootpage` during a Day0 path,
and the open question was whether that malformed DB object could crash the
cycle or be treated as valid solar/DST context.
**Structural closure:** Wave39 verified the existing runtime shape: a
`solar_daily` read failure is not a valid `SolarDay`; it returns no Day0
temporal context. Evaluator Day0 candidates degrade to `DATA_STALE` before
probability-vector construction, and held-position monitor refresh preserves
the prior posterior with `missing_solar_context` instead of producing fresh
probability.
**Evidence:** `tests/test_diurnal.py::test_build_day0_temporal_context_degrades_on_malformed_solar_daily_rootpage`
injects the historical SQLite error string and asserts no Day0 temporal context
is produced. `tests/test_runtime_guards.py::test_day0_observation_path_rejects_missing_solar_context`
asserts evaluator rejection is `SIGNAL_QUALITY` / `DATA_STALE`.
`tests/test_runtime_guards.py::test_day0_monitor_refresh_degrades_on_malformed_solar_daily_rootpage`
asserts monitor refresh returns the prior posterior, marks probability stale,
and emits `missing_solar_context`.
**Residual:** This is behavior verification, not DB repair. If a canonical DB
file physically contains a corrupt rootpage, physical repair still requires a
separate operator-approved data-layer packet with inventory, backup, dry-run,
and rollback. No such mutation was run.
