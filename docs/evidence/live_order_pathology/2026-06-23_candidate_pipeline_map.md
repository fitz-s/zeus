# Entry-Candidate Pipeline Map
# Created: 2026-06-23
# Authority basis: src/main.py, src/events/continuous_redecision.py, src/data/market_scanner.py — live code audit

---

## Q1 — SCAN SCOPE: how many families does the screen evaluate per cycle?

**Answer: FULL UNIVERSE — all families with a cached belief are scanned. The scan is not capped to 2-4 families.**

### Trace

`edli_redecision_screen` (main.py:6253+) calls:

1. `beliefs = _all_latest_beliefs(world_ro)` (main.py:6307)
2. `redecisions = screen_entry_redecisions(world_ro, trade_ro, ..., beliefs=beliefs)` (main.py:6309)

`_all_latest_beliefs` (continuous_redecision.py:629–667):
```python
rows = conn.execute(
    "SELECT ... FROM probability_trace_fact "
    "WHERE decision_id >= ? AND decision_id < ?",
    (_BELIEF_PREFIX, _prefix_upper_bound(_BELIEF_PREFIX)),
).fetchall()
```
This is an **unbounded query over all `edli_belief:*` rows** in `probability_trace_fact`. It deduplicates by `(city, target_date, metric)` stable key and skips venue-closed families. No family count cap. If 49 cities have cached beliefs, all 49 are returned.

`screen_entry_redecisions` (continuous_redecision.py:1426–1471):
- Collects every `condition_id` from every belief (`all_cids`) — one price read for the entire batch (main.py line 1447: `read_freshest_executable_prices(trade_conn, condition_ids=all_cids)`)
- Iterates ALL beliefs × ALL bins × both directions through `enqueue_live_redecisions`

`enqueue_live_redecisions` (continuous_redecision.py:688–~800):
- Loops `for belief in beliefs` — iterates ALL beliefs, no count cap

**Conclusion for Q1:** `entry_candidates` in the log is the genuine count of edge-firing (belief×price > min_edge) pairs from a full-universe scan. 2-4 candidates out of 49 cities means the forecast+price edge filter is selective, NOT that only 2-4 families were scanned.

### Where `executable_candidate_city_count: 2` comes from

This counter is from `refresh_executable_market_substrate_snapshots` (market_scanner.py:4253):
```python
"executable_candidate_city_count": len(candidate_cities),
```
This reflects the *substrate warm cycle*, not the belief screen. It tells us how many cities had CLOB-fetchable snapshots inserted in that warm tick — not the belief universe size.

---

## Q2 — EDGE-FIRING vs SCAN

**The 2-4 candidates are genuine low-edge-fire-rate results from a full-universe scan, not a scan cap.**

### The edge screen (continuous_redecision.py:719–724)

```python
score = _entry_screen_robust_trade_score(
    q_posterior=posterior_q,
    q_lcb_5pct=float(conservative_q),
    price=float(quote.price),
)
if score < min_edge - _EPS:
    continue
```

`min_edge` defaults to `edli_cfg.get("redecision_screen_min_edge", 0.01)` (main.py:6298).

Additional kills:
1. `quote = price_lookup.get(legacy_key)` — if no fresh snapshot exists for the condition, `quote is None` → `continue` (line 707). This is the main killer: bins with no fresh executable snapshot simply have no price to screen against.
2. `_parse(quote.freshness_deadline) <= dt` → stale price skip (line 709)
3. `conservative_q is None` → no q_lcb evidence (line 712)
4. `_full_economics_reject_still_blocks(...)` → recent full-cert rejection still valid (line 733)
5. `acted_state` cooldown — pair re-fires only if edge improves by `IMPROVE_DELTA` (2 ticks) since last act (lines ~750+)

The families that produce no candidates are those where: (a) no fresh executable snapshot exists (price_lookup miss), or (b) q_lcb score doesn't clear min_edge after fees, or (c) cooldown still active.

`filter_redecisions_with_spine_members` (continuous_redecision.py:1572–1627) further reduces the list: only keeps redecisions where ≥3 distinct `raw_model_forecasts` members exist for the family's latest posterior source-cycle date. Families with <3 raw model members are dropped here.

**Conclusion for Q2:** `entry_candidates=2-4` is correct and expected for a full scan. The universe is scanned; only those with fresh price + positive q_lcb edge + spine members fire.

---

## Q3 — SNAPSHOT-CONFIRM BOTTLENECK

**The snapshot-confirm is the dominant gate. PARTIAL or NONE coverage causes the entire emit to be dropped. DB-lock contention (757× in logs) is a contributing factor but the architecture has a deeper structural problem: even PARTIAL coverage triggers a full skip.**

### The warm cycle budget

