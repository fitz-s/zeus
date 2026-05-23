# Live Money-Path Risk Audit — 2026-05-22

Status: generated evidence / Authority: false

---

## Coverage Statement

Files read in full or partial audit:

| File | Coverage |
|------|----------|
| `src/main.py` | Full (scheduler wiring, _scheduler_job, locks) |
| `src/engine/cycle_runner.py` | Full (imports, get_connection, KNOWN_STRATEGIES, dispatch) |
| `src/engine/cycle_runtime.py` | Full (all helpers: ~5200 lines read across all key sections) |
| `src/engine/evaluator.py` | Full (structure, key gates: ~4900 lines read) |
| `src/data/executable_forecast_reader.py` | Full |
| `src/data/forecast_source_registry.py` | Partial (SOURCES dict, spec, gate logic) |
| `src/data/ensemble_client.py` | Partial (fetch_ensemble, cache, retry) |
| `src/signal/model_agreement.py` | Full |
| `src/strategy/market_analysis.py` | Partial (EdgeScanTrace, find_edges bootstrap path) |
| `src/strategy/family_exclusive_dedup.py` | Full |
| `src/contracts/execution_intent.py` | Partial (types, forbidden recompute inputs) |
| `src/execution/executor.py` | Partial (MODE_TIMEOUTS, gates) |
| `src/execution/command_recovery.py` | Partial (strategy key map) |
| `src/execution/exchange_reconcile.py` | Partial (schema, finding kinds) |
| `src/execution/settlement_commands.py` | Partial (schema, state machine) |
| `src/engine/monitor_refresh.py` | Partial (structure, whale toxicity, divergence) |
| `src/execution/harvester.py` | Partial (economics, P&L chain) |
| `src/contracts/no_trade_reason.py` | Full |
| Supporting: `docs/operations/task_2026-05-22_crosscheck_valid_window/CROSSCHECK_VALID_WINDOW_PLAN.md` | Full |
| Supporting: `docs/operations/task_2026-05-22_live_math_frontier/PLAN.md` | Full |

---

## Risk Heatmap by Frontier

| Frontier | Severity floor | Open findings |
|----------|----------------|---------------|
| source | Critical | 1 (valid-window null fallback; active blocker) |
| math/signal | Important | 3 (market-level CONFLICT pre-edge hard kill; Day0 HIGH max semantics; GFS MC probability space) |
| strategy/family | Important | 2 (command_recovery hardcoded strategy keys; family_selection_dedup/blocked split string heuristic) |
| execution/lifecycle | Important | 1 (no-trade attribution: `strategy_key` not propagated through dedup-rejected decisions) |
| settlement | Nit/Uncertain | 1 (REDEEM_OPERATOR_REQUIRED not reflected in exchange_reconcile reconcile context) |
| scheduler | Nit | 1 (cycle_lock skip not counted in rejection_reason_counts) |
| debugability | Important | 3 gaps (see Section 5) |

---

## Findings

### F1 — CRITICAL | source | `src/data/executable_forecast_reader.py:769-770`

**What:** When the forecast source_run_coverage row has `target_window_start_utc` or `target_window_end_utc` as NULL or absent in the DB (e.g., older ingest runs), `_parse_utc(...) or now` silently substitutes `now` (the query time) for the missing window bound. This flows through `ExecutableForecastEvidence.target_window_start_utc`/`target_window_end_utc` and then into `ExecutableForecastBundle.to_ens_result()`, which exports these as `target_day_valid_window`, `target_window_start_utc`, and `target_window_end_utc` in the `ens_result` dict passed to `_explicit_target_day_valid_window()` in the evaluator.

**Why it matters:** `_crosscheck_comparable_context()` at evaluator line 1863-1866 calls `_forecast_valid_window_for_target_day()` on both primary and crosscheck. If primary has `("", "")` — which happens when `now` is passed as start/end and `_parse_utc` + `_forecast_window_label` returns non-ISO strings — the comparability gate emits `primary_missing_target_day_valid_window` and returns `comparable=False`, which hard-blocks the candidate with `SOURCE_COMPARABILITY_FAILED`. This is the current active live blocker (confirmed by CROSSCHECK_VALID_WINDOW_PLAN.md).

