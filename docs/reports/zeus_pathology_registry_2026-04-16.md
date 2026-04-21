# Zeus Pathology Registry
> LEGACY EXTRACTION SOURCE - NOT DEFAULT READ.
> Durable failure classes have been extracted into
> `docs/reference/zeus_failure_modes_reference.md`. Treat this file as
> historical diagnostic evidence only; manifests, tests, and current source win
> on disagreement.
>
> Verified against source code. Each entry includes exact file, line numbers, code evidence, and severity.
> Last updated: 2026-04-16

---

## Verification Summary

| ID | Verdict | Severity | Category |
|----|---------|----------|----------|
| P1 | ✅ VERIFIED | HIGH | Data integrity |
| P2 | ✅ VERIFIED (79 fields, not ~90) | MEDIUM | Code structure |
| P3 | ✅ VERIFIED | MEDIUM | Testability |
| P4 | ✅ VERIFIED (understated) | HIGH | Code structure |
| P5 | ✅ VERIFIED (116 scripts) | MEDIUM | Operational |
| P6 | ✅ VERIFIED | MEDIUM | State management |
| P8 | ⚠️ OVERSTATED | LOW→MEDIUM | Error handling |
| P9 | ✅ VERIFIED | HIGH | Probability accuracy |
| P10 | ✅ VERIFIED | MEDIUM | Performance |
| P11 | ✅ VERIFIED (= P1) | HIGH | Data integrity |
| P12 | ✅ VERIFIED | MEDIUM | Availability |
| P13 | ⚠️ OVERSTATED | LOW | Memory |
| P14 | ✅ VERIFIED | CRITICAL | Position truth |
| P15 | ✅ VERIFIED (mislabeled) | LOW | Cosmetic |
| P16 | ✅ VERIFIED | MEDIUM | Settlement accuracy |
| C1 | ✅ VERIFIED | HIGH | Data integrity |
| C2 | ✅ VERIFIED | HIGH | Authority bypass |
| C3 | ✅ VERIFIED (= P14) | CRITICAL | Position truth |
| C4 | ✅ VERIFIED | MEDIUM | Exit lifecycle |
| C5 | ❓ UNVERIFIED | — | Replay |
| C6 | ✅ VERIFIED (= P1) | HIGH | Data integrity |
| C7 | ❌ WRONG | — | sequence_no IS unique |
| TB-1 | ✅ VERIFIED | LOW | Timezone |
| TB-2 | ✅ VERIFIED | MEDIUM | Calibration |
| TB-3 | ✅ VERIFIED | HIGH | Performance |
| TB-4 | ✅ VERIFIED | LOW | Edge case |
| TB-5 | ✅ VERIFIED | MEDIUM | Config debt |
| TB-6 | ✅ VERIFIED | MEDIUM | Timing |
| TB-7 | ✅ VERIFIED | MEDIUM | Availability |

---

## Structural Pathologies

### P1. Portfolio Save Before DB Commit (HIGH)
**File:** `src/execution/harvester.py` L293-300
**Also labeled:** P11, C6

```python
    if positions_settled > 0:
        save_portfolio(portfolio)        # ← JSON write FIRST
    if tracker_dirty:
        save_tracker(tracker)

    trade_conn.commit()                  # ← DB commit SECOND
    shared_conn.commit()
```

**Impact:** If process crashes between `save_portfolio()` and `conn.commit()`, JSON says positions are settled but DB has them active. Calibration pairs from this cycle are lost (rolled back). On restart, `chain_reconciliation` sees a diverged state — irrecoverable without manual intervention.

**Partial guard:** `_settle_positions` is called per-event inside try/except (L286), so individual event failures are caught. But the final save/commit pair at the cycle boundary has no transactional wrapper.

**Fix:** Move `save_portfolio()` AFTER both `commit()` calls. If commit fails, portfolio is unchanged and cycle can retry.

---

### P2. Position God Object — 79 fields (MEDIUM)
**File:** `src/state/portfolio.py` L131-254

**75 dataclass fields** spanning identity, probability, entry context, strategy, lifecycle, chain recon, token IDs, quarantine, exit state, anti-churn, JSON snapshots, P&L. Plus **4 `@property` methods** (L259, 267, 271, 278).

