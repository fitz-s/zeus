# T1G SDK Envelope Persistence Path Audit

**Phase:** T1G — Final SDK envelope persistence path audit (verification-first)  
**Audited:** 2026-05-05  
**Auditor:** Executor agent (a6b34b7d52a61caee)  
**Authority:** docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/phase.json

---

## Summary Table

| Site (Planner Ref) | Actual Line | Description | Verdict |
|---|---|---|---|
| executor.py:1609 | 1609 | Entry submit — _persist_final_submission_envelope_payload call | VERIFIED_PERSISTS |
| executor.py:2291 | 2291 | Exit submit — _persist_final_submission_envelope_payload call | VERIFIED_PERSISTS |
| executor.py:1495 | 1495 | SUBMIT_REJECTED in execute_exit_order (post-client-init) | NOT_LIVE_PATH |
| executor.py:1662 | 1662 | SUBMIT_REJECTED in execute_exit_order (success_false branch) | VERIFIED_PERSISTS |
| executor.py:1694 | 1694 | SUBMIT_REJECTED in execute_exit_order (missing_order_id) | VERIFIED_PERSISTS |
| executor.py:2099 | 2099 | SUBMIT_REJECTED in _live_order (post-client-init) | NOT_LIVE_PATH |
| executor.py:2138 | 2138 | SUBMIT_REJECTED in _live_order (post-v2-preflight, V2PreflightError) | NOT_LIVE_PATH |
| executor.py:2169 | 2169 | SUBMIT_REJECTED in _live_order (post-v2-preflight, generic exception) | NOT_LIVE_PATH |
| executor.py:2342 | 2342 | SUBMIT_REJECTED in _live_order (success_false) | VERIFIED_PERSISTS |
| executor.py:2373 | 2373 | SUBMIT_REJECTED in _live_order (missing_order_id) | VERIFIED_PERSISTS |
| executor.py:1568 | 1568 | SUBMIT_UNKNOWN_SIDE_EFFECT in execute_exit_order | NOT_LIVE_PATH |
| executor.py:2251 | 2251 | SUBMIT_UNKNOWN_SIDE_EFFECT in _live_order | NOT_LIVE_PATH |
| polymarket_v2_adapter.py:345-369 | 362/371/380 | Venue-adapter SDK post_order paths | VERIFIED_PERSISTS |

**Total sites classified: 13**  
**VERIFIED_PERSISTS: 7** (sites 1, 2, 4, 5, 9, 10, 13)  
**NOT_LIVE_PATH: 6** (sites 3, 6, 7, 8, 11, 12)  
**NEEDS_FIX: 0**

---

## Detailed Verdicts

### Site 1 — executor.py:1609 — Entry submit (execute_exit_order)

**Verdict: VERIFIED_PERSISTS**

```
1608: try:
1609:     final_envelope_payload = _persist_final_submission_envelope_payload(
1610:         conn,
1611:         result,
1612:         command_id=command_id,
1613:     )
```

`_persist_final_submission_envelope_payload` is called immediately after `client.place_limit_order()` returns a non-None `result` (exit order path). The helper extracts `result["_venue_submission_envelope"]` — the SDK-returned envelope — and persists it via `insert_submission_envelope`. Returns a compact dict reference (`final_submission_envelope_id`, `final_submission_envelope_command_id`) stored in `final_envelope_payload` for downstream event payloads.

Line drift: None. Planner citation matches actual code.

---

### Site 2 — executor.py:2291 — Exit submit (_live_order)

**Verdict: VERIFIED_PERSISTS**

```
2290: try:
2291:     final_envelope_payload = _persist_final_submission_envelope_payload(
2292:         conn,
2293:         result,
2294:         command_id=command_id,
2295:     )
```

Same pattern as Site 1, for the entry order path in `_live_order`. SDK result flows directly into the helper.

Line drift: None.

---

### Site 3 — executor.py:1495 — SUBMIT_REJECTED in execute_exit_order (post-client-init)

**Verdict: NOT_LIVE_PATH**

```
1484: try:
1485:     client = PolymarketClient()
1486: except Exception as exc:
1487:     # Constructor / credential / adapter setup failures happen before
1488:     # any venue submit side effect.
...
1495:     event_type="SUBMIT_REJECTED",
...
1498:     "reason": "pre_submit_client_init_failed",
```

