# Zeus Legacy Truth Surface Audit

**Date:** 2026-04-06
**Auditor:** legacy-scanner (automated)
**Scope:** All persistent surfaces in `state/zeus.db`, `state/risk_state-paper.db`, and JSON state files

---

## 1. Truth Surface Inventory

### SQLite Tables (`state/zeus.db` — 398MB)

| Table | Rows | Writes From | Reads From | Classification |
|-------|------|-------------|------------|----------------|
| `trade_decisions` | 108 (49 entered, 37 exited, 22 day0_window) | `db.py:record_trade_decision()`, `db.py:record_lifecycle_trade_decision()` | riskguard, status_summary, strategy_tracker, harvester | **CANONICAL** — trade intent + execution record |
| `position_events` | 36 (12 positions × 3 events) | `lifecycle_events.py` via `projection.py` | `query_portfolio_loader_view()`, settlement queries | **CANONICAL (P7)** — append-only event source, but **STALE since Apr 3** |
| `position_current` | 12 | `projection.py:upsert_position_current()` | `query_portfolio_loader_view()` | **CANONICAL (P7)** — materialized view of position_events, but **STALE since Apr 3** |
| `position_events_legacy` | 99 | legacy runtime lifecycle code | `query_portfolio_loader_view()` staleness check | **LEGACY** — old event format, still actively written |
| `settlements` | 1399 | settlement harvester | calibration, outcome analysis | **CANONICAL** — settlement facts (no P&L, just outcomes) |
| `outcome_fact` | **0** | should be populated by lifecycle close | riskguard analytics | **CANONICAL (P7)** — **NEVER POPULATED** |
| `execution_fact` | **0** | should be populated by order lifecycle | execution quality analysis | **CANONICAL (P7)** — **NEVER POPULATED** |
| `ensemble_snapshots` | many | forecast engine | trade_decisions FK | CANONICAL — forecast snapshots |
| `shadow_signals` | many | pre-trade validation | shadow proof analysis | DERIVED — validation artifacts |
| `risk_actions` | many | riskguard | status_summary | CANONICAL — risk action log |
| `strategy_health` | many | riskguard | status_summary | DERIVED — materialized risk view |
| `chronicle` | many | chronicler | debugging/audit | CANONICAL — system event log |
| `decision_log` | many | decision engine | replay, debugging | CANONICAL — decision trace |
| `observations`, `hourly_observations` | many | data ingest | forecast engine | CANONICAL — weather data |
| `market_events`, `market_price_history` | many | market scanner | forecast, monitoring | CANONICAL — market data |
| `diurnal_curves`, `diurnal_peak_prob` | many | analysis | forecast calibration | DERIVED — analytical |
| `calibration_pairs`, `platt_models` | many | calibration engine | forecast | CANONICAL — model parameters |
| `model_bias`, `model_skill`, `forecast_skill` | many | calibration | model monitoring | DERIVED — quality metrics |
| `alpha_overrides`, `control_overrides` | small | operator | engine | CANONICAL — manual overrides |
| `token_price_log` | many | price tracker | monitoring | CANONICAL — price history |
| `replay_results` | varies | replay scripts | analysis | DERIVED — replay artifacts |
| `asos_wu_offsets` | small | data quality | observation correction | CANONICAL — station offsets |
| `temp_persistence`, `solar_daily` | many | analysis | forecast features | DERIVED — analytical |

### SQLite (`state/risk_state-paper.db` — 20MB)

| Table | Rows | Writes From | Reads From | Classification |
|-------|------|-------------|------------|----------------|
| `risk_state` | 5012 | `riskguard.py:check_risk()` (every ~60s) | status_summary, Venus reporting | **CANONICAL** — risk assessment time series |
| `alert_cooldown` | small | riskguard alerting | riskguard | CANONICAL — alert dedup |

### JSON State Files (`state/`)