`_refresh_pending_family_snapshots` (main.py:2874+), called from `_edli_market_substrate_warm_cycle`:
- Wall-clock budget: `ZEUS_REACTOR_REFRESH_BUDGET_SECONDS` default **17.0s** (main.py:3067)
- Snapshot reserve: `ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS` default **12.0s** (main.py:3071)
- Topology budget: ~5s (refresh_budget - snapshot_reserve)
- Warm cycle interval: `_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0s` (main.py:96)

For ~150 live families, with `reconstruct_weather_market_from_static_topology` at ~225ms each, a full sweep takes ~34s → two warm cycles. Fair rotation cursor (main.py:3043–3049) splits this across cycles.

### The confirm-refresh call

`_edli_refresh_continuous_money_path_families` (main.py:6876–6965) calls `_refresh_pending_family_snapshots` with `include_pending_families=False` and `extra_priority_families=clean_families` (main.py:6936–6937). This means it ONLY refreshes the families that already have cheap-screen edge — a small targeted set. Budget: `ZEUS_REDECISION_CONFIRM_REFRESH_LOCK_TIMEOUT_SECONDS` default **25.0s** (main.py:6901).

### DB-lock contention mechanism

`refresh_executable_market_substrate_snapshots` (market_scanner.py:4121+):
- Per-row WAL write lock: `conn.commit()` per item (market_scanner.py:4213) — fixed design to avoid holding the write lock across HTTP calls
- Busy timeout: floor **4000ms**, ceiling **8000ms** (`ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS=4000`, market_scanner.py:117)
- Retry count: `ZEUS_SNAPSHOT_CAPTURE_SQLITE_LOCK_RETRIES` default **2** (market_scanner.py:128)
- Contention sources: executor submit path, exit lifecycle, CollateralLedger heartbeat — all independent trade DB connections not serialized by in-process mutex

On "database is locked" → retry with `time.sleep(min(0.05 * attempt, remaining))` (line 4231). With 757 lock events, this sleep-and-retry eats wall-clock budget → `budget_exhausted=1` → `coverage_status="PARTIAL"` or `"NONE"`.

### The structural PARTIAL=skip bug

`_edli_confirmation_refresh_unavailable` (main.py:6996–7007):
```python
coverage = str(summary.get("executable_substrate_coverage_status") or "")
if coverage in {"NONE", "PARTIAL"}:
    return True
```

`_edli_confirmation_refresh_needs_family_freshness_filter` (main.py:7010–7017) catches `status=="refreshed" AND coverage=="PARTIAL"` — but then `_edli_families_with_fresh_executable_substrate` (main.py:7020+) checks each family individually and only admits those where ALL condition_ids have fresh YES+NO snapshots (line 7074: `all(_condition_buy_sides_fresh(...) for cid in condition_ids)`). If that returns an empty set, `confirmed_entry_scope` becomes empty → return at main.py:6406. The net effect: PARTIAL coverage from DB-lock contention drops ALL candidates even if 1-2 families fully refreshed.

**Conclusion for Q3:** The DB-lock contention is real (757 hits), but the DEEPER structural issue is that `coverage=PARTIAL` → `_edli_confirmation_refresh_unavailable=True` → full skip. If any row fails during snapshot capture due to lock contention, the whole tick's candidates are dropped. The family-level freshness filter is the only escape hatch (PARTIAL → per-family check), but it still requires ALL condition_ids of a family to be fresh.

---

## Q4 — CONTINUOUS RE-PRICING

**Re-pricing covers ALL open resting orders, not just snapshot-confirmed families. However, rest-pulls are also subject to the same snapshot-confirm gate before cancel+re-decide is emitted.**

### `screen_resting_orders` (continuous_redecision.py:1669–1758)

Called at main.py:6337–6342 and again at main.py:6454–6459 with ALL open rests (`open_rests = _edli_open_maker_rests_for_screen(trade_ro, world_ro, beliefs=beliefs)`).

Three re-price triggers per rest:
1. **Belief-decay** (line 1690): `screen_reprice(...)` — pulls if posterior changed by ≥ `BELIEF_REPRICE_DELTA` (3 ticks = 0.03) on NEW snapshot evidence (anti-twitch: only fires on new snapshot_id)
2. **Book-moved** (line 1722): `drift >= REST_BOOK_DRIFT_TICKS * TICK_SIZE - _EPS AND rest.quote_age_ms >= value_refresh_min_age_seconds * 1000.0` — pulls if best bid moved ≥1 tick AND rest is ≥ `REST_VALUE_REFRESH_MIN_AGE_SECONDS` (300s = 5min) old. **Note 2026-06-23 fix:** the age guard was ADDED to BOOK_MOVED to prevent sub-floor pull-requeue loops (comment at line 1709–1720).
3. **Confirmed-value refresh** (line 1729): if rest age ≥300s AND q_lcb still clears ask after fees → `CONFIRMED_VALUE_REFRESH` pull.

`read_freshest_resting_best_bids` and `read_freshest_executable_prices` are called ONCE for all `condition_ids` from all rests (batch read, lines 1685–1686). This is a full coverage read over all open rests.