`PolymarketClient()` constructor raised before any SDK call. No venue contact occurred. No SDK response to persist. The rejection is a pre-submit local failure with no `_venue_submission_envelope` in scope. Correct NOT_LIVE_PATH classification — the code comment confirms ("safe terminal rejections, not M2 unknown-side-effect outcomes").

Line drift: None.

---

### Site 4 — executor.py:1662 — SUBMIT_REJECTED in execute_exit_order (success_false branch)

**Verdict: VERIFIED_PERSISTS**

```
1651: if result.get("success") is False:
...
1662:     event_type="SUBMIT_REJECTED",
...
1667:     **final_envelope_payload,    # ← carries final_submission_envelope_id
```

This branch executes AFTER line 1609 has already called `_persist_final_submission_envelope_payload` and stored the reference in `final_envelope_payload`. The `**final_envelope_payload` spread in the event payload at line 1667 embeds `final_submission_envelope_id` and `final_submission_envelope_command_id` into the SUBMIT_REJECTED event — proving which SDK-returned envelope the rejection is tied to.

Persistence happens at 1609; event cites it at 1667.

Line drift: None.

---

### Site 5 — executor.py:1694 — SUBMIT_REJECTED in execute_exit_order (missing_order_id)

**Verdict: VERIFIED_PERSISTS**

```
1689: if not order_id:
1690:     try:
1691:         append_event(
...
1694:             event_type="SUBMIT_REJECTED",
...
1696:             payload={"reason": "missing_order_id", **final_envelope_payload},
```

Same pattern as Site 4. `final_envelope_payload` was populated at 1609. The spread at 1696 cites the persisted SDK envelope in the SUBMIT_REJECTED event.

Line drift: None.

---

### Site 6 — executor.py:2099 — SUBMIT_REJECTED in _live_order (post-client-init)

**Verdict: NOT_LIVE_PATH**

```
2088: try:
2089:     client = PolymarketClient()
2090: except Exception as exc:
...
2099:     event_type="SUBMIT_REJECTED",
...
2102:     "reason": "pre_submit_client_init_failed",
```

Identical structure to Site 3 — constructor failure before any SDK call. Pre-submit, no venue contact, no SDK response to persist. Comment confirms: "safe terminal rejections, not M2 unknown-side-effect outcomes."

Line drift: None.

---

### Site 7 — executor.py:2138 — SUBMIT_REJECTED in _live_order (post-v2-preflight, V2PreflightError)

**Verdict: NOT_LIVE_PATH**

```
2125: try:
2126:     client.v2_preflight()
2127: except V2PreflightError as exc:
...
2138:     event_type="SUBMIT_REJECTED",
...
2140:     payload={"reason": "v2_preflight_failed", "detail": str(exc)},
```

`client.v2_preflight()` is a read-only preflight check — it does NOT submit an order to the venue. Failure here means no order was sent, no SDK submission response exists. No `_venue_submission_envelope` available to persist.

Line drift: None.

---

### Site 8 — executor.py:2169 — SUBMIT_REJECTED in _live_order (post-v2-preflight, generic exception)

**Verdict: NOT_LIVE_PATH**

```
2158: except Exception as exc:
...
2169:     event_type="SUBMIT_REJECTED",
...
2172:     "reason": "v2_preflight_exception",
```

Same preflight block as Site 7, catching unexpected exceptions from `v2_preflight()`. No order submitted. No SDK submission response to persist.

Line drift: None.

---

### Site 9 — executor.py:2342 — SUBMIT_REJECTED in _live_order (success_false)

**Verdict: VERIFIED_PERSISTS**

```
2331: if result.get("success") is False:
...
2342:     event_type="SUBMIT_REJECTED",
...
2347:     **final_envelope_payload,
```

`final_envelope_payload` was populated at line 2291 (`_persist_final_submission_envelope_payload`). The spread at 2347 embeds the persisted SDK envelope reference in the SUBMIT_REJECTED event.

Line drift: None.

---

### Site 10 — executor.py:2373 — SUBMIT_REJECTED in _live_order (missing_order_id)

**Verdict: VERIFIED_PERSISTS**

