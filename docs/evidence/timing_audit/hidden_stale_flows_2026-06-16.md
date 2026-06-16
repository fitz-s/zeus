# Hidden Ungated Stale-Data Flows — Beyond G1-G7
# Produced: 2026-06-16
# Worktree: timing-fixes (live/iteration-2026-06-13)
# Method: parallel grep + targeted Read across full src/ money path
# Context: freshness_fallback_map_2026-06-16.md already documents G1-G7. This report covers NEW flows ONLY.

---

## Priority-ranked findings (most-money-corrupting first)

---

### H1 — EMOS calibration table + σ-floor + μ-offset: loaded ONCE per process, no TTL, no reload
**Severity: CORRUPTS-EDGE (and consequently corrupts q_lcb, and size)**

**Files:**
- `src/calibration/emos.py:56-113` — `_emos_table_cache: dict | None = None`, loaded from `state/emos_calibration.json` via `load_emos_table()` on first call; never reloaded.
- `src/calibration/emos.py:64-172` — `_sigma_floor_cache`, loaded from `state/settlement_sigma_floor.json` on first call; never reloaded.
- `src/calibration/emos.py:76-303` — `_mu_offset_cache`, loaded from `state/emos_mu_offset.json` on first call; never reloaded.
- `src/calibration/anchor_representativeness_debias.py:55-74` — `_cache: dict | None = None`, loaded from its artifact path on first call; never reloaded.
- `src/calibration/emos_ci_license.py:57-109` — `_license_cache: dict | None = None`, "loaded once per process" (docstring explicitly states this).

**Stale mechanism:** All five module-level globals are set once at first call and held for the entire process lifetime. There is NO per-cycle, per-hour, or time-based eviction. Test-only `reset_*()` hooks exist in `anchor_representativeness_debias.py` and `emos_ci_license.py` but are never called in production code.

**Money path:** `emos_predictive()` (called from `src/forecast/center.py:469`) → `build_sigma()` (`src/forecast/sigma_authority.py`) → `predictive_distribution_builder.py` → q computation. If the operator re-runs offline calibration scripts (`fit_emos_mu_offset.py`, `fit_settlement_sigma_floor.py`) while the daemon is running, the updated JSON files are silently ignored; every subsequent decision uses the stale calibration artifact for the rest of the process lifetime.

**Why matters:** EMOS μ-offset corrects systematic city bias; σ-floor widens dispersion to prevent overconfidence; CI license keys k_cov. All three directly corrupt q_lcb (edge estimate) and size. A recalibrated artifact that the process never sees means the operator improvement is invisible while the daemon runs.

**Distinct from G1-G7:** G7 is about ensemble member freshness at assembly time; H1 is about the calibration coefficients themselves aging out without reload.

---

### H2 — Bankroll staleness_seconds: telemetry only; sizing proceeds on up-to-30min stale wallet value without rate-limited gate
**Severity: CORRUPTS-SIZE**

**Files:**
- `src/runtime/bankroll_provider.py:597-642` — `cached()` serves a bankroll up to `_resilient_cached_bound_seconds()` (default 30 min) old. Returns `BankrollOfRecord(staleness_seconds=age, cached=True, ...)`.
- `src/engine/event_reactor_adapter.py:14779-14870` — `_runtime_bankroll_usd(cached_only=True)` → `bankroll_provider.cached()` → `sizing_equity` used as Kelly basis. No gate on `staleness_seconds`.
- `src/main.py:5498-5511` — `_bk = bankroll_provider.cached(); _current_bankroll = float(getattr(_bk, "value_usd", 0.0) or 0.0)` — used to compute `_drawdown_pct` for risk allocator. No gate on `staleness_seconds`.
- `src/riskguard/riskguard.py:1719` — logs `staleness_seconds` in telemetry but never gates on it; `current_bankroll_usd = float(bankroll_of_record.value_usd)` is used unconditionally at line 1486.

**Stale mechanism:** `cached()` deliberately serves up-to-30min-old wallet values during RPC blip periods. The `staleness_seconds` field is attached to the returned struct and visible to callers, but NO caller in the active money path (`_runtime_bankroll_usd`, `refresh_global_allocator`, riskguard tick) checks it before using the value.

**Why matters:** A 30-min-old wallet balance feeds both the Kelly sizing basis and the drawdown-pct gate. Fills or losses in the intervening window are invisible to the sizing engine. A large loss outside Zeus (operator co-trades on same wallet, per MEMORY) could push actual equity below the Kelly basis by a significant fraction, causing size to be over-stated.

**Distinct from G1-G7:** Not in the freshness map. The 300s `current()` path has a gate; the 30min `cached()` path does not. The stale value is consumed without gating at the sizing boundary.

---

### H3 — Day0 Horizon Platt fit: `read_latest_platt_fit()` returns most-recent DB row with NO age gate; fit can be arbitrarily old
**Severity: CORRUPTS-EDGE (day0 q_lcb)**

