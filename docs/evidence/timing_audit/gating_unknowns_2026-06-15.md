# Gating Unknowns Audit — 2026-06-15

Investigation of two gating unknowns for the timing-fix plan.
Read-only. No code changed.

---

## U2 — Is `src/contracts/time_semantics.py` READ AT RUNTIME or TEST-ONLY?

### Verdict: **TEST_ONLY** (with one qualified offline exception)

The `time_semantics` module is **never imported by any runtime daemon path**.
All importer evidence:

| File | Classification | Import / Reference |
|---|---|---|
| `tests/test_time_semantics_relations.py:30` | TEST | `from src.contracts import time_semantics as ts` — the only direct import of the module |
| `tests/data/test_cycle_staleness_derivation.py:88` | TEST | `from src.contracts.time_semantics import _readiness_ttl_hours` |
| `tests/strategy/live_inference/test_rest_then_cross_policy.py:254` | TEST | `from src.contracts.time_semantics import REGISTRY` |
| `scripts/audit_time_semantics.py` | OFFLINE SCRIPT | standalone audit tool, not on the daemon boot/run path |
| `scripts/semantic_linter.py:226,238,256` | OFFLINE SCRIPT | `_check_time_semantics_symbol` — AST-level linter, not runtime |

No file in `src/engine/`, `src/execution/`, `src/events/`, `src/venue/`, `src/ingest/`, or `src/main.py` imports `time_semantics` or the `REGISTRY` object.

### Does any RUNTIME path call the REGISTRY entries at runtime?

No. The `executable_price_freshness_window` entry in `time_semantics.REGISTRY` has `source=lambda: 30.0` (line 624 of `src/contracts/time_semantics.py`). This lambda is **never called at runtime** — it is only evaluated by:
- `tests/test_time_semantics_relations.py` (the relational consistency test)
- `scripts/audit_time_semantics.py`