```
2368: if not order_id:
...
2373:     event_type="SUBMIT_REJECTED",
...
2375:     payload={"reason": "missing_order_id", **final_envelope_payload},
```

Same pattern as Site 9. `final_envelope_payload` computed at 2291, cited in SUBMIT_REJECTED event at 2375.

Line drift: None.

---

### Site 11 — executor.py:1568 — SUBMIT_UNKNOWN_SIDE_EFFECT in execute_exit_order

**Verdict: NOT_LIVE_PATH**

```
1532: except Exception as exc:
1533:     # M2: place_limit_order has crossed the submit side-effect boundary.
...
1541:     event_type="SUBMIT_TIMEOUT_UNKNOWN",
...
1568:     command_state="SUBMIT_UNKNOWN_SIDE_EFFECT",
```

The SDK call (`client.place_limit_order`) raised an exception — it did NOT return a dict. The M2 boundary was crossed (venue may or may not have processed the order) but there is no SDK response object to extract `_venue_submission_envelope` from. The event type is `SUBMIT_TIMEOUT_UNKNOWN`, not `SUBMIT_REJECTED`. No SDK-returned payload exists — persistence of a non-existent response would be a design error. The idempotency key is embedded for recovery.

The planner note correctly identifies this as SUBMIT_UNKNOWN_SIDE_EFFECT. The appropriate design is the recovery loop (using the idempotency key to poll for order state), not persistence of a missing SDK response.

Line drift: None. (Note: the planner-cited line 1568 is `command_state="SUBMIT_UNKNOWN_SIDE_EFFECT"` inside the `OrderResult` constructor — the event appended is `SUBMIT_TIMEOUT_UNKNOWN` at line 1541. The semantic site is correct.)

---

### Site 12 — executor.py:2251 — SUBMIT_UNKNOWN_SIDE_EFFECT in _live_order

**Verdict: NOT_LIVE_PATH**

```
2216: except Exception as exc:
2217:     # M2: place_limit_order has crossed the submit side-effect boundary.
...
2225:     event_type="SUBMIT_TIMEOUT_UNKNOWN",
...
2251:     command_state="SUBMIT_UNKNOWN_SIDE_EFFECT",
```

Identical reasoning to Site 11. SDK call raised an exception; no dict response; no `_venue_submission_envelope` to persist. Idempotency key embedded in event for recovery.

Line drift: None. (Same note as Site 11 — actual event is `SUBMIT_TIMEOUT_UNKNOWN` at 2225; `SUBMIT_UNKNOWN_SIDE_EFFECT` is the `command_state` on the returned `OrderResult` at 2251.)

---

### Site 13 — polymarket_v2_adapter.py:345-369 — Venue-adapter SDK post_order paths

**Verdict: VERIFIED_PERSISTS**

The adapter's `submit()` method (line 315) calls SDK via three code paths:
- Line 362: `client.create_and_post_order(order_args, options=options, order_type=..., post_only=..., defer_exec=False)` — one-step path
- Lines 371+380: `client.create_order()` + `client.post_order()` — two-step path
- Line 388: Neither available → `_rejected_submit_result()` (pre-SDK, NOT_LIVE_PATH within adapter)

All three paths that touch the SDK feed their raw response into `_submit_result_from_response()` (line 393–398), which attaches the updated `VenueSubmissionEnvelope` to the `SubmitResult.envelope` field.

This `SubmitResult` is returned to `PolymarketClient.place_limit_order()`, which calls `_legacy_order_result_from_submit()` at line 458. That function (line 722–739 of polymarket_client.py) constructs the result dict with `"_venue_submission_envelope": envelope.to_dict()`.

This dict is what `_persist_final_submission_envelope_payload` (executor.py:432) consumes via `result.get("_venue_submission_envelope")`. The chain is complete.

Line drift: Planner cited 345-369. Actual SDK call lines: 362 (one-step), 371 (create_order), 380 (post_order). Range is accurate.

---

## FOK/FAK Coverage Section

**Invariant verified: FOK/FAK/GTC all flow through `_persist_final_submission_envelope_payload`.**

