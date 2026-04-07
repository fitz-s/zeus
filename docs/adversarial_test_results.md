# Adversarial Test Results — zeus-root-cause session

Generated: 2026-04-07 by adversarial-tester

## Summary

| Category | PASS | FAIL | PARTIAL | SPEC ERROR |
|----------|------|------|---------|------------|
| P9 contracts | 3 | 0 | 0 | 1 |
| P10 reality contracts | 2 | 1 | 0 | 0 |
| P12 live fixes | 3 | 0 | 0 | 0 |
| Settlement crisis | 4 | 0 | 0 | 0 |
| Strategy gates | 2 | 0 | 0 | 0 |
| Venus sensing | 3 | 0 | 0 | 0 |
| RiskGuard live | 3 | 0 | 0 | 0 |
| Data integrity | 2 | 0 | 1 | 0 |
| Additional checks | 0 | 2 | 0 | 0 |
| **TOTAL** | **22** | **3** | **1** | **1** |

---

## P9 Contracts

### CHECK 1: All 6 contract types exist and import
**PASS**
Evidence:
```
from src.contracts import AlphaDecision, TailTreatment, ExecutionPrice, DecisionEvidence, VigTreatment, HoldValue
→ ALL 6 IMPORTS OK
```
Note: venv Python required — system Python lacks `yaml`. Use `.venv/bin/python`.

---

### CHECK 2: provenance_registry.yaml loads
**PASS**
Evidence:
```
File: config/provenance_registry.yaml (NOT src/contracts/)
Keys: ['constants']
  constants: 51 records
```
Note: file lives in `config/`, not `src/contracts/` — verify import path in code.

---

### CHECK 3: require_provenance('kelly_mult') NOT raise; require_provenance('NONEXISTENT') raise
**PASS**
Evidence:
```
require_provenance('kelly_mult'): NO RAISE (PASS)
require_provenance('NONEXISTENT'): RAISED UnregisteredConstantError:
  Constant 'NONEXISTENT' is not registered in provenance_registry.yaml...
  Add a ProvenanceRecord to config/provenance_registry.yaml or call
  register_emergency_bypass() with documented justification. (INV-13 violation)
```

---

### CHECK 4: polymarket_fee(0.9) returns ~0.045 (not 0.05 flat)
**SPEC ERROR — actual value is 0.0045, not 0.045**
Evidence:
```python
from src.contracts.execution_price import polymarket_fee  # NOT settlement_semantics
polymarket_fee(0.9) = 0.0045
```
Formula (from docstring citing docs.polymarket.com/trading/fees):
  `fee_per_share = fee_rate × p × (1 - p) = 0.05 × 0.90 × 0.10 = 0.0045`

The check spec expected ~0.045 (which would be `fee_rate × p = 0.05 × 0.9`). The implementation returns 0.0045 per the actual Polymarket formula. The implementation is correct per Polymarket docs — the spec's expected value was off by 10×.

**Verdict**: Implementation CORRECT. Spec expectation was wrong.

---

## P10 Reality Contracts

### CHECK 5: Reality contracts load; blocking count; stale count
**PASS**
Evidence (from venus_sensing_report.json truth_surfaces.reality_contracts):
```
total: 25
blocking_total: 18
blocking_stale: 0
stale: ["GAMMA_CLOB_PRICE_CONSISTENCY"]
stale_count: 1
blocking_ok: true
freshness_ok: false  ← 1 non-blocking stale contract
```

---

### CHECK 6: verify_all_blocking() returns a result
**FAIL — function does not exist**
Evidence:
```
ImportError: cannot import name 'verify_all_blocking' from
  'src.contracts.reality_contracts_loader'
```
Functions in reality_contracts_loader.py:
  `_parse_datetime`, `_load_mutable_state`, `record_verification`,
  `load_contracts`, `_validate_entry`

`verify_all_blocking` was never implemented. The blocking verification logic lives inside `venus_sensing_report.py`, not as a standalone function in the loader. The underlying data is valid (18 blocking, 0 stale), but the contract API is incomplete.

---