**Files:**
- `src/state/day0_nowcast_store.py:305-330` — `read_latest_platt_fit()` executes `SELECT * FROM day0_horizon_platt_fits WHERE fit_version=? ORDER BY rowid DESC LIMIT 1`. Returns the row unconditionally; no `WHERE fit_date >= ?` or age comparison.
- `src/calibration/blocked_oos.py:131` — `return p_cal, "platt_fit"` (uses the fit coefficients for the live no-submit edge calculation).

**Stale mechanism:** If the scheduled re-fit job hasn't run (job failure, schema mismatch, operator hasn't run it yet), the returned `HorizonPlattFit` has no timestamp that the consumer checks. The consumer receives whatever was last inserted — potentially from days or weeks ago.

**Why matters:** The Horizon Platt fit calibrates day0 in-play probability CIs. A stale fit whose `beta` coefficient reflects old data can systematically widen or narrow the calibrated band, biasing q_lcb in either direction.

**Distinct from G1-G7:** Not listed. G7 covers ensemble members; H3 covers the calibration fit coefficients themselves.

---

### H4 — `load_domain_polygons()`: `@functools.lru_cache(maxsize=1)` with no TTL; model domain eligibility frozen at first call
**Severity: CORRUPTS-EDGE (model selection → forecast member set)**

**Files:**
- `src/forecast/model_selection.py:120-159` — `@functools.lru_cache(maxsize=1)` on `load_domain_polygons(path=None)`. Reads `config/model_domain_polygons.yaml` once and never re-reads.
- `src/data/day0_hourly_vectors.py:101-103` — calls `load_domain_polygons()` to gate which regional models (NBM, UKMO, AROME, ICON-D2) contribute ensemble members.
- `src/data/bayes_precision_fusion_download.py:338` — same gate.

**Stale mechanism:** `lru_cache(maxsize=1)` with no `maxsize=0` or TTL. If the operator updates `config/model_domain_polygons.yaml` (e.g., to add a new regional model or adjust a polygon boundary) while the process is running, the change is invisible until process restart.

**Why matters:** The domain polygon gate decides which regional models are `ELIGIBLE` vs `ABSENT` for a given city. A stale polygon can silently exclude a newly-added regional model from the ensemble, degrading forecast quality without any error signal.

**Distinct from G1-G7:** Not listed. Lower money risk than H1-H3 (polygon changes are rare), but silently corrupts the ensemble composition.

---

### H5 — `venue_summary_cache` / `collateral_payload_cache` in `_edli_venue_connectivity_authority_summary`: initialized-once per cycle invocation with no per-re-call invalidation
**Severity: CORRUPTS-EXIT (pre-submit quote validation)**

**Files:**
- `src/main.py:7577-7593` — two closure-local `None` caches are set on first call within a single pre-submit flow and returned on subsequent calls. The `venue_summary_cache` holds the venue connectivity verdict; `collateral_payload_cache` holds live collateral data.

**Stale mechanism:** Both caches are closure-scoped and reset to `None` at flow entry. Within a single pre-submit decision flow, if the function is called more than once (e.g., for retry / double-check), the second call returns the cached result from the first call without re-querying. The venue connectivity result (checked via `_edli_venue_connectivity_authority_summary(checked_at)`) is effectively frozen for the lifetime of the flow invocation.

**Why matters:** Pre-submit venue connectivity check and collateral balance are load-bearing for whether an order actually submits. If the venue becomes unavailable between the first and second check within a single flow, the stale `OK` result lets the submit proceed against a down venue.

**Distinct from G1-G7:** Not listed. Lower severity than H1-H3 since the window is within a single decision flow (seconds), not cross-cycle.

---

### H6 — `last_monitor_prob` / `last_monitor_edge` in `Position`: retained across cycles when `last_monitor_prob_is_fresh=False`; used as "best available" for exit decisions without an age gate
**Severity: CORRUPTS-EXIT**

**Files:**
- `src/state/portfolio.py:618-621` — `last_monitor_prob: float = 0.0`, `last_monitor_prob_is_fresh: bool = False`, `last_monitor_edge: float = 0.0` — position fields updated each monitor cycle.
- `src/engine/monitor_refresh.py:2290` — docstring: "the exit organ treats belief as unavailable (never a stale-as-fresh)" when `last_monitor_prob_is_fresh=False`.
- `src/engine/monitor_refresh.py:2413-2421` — if `prob_refresh_is_fresh` is False, the OLD `pos.last_monitor_prob` is retained but `pos.last_monitor_prob_is_fresh = False`.
- `src/engine/monitor_refresh.py:340-367` — `_track_belief_staleness()` only raises `BELIEF_AUTHORITY_FAULT` after `_BELIEF_STALE_FAULT_THRESHOLD = 3` consecutive cycles; no hard exit block.

**Stale mechanism:** When the forecast source is stale (G3, G7) or ensemble unavailable, `last_monitor_prob_is_fresh` is set `False`. The old probability value sits in the Position struct. While the fresh-flag correctly prevents using it as a "belief is fresh" exit signal, the staleness counter is only a WARNING after 3 cycles. The exit organ is documented as "blind" but continues to hold the position without fail-closed escalation.