| File | Writes From | Reads From | Classification |
|------|-------------|------------|----------------|
| `positions-paper.json` | `portfolio.py:save_portfolio()` | `load_portfolio()` (fallback), riskguard (via fallback), Venus | **LEGACY/WORKING STATE** — actively written, currently the de facto authority due to P7 staleness |
| `status_summary-paper.json` | `status_summary.py` | Venus reporting, operator dashboard | **DERIVED** — aggregated from riskguard + portfolio |
| `strategy_tracker-paper.json` | strategy tracker module | riskguard (compatibility), reporting | **COMPATIBILITY SURFACE** — self-declares as `non_authority_compatibility` |
| `control_plane-paper.json` | operator commands, auto-controls | cycle_runner, entry engine | **CANONICAL** — operator control state |

---

## 2. Divergence Report

### Critical: position_current (12) vs trade_decisions entered (49) vs positions-paper.json (12 active + 36 exits)

| Surface | Count | Time Range | Notes |
|---------|-------|-----------|-------|
| `trade_decisions` WHERE status='entered' | 49 | Mar 31 - Apr 3 | All positions that entered |
| `position_current` | 12 | Apr 2 - Apr 3 | Only 12 of 49 got P7 event records |
| `position_events` | 36 events for 12 positions | Apr 2 - Apr 3 | Only entry events (OPEN_INTENT + ORDER_POSTED + ORDER_FILLED) |
| `position_events_legacy` | 99 events | Apr 2 - Apr 6 | Includes 22 SETTLED, 14 EXIT_RECORDED, 25 LIFECYCLE_UPDATED |
| `positions-paper.json` active | 12 | Apr 2 - Apr 3 | Matches position_current count (same 12 positions) |
| `positions-paper.json` recent_exits | 36 | Mar 31 - Apr 6 | Exited positions tracked here |

**Root cause of 49 vs 12 gap:** The P7 event-sourcing pipeline (`lifecycle_events.py` → `projection.py`) was only operational for positions entered on Apr 2-3. The 37 positions entered Mar 31 - Apr 2 never got P7 records. They exist only in `trade_decisions` and `positions-paper.json`.

**Root cause of staleness fallback:** `query_portfolio_loader_view()` at `db.py:2390` compares `position_current.updated_at` against `position_events_legacy` timestamps. Since legacy events continue to Apr 6 but projections stopped at Apr 3, all 12 positions are marked stale → returns `stale_legacy_fallback` → riskguard falls back to JSON.

### Moderate: strategy_tracker vs trade_decisions

| Source | settlement_capture | shoulder_sell | center_buy | opening_inertia | Total |
|--------|-------------------|---------------|------------|-----------------|-------|
| `strategy_tracker-paper.json` | 0 | 5 | 8 | 29 | 42 |
| `trade_decisions` entered | — | — | — | — | 49 |
| `trade_decisions` exited | — | — | — | — | 37 |

Strategy tracker shows 42 total trades but with `realized_pnl_usd = 0` for all trade objects. It self-declares as non-authoritative (`authority_mode: "non_authority_compatibility"`).

### Minor: risk_state P&L timing variance

- `risk_state` (id=5012): total_pnl = -$9.20 (realized -$8.47, unrealized -$0.73)
- `status_summary-paper.json`: total_pnl = -$9.16 (realized -$8.47, unrealized -$0.69)
- Discrepancy: $0.04 — caused by unrealized mark-to-market evaluated at different timestamps

---

## 3. P&L Source Chain

### Which number is trustworthy?

| Source | Number | Computation | Trustworthy? |
|--------|--------|-------------|--------------|
| `positions-paper.json` recent_exits sum | -$8.47 realized | Sum of `pnl` field for all non-mock, non-admin exits | **YES** — this is the authoritative realized P&L |
| `risk_state-paper.db` realized_pnl | -$8.47 | Reads from `load_portfolio()` → `_realized_pnl_value()` which sums recent_exits | **YES** — derived from same source |
| `risk_state-paper.db` total_pnl | -$9.20 | realized + unrealized (mark-to-market of 12 active positions) | **YES** — best current total |
| `status_summary-paper.json` total_pnl | -$9.16 | Same computation, different timestamp | **YES** — slight timing variance |
| Venus reported | -$9.72 | Reads status_summary | Depends on when Venus last read |
| FINAL spec | -$6.82 | Unknown provenance | **UNVERIFIED** — may be from different time window or computation |
| `settlement_edge_usd` SUM | +$13.50 → +$11.64 exited | Only counts P&L for positions held to settlement (0 for early exits) | **NO** — NOT P&L. This is "settlement edge captured", a misleading subset |