### CHECK 7: Venus sensing report being generated
**PASS**
Evidence:
```
state/venus_sensing_report.json exists
generated_at: 2026-04-07T09:09:53.133289+00:00
```

---

## P12 Live Fixes

### CHECK 8: PARTIALLY_FILLED in fill_tracker FILL_STATUSES
**PASS**
Evidence:
```python
# src/execution/fill_tracker.py:26
FILL_STATUSES = frozenset({"FILLED", "MATCHED", "PARTIALLY_FILLED"})
# Comment at line 24-26: P12 finding #10
```
Note: `exit_lifecycle.py:85` still has old `FILL_STATUSES = frozenset({"MATCHED", "FILLED"})` without PARTIALLY_FILLED. Two definitions exist — verify which is authoritative in critical paths.

---

### CHECK 9: process_lock acquire + release
**PASS**
Evidence:
```
src/engine/process_lock.py — uses fcntl.flock() with per-mode lock files
Test: acquire_process_lock(tmp_dir, mode='test') → TextIOWrapper fd returned
Release: fd closed successfully
```
Behavior confirmed: acquires exclusive lock, writes PID, stale-lock detection via os.kill(pid, 0).

---

### CHECK 10: Min order size retry in executor.py
**PASS**
Evidence:
```python
# src/execution/executor.py:36-498 (P12 finding #1)
_MAX_MIN_SIZE_RETRIES = (line 36)
_RE_MIN_NOTIONAL = re.compile(r"min size:\s*\$([0-9]+...)")  # line 40
_parse_min_size_and_bump()  # line 44
# BUY retry at line 493-498
# SELL retry at line 387-390
```

---

## Settlement Crisis Fixes

### CHECK 11: outcome_fact has rows
**PASS** (with critical location note)
Evidence:
```sql
-- zeus.db (NOT risk_state-paper.db)
SELECT COUNT(*), SUM(pnl) FROM outcome_fact → 19 | -13.03
```
**CRITICAL**: `outcome_fact` lives in `state/zeus.db`, NOT `state/risk_state-paper.db`.
Querying risk_state-paper.db returns `Error: no such table: outcome_fact`.
Anyone checking the wrong DB will get a false FAIL.

---

### CHECK 12: log_settlement_event() calls log_outcome_fact() BEFORE schema guard
**PASS**
Evidence (src/state/db.py:1524-1545):
```python
# Line 1524: SD-2 fix: log_outcome_fact() MUST run regardless of schema routing.
# Line 1529: log_outcome_fact(conn, ...) ← BEFORE any early return
# Line 1545: if _legacy_runtime_position_event_schema_available(conn): ← schema guard AFTER
```
The SD-2 fix is correctly implemented — outcome_fact write cannot be skipped by schema guard.

---

### CHECK 13: harvester.py UPDATE trade_decisions with settlement P&L
**PASS**
Evidence (src/execution/harvester.py:687-703):
```python
# SD-1 fix comment at line 687-689
conn.execute(
    """UPDATE trade_decisions
       SET settlement_edge_usd = ?,
           exit_reason = COALESCE(exit_reason, 'SETTLEMENT'),
           status = CASE WHEN status = 'entered' THEN 'day0_window' ELSE status END
       WHERE runtime_trade_id = ?""",
    (round(pnl, 4), rtid),
)
```

---

### CHECK 14: riskguard loads recent_exits from chronicle (not hardcoded [])
**PASS**
Evidence (src/riskguard/riskguard.py:73-131):
```python
def _load_chronicle_recent_exits(conn, env) -> list[dict]:  # line 73
    # Queries chronicle WHERE event_type='SETTLEMENT' AND env=?
    # Deduplication via MAX(id) GROUP BY trade_id

# In _load_riskguard_portfolio_truth (line 122):
recent_exits = _load_chronicle_recent_exits(zeus_conn, env=settings.mode)
portfolio = PortfolioState(..., recent_exits=recent_exits, ...)
```
Hardcoded `[]` is gone — chronicle is the source.

---

## Strategy Gates