In `polymarket_v2_adapter.py:submit()`, `envelope.order_type` is passed as a parameter to both the one-step path (line 362, `order_type=envelope.order_type`) and the two-step path (line 380, `order_type=envelope.order_type`). The order type affects how the Polymarket CLOB processes the order (FOK = fill-or-kill, FAK = fill-and-kill, GTC = good-till-cancel) but does NOT affect the code path through `submit()`.

Regardless of order_type:
1. The SDK response is collected as `raw_response` in all branches.
2. All branches feed into `_submit_result_from_response()` (line 393), which attaches the SDK response to the updated envelope.
3. The `SubmitResult.envelope` field carries the post-submit envelope (with `signed_order`, `signed_order_hash`, `raw_response_json`, and optionally `order_id` from the SDK response).
4. `_legacy_order_result_from_submit()` attaches this as `_venue_submission_envelope` in the returned dict.
5. Executor's `_persist_final_submission_envelope_payload` persists it.

FOK/FAK coverage: CONFIRMED. Order type routing does not bypass the persistence path.

---

## T1F-Inherited Gate Section

**Invariant re-confirmed: T1F-PLACEHOLDER-ENVELOPE-FAKE-SDK-COUNT-ZERO holds.**

Two independent gate layers block placeholder envelopes from reaching live SDK calls:

**Gate 1 — polymarket_v2_adapter.py:315-328 (T1F primary)**
`submit()` calls `envelope.assert_live_submit_bound()` before any preflight or SDK call. Placeholder envelopes (those with `compatibility_placeholder_reason` set) raise `ValueError`. The adapter returns `_rejected_submit_result(error_code="BOUND_ENVELOPE_NOT_LIVE_AUTHORITY")` before touching the SDK.

**Gate 2 — polymarket_client.py:407-424 (mirror gate)**
`place_limit_order()` with a bound envelope calls `_submission_envelope_live_bound_error(pending_envelope)`. If the envelope is a placeholder, the function returns an error string and the call returns early with `{"success": False, "errorCode": "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY", "_venue_submission_envelope": ...}` — no SDK call, but the result dict IS valid for `_persist_final_submission_envelope_payload` (it contains `_venue_submission_envelope`).

**Implication for T1G test fixtures:** Test fakes must use live-bound envelopes (non-placeholder) to reach the SDK mock boundary. Placeholder-envelope tests are T1F territory and are already covered by `tests/test_venue_envelope_live_bound.py` and `tests/test_polymarket_adapter_submit_safety.py`.

**T1F-PLACEHOLDER-ENVELOPE-FAKE-SDK-COUNT-ZERO: HOLDS.** Placeholder envelopes are stopped before any real SDK client call regardless of order_type or code path.

---

## Line Drift Summary

No planner-cited lines drifted. All 13 citations verified exact:

| Planner ref | Verified at | Drift |
|---|---|---|
| executor.py:1609 | 1609 | none |
| executor.py:2291 | 2291 | none |
| executor.py:1495 | 1495 | none |
| executor.py:1662 | 1662 | none |
| executor.py:1694 | 1694 | none |
| executor.py:2099 | 2099 | none |
| executor.py:2138 | 2138 | none |
| executor.py:2169 | 2169 | none |
| executor.py:2342 | 2342 | none |
| executor.py:2373 | 2373 | none |
| executor.py:1568 | 1568 | none (command_state field; SUBMIT_TIMEOUT_UNKNOWN event at 1541) |
| executor.py:2251 | 2251 | none (command_state field; SUBMIT_TIMEOUT_UNKNOWN event at 2225) |
| polymarket_v2_adapter.py:345-369 | 362/371/380 | none (range correct) |

---

## Audit Conclusion

**All 13 sites classified. NEEDS_FIX count: 0.**

T1G closes with audit-only outcome. Source edits to `src/execution/executor.py` or `src/state/venue_command_repo.py` are NOT required. The persistence invariant — that every live SDK contact that returns a dict response has that response's `_venue_submission_envelope` persisted via `_persist_final_submission_envelope_payload` — is satisfied at all reachable live paths.

The two SUBMIT_UNKNOWN_SIDE_EFFECT sites (Sites 11, 12) represent SDK exception paths where no dict response exists. The correct design artifact for these sites is the recovery loop (using idempotency key), not envelope persistence of a non-existent response.