**Suggested direction:** `_target_day_valid_window_from_coverage()` already correctly returns `("", "")` when `start` or `end` is None (lines 188-189). The bug is at the call site (reader line 769-770): fall back to empty string or `None` rather than `now`. The fix-crosscheck branch addresses this; verify the coverage-row query path writes non-NULL windows before using the `or now` path.

**Evidence:** `src/data/executable_forecast_reader.py:769-770`; `CROSSCHECK_VALID_WINDOW_PLAN.md §Broken Relationship`; evaluator `_crosscheck_comparable_context` line 1896.

---

### F2 — Important | math/signal | `src/engine/evaluator.py:4362-4378`

**What:** The market-level `analyze_model_agreement()` call (line 4362) does NOT pass `candidate_support_index`, so it is called with `candidate_support_index=None`. The `model_agreement.py` `analyze_model_agreement()` function defaults `require_candidate_support_for_conflict=True` — but when `candidate_support_index is None`, `candidate_supported_by_crosscheck` is `None`, and the conflict-gate logic evaluates `candidate_not_supported = not require_candidate_support_for_conflict = False`, effectively requiring physical temperature separation only. This means CONFLICT at market level (before edge scan) requires physical separation (≥2°F gap in expected value OR mode temperature), which is reasonable — but the edge scan (line 4866) passes `candidate_support_index=bin_idx` correctly. The risk is the two-gate structure: market-level CONFLICT hard-kills the entire candidate (line 4399-4419) before any edge is scanned; the edge-level re-check (line 4868) is never reached if the market-level gate fires.

**Why it matters (likely addressed in-flight):** PLAN.md §"Delay hard model-conflict rejection until edge support is known" documents this as an intended in-flight fix. Marking as Important/verified-in-flight. The existing market-level gate does NOT kill based on candidate support since `candidate_support_index=None` → `candidate_supported_by_crosscheck=None` → falls to the physical-separation-only branch. So actual behavior is slightly safer than the spec worry: CONFLICT requires physical separation even at market level. However, `candidate_support_floor` is ignored entirely at this level — a market with high JSD + 2°F gap but crosscheck supporting the target bin would still be CONFLICT-killed. This is conservative but may over-reject on tail bins.

**Suggested direction:** Per in-flight PLAN.md: move global-conflict to haircut/evidence before edge scan; reject only edge-unsupported candidates inside the edge loop. Verify when fix-model-conflict-frontier branch lands.

**Evidence:** evaluator line 4362 (no `candidate_support_index`); model_agreement.py lines 180-188; evaluator 4399-4419 (market-level CONFLICT return before find_edges).

---

### F3 — Important | math/signal | `src/engine/evaluator.py:4322-4326` / `src/signal/model_agreement.py`

**What:** GFS crosscheck p-vector is built via `p_raw_vector_from_maxes(gfs_metric_values, ...)` using Open-Meteo hourly members filtered to local-day TZ hours — this uses the raw hourly GFS member extrema for the target day. ECMWF primary uses `period_extrema_members` (pre-computed local calendar day extrema from the executable snapshot). These two probability objects may have different MC/noise layers applied: primary goes through `EnsembleSignal` with full MC instrument noise; GFS uses `p_raw_vector_from_maxes` which also applies MC noise (via `ensemble_n_mc()`). However, GFS uses direct hourly member arrays while ECMWF uses per-member day-level extrema — the two sampling distributions are not necessarily comparable on multi-day lead targets.

**Why it matters (likely addressed in-flight):** The PLAN.md §"Replace GFS direct member-count crosscheck with the shared MC probability generator over target-day extrema" documents this as an in-flight fix. Flagging for verification when fix-evaluator-signal-strategy-semantics lands.

**Suggested direction:** Use the same `EnsembleSignal` path (period_extrema → p_raw_vector_from_maxes with identical MC parameters) for both primary and crosscheck. Verify comparability: crosscheck window must equal primary window before probability-space comparison.

**Evidence:** evaluator lines 4322-4333; in-flight PLAN.md §"GFS crosscheck probability must use the same MC/noise/settlement probability space as the primary vector."

---

### F4 — Important | math/signal | `src/engine/evaluator.py` (Day0 HIGH path)