**Companion:** `ExitContext` (L68-93) is a frozen dataclass with 16 authority fields. Combined surface: ~95 fields.

**Correction from audit:** Original claimed ~90 fields. Actual count is 79. Still a god object, but ~14% inflated.

---

### P3. `deps=sys.modules[__name__]` Opaque DI (MEDIUM)
**File:** `src/engine/cycle_runner.py` L87-134 (7 call sites)

```python
def _run_chain_sync(portfolio, clob, conn):
    return _runtime.run_chain_sync(portfolio, clob, conn=conn, deps=sys.modules[__name__])
```

The entire `cycle_runner` module is passed as `deps` to `cycle_runtime.py`, which accesses `deps.evaluate_candidate`, `deps.execute_intent`, etc. Zero type safety — typos produce runtime `AttributeError`.

**Also used by:** `fill_tracker.py` (L87-97) accesses `deps.get_connection`, `deps.PENDING_FILL_STATUSES`, `deps._utcnow`.

---

### P4. Evaluator Monolith — 892-line function (HIGH)
**File:** `src/engine/evaluator.py` — 1632 LOC, 27 methods

`evaluate_candidate()` spans L631-L1523 — **892 lines for a single function**. Handles bin construction, ENS fetching, ENS validation, epistemic context, settlement semantics, signal generation, calibration, full-family hypothesis scanning, FDR filtering, Kelly sizing, risk limit checks, snapshot storage, and decision recording ALL inline.

---

### P5. 114→116 Scripts Shadow API (MEDIUM)
**File:** `scripts/` — 116 files

Several scripts directly import from `src/` and replicate core logic:
- `backfill_*.py` (14) replicate ETL paths
- `rebuild_calibration.py`, `rebuild_settlements.py` duplicate calibration logic
- `nuke_rebuild_projections.py` directly manipulates state DB
- `force_lifecycle.py` bypasses lifecycle state machine

---

### P6. Implicit State Machine in cycle_runner (MEDIUM)
**File:** `src/engine/cycle_runner.py` L150-330

`run_cycle()` orchestrates 4 phases: chain_sync → pending fill recon → monitoring → discovery. State transitions via string comparisons on `entries_blocked_reason`, boolean flags, and inline guards:

```python
if not chain_ready:
    entries_blocked_reason = "chain_sync_unavailable"
elif has_quarantine:
    entries_blocked_reason = "portfolio_quarantined"
elif force_exit:
    entries_blocked_reason = "force_exit_review_daily_loss_red"
elif risk_level in (RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED):
    entries_blocked_reason = f"risk_level={risk_level.value}"
```

No formal state enum or FSM.

---

### P8. fill_tracker.py Error Handling — OVERSTATED (LOW→MEDIUM)
**File:** `src/execution/fill_tracker.py`

**Correction:** Original audit claimed "triple silent exception swallow" with `except Exception: pass`. Actual code:
- `_maybe_update_trade_lifecycle`: catches Exception, **logs error**, returns False
- `_maybe_emit_canonical_entry_fill`: catches Exception, **logs error**, returns False
- `_maybe_log_execution_fill`: catches Exception, **raises RuntimeError**

The `except Exception: pass` pattern only appears in `finally` blocks for connection cleanup — standard resource cleanup pattern. Business errors are logged, not silently swallowed.

**Remaining concern:** Returning `False` on Exception still means the lifecycle write is abandoned. The caller logs a warning but doesn't retry. Over time this creates DB inconsistency (position filled in-memory, not in DB).

---

### P9. Monitor Hardcodes `model_agreement="AGREE"` (HIGH)
**File:** `src/engine/monitor_refresh.py` L189, L379

```python
    alpha = compute_alpha(
        calibration_level=cal_level,
        ensemble_spread=ens.spread(),
        model_agreement="AGREE",          # ← HARDCODED
        lead_days=float(lead_days),
        hours_since_open=hours_since_open,
        authority_verified=_authority_verified,
    ).value_for_consumer("ev")
```

