# Known Gaps — Antibody Archive

This file is the immune-system record: gaps that were resolved by code, type,
or test antibodies, kept for traceability when a similar pattern resurfaces.
Active gaps live in `known_gaps.md`.

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
**Location:** `zeus/state/control_plane-paper.json`, `zeus/state/status_summary-paper.json`, status renderer
**Problem (historical):** The rendered paper summary previously exposed a stale duplicate gate field for `opening_inertia` that disagreed with the control plane.
**Resolution:** The current paper snapshot no longer shows that split-brain state. `status_summary-paper.json` no longer presents the old stale `strategy_summary.gated` conflict in the latest snapshot, and the canonical gate authority remains the control plane.
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
**Antibody deployed (2026-04-24, T6.3 Option C, commit `6f53ef2`):** `VigTreatment.from_raw(p_market, *, sibling_snapshot=None, imputation_source="none")` gains impute path — when raw market has zero bins and a sibling_snapshot (e.g., p_cal) is supplied, zeros are filled from the sibling and the record carries `imputed_bins: tuple` + `imputation_source: Literal["none","sibling_market","p_cal_fallback"]`. The sparse branch of `compute_posterior` now routes through this with `imputation_source="p_cal_fallback"`. Silent revival of pre-B086 policy is structurally impossible — typed-visible on the VigTreatment record. Archive supersedence recorded at `docs/archives/local_scratch/2026-04-19/zeus_data_improve_bug_audit_100_resolved.md:17` (local-only; durable record in `docs/operations/task_2026-04-23_midstream_remediation/T6_receipt.json` + `src/contracts/vig_treatment.py` module docstring).
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
- `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`
- `docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json`
**Invalidation:** only a fresh gamma-api probe with HTTP-evidence showing LOW events with endDate < 2026-04-15 OR coverage beyond 8 cities may relax this.

### [CLOSED — 2026-04-30; archived from MITIGATED] strategy_tracker no longer reports JSON PnL as independent truth
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, runtime strategy
tracking was DB/event-derived and no longer wrote or trusted JSON projection
authority; legacy JSON references were fixture/history hygiene.
**Location:** `src/state/strategy_tracker.py`, legacy `strategy_tracker-*.json` artifacts.
**Original problem:** `strategy_tracker-paper.json` could report `opening_inertia`
cumulative PnL that was not reconstructible from durable DB/event truth.
**Antibody deployed:** `StrategyTracker` is now a no-write canonical projection:
`record_*` methods are no-op compatibility shims, `save_tracker()` does not
write disk, `load_tracker()` ignores legacy files, and `summary()` derives from
`query_authoritative_settlement_rows()` / `position_events`.
**Evidence:** `src/state/strategy_tracker.py` module contract and no-op
`record_*` / `save_tracker()` implementations.
**Residual:** Legacy `strategy_tracker-paper.json` references in tests or
historical artifacts remain fixture/archive surfaces. They should not be used as
runtime or wallet truth.

### [CLOSED — 2026-04-30; archived from MITIGATED] Paper JSON fallback no longer becomes portfolio authority
**Archived from active register:** 2026-04-30.
**Archive rationale:** As of the 2026-04-30 archive cutover, portfolio loading
was DB-first and degraded instead of promoting JSON; residual references were
fixtures/history, not live authority.
**Location:** `src/state/portfolio.py::load_portfolio`,
`src/engine/cycle_runtime.py`, `src/riskguard/riskguard.py`.
**Original problem (filed 2026-04-10):** Paper positions with missing token ids
could make canonical projection non-authoritative, after which
`load_portfolio()` could fall back to stale JSON and let RiskGuard reason over a
broken paper portfolio as if it were current truth.
**Antibody deployed:** `load_portfolio()` is DB-first. If the DB connection or
projection is not authoritative, it returns a degraded `PortfolioState`
(`authority="unverified"`, `portfolio_loader_degraded=True`) and suppresses new
entries; it does not promote deprecated JSON into authority. Chain-only
quarantine evidence can still be rehydrated explicitly.
**Evidence:** `src/state/portfolio.py::load_portfolio`,
`tests/test_runtime_guards.py::test_load_portfolio_db_connection_failure_ignores_corrupt_json_and_degrades`,
`test_load_portfolio_treats_empty_projection_as_canonical_despite_legacy_json`,
and `tests/test_truth_layer.py::test_load_portfolio_rejects_deprecated_state_file`.
**Residual:** `positions-paper.json` remains in legacy fixtures/history and
truth-surface stale-status tests. Those references are not live portfolio
authority and can be cleaned in a separate test/docs hygiene packet.