**What:** Day0 HIGH `p_vector` sampling at the evaluator uses `Day0HighNowcastSignal` (imported at line 84-87). Per PLAN.md invariants: "Day0 HIGH `p_vector` must use the documented physical max semantics: final high samples are `max(observed_high_so_far, remaining_high)`." If residual compression is retained, it must be named and governed as nowcast, not settlement truth. The evaluator does NOT currently check whether the returned nowcast vector is observation-locked (Day0 truth where max(observed) >= all remaining) vs. a nowcast (still uncertain).

**Why it matters:** A Jeddah-shaped candidate (34°C observed, 36°C target bin, remaining forecast > 34) would reach sizing and execution with `settlement_capture` strategy classification even though the high outcome is not yet observed-locked — creating a misrepresented strategy_key in the command record and calibration pair. The PLAN.md identifies this as an explicit in-flight fix.

**Suggested direction:** Add Day0 truth classification evidence to the evaluator decision: if target bin is above current observed_high, classify as `day0_nowcast_entry` (or reject as `settlement_capture`). Verify against fix-evaluator-signal-strategy-semantics branch.

**Evidence:** PLAN.md §"Day0 HIGH probability generation must match the documented physical hard-floor semantics"; evaluator line 2235-2340 (day0_truth classification paths).

---

### F5 — Important | strategy/family | `src/execution/command_recovery.py:84-91`

**What:** `command_recovery.py` has a hardcoded `_CANONICAL_STRATEGY_KEYS = frozenset({"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"})` at module level, plus a `_LEGACY_STRATEGY_KEY_ALIASES` dict. This is the same anti-pattern that `cycle_runner.py` and `cycle_runtime.py` already fixed by routing through `strategy_profile.live_safe_keys()`. If a new strategy is added to the registry (e.g., `day0_nowcast_entry` as a distinct live key), command recovery would silently misclassify its commands as unknown-strategy-key at lines 1452 and 1537.

**Why it matters:** Recovery decisions for orders under new strategy keys would fall into the fallback path. Not an immediate blocker (current live strategies match the hardcoded set), but is structural drift that will bite on the next strategy addition.

**Suggested direction:** Replace the hardcoded frozenset with `from src.strategy.strategy_profile import live_safe_keys; _CANONICAL_STRATEGY_KEYS = live_safe_keys()` at import time, mirroring the pattern in `cycle_runner.py`.

**Evidence:** command_recovery.py lines 84-91, 1452, 1537.

---

### F6 — Important | execution/lifecycle | `src/engine/cycle_runtime.py:4291-4310`

**What:** The `family_selection_dedup` vs. `blocked_existing_family_exposure` frontier counter split (lines 4291-4310) relies on the string `"existing family exposure"` appearing in `rejection_reason_detail`. This string comes from `dedup_mutually_exclusive_families()` in `family_exclusive_dedup.py`. If the detail string changes (even a minor refactor) the counter split silently breaks — `blocked_existing_family_exposure` would return 0 even when exposures are present, and `family_selection_dedup` would over-count. The `_current_active_blocker_hypothesis` at line 3314 reads `blocked_existing_family_exposure` directly for the "family_exposure_block" classification.

**Why it matters:** If existing open positions in a family are silently dropped from the `blocked_existing_family_exposure` count, `_current_active_blocker_hypothesis()` never returns `"family_exposure_block"` and the operator's diagnostic trail misses that live positions are blocking new entries.

**Suggested direction:** Replace the string-heuristic split with a typed field on `EdgeDecision` (e.g., `family_block_kind: Literal["preselection_dedup", "existing_exposure"] | None`) so the counter is authoritative rather than string-grep-dependent.

**Evidence:** cycle_runtime.py lines 4291-4310; family_exclusive_dedup.py MUTUALLY_EXCLUSIVE_FAMILY_DEDUP constant.

---

### F7 — Important | execution/lifecycle | `src/engine/cycle_runtime.py:4356-4358`

**What:** When `dedup_mutually_exclusive_families()` drops a sibling bin (setting `should_trade=False` on an already-evaluated `EdgeDecision`), the resulting `rejection_reason_enum=NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP`. However, the `strategy_key` on such a decision may be empty string (`""`) or the strategy key is not preserved through the dedup rewrite path, because `dedup_mutually_exclusive_families()` returns modified EdgeDecision objects without carrying strategy_key attribution from the dropped edge. At the no_trade_event write (line 4356), `str(getattr(_nd, "strategy_key", "") or "")` may be empty for dedup-dropped decisions.