### Re-pricing gate

`rest_pulls` produced by `screen_resting_orders` flows into `rest_pull_families` (main.py:6356–6364). These families go into `confirm_families` (main.py:6374) and through the same `_edli_refresh_continuous_money_path_families` snapshot-confirm gate (main.py:6377). Then `confirmed_rest_scope &= fresh_confirmed_families` (main.py:6388). If snapshot-confirm fails (NONE/PARTIAL coverage), rest-pull cancels are also dropped this tick (main.py:6406).

**Conclusion for Q4:** `screen_resting_orders` correctly re-prices ALL open rests each tick (not just a few), using fresh bid+ask from trades DB. But the cancel+re-decide emit is blocked by the same snapshot-confirm gate that blocks entry candidates. So re-pricing detection is continuous and complete; the *action* (cancel emit) is gated by snapshot freshness.

---

## Q5 — PAUSE INTERACTION

**The entries pause gates only final SUBMIT inside `executor.py`. Generation, screening, snapshot-confirm, and re-pricing all run regardless of pause state.**

### Where pause is checked

`executor.py:4390–4411`:
```python
entries_pause_component = _entry_control_pause_component(conn)
if not entries_pause_component.get("allowed"):
    reason = str(entries_pause_component.get("reason") or "entries_paused")
    ...
    return OrderResult(..., status="rejected", reason=f"entries_paused:{reason}")
```
This is inside `_live_order` — the function called only when the reactor actually attempts to submit an entry order to the venue.

In `edli_redecision_screen` (main.py:6253+): **no `is_entries_paused()` check anywhere in the function**. The screen runs fully: belief load, edge screen, spine filter, snapshot-confirm, family freshness check, EDLI_REDECISION_PENDING emit — all proceed without consulting the pause flag.

In `continuous_redecision.py`: no pause check of any kind.

In the reactor's event consumption path (main.py ~5400+), pause is enforced at the point of `_live_order` call via `_entry_control_pause_component(conn)` inside executor.py.

### Boot-time pause check

`_boot_deployment_freshness_auto_resume()` (main.py:4240+) checks and may clear a `deployment_freshness_4h_divergence` pause at startup — but this is not a per-tick gate.

**Conclusion for Q5:** Entries pause blocks only the final venue submit call. All upstream pipeline stages — universe belief load, edge screen, spine filter, snapshot capture, snapshot-confirm, candidate emit, and rest-pull detection — run unconditionally regardless of pause state.

---

## CONCLUSION

**Scan scope:** FULL UNIVERSE (all `edli_belief:*` families). `entry_candidates=2-4` is the genuine count of edge-firing families after price × q_lcb × min_edge × spine-member filters, NOT a scan cap. The pipeline is correctly scanning the full belief universe.

**Edge-firing count meaning:** Mostly healthy — the low count reflects few families having simultaneous fresh executable price + positive q_lcb edge + ≥3 raw model spine members. The dominant suppressor is missing fresh executable snapshots (price_lookup miss in `enqueue_live_redecisions`).

**Snapshot-confirm / DB-lock bottleneck:** This is the DOMINANT OPERATIONAL CHOKEPOINT. The 757× "database is locked" events exhaust the per-tick snapshot capture budget → `coverage_status=PARTIAL` or `NONE` → `_edli_confirmation_refresh_unavailable=True` → full emit skip (main.py:6407–6417). Even when coverage is PARTIAL and the per-family freshness filter (`_edli_families_with_fresh_executable_substrate`) runs, it requires ALL condition_ids of a family to have fresh YES+NO snapshots — a strict all-or-nothing per-family gate.

**Re-pricing coverage:** `screen_resting_orders` covers ALL open rests each tick with fresh prices. But the cancel+re-decide emit is blocked by the same snapshot-confirm gate, so detected reprices are silently dropped when coverage is PARTIAL/NONE.

**Pause interaction:** Entries pause blocks only final venue submit. Generation, screening, confirm, and re-pricing run unconditionally.

**TOP FIX SEAM:** `main.py:6996–7007` — `_edli_confirmation_refresh_unavailable` treats `coverage=PARTIAL` as unavailable, causing a full tick skip whenever any DB-lock causes even one snapshot to fail. The fix is to allow PARTIAL coverage to pass through to the per-family freshness filter (`_edli_families_with_fresh_executable_substrate`) rather than treating it identically to NONE. Currently only `status=="refreshed" AND coverage=="PARTIAL"` (not lock failures) routes to the per-family path (via `_edli_confirmation_refresh_needs_family_freshness_filter`); if the DB-lock retry path also sets `status="refreshed"` with `coverage="PARTIAL"`, it hits `_edli_refresh_summary_has_sqlite_lock_failures=True` → the lock check at line 7002 returns True → full skip, bypassing the per-family filter. Fix: decouple the DB-lock failure signal from the coverage-PARTIAL skip; let PARTIAL coverage always route to the per-family freshness check regardless of lock history.