**Why matters:** A position held open because the exit organ has no fresh belief — for 3+ cycles — is not gated. The "BELIEF_AUTHORITY_FAULT" at line 359 is an `logger.error` only; no automatic exit or size reduction.

**Distinct from G1-G7:** G1 flags portfolio degradation; H6 is specifically about the in-flight position belief going stale without an exit-forcing escalation path.

---

### H7 — `execution/day0_hard_fact_exit.py`: snapshot lookup `ORDER BY captured_at DESC LIMIT 1` — no age check on the returned row
**Severity: CORRUPTS-EXIT (exit direction)**

**Files:**
- `src/execution/day0_hard_fact_exit.py:373-386` — `SELECT ... FROM executable_market_snapshots WHERE yes_token_id=? OR no_token_id=? ORDER BY captured_at DESC LIMIT 1`. The returned row's `captured_at` is never compared to `now` — the row could be hours old.
- `src/execution/day0_hard_fact_exit.py:456` — second similar read from `market_events ORDER BY recorded_at DESC LIMIT 1` without age check.

**Stale mechanism:** Both reads return the most-recent DB row regardless of age. If market data stopped flowing (WS gap, ingest failure), the direction / condition_id for the exit order is derived from a potentially hours-old snapshot row. Wrong direction on an exit is a cost multiplier, not just a no-trade.

**Why matters:** A day0 hard fact exit with a stale snapshot could submit on the wrong token_id (yes vs no confusion) or with outdated market metadata if the condition_id was corrected in a later row.

**Distinct from G1-G7:** Not listed. Specifically about the cancel/exit execution path reading stale direction data.

---

### H8 — `anchor_representativeness_debias._cache`: load-once per process, no TTL, affects μ centering in replacement forecast materializer
**Severity: CORRUPTS-EDGE (moderate, only for HIGH family with activated city)**

**Files:**
- `src/calibration/anchor_representativeness_debias.py:55-74` — `_cache: dict | None = None`; `_load_table()` loads from artifact path once and caches forever.
- `src/data/replacement_forecast_materializer.py:1535-1537` — `bias_shift_c = get_city_debias_c(request.city, metric)` called per-family without age check.

**Stale mechanism:** Same as H1 — process-lifetime cache. No production reset path. `reset_cache()` exists for tests only.

**Why matters:** The per-city de-bias shift adjusts μ for activated HIGH family cities. If the operator updates the artifact (re-runs the offline fit) while the daemon runs, the updated correction is ignored for the process lifetime. Net effect: systematic city bias persists in center estimates after a correction is deployed.

**Distinct from H1:** H1 covers the EMOS table, σ-floor, and μ-offset (all in `emos.py`); H8 is a separate file and separate artifact (`anchor_debias`), but shares the same structural gap.

---

## Summary table

| ID | Location | Value | Stale mechanism | Severity | Money path |
|----|----------|-------|-----------------|----------|------------|
| H1 | `calibration/emos.py:56,64,76` | EMOS table, σ-floor, μ-offset | Load-once per process; no TTL or reload | corrupts-edge | q_lcb → size |
| H2 | `runtime/bankroll_provider.py:597` | wallet equity (sizing basis) | 30min resilient cache; staleness_seconds not gated at consumption | corrupts-size | Kelly sizing → notional |
| H3 | `state/day0_nowcast_store.py:305` | Horizon Platt fit coefficients | Latest DB row; no age check | corrupts-edge | day0 q_lcb |
| H4 | `forecast/model_selection.py:120` | Model domain polygons | `lru_cache(maxsize=1)`; no TTL | corrupts-edge | ensemble member eligibility |
| H5 | `main.py:7577` | venue connectivity + collateral payload | Closure-local cache; frozen within pre-submit flow | corrupts-exit | pre-submit gate |
| H6 | `state/portfolio.py:618` | last_monitor_prob / last_monitor_edge | Retained across cycles; `BELIEF_AUTHORITY_FAULT` is warn-only | corrupts-exit | exit decision |
| H7 | `execution/day0_hard_fact_exit.py:373` | snapshot row for exit direction | `ORDER BY captured_at DESC LIMIT 1`; no age filter | corrupts-exit | exit token direction |
| H8 | `calibration/anchor_representativeness_debias.py:55` | per-city μ de-bias shift | Load-once per process; no TTL | corrupts-edge (moderate) | replacement forecast center |

---

## Notes on what was checked and NOT flagged

- **bankroll `current()` path** (5-min `fail_closed_after_seconds`): has a gate; correctly excluded.
- **ensemble client 15min cache (G2)**: already in the known list.
- **`mainstream_forecast_source._WARM_CACHE`**: correctly drops stale entries via `_is_fresh()` check at read; not a hidden gap.
- **`riskguard.get_current_level()`**: 5-min freshness window enforced on `checked_at`; gated.
- **`load_portfolio()`**: reads from DB on each call (not a module cache); not stale.
- **`exit_fee_rate()`**: reads from `config/settings.json` at each call (via `settings` dict which is re-read); not a process-lifetime cache.