**Why it matters:** No-trade attribution for `MUTUALLY_EXCLUSIVE_FAMILY_DEDUP` events loses the strategy_key, making it impossible to distinguish which strategy's edge was dropped (e.g., center_buy vs. shoulder_sell sibling). PLAN.md §"Split `family_selection_dedup` from `blocked_existing_family_exposure`" addresses this.

**Suggested direction:** Ensure `dedup_mutually_exclusive_families()` preserves `strategy_key` on the dropped EdgeDecision, or set it explicitly when building the rejection. Verify when fix-model-conflict-frontier lands.

**Evidence:** family_exclusive_dedup.py lines 78-120 (FamilyPreselectionDrop, no strategy_key field); cycle_runtime.py line 4356.

---

### F8 — Nit | settlement | `src/execution/settlement_commands.py` / `src/execution/exchange_reconcile.py`

**What:** `REDEEM_OPERATOR_REQUIRED` state is listed as a "designed-terminal-with-operator-action" state in settlement_commands.py (line 93-96). However, `exchange_reconcile.py` defines `_REDEEM_PENDING_WALLET_HOLDING_STATES` (line 72-80) which includes `REDEEM_OPERATOR_REQUIRED`. The exchange reconcile sweep can therefore detect a position as "pending wallet holding" even when the operator CLI has not yet acted, but the sweep's `ReconcileContext` values do not include an `operator_required` context — reconcile findings for these positions would be logged as `periodic` context, which may mislead triage. Low risk for now as redemption volume is low.

**Suggested direction:** Add `operator_required` to `ReconcileContext` or annotate `REDEEM_OPERATOR_REQUIRED` findings with a note that they are expected-pending states.

**Evidence:** settlement_commands.py lines 93-96; exchange_reconcile.py lines 72-80, 36-37 (ReconcileContext literals).

---

### F9 — Nit | scheduler | `src/main.py:104-113`

**What:** The `_run_mode` wrapper skips cycles when `_cycle_lock` is busy, writing a `skipped=True` status but NOT incrementing any counter in `summary["rejection_reason_counts"]` or `summary["no_trades"]`. The `_current_active_blocker_hypothesis()` logic therefore never sees a "cycle_lock_busy" cause — from the operator's perspective, a cycle that was perpetually skipped due to long-running prior cycles looks identical to a cycle with no candidates.

**Why it matters:** Observability gap, not a money-risk. If two discovery modes are running with overlapping intervals, one is silently skipped with no attribution in the frontier.

**Suggested direction:** Write `cycle_lock_skip_count` to a persistent counter (via `_cnt_inc`) or to `scheduler_jobs_health.json` at the cycle level. The `_write_scheduler_health(... skipped=True)` call at line 107-112 writes per-job health but this does not aggregate into cycle-level frontier reports.

**Evidence:** main.py lines 104-113.

---

## Missing Frontier Counters / Observability Gaps

### Gap 1 — Source writer status is read-only at cycle start; no mid-cycle probe

`_source_writer_frontier_status()` is called once at cycle initialization (cycle_runtime.py line 3365-3368) before any candidate evaluation. If source health degrades mid-cycle (e.g., the ingest daemon stalls during a long evaluation budget), the frontier report shows the cycle-start status. For a 6-minute evaluation budget, this is a meaningful lag.

**Direction:** Add a `source_frontier.source_writer_status_at_close` field written after candidate evaluation completes, mirroring the existing pattern.

---

### Gap 2 — No per-candidate edge-scan-trace frontier counter

When `find_edges_with_trace()` returns zero edges (or FDR filters all), the `EDGE_SCAN_TRACE(...)` string is appended to `rejection_reasons`. This is readable in logs but is NOT machine-parseable for aggregation. There is no `math_frontier["yes_positive_raw_edge_count"]` or `math_frontier["no_ci_pass_count"]` counter.

**Direction:** Parse `EdgeScanTrace` objects into `math_frontier` integer counters inside `_edge_scan_trace_frontier_detail()` — yes_positive_raw_edge, no_ci_pass, no_quote_unavailable — so the cycle JSON is queryable without log parsing.

---

### Gap 3 — No frontier counter for `SOURCE_COMPARABILITY_FAILED` vs `CROSSCHECK_UNAVAILABLE`