Entry path (evaluator.py) performs real GFS crosscheck → may produce `SOFT_DISAGREE` (α-0.10) or `CONFLICT` (α-0.20). Monitor always assumes models agree. This creates asymmetric entry/exit probability surfaces:
- Positions appear healthier during monitoring than warranted
- Exits that should fire on CONFLICT are delayed
- α is systematically inflated by up to +0.20

---

### P10. New PolymarketClient() Per Order (MEDIUM)
**File:** `src/execution/executor.py` L264, L357

```python
        client = PolymarketClient()    # BUY path
        result = client.place_limit_order(...)
```
```python
        client = PolymarketClient()    # SELL path
        result = client.place_limit_order(...)
```

Each construction triggers credential resolution (macOS Keychain subprocess). ~100ms overhead per order plus subprocess failure risk on hot paths (backoff retry, day0 settlement rush).

---

### P12. Cycle Lock with No Watchdog (MEDIUM)
**File:** `src/main.py` L25-50

```python
_cycle_lock = threading.Lock()

def _run_mode(mode: DiscoveryMode):
    acquired = _cycle_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("%s skipped: another cycle is still running", mode.value)
        return
    try:
        summary = run_cycle(mode)
    finally:
        _cycle_lock.release()
```

If a cycle hangs (CLOB timeout, infinite retry), ALL subsequent cycles skip silently forever. No watchdog, no max duration, no alert. Heartbeat writer still runs → daemon appears alive.

---

### P13. Ensemble Cache — OVERSTATED (LOW)
**File:** `src/data/ensemble_client.py` L26-27

```python
CACHE_TTL_SECONDS = 15 * 60
_ENSEMBLE_CACHE: dict[tuple[str, float, float, str, str, int], dict] = {}
```

**Correction:** While structurally unbounded, TTL is 15 minutes and results are deep-copied via `_clone_result()`. In practice bounded by ~50-100 entries (unique city/model combos within 15-min window). "Unbounded" label overstates severity.

---

### P14. Phantom Position Persistence (CRITICAL)
**File:** `src/state/chain_reconciliation.py` L341-351

```python
    skip_voiding = active_local > 0 and len(chain_positions) == 0
    if skip_voiding:
        logger.warning(
            "INCOMPLETE CHAIN RESPONSE: 0 chain positions but %d local active. "
            "Skipping Rule 2 (void) to prevent false PHANTOM kills.",
            active_local,
        )
```