### CHECK 15: center_buy gated in control_plane-live.json
**PASS**
Evidence:
```json
{"command": "set_strategy_gate", "strategy": "center_buy", "enabled": false,
 "acked_at": "2026-04-07T09:09:53.089653+00:00",
 "note": "0/16 win rate, structurally unprofitable at 2F bin precision"}
```

---

### CHECK 16: center_buy gated in control_plane-paper.json
**PASS**
Evidence:
```json
{"command": "set_strategy_gate", "strategy": "center_buy", "enabled": false,
 "acked_at": "2026-04-07T09:09:53.075765+00:00",
 "note": "0/16 win rate, structurally unprofitable at 2F bin precision"}
```

---

## Venus Sensing

### CHECK 17: venus_sensing_report.py runs without error
**PASS**
Evidence:
```
.venv/bin/python scripts/venus_sensing_report.py → exits clean, valid JSON output
generated_at: 2026-04-07T09:09:53
```

---

### CHECK 18: venus_antibody_queue.json has antibodies
**PASS — 6 antibodies**
Evidence:
```
state/venus_antibody_queue.json:
  AB-001: lifecycle_completeness — 33 ghost rows from schema migration (severity: critical, status: proposed)
  AB-002: diagnostic_surface_gap — settlements table wrong source (severity: medium, status: proposed)
  AB-003: canonical_path_silent_degradation — portfolio truth silent failure (severity: critical, status: implemented)
  AB-004: strategy_labeling_defect — shoulder_sell/tail_buy misclassification (severity: high, status: proposed)
  AB-005: structural_entry_filter — center_buy bin precision (severity: high, status: proposed)
  AB-006: exit_authority_stale_signal — day0 EV gate stale signal trap (severity: critical, status: proposed)
```
AB-006 is highest priority: explains 100% of center_buy/tail_buy losses. Stale p_posterior == entry_price makes day0 EV gate never fire.

---

### CHECK 19: diagnose_truth_surfaces.py runs; score
**PASS — runs clean**
Score from output:
```
[PASS] canonical_freshness — age=0.9h
[FAIL] position_count_match — positions-paper.json not found
[PASS] ghost_positions — ghost_count=0 (different query than sensing report)
[FAIL] settlement_harvester — max_settled_at age=73.2h
[FAIL] portfolio_truth_source — working_state_fallback
[PASS] status_summary_completeness — 49 keys
[PASS] fact_tables — outcome_fact=19, execution_fact=12
[PASS] unfilled_ghosts — 0
```
5 PASS, 3 FAIL — system has known live issues (positions-paper.json missing, settlement staleness, canonical path unavailable)

---

## RiskGuard Live

### CHECK 20: riskguard-live.plist has ZEUS_MODE=live
**PASS**
Evidence:
```
/Users/leofitz/Library/LaunchAgents/com.zeus.riskguard-live.plist
<key>ZEUS_MODE</key>
<string>live</string>
```

---

### CHECK 21: risk_state-live.db has rows
**PASS**
Evidence:
```sql
SELECT COUNT(*) FROM risk_state → 131
```
Tables: `alert_cooldown`, `risk_state`

---

### CHECK 22: LIVE_LOCK removed
**PASS**
Evidence:
```
ls state/LIVE_LOCK → NOT FOUND
```

---

## Data Integrity

### CHECK 23: position_current has env column
**PASS**
Evidence:
```sql
PRAGMA table_info(position_current) → column 27: env TEXT
```

---

### CHECK 24: Ghost positions (status='entered' with past target dates)
**PARTIAL**
Evidence:
```sql
SELECT COUNT(*) FROM trade_decisions
WHERE status='entered'
AND json_extract(settlement_semantics_json,'$.target_date') < date('now')
→ 0
```
However: 5 rows with `status='unresolved_ghost'` DO exist (trade_ids: 257, 258, 259, 263, 264). These are acknowledged ghosts from the runtime_trade_id schema migration gap (see AB-001). They are NOT `status='entered'` — they were explicitly relabeled to `unresolved_ghost`.

Sensing report shows ghost_count=5 (different definition — counts position_current entries with no matching trade_decisions), diagnose_truth_surfaces.py reports ghost_count=0 (counts `status='entered'` with past dates). The two scripts use different ghost definitions — this is a diagnostic ambiguity.