The **actual live freshness window** used by the daemon is defined independently in `src/contracts/executable_market_snapshot.py:47`:
```python
FRESHNESS_WINDOW_DEFAULT = timedelta(seconds=180)
```
This was widened from 30s → 180s in commit `2ce71dc9a8` (#122, 2026-06-15). That constant IS imported at runtime via `src/contracts/__init__.py:29` and consumed by the reactor's SELECTION lane.

### Key finding on the stale registry entry

The `time_semantics.REGISTRY` entry `executable_price_freshness_window` still has `source=lambda: 30.0` with `source_ref="src/main.py comments (30s executable-price freshness window)"`. This is **doc-rot / stale documentation**, not a live correctness bug. The REGISTRY value is consumed only by `test_time_semantics_relations.py` which uses it to assert relational consistency (that `warm_interval_seconds < executable_price_freshness_window`). The warm interval is 20s; 20s < 30s still holds; so the stale 30.0 does not cause a test failure either — it just documents the wrong number.

**The stale `executable_price_freshness_window=30.0` is doc-rot, not a live correctness bug.**

---

## U3 — Does the Polymarket integration capture a SERVER-SIDE venue timestamp?

### Verdict: **SDK_CARRIES_VENUE_TS** — Partially used; critical submit path discards it

The evidence splits by code path:

### Path 1: REST order submit (`executor.py` — entry and exit orders)

The `place_limit_order` SDK call returns a raw response dict. After the call returns, `ack_time` is immediately set to Zeus wallclock:

```python
# src/execution/executor.py:3835 (entry order path)
ack_time = datetime.now(timezone.utc).isoformat()
```

And again for exit orders at line 2758:
```python
ack_time = datetime.now(timezone.utc).isoformat()
```

This `ack_time` is then passed as **both** `observed_at` AND `venue_timestamp` when writing the order fact:

```python
# src/execution/executor.py:3029-3030
observed_at=ack_time,
venue_timestamp=ack_time,
```

And when persisting to `settlement_commands`:
```python
# src/execution/executor.py:3048-3049
"UPDATE settlement_commands SET zeus_submit_intent_time = COALESCE(zeus_submit_intent_time, ?), venue_ack_time = COALESCE(venue_ack_time, ?) WHERE command_id = ?",
(_zeus_submit_intent_time, ack_time, command_id),
```

The SDK response (`_submit_result_from_response` in `src/venue/polymarket_v2_adapter.py:3065`) does **not** extract any `created_at` / `timestamp` field from `raw_response` for timestamp purposes — it only extracts `order_id`, `tradeIDs`, and `transactionsHashes`. So `venue_timestamp` on the initial submit ACK fact = Zeus wallclock (`datetime.now()`), not a server-side timestamp.

### Path 2: WebSocket user-channel fills (`src/ingest/polymarket_user_channel.py`)

This path **does** attempt to use the venue server timestamp. For order facts (line 848):
```python
observed = _parse_dt(message.get("timestamp") or message.get("last_update"))
```
Then passes it as both `observed_at` and `venue_timestamp` (lines 857-858):
```python
observed_at=observed,
venue_timestamp=observed,
```

For trade facts (line 906):
```python
observed = _parse_dt(message.get("timestamp") or message.get("matchtime") or message.get("last_update"))
```
Then (lines 1045-1046):
```python
observed_at=observed,
venue_timestamp=observed,
```

If the WS message carries a `"timestamp"`, `"matchtime"`, or `"last_update"` field, Zeus parses and uses it. If absent, `_parse_dt` falls back to `_utcnow()` (Zeus wallclock) via:
```python
# src/ingest/polymarket_user_channel.py:109-110
if value is None or value == "":
    return fallback or _utcnow()
```

### Path 3: REST reconciliation (`src/execution/exchange_reconcile.py`)

The REST reconciliation trade fact write at line 3226 **does** attempt to pull a venue timestamp from the raw response:
```python
venue_timestamp=_first_present(raw, "timestamp", "created_at", "createdAt", default=None),
```
This will be non-None if the Polymarket REST polling endpoint returns any of those fields. For order facts (lines 1288, 1753) it uses `observed_at` (Zeus wallclock) as `venue_timestamp`.

### Summary table

| Code path | venue_timestamp source |
|---|---|
| Submit ACK (executor.py REST) | Zeus wallclock `datetime.now()` — server ts **discarded** |
| WS order fact (user_channel) | `message["timestamp"]` if present, else Zeus wallclock fallback |
| WS trade fact (user_channel) | `message["timestamp"]` or `message["matchtime"]` if present, else Zeus wallclock |
| REST reconcile trade fact | `raw["timestamp"]` / `raw["created_at"]` / `raw["createdAt"]` if present |
| REST reconcile order fact | Zeus wallclock (`observed_at`) |

**The Polymarket SDK/WS protocol does carry server-side timestamps** (`timestamp`, `matchtime`, `created_at`, `createdAt`) in WS and REST responses. Zeus uses them opportunistically in the WS and REST-reconcile paths. The **critical submit ACK path unconditionally overwrites with Zeus wallclock** — the submit response dict contains those fields in the raw payload (passed through `_legacy_order_result_from_submit` as `**payload`) but `ack_time = datetime.now(timezone.utc).isoformat()` is set immediately after the SDK call returns and the raw response fields are never inspected for a server timestamp at submit time.

**This is FIXABLE**: the `_submit_result_from_response` (v2 adapter) wraps the raw response in `raw_json` and passes it through as `envelope.raw_response_json`, so the server `created_at` / `timestamp` field in the POST response body (if present) is available but not currently extracted for `venue_timestamp` at the ACK step. The fix would be to extract `"timestamp"` or `"created_at"` from `result` (the `place_limit_order` return dict) and use it as `venue_timestamp` rather than `ack_time`, while keeping `ack_time` as `observed_at`.

**Caveat**: whether the Polymarket CLOB REST POST response actually includes a server-side `created_at` or `timestamp` field requires live SDK inspection or a network trace — the code is wired to extract it if present, but the current submit ACK path bypasses this extraction.