**Design intent:** Safety guard against API outages (empty chain response shouldn't void real positions).

**Actual failure mode:** Also fires when all positions are genuinely redeemed/settled on-chain. Chain returns 0 legitimately → `skip_voiding=True` → phantom positions persist indefinitely → block new entries (quarantine check), consume risk limits, create stale monitor cycles.

---

### P15. Duplicate Append in ExitContext (LOW)
**File:** `src/state/portfolio.py` L103-107

```python
        elif not self.fresh_prob_is_fresh and not self.day0_active:
            missing.append("fresh_prob_is_fresh")
            missing.append("fresh_prob_is_fresh")   # ← DUPLICATE
```

Bug: `"fresh_prob_is_fresh"` appended twice, inflating `missing` list length.

---

### P16. Harvester Uses `round()` Not WMO Half-Up (MEDIUM)
**File:** `src/execution/harvester.py` L680-681

```python
            settlement_value=(round(float(settlement_value))
                              if settlement_value is not None else None),
```

Python `round()` uses banker's rounding (half-even). WMO standard uses `floor(x + 0.5)` (half-up). Difference at half-integers:
- `round(0.5)` → `0` (Python)
- WMO half-up(0.5) → `1`

`round_wmo_half_up_value()` exists in `settlement_semantics.py` but is NOT imported in harvester.py. Settlement values stored in calibration pairs could be off by 1°F at boundaries.

---

## Contamination Vectors

### C1. Harvester Stale JSON Fallback (HIGH)
**File:** `src/execution/harvester.py` L715-742

`_settle_positions` loads `pc_phase_by_id` from `position_current` table as guard. If query fails (L742 `except`), falls through with `pc_phase_by_id = None` → stale portfolio state drives settlement.

### C2. Monitor Authority Bypass on Exception (HIGH)
**File:** `src/engine/monitor_refresh.py` L174-186

```python
        try:
            _unverified_pairs = _get_pairs(conn, city.cluster, _cal_season, authority_filter='UNVERIFIED')
        except Exception:
            _unverified_pairs = []
```

If `_get_pairs` throws → `_unverified_pairs = []` → authority check passes → proceeds as if verified.

### C3. Chain Recon skip_voiding (CRITICAL)
Same as P14. See above.

### C4. Exit Lifecycle No Fallback After Exhaustion (MEDIUM)
Position `exit_state` machine includes `backoff_exhausted` as terminal state with no auto-recovery.

### C5. Replay Synthetic Timestamp — UNVERIFIED
Would need to check `run_replay.py`. Not confirmed from code reads.

### C6. Dual-Write Order Inversion (HIGH)
Same as P1. See above.

### C7. Ledger sequence_no Not Unique — ❌ WRONG
**File:** `architecture/2026_04_02_architecture_kernel.sql` L67

```sql
    UNIQUE(position_id, sequence_no)
```

The schema HAS a `UNIQUE(position_id, sequence_no)` constraint. This contamination vector was incorrectly identified — the uniqueness IS enforced at the DB level.

---

## Time Bombs

### TB-1. DST Transition in `hours_since_open` (LOW)
**File:** `src/engine/monitor_refresh.py` L157-163, L348-354

```python
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
```

Assumes naive timestamps are UTC. In practice, `entered_at` is set with `datetime.now(timezone.utc).isoformat()` which includes timezone. Low risk unless legacy positions exist.

### TB-2. Season Flip Mid-Position (MEDIUM)
Position entered March 19 (DJF) settling March 21 (MAM) uses different calibration season for entry vs monitoring. `monitor_refresh` recomputes `season_from_date(target_d)` → may apply DJF Platt model to MAM settlement.

### TB-3. Gamma API Pagination — O(n) Growth (HIGH)
**File:** `src/execution/harvester.py` L309-340

```python
    while True:
        resp = httpx.get(f"{GAMMA_BASE}/events", params={
            "closed": "true", "limit": 200, "offset": offset,
        }, timeout=15.0)
```

No date filter. Each harvester cycle paginates ALL closed events (not just recent). As historical events accumulate, each cycle gets progressively slower. No dedup by event ID.

### TB-4. Year Boundary Lead Days (LOW)
For UTC+13 cities, `lead_days_to_date_start` at year boundary can produce off-by-one.

### TB-5. `smoke_test_portfolio_cap_usd` Never Removed (MEDIUM)
**File:** `src/engine/cycle_runner.py` L249-260

Comment says "Remove after first lifecycle observed" — still present. Permanent entry blocker when cap is hit.

### TB-6. Harvester Hourly vs Real-Time Settlement (MEDIUM)
Harvester runs every 1 hour. Settled position at minute 1 → up to 59 minutes delay. During this window, `monitor_refresh` still runs on the settled position.

### TB-7. APScheduler Drops Cycles Under Load (MEDIUM)
**File:** `src/main.py` L376-396

`max_instances=1, coalesce=True` on all discovery jobs. Combined with `_cycle_lock`, overlapping cycles are silently dropped.

**Additional finding:** `_harvester_cycle` job does NOT have `max_instances=1` — it's the only job without this guard, meaning multiple harvester cycles could run concurrently.

---

## Priority Fix Order (Recommended)

1. **P14/C3** — Phantom positions (CRITICAL): Add max-age or confirmed-empty-count before accepting chain 0 as truth
2. **P1/P11/C6** — Save before commit (HIGH): Reorder to commit-then-save
3. **P9** — Hardcoded AGREE (HIGH): Wire real GFS crosscheck into monitor_refresh
4. **C2** — Authority bypass (HIGH): Change `except Exception: _unverified_pairs = []` to re-raise or flag position
5. **TB-3** — Gamma pagination (HIGH): Add `since` date filter
6. **P16** — WMO rounding (MEDIUM): Import and use `round_wmo_half_up_value()`
7. **P4** — Evaluator monolith (HIGH): Extract into composable steps
8. **P12** — Watchdog (MEDIUM): Add max cycle duration + alert
9. **TB-7** — Harvester concurrency guard: Add `max_instances=1`
10. **C7 removal** — Remove false contamination vector from docs