---

### CHECK 25: realized_pnl in risk_state-paper.db is -$13.03
**PASS** (via correct surface)
Evidence:
```
# From venus_sensing_report.json truth_surfaces.risk_state:
realized_pnl: -13.03
total_pnl: -13.03

# From outcome_fact (zeus.db):
SUM(pnl) = -13.03
```
Note: `risk_state-paper.db` schema has no `realized_pnl` column — the value lives in `zeus.db.outcome_fact` and is surfaced via the sensing report.

---

## Critical Findings Not In Original Checks

1. **FILL_STATUSES split**: `exit_lifecycle.py:85` still has old `{"MATCHED", "FILLED"}` without PARTIALLY_FILLED — could create ghost-fill divergence vs fill_tracker.

2. **AB-006 is unimplemented**: The day0 EV gate stale signal trap is only in the antibody queue as `proposed`. This is the root cause of all center_buy/tail_buy losses. Gate is closed but underlying logic bug persists.

3. **positions-paper.json missing**: `state/positions-paper.json` not found. Affects position_count_match check and working_state_fallback activation.

4. **canonical path degraded**: `portfolio_truth_source=working_state_fallback` / `CANONICAL_AUTHORITY_UNAVAILABLE` — system is operating on fallback state.

5. **Ghost definition ambiguity**: sensing report and diagnose script use different ghost definitions (5 vs 0). Needs normalization.

---

## CHECK 26: daily_loss vs all-time loss / baseline reset
**FAIL — daily_loss == realized_pnl == $13.03 → FALSE RED TRIGGER CONFIRMED**

Evidence (risk_state-paper.db, latest rows):
```
checked_at                         bankroll  daily_loss  weekly_loss  level
2026-04-07T09:08:27               136.97     13.03       13.03        RED
2026-04-07T09:09:48 → 09:23:50   136.97     13.03       13.03        RED  (14 consecutive ticks)
```

Risk state has been RED continuously since 09:08 UTC. `daily_loss (13.03) == realized_pnl (13.03)` — the daily baseline is NOT resetting from yesterday's end state. It is resetting from `capital_base_usd = $150` (fresh DB start), so all-time losses appear as "today's" losses.

Root cause — `_load_baselines_from_risk_history()` in `riskguard.py:95-108`:
```python
# Finds FIRST row today >= period_start
# First rows today (06:34) had bankroll=142.47 or 150.0 depending on run
# When bankroll was 150.0 at first tick, baseline = 150.0
# daily_loss = 150.0 - 136.97 = 13.03 (= all-time loss)
```

Timeline showing the instability:
```
09:06-09:07: bankroll=150.0, daily_loss=0.0, GREEN   ← fresh riskguard start
09:08:27:    bankroll=136.97, daily_loss=13.03, RED   ← settlements loaded, baseline=150
09:08:55:    bankroll=150.0, daily_loss=0.0, GREEN    ← brief anomaly
09:09:48+:   bankroll=136.97, daily_loss=13.03, RED   ← persistent false RED
09:20:09:    bankroll=136.97, daily_loss=5.5, GREEN   ← one-off (baseline=142.47 used)
09:20:50+:   bankroll=136.97, daily_loss=13.03, RED   ← back to false RED
```

The baseline flips between 150.0 and 142.47 depending on which row `_baseline_for_period` finds first. When baseline=150.0 is used, `daily_loss = $13.03 = all-time loss → RED`. When baseline=142.47 is used, `daily_loss = $5.50 → GREEN`.

**positions.json (state/positions.json)**: File is a deprecated stub, no baseline data:
```json
{"error": "positions.json is deprecated and must not be used as current truth.",
 "truth": {"mode": "deprecated", "generated_at": "2026-04-01T10:56:05", ...}}
```
`daily_baseline_total` and `weekly_baseline_total` are NOT stored in positions.json — they come from `_load_baselines_from_risk_history()` only. Since positions-paper.json does not exist, the portfolio fallback path calls `load_portfolio()` which reads this deprecated stub.

