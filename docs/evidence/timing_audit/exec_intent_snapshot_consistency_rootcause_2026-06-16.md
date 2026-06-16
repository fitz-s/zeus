# Pre-venue Reject Root-cause: Intent vs Snapshot Consistency
## Trace date: 2026-06-16
## Author: oh-my-claudecode:tracer

---

## SUMMARY (ranking by no-trade impact)

| Rank | Failure | Count | Root-cause verdict | Fix direction |
|------|---------|-------|--------------------|---------------|
| 1 | event_id namespace mismatch | 42 | CONFIRMED BUG — partially fixed; residual under a specific condition | Plug the `trade_conn is None` gap in `_snap_for_context` hydration |
| 2 | tick_size mismatch | 28 | CONFIRMED historical bug (BUG #92), fix present in changeset | Verify the `_required_bound_tick_size` path is reached for all MAKER orders |
| 3 | expected_fill_price mismatch | 9 | Likely related to tick_size fix gap; secondary sweep drift | Same root fix as #2; separate sweep alignment issue |
| 4 | decision_source_context provenance | 8 | Source-run `source_available_at` NULL in DB rows; partially interacts with C1-AVAIL-CLOCK | Backfill NULL `source_available_at` in `source_run`; verify all `ens_result` fields non-None |

The single highest-value fix is resolving the `trade_conn is None` branch in failure 1 (42 events), which also unblocks the tick_size and fill_price paths since those downstream checks never trigger when the event_id check fires first.

---

## FAILURE 1: EVENT_ID NAMESPACE MISMATCH (42 events — highest priority)

### Observation

Reject message: `FinalExecutionIntent event_id does not match executable snapshot: intent='edli_evt_03f2cd42...' snapshot='highest-temperature-in-warsaw-on-june-7-2026'`

The intent carries an EDLI event-hash id; the snapshot carries a Gamma/CLOB market slug. These are two different identifier namespaces that can never be equal.

### THE CODE

**Validation site:**
`src/execution/executor.py:1891-1899`
```python
snapshot_event_id = str(event_id or "").strip()
intent_event_id = str(intent.event_id or "").strip()
if intent_event_id and snapshot_event_id and intent_event_id != snapshot_event_id:
    raise ValueError(
        "FinalExecutionIntent event_id does not match executable snapshot: "
        f"intent={intent_event_id!r} snapshot={snapshot_event_id!r}"
    )
```

`event_id` here is the RETURN VALUE of `_final_intent_snapshot_metadata` (executor.py:2070), specifically `snapshot.event_id` (executor.py:1854/1865). The snapshot is loaded by PK from `intent.snapshot_id` (executor.py:1804).

**Snapshot event_id source:**
`src/state/snapshot_repo.py:233` — reads `row["event_id"]`.
`src/data/market_scanner.py:2950`:
```python
event_id=str(market.get("event_id") or market.get("id") or ""),
```
This is a Gamma numeric market event id or the market's slug, populated from the Gamma/CLOB API payload.

**Intent event_id source:**
`src/engine/event_bound_final_intent.py:288`:
```python
event_id=str(final_payload.get("market_event_id") or final_payload["event_id"]),
```
If `market_event_id` is present and non-None, the intent gets the snapshot's Gamma event id — matching the snapshot. If `market_event_id` is None, the intent falls back to `final_payload["event_id"]` which is the EDLI event hash (`edli_evt_...`) from `action["event_id"]` (certificates/execution.py:160), causing the namespace mismatch.

**market_event_id population:**
`src/decision_kernel/certificates/execution.py:162-166`:
```python
"market_event_id": _market_context_value(
    executable_market_context,
    "event_id",
    executable_snapshot_cert.payload.get("event_id"),
),
```
`executable_market_context` comes from `_executable_market_context_from_snapshot(_snap_for_context)` (event_reactor_adapter.py:4491). This returns None if `_snap_for_context` is None.

**`_snap_for_context` hydration (the critical gap):**
`src/engine/event_reactor_adapter.py:4311-4333`:
```python
_snap_for_context = None
if trade_conn is not None:        # <--- CONDITIONAL on trade_conn
    _snap_id_for_context = str(
        executable_snapshot.payload.get("identity")
        or executable_snapshot.payload.get("selected_snapshot_id")
        or ""
    )
    try:
        _snap_for_context = get_snapshot(trade_conn, _snap_id_for_context) if _snap_id_for_context else None
    except Exception:
        _snap_for_context = None
```

The code comment at lines 4311-4322 explicitly documents a fix applied for the MAKER path ("live 2026-06-12 00:52-01:13Z, five maker intents PRE_SUBMIT_ERROR"), but the fix itself is still gated on `trade_conn is not None`. When `trade_conn` is None at the call site, `_snap_for_context` remains None → `executable_market_context` is None → `market_event_id` in the cert is None → `event_bound_final_intent.py:288` falls back to `final_payload["event_id"]` (the EDLI hash) → namespace mismatch → pre-venue reject.

### ROOT CAUSE

Population bug, not a comparison bug. The comparison logic at executor.py:1895 is correct (compare two strings from the same namespace). The defect is that `_snap_for_context` is only hydrated when `trade_conn is not None` (event_reactor_adapter.py:4324). When `trade_conn` is None, `market_event_id` is not written into the cert, causing the fallback to the EDLI event hash namespace.

### COMPETING HYPOTHESES

| Hypothesis | Evidence For | Evidence Against | Verdict |
|---|---|---|---|
| H1: `trade_conn is None` gap leaves `_snap_for_context = None` → `market_event_id = None` → fallback to EDLI hash | Code at era:4323-4333; comment era:4311-4322 explicitly documents the MAKER fix as "loading the object ONLY inside the TAKER depth block"; `event_bound_final_intent.py:288` confirms the fallback | The fix comment says it was applied 2026-06-12; but the fix is only applied when `trade_conn` is not None | CONFIRMED as residual gap |
| H2: `executable_snapshot.payload.get("identity")` is empty, causing `_snap_for_context = None` even when `trade_conn` is present | Could explain some failures | The snapshot_id should always be set since the cert was built successfully (the candidate cleared all upstream gates); this would fail earlier | LESS LIKELY — secondary possibility |
| H3: snapshot row's `event_id` column itself is NULL in old DB rows | Some markets may have been captured before `event_id` was populated | executor.py:1893 `str(event_id or "")` → empty string → the `if intent_event_id and snapshot_event_id` guard at line 1895 would SKIP the check if snapshot_event_id is empty | RULED OUT — the reject message shows a non-empty slug, confirming event_id IS present in the snapshot row |

### REBUTTAL

Strongest challenge to H1: the code comment says the maker fix was applied on 2026-06-12 and five maker intents that died pre-venue are mentioned as the trigger. Did the fix fully resolve it?

Answer: No. The fix (loading `_snap_for_context` outside the TAKER block) is itself conditional on `trade_conn is not None`. When the adapter function is called without a `trade_conn`, the entire hydration block is skipped. The call at event_reactor_adapter.py:1765-1772 passes `trade_conn=trade_conn` where `trade_conn` is optional (`sqlite3.Connection | None = None`, era:4102). The 42 live failures post-date 2026-06-12, confirming the fix is partial.

### FIX DIRECTION

In `_build_live_execution_command_certificates` (event_reactor_adapter.py:4096), when `trade_conn is None`, attempt to hydrate `_snap_for_context` using an alternative DB connection (e.g., `live_cap_conn`), OR fall back to the `executable_snapshot.payload.get("event_id")` field already present in the executable_snapshot cert payload (which IS bound to the snapshot's actual event_id from the market scanner capture). The fallback in `_market_context_value` already accepts a default: certificates/execution.py:165 `executable_snapshot_cert.payload.get("event_id")` — that default IS the correct Gamma event id stored in the snapshot payload. The fix is ensuring that default is always populated in the `executable_snapshot_cert.payload` so the fallback works.

**Confidence: HIGH** — code path fully traced, residual `trade_conn is None` gap confirmed by code inspection and consistent with the reported failure volume (42, the largest category).

---

## FAILURE 2: TICK_SIZE MISMATCH (28 events)

### Observation

Reject message: `FinalExecutionIntent tick_size does not match executable snapshot`

Intent carries `tick_size` value that differs from `snapshot.min_tick_size` when the executor re-hydrates the snapshot row from DB.

### THE CODE

**Validation site:**
`src/execution/executor.py:1828-1829`:
```python
if intent.tick_size != snapshot.min_tick_size:
    raise ValueError("FinalExecutionIntent tick_size does not match executable snapshot")
```

Snapshot is loaded at executor.py:1804 from `intent.snapshot_id`. Both `intent.tick_size` and `snapshot.min_tick_size` are `Decimal`.

**Intent tick_size source:**
`src/engine/event_reactor_adapter.py:4517`:
```python
tick_size=str(_snap_for_depth.min_tick_size) if _snap_for_depth is not None else _required_bound_tick_size(_snap_for_depth, executable_snapshot.payload),
```

For TAKER+`trade_conn`, `_snap_for_depth = _snap_for_context` (era:4339). For MAKER or TAKER without `trade_conn`, `_snap_for_depth = None` → falls to `_required_bound_tick_size(None, executable_snapshot.payload)`.

**`_required_bound_tick_size` logic:**
`src/engine/event_reactor_adapter.py:14930-14959`:
Reads `executable_snapshot_payload.get("min_tick_size")`. If present, returns `str(Decimal(str(payload_tick)))`.

The executor re-hydrates via `get_snapshot(conn, intent.snapshot_id)` at executor.py:1804. The validator compares `intent.tick_size` (from the cert payload) against `snapshot.min_tick_size` (from the DB row, a `Decimal`).

**Historical bug (BUG #92):**
Comment at era:4502-4514 describes the pre-fix behavior: "the pre-fix `_float_or_default(..., 0.01)` silent default was an UNBOUND tick source: when the canonical tick disagreed with a hardcoded 0.01 the intent diverged from the executor's snapshot (live 2026-06-01: intent tick=0.001 vs bound snapshot tick=0.01 → 28 EXECUTOR_PRE_VENUE_REJECTED)". The fix is present in this changeset.

### ROOT CAUSE

### COMPETING HYPOTHESES

| Hypothesis | Evidence For | Evidence Against | Verdict |
|---|---|---|---|
| H1: Residual from the SAME `_snap_for_context = None` gap as failure 1 — when `trade_conn` is None, `_snap_for_depth = None` too, and `_required_bound_tick_size` reads the snapshot payload instead of the live snapshot object. If `executable_snapshot.payload["min_tick_size"]` was serialized as a float (e.g., 0.01) but the DB row has Decimal("0.001"), Decimal comparison fails. | Both `_snap_for_depth` and `_snap_for_context` are None in the same `trade_conn is None` scenario; BUG #92 comment confirms tick drift caused 28 rejects historically | The `_required_bound_tick_size` normalizes through `Decimal(str(payload_tick))` — should match if the payload was originally copied from the same snapshot row | PLAUSIBLE — the serialization path matters; string→Decimal normalization may not preserve all tick representations |
| H2: Snapshot was recaptured (new snapshot_id) between cert-build time and executor validation, but `intent.snapshot_id` still points to old snapshot with different tick | `_recapture_fresh_entry_snapshot_if_needed` at executor.py:1934 runs AFTER `_final_intent_snapshot_metadata` (which validates tick_size). The recapture function validates `fresh.min_tick_size != final_intent.tick_size` at line 1983 and raises if different — so it can't silently swap a tick-divergent snapshot | The recapture runs on `legacy_intent` not `intent`; the `_final_intent_snapshot_metadata` validates against the ORIGINAL snapshot at `intent.snapshot_id` which is immutable by this point | PARTIALLY RULED OUT — the tick check would catch it, but would result in a `ValueError` wrapped as `PreVenueSubmitError` anyway |
| H3: BUG #92 fix incomplete — the `_required_bound_tick_size` fallback reads `executable_snapshot.payload.get("min_tick_size")` which may have been set from a DIFFERENT snapshot (e.g., a JIT book refresh path that re-elected a different snapshot before cert build) | The cert-build site uses `proof.executable_snapshot_id` as the snapshot identity; if the JIT path re-elected a different snapshot ID but the `executable_snapshot.payload` still carries the OLD tick, drift occurs | Complex; would require verifying the JIT path in detail | POSSIBLE but lower evidence weight than H1 |

### REBUTTAL

BUG #92 is documented as fixed in this changeset. The remaining 28 failures after the fix comment date (2026-06-01) either:
(a) Are the SAME 28 counted before the fix was deployed (the comment says "live 2026-06-01: 28 EXECUTOR_PRE_VENUE_REJECTED" — these may be historical events not new ones), OR
(b) Represent a residual where `_required_bound_tick_size` returns a value that doesn't match the DB decimal precisely.

The grounded context says the 28 are from the Jun 1-16 window. If the fix at era:4517 landed before the observation period, the 28 must be explained by H1 or H3.

**Confidence: MEDIUM** — BUG #92 fix is present; residual cause is either the `trade_conn is None` tick path or tick serialization drift. A live query confirming whether the 28 events cluster pre-2026-06-12 or post would discriminate H1 vs historical pre-fix.

### FIX DIRECTION

Ensure `executable_snapshot.payload["min_tick_size"]` is always populated from the BOUND snapshot row (not hardcoded or defaulted). The TAKER path already ensures this via `_snap_for_depth.min_tick_size`. The MAKER / no-`trade_conn` path must also either (a) hydrate `_snap_for_context` unconditionally (same fix as failure 1), or (b) ensure the `executable_snapshot` cert payload carries `min_tick_size` as a canonical Decimal string sourced from the DB snapshot row rather than from any legacy float default.

---

## FAILURE 3: EXPECTED_FILL_PRICE MISMATCH (9 events)

### Observation

Reject message: `FinalExecutionIntent expected_fill_price_before_fee does not match executable snapshot sweep`

The executor simulates a CLOB sweep against the DB snapshot (executor.py:1834-1840) and compares `sweep.average_price` against `intent.expected_fill_price_before_fee` at executor.py:1860.

### THE CODE

**Validation site:**
`src/execution/executor.py:1855-1864`:
```python
if sweep.depth_status != "PASS" or sweep.average_price is None:
    raise ValueError(
        "FinalExecutionIntent executable depth validation failed: "
        f"{sweep.depth_status}"
    )
if sweep.average_price != intent.expected_fill_price_before_fee:
    raise ValueError(
        "FinalExecutionIntent expected_fill_price_before_fee does not match "
        "executable snapshot sweep"
    )
```

The sweep is against the snapshot loaded at line 1804 (`intent.snapshot_id`). Intent's `expected_fill_price_before_fee` is set in the cert builder from `sweep_expected_fill_price` (era:4487-4490), which is the average_price of the TAKER depth sweep done at cert-build time (era:4442-4489) against `_snap_for_depth`.

**Fill price source (TAKER):**
`src/engine/event_reactor_adapter.py:4487-4490`:
```python
sweep_expected_fill_price = (
    str(_venue_quantized_sweep.average_price)
    if _venue_quantized_sweep.average_price is not None else None
)
```
This is computed against `_snap_for_depth` at cert-build time. The executor re-sweeps against `snapshot` (loaded from DB by `intent.snapshot_id`) at submit time. If the book depth changed between cert-build and submit, the two sweeps will yield different VWAPs.

**Fill price source (MAKER):**
For MAKER (post_only), the validator at executor.py:1841-1854 takes the `post_only_passive_limit` branch: `return snapshot.gamma_market_id, snapshot.event_id` — it does NOT run the sweep-average check. So fill price mismatch affects TAKER only.

### ROOT CAUSE

### COMPETING HYPOTHESES

| Hypothesis | Evidence For | Evidence Against | Verdict |
|---|---|---|---|
| H1: Book depth changed between cert-build (JIT sweep) and executor validation (submit-time sweep). The cert-build sweep used `_snap_for_depth` (at cert-build); the executor uses the same snapshot_id but orderbook may have changed if the DB snapshot was recaptured at a different time | The snapshot stored in DB has `orderbook_depth_jsonb` frozen at capture time; executor.py:1804 loads the SAME snapshot_id; the depth CAN'T change because the snapshot is immutable | CONCLUSIVE AGAINST — both sweeps use the same snapshot row's frozen depth |
| H2: The cert-build sweep used `_snap_for_depth` (loaded via `get_snapshot(trade_conn, _snap_id_for_context)`) while the executor loads `get_snapshot(conn, intent.snapshot_id)`. If `_snap_id_for_context` != `intent.snapshot_id`, the cert-build sweep was done against a DIFFERENT snapshot than the executor validates against | The cert-build sets `snapshot_id = cost_basis.quote_snapshot_id`; the JIT block sets `_snap_for_depth = _snap_for_context` which uses `executable_snapshot.payload.get("identity") or payload.get("selected_snapshot_id")`. If the `identity` field doesn't exactly match `cost_basis.quote_snapshot_id`, two different snapshots are used | Era:4504-4508 comment confirms both must be the SAME `proof.executable_snapshot_id`; a snapshot_id mismatch here is a provenance fault | POSSIBLE residual if JIT snapshot election diverges from cost_basis snapshot |
| H3: The `available_crossable_shares` used in the cert-build sweep (era:4461-4483) was venue-quantized, but the executor sweep at line 1834 uses `submitted_shares = _final_intent_submit_shares(intent)` from `intent.submitted_shares`. If `_venue_quantized_shares != intent.submitted_shares`, the two VWAPs diverge even on the same book | Venue quantization at era:4462-4468 produces `_venue_quantized_shares`; the cert stores `submitted_shares` from this quantized value (via `build_final_intent_certificate_from_actionable`). If both sweeps use the same shares and the same snapshot, the VWAPs must be equal | If the rounding in quantize_submit_shares uses different arithmetic than the cert's stored submitted_shares, drift occurs | POSSIBLE — rounding-mode mismatch between builder and executor sweep |

### REBUTTAL

H1 is ruled out definitively (frozen snapshot). H2 is the most mechanically plausible: if `executable_snapshot.payload.get("identity")` differs from `cost_basis.quote_snapshot_id`, two different snapshot rows are swept. The cert comment (era:4502-4508) explicitly identifies this as a correctness invariant that must hold. Whether it holds is not verifiable by static analysis — requires a live query comparing `final_intent cert payload.executable_snapshot_id` against `executable_snapshot.payload['identity']` in the stored certs for the 9 rejections.

**Confidence: MEDIUM** — the mechanics are clear; the discriminating unknown is which of H2/H3 applies to the specific 9 events.

### FIX DIRECTION

Ensure the cert-build JIT sweep uses the SAME snapshot_id as `cost_basis.quote_snapshot_id`. Add an assertion in cert-build that `str(executable_snapshot.payload.get("identity") or "") == str(cost_basis.quote_snapshot_id or "")` before running the sweep. If they differ, fail closed rather than running the sweep against the wrong snapshot.

---

## FAILURE 4: DECISION_SOURCE_CONTEXT MISSING PROVENANCE (8 events)

### Observation

Reject message: `FinalExecutionIntent decision_source_context failed integrity: missing_model_family, missing_forecast_issue_time, missing_forecast_valid_time, missing_forecast_fetch_time, missing_forecast_available_at, missing_raw_payload_hash, missing_degradation_level, missing_forecast_source_role, missing_authority_tier, missing_decision_time, missing_decision_time_status`

All 11 required fields are missing simultaneously, not a subset. This is an all-or-nothing absence pattern.

### THE CODE

**Validation site:**
`src/execution/executor.py:1882-1891` — calls `context.integrity_errors()`.

**`DecisionSourceContext.integrity_errors`:**
`src/contracts/execution_intent.py:865-887` — for each of the 12 required fields, appends `f"missing_{field}"` if the value is falsy.

**`DecisionSourceContext` construction:**
`src/engine/event_bound_final_intent.py:201-202`:
```python
decision_source_payload = final_payload.get("decision_source_context")
decision_source_context = DecisionSourceContext.from_forecast_context(decision_source_payload)
```
`from_forecast_context` (execution_intent.py:834) returns None if `decision_source_payload` is not a Mapping; `from_forecast_context` is called and if None, raises `EventBoundExecutorExpressibilityError` at line 203-204 — NOT the `decision_source_context failed integrity` error. So `from_forecast_context` DID return a `DecisionSourceContext`, but all fields are empty strings.

**`from_forecast_context` field mapping:**
`execution_intent.py:838-863` — maps `context.get("forecast_source_id")`, `context.get("model_family")`, `context.get("forecast_issue_time")`, `context.get("forecast_available_at")`, etc. If the input Mapping lacks these keys (or has None values), `_context_text(None)` returns `""` → all fields are empty → all 11 errors.

**How the Mapping could lack all keys:**
Path: event_reactor_adapter.py:4498 `decision_source_context=forecast_authority.payload` → certificates/execution.py:730-750 `_decision_source_context_payload` copies the mapping → final_intent cert payload `"decision_source_context"` dict is the copy. Then event_bound_final_intent.py:201 reads it back.

The `_forecast_authority_payload_and_clock` (era:6554-6634) populates `model_family` at line 6587 as `ens_result.get("model")`. In `to_ens_result` (executable_forecast_reader.py:140) `"model"` is hardcoded `"ecmwf_ens"` — so it is never None. Similarly `forecast_available_at` at era:6591 is `ens_result.get("available_at")` = `evidence.source_available_at` (executable_forecast_reader.py:160), which is `str(source_run["source_available_at"])` (executable_forecast_reader.py:1134).

**All-fields-empty scenario:**
If `source_run["source_available_at"]` is NULL in the DB, the evidence read at line 1076-1077 returns `None, "SOURCE_AVAILABLE_AT_MISSING"` — which causes `_read_executable_forecast_bundle_result` to return a non-ok result → `_forecast_authority_payload_and_clock` raises `ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:...")` before the payload is even constructed. So a NULL `source_available_at` would block the entire cert build, not produce empty fields.

This means the 8 failures must represent cases where `_forecast_authority_payload_and_clock` SUCCEEDED (returning a payload), but the payload's fields mapped through `from_forecast_context` came out empty. This can happen if the `ens_result` dict returns None for all keys — meaning a legacy-path forecast that doesn't use `ExecutableForecastBundle.to_ens_result()`.

### C1-AVAIL-CLOCK INTERACTION CHECK

`src/contracts/availability_time.py` — `proof_of_possession_available_at` was introduced as the canonical source of `available_at`. `src/data/replacement_forecast_materializer.py:1567-1590` — the C1-AVAIL-CLOCK block computes new `available_at` for FUSED posteriors. `src/data/ecmwf_open_data_ingest.py:346-354` — C1-AVAIL-CLOCK comment notes the ingest now emits `None` for `available_at` when no possession time is known (instead of using `run_init_dt`).

**Key finding:** The ingest at `ecmwf_open_data_ingest.py:354` now emits `"available_at": provenance["available_at"]` where `provenance["available_at"]` can be None if no genuine possession time exists. The comment says "downstream is None-safe: ensemble_client only sets available_at when non-None". But the `source_run_repo.py:132` writer stores `_to_iso(source_available_at)` — and `_to_iso(None)` returns None, writing NULL to `source_run.source_available_at`. The `executable_forecast_reader.py:1076-1077` check catches this as `SOURCE_AVAILABLE_AT_MISSING` → the ENTIRE evidence bundle is blocked → `_forecast_authority_payload_and_clock` raises → the cert build fails early, before any `decision_source_context` is populated.

So C1-AVAIL-CLOCK does NOT directly cause the all-fields-empty pattern. It causes an earlier FORECAST_AUTHORITY_EVIDENCE_MISSING failure, not a `missing_*` integrity error.

**Most likely cause of all-fields-empty pattern:**
An alternative `forecast_authority` cert path that does not go through `_forecast_authority_payload_and_clock` but still produces a Mapping. Looking at `src/decision_kernel/adapters/forecast_authority_adapter.py:15-55` — `build_forecast_authority_certificate` with `source_available_at=decision_time`. This is the THIN adapter path used in tests/compile path. If a live cert accidentally uses this thin adapter (which carries only minimal metadata), the resulting `forecast_authority.payload` would be missing `model_family`, `forecast_issue_time`, etc.

### ROOT CAUSE

### COMPETING HYPOTHESES

| Hypothesis | Evidence For | Evidence Against | Verdict |
|---|---|---|---|
| H1: A live code path constructs the final_intent cert via the thin `build_forecast_authority_certificate` adapter (forecast_authority_adapter.py) instead of `_forecast_authority_payload_and_clock`, producing a payload with none of the required DecisionSourceContext fields | The thin adapter exists (forecast_authority_adapter.py:15) and is the ONLY other producer of a FORECAST_AUTHORITY cert; its payload at line 40-55 contains no `model_family`, `forecast_issue_time`, etc. | The live reactor path (era:6092) always calls `_forecast_authority_payload_and_clock`; the adapter is marked "AVAIL-POSSESSION-EXEMPTED: thin status-record cert" and its comment says it "has no caller in the live reactor path" | POSSIBLE for edge cases not seen in static analysis |
| H2: The `forecast_authority.payload` dict IS from `_forecast_authority_payload_and_clock` but `ens_result` fields are all None — e.g., the `result.bundle` is not an `ExecutableForecastBundle` instance and returns a different dict from `to_ens_result()` | Could occur if a non-standard bundle type is returned | The reader always returns `ExecutableForecastBundle`; `to_ens_result()` hardcodes `"model": "ecmwf_ens"` so at least that would not be missing | UNLIKELY — model_family alone cannot go missing through this path |
| H3: C1-AVAIL-CLOCK emits None for `available_at` in ingest; `source_run.source_available_at` is NULL → `executable_forecast_reader.py:1076` returns early → FORECAST_AUTHORITY_EVIDENCE_MISSING raised → the no-submit compile step returns FAILURE → the decision path that reaches `event_bound_final_intent.py` was compiled from a STALE/CACHED cert where the `decision_source_context` was stored with empty fields in a previous cycle | Chain is complex; would require old cached certs with empty fields to be replayed | The cert is freshly built at each submit attempt, not replayed from cache | LOW PROBABILITY |
| H4: The 8 events predate the `decision_source_context` field being required. An old FINAL_INTENT cert stored in `edli_live_order_events` from before the `decision_source_context` integrity gate was introduced gets replayed against the current validator | Replayable certs from an earlier era would have an empty or absent `decision_source_context` field | The pattern appears in Jun 1-16 window which is post-gate; event_bound_final_intent.py line 203-204 would reject a completely missing context with a different error | UNLIKELY |

### REBUTTAL

The all-11-missing pattern is structurally unlike a partial field failure. It suggests the `decision_source_payload` dict is present (otherwise a different error fires) but lacks all the canonical keys. The most parsimonious explanation is that the `decision_source_context` key in the FINAL_INTENT cert payload is not the `_forecast_authority_payload_and_clock` output, but rather the SAME key from a cert that went through a different compilation path — possibly the DecisionCompiler in a mode that doesn't invoke `_forecast_authority_payload_and_clock`. This is a case where static analysis cannot settle the question.

**Confidence: LOW-MEDIUM** — the missing-all-fields pattern is definitively identified; the root cause among H1/H2 requires inspection of the actual stored `final_intent` cert payload for one of the 8 events.

### FIX DIRECTION

1. Query `edli_live_order_events` for the 8 rejected events, extract the `final_intent_cert` payload, and inspect the `decision_source_context` sub-dict to determine which keys are present. If no keys are present, confirm the thin adapter path; if all keys are present but empty, investigate the ens_result population.
2. Add an assertion in `_build_live_execution_command_certificates` that `forecast_authority.payload.get("model_family")` is non-empty before building the final intent cert. This would catch the thin-adapter path at cert-build time rather than at pre-venue validation.
3. Separately: verify C1-AVAIL-CLOCK doesn't create NULL `source_available_at` for rows that are currently live and being queried — though this appears to produce a different error (FORECAST_AUTHORITY_EVIDENCE_MISSING), not the decision_source_context integrity error.

---

## CRITICAL UNKNOWNS AND DISCRIMINATING PROBES

### Failure 1 (42 events — highest ROI)
**Critical unknown:** Under which caller conditions is `trade_conn` None when `_build_live_execution_command_certificates` is invoked on the live submit path?
**Discriminating probe:** `SELECT e.event_id, e.raw_response_hash, f.payload->'trade_conn_present' FROM edli_live_order_events WHERE reason LIKE '%event_id does not match%'` — OR inspect the `_run_live_order_build_savepoint` call at era:1763 to see if `trade_conn` is always set before the lambda is invoked.

### Failure 2 (28 events)
**Critical unknown:** Do all 28 tick_size failures cluster before 2026-06-01 (the BUG #92 fix date) or do they also occur after?
**Discriminating probe:** Filter `edli_live_order_events` by `reject_reason LIKE '%tick_size does not match%'` AND `created_at > '2026-06-01'`. If zero post-2026-06-01, the 28 are historical and the fix is complete. If some remain, inspect the stored `final_intent` tick_size vs snapshot's `min_tick_size`.

### Failure 3 (9 events)
**Critical unknown:** Is the cert-build sweep snapshot_id (`executable_snapshot.payload.get("identity")`) the same as `cost_basis.quote_snapshot_id` for the failing events?
**Discriminating probe:** Extract from stored final_intent certs: compare `payload['executable_snapshot_id']` (from cost_basis) vs `payload['executable_snapshot_cert']['identity']` (from the executable_snapshot cert used at build time).

### Failure 4 (8 events)
**Critical unknown:** What does the `decision_source_context` sub-dict look like in the stored FINAL_INTENT cert for the 8 rejecting events?
**Discriminating probe:** `SELECT payload->'decision_source_context' FROM edli_live_order_events WHERE reason LIKE '%decision_source_context failed integrity%'` — if the sub-dict is `{}` or `{"event_id": "...", "event_type": "..."}` (thin adapter payload shape), H1 is confirmed; if it has all the right keys but empty values, H2/H3 are active.

---

## UNCERTAINTY NOTES

- Failures 2 and 3 may have pre-dated their respective fixes (BUG #92 2026-06-01 for tick, JIT sweep fix). The reported counts include Jun 1-16; if the fixes landed mid-period the surviving post-fix counts could be very low, making them pre-existing resolved issues rather than current blockers.
- Failure 1 (42 events) is the only one confirmed by static analysis as having a mechanically live residual gap (`trade_conn is None` path). The MAKER-event comment at era:4311-4322 explicitly documents this was partially fixed 2026-06-12 but the fix is conditional.
- The `_PRE_SUBMIT_AUDIT_ONLY_DECISION_SOURCE_ERRORS` set at executor.py:582-590 defers `missing_observation_time` and `missing_observation_available_at` — these are NOT in the observed error string, suggesting they were correctly deferred. The 11 observed errors are all non-deferred blockers, which is consistent with a completely unpopulated context.
- No claim is made about whether fixing failure 1 alone is sufficient to unblock 3 end-to-end fills — the other reject reasons (tick_size, fill_price, decision_source) would each block the remaining candidates.