Both `CROSSCHECK_UNAVAILABLE` and `SOURCE_COMPARABILITY_FAILED` collapse to `NoTradeReason.CROSSCHECK_UNAVAILABLE`, making them indistinguishable in `rejection_reason_counts`. During the active CROSSCHECK_VALID_WINDOW_PLAN fix, operators cannot determine from cycle JSON alone whether the failure is a window-missing issue or an actual data fetch failure.

**Direction:** Add a `math_frontier["source_comparability_failed"]` counter incremented specifically when `rejection_reason_detail.startswith("SOURCE_COMPARABILITY_FAILED")`.

---

## Current-Active-Blocker Hypothesis

Cross-check against `docs/operations/task_2026-05-22_crosscheck_valid_window/CROSSCHECK_VALID_WINDOW_PLAN.md`:

The plan confirms: **`primary_valid_window = ("", "")` because `ExecutableForecastBundle.to_ens_result()` does not correctly export the local-calendar-day UTC window when the coverage row has NULL `target_window_start_utc`/`target_window_end_utc`.**

Evidence path:
1. `read_executable_forecast_bundle()` at line 769-770: `_parse_utc(coverage.get("target_window_start_utc")) or now` — if the DB value is NULL, `now` is substituted.
2. This propagates to `ExecutableForecastEvidence.target_window_start_utc` as a datetime string of query time.
3. `_target_day_valid_window_from_coverage()` at line 182-191: if both `start` and `end` are valid timestamps, returns `(start.isoformat(), last_observed_hour.isoformat())`. This means the window is set to something like `("2026-05-22T10:00:00+00:00", "2026-05-22T10:59:00+00:00")` — the query time, not the forecast target day.
4. The evaluator's `_explicit_target_day_valid_window()` at line 1794-1808 reads this wrong window. The GFS crosscheck's window is derived differently (via `select_hours_for_target_date`), so the two windows will not match → `target_day_valid_window_mismatch` reason → `comparable=False` → `SOURCE_COMPARABILITY_FAILED`.

**The active blocker IS F1 (above).** The CROSSCHECK_VALID_WINDOW_PLAN.md fix-scope covers exactly this: preserve the explicit target-day UTC window in the bundle result from the actual coverage record, or fail-close with a distinct reason code if the coverage row is missing the window.

The fix-crosscheck branches (`fix-crosscheck-*/` referenced in the task brief) likely address this. Recommendation: verify the fix ensures that NULL coverage windows produce `("", "")` (forcing GFS fallback path) rather than `now`-based windows.

---

## Tests to Add

| Test | Frontier | Rationale |
|------|----------|-----------|
| Relationship test: `read_executable_forecast_bundle()` with NULL `target_window_start_utc` in coverage row returns `ExecutableForecastBundle` with `to_ens_result()["target_day_valid_window"] == ("", "")`, NOT a query-time window | source | Antibody for F1; prevents regression of the active blocker |
| Relationship test: `_crosscheck_comparable_context()` with primary `target_day_valid_window=("", "")` always sets `comparable=False` and reason `primary_missing_target_day_valid_window` | source/math | Antibody for the evaluator comparability gate relying on explicit windows |
| Relationship test: market-level `analyze_model_agreement()` (no `candidate_support_index`) with JSD > JSD_SOFT_DISAGREE + mode_gap > 1 but physical_temp_gap < 2°F returns `SOFT_DISAGREE`, not `CONFLICT` | math | Verify physical-separation gate behaves correctly at market level |
| Relationship test: `command_recovery.py` recovery path for a position with strategy_key `day0_nowcast_entry` (currently absent from `_CANONICAL_STRATEGY_KEYS`) does not silently fall into unknown-key fallback | strategy | Antibody for F5 drift |
| Relationship test: `dedup_mutually_exclusive_families()` with a decision that has `should_trade=False` due to existing family exposure preserves the original `strategy_key` on the returned decision | family | Antibody for F7 no-trade attribution loss |
| Relationship test: frontier counter `family_selection_dedup` increments for intra-cycle sibling drops; `blocked_existing_family_exposure` increments when an existing portfolio position blocks a new entry — the two counts are non-overlapping | family | Antibody for F6 string-heuristic split fragility |
| Relationship test: `_source_writer_frontier_status()` returns `source_data_fresh=False` and lists stale sources when `source_health.json` reports at least one `STALE` source; `observability_degraded=True` when writer age > 5 min | source | Observability correctness |