### settlement_edge_usd semantics (CRITICAL)

```python
# db.py line 799:
settlement_edge_usd = final_pnl_usd if not is_early_exit else 0.0
```

This column stores the P&L for positions that were held to settlement, and **zero for all early exits**. It is an attribution metric ("how much edge did we capture by holding to settlement?"), NOT realized P&L. Using it as P&L systematically overcounts by hiding all early-exit losses.

### P&L breakdown by exit category

| Category | Count | P&L |
|----------|-------|-----|
| SETTLEMENT | 19 | -$13.03 |
| Early exits (divergence, edge, near) | 15 | +$0.12 |
| MOCK_PROFIT_TEST | 2 | +$4.44 |
| **Total all exits** | **36** | **-$8.47** (excluding mock) |

### Realized P&L computation chain

```
positions-paper.json.recent_exits
  → each exit has .pnl field (set by _compute_realized_pnl at exit time)
  → _realized_pnl_value() sums these, excluding admin exits
  → PortfolioState.realized_pnl property
  → riskguard reads via _load_riskguard_portfolio_truth() fallback
  → risk_state.details_json.realized_pnl
  → status_summary-paper.json.risk.details.realized_pnl
```

---

## 4. Portfolio Loader Diagnosis

### Current state: DEGRADED (stale_legacy_fallback)

**Flow:**
1. `_load_riskguard_portfolio_truth()` calls `query_portfolio_loader_view()`
2. `query_portfolio_loader_view()` reads `position_current` (12 rows)
3. For each row, checks if `position_events_legacy` has newer timestamps
4. ALL 12 positions have legacy events newer than their projection → ALL marked stale
5. Returns `status: "stale_legacy_fallback"` with empty positions list
6. Riskguard falls back to `load_portfolio()`
7. `load_portfolio()` also calls `query_portfolio_loader_view()` → same result → falls back to JSON
8. `_load_portfolio_from_json_data()` reads `positions-paper.json` directly

**Consequence:** The entire position truth chain runs through `positions-paper.json`, a working state file that was designed as a compatibility surface, not the canonical authority.

---

## 5. Recommendations

### P0: Restore P7 event-sourcing pipeline
- **Root cause:** `lifecycle_events.py` stopped emitting events after initial entry. Exit events, monitor events, and settlement events are only written to `position_events_legacy`.
- **Fix:** Ensure all lifecycle transitions emit to `position_events` AND update `position_current` via `upsert_position_current()`.
- **Verification:** After fix, `position_events` should have exit/settlement events and `position_current.updated_at` should be newer than any `position_events_legacy` timestamp.

### P0: Backfill position_current projections
- The 37 positions from Mar 31 - Apr 2 that never got P7 records need backfill.
- Script exists: `scripts/backfill_open_positions_canonical.py` — verify it covers this case.
- After backfill, `query_portfolio_loader_view()` should return `status: "ok"`.

### P1: Populate outcome_fact and execution_fact
- Both P7 analytical fact tables have 0 rows.
- These are needed for proper settlement analysis and execution quality monitoring.
- Can be backfilled from `trade_decisions` + `position_events_legacy` data.

### P1: Rename or document settlement_edge_usd
- This column name is actively misleading — it reads like P&L but excludes early-exit losses.
- Options: rename to `settlement_held_pnl_usd`, or add a companion `realized_pnl_usd` column to `trade_decisions`.

### P2: Resolve strategy_tracker zero P&L
- `strategy_tracker-paper.json` shows `realized_pnl_usd = 0` for all trades.
- While it self-declares as non-authoritative, it's still used in riskguard's `strategy_tracker_summary`.
- The summary in risk_state shows correct P&L (shoulder_sell: -$3.85, center_buy: -$9.00) — unclear where this comes from if not from the tracker.

### P2: Reconcile FINAL spec P&L (-$6.82)
- This number doesn't match any current surface. May represent a different time window or computation method.
- Needs manual investigation to determine provenance.