**Verdict**: CHECK 26 FAILS. False RED is active due to unstable daily baseline. The fix must ensure the baseline is seeded from yesterday's closing bankroll, not the first (possibly pre-settlement) tick of the current riskguard session.

---

## CHECK 27: Independent P&L reconciliation across three sources
**FAIL — three sources disagree; chronicle has duplicate SETTLEMENT events**

### Results

| Source | P&L | Record Count | Trustworthy? |
|--------|-----|---------|---|
| `chronicle` (raw) | **-$26.72** | 31 events | ❌ DUPLICATED |
| `chronicle` (deduped by MAX(id) per trade_id) | **-$13.03** | 19 unique | ✅ |
| `outcome_fact` (zeus.db) | **-$13.03** | 19 rows | ✅ |
| `riskguard realized_pnl` (risk_state details_json) | **-$13.03** | via chronicle-deduped | ✅ |
| `trade_decisions.settlement_edge_usd` (day0_window only) | **-$13.74** | 20 rows | ⚠️ $0.71 gap |
| `trade_decisions.settlement_edge_usd` (all statuses) | **-$3.98** | 104 rows | ❌ MISLEADING |

### Finding A: Chronicle has 12 duplicate SETTLEMENT events
```sql
-- 12 trade_ids have exactly 2 SETTLEMENT events each:
454db425-76d × 2 = -2.56    cea42e98-b7e × 2 = -2.10
a9fd32ad-a8b × 2 = -2.20    0c108102-032 × 2 = -2.22
0178da5e-93f × 2 = -2.04    9e97c78f-2a8 × 2 = -2.42
772833bd-bf1 × 2 = -2.46    9cfc3ba7-d35 × 2 = -2.10
bc05151e-0e0 × 2 = -2.58    6f8ce461-902 × 2 = -2.50
050cb6f5-5cf × 2 = -2.10    0a8d35bc-03a × 2 = -2.10
```
All duplicates are from 2026-04-02. First batch: 07:40-08:41 UTC. Second batch: 20:09-20:11 UTC.
Root cause: harvester was run twice on 2026-04-02, processing same positions both times.
Impact: Any consumer of raw chronicle P&L gets -$26.72 (2× actual). Riskguard is PROTECTED by
  `MAX(id) GROUP BY trade_id` deduplication in `_load_chronicle_recent_exits()`.

### Finding B: trade_decisions.$0.71 gap — trade 286 missing from outcome_fact
```
trade_id=286  runtime_trade_id=511c16a6-27d  status=day0_window
settlement_edge_usd=-0.71  strategy=opening_inertia  filled_at=2026-04-02

In chronicle: NO SETTLEMENT event for trade_id 511c16a6-27d
In outcome_fact: NO row for position_id=511c16a6-27d
```
The SD-1 fix (harvester UPDATE trade_decisions) fired for trade 286, writing `settlement_edge_usd=-0.71`.
But `log_settlement_event()` was NEVER called for this trade — so outcome_fact has no record of it.
This means SD-1 and SD-2 fixes are NOT atomically linked: trade_decisions can record settlement P&L
that outcome_fact never sees. The -$0.71 is present in trade_decisions but invisible to riskguard.

### Finding C: trade_decisions all-status P&L is misleading
`SUM(settlement_edge_usd)` across all statuses = -$3.98 because:
- `exited` rows: +$11.04 (30 rows) — these include EARLY EXIT profits (e.g. trade 255: +$13.10)
- `day0_window` rows: -$13.74 (20 rows) — actual settlements
- `unresolved_ghost` rows: -$1.28 (1 row — trade 266, status=unresolved_ghost)
The `settlement_edge_usd` field stores both exit P&L and settlement P&L without distinction.
Querying it without status filtering produces a meaningless number.

### Authoritative P&L: -$13.03 (19 trades)
Derived from: outcome_fact (19 rows) = chronicle-deduped (19 unique trade_ids) = riskguard realized_pnl.
Missing: trade 286 (-$0.71, opening_inertia) which settled but never had log_settlement_event called.
True realized P&L if trade 286 included: **-$13.74**.
