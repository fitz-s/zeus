# Zeus Execution & Lifecycle Reference

Status: canonical durable reference  
Authority rank: reference. Code, manifests, tests, and authority docs win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Execution Boundary

`src/execution/executor.py` is the live order side-effect boundary. It is limit-order-only and routes live submissions through venue adapters; shadow/replay/backtest routes are separate.

Entry submit order:

```text
selected candidate/proof
  -> risk/cutover/heartbeat/ws/collateral/freshness checks
  -> command/idempotency persistence
  -> venue adapter side effect
  -> venue ack/fill facts
  -> order truth reducer
  -> position event/projection
```

No final execution intent may carry posterior, p_market, VWMP, edge, market prior, or entry-price recompute inputs. Final intent validates and submits; it must not silently re-decide.

---

## 2. Pre-Submit Witness

`src/engine/event_reactor_adapter.py` constructs a pre-submit authority witness with:

- quote observed time;
- book hash;
- current best bid/ask;
- tick and min order size;
- negative-risk flag;
- heartbeat status;
- user-channel status;
- venue connectivity status;
- balance/allowance status;
- checked-at freshness.

Missing/stale required witness evidence fails closed before live submit.

---

## 3. Command And Idempotency Law

`src/state/venue_command_repo.py` owns venue command/event truth. Direct mutation of command tables outside that seam is forbidden.

Unknown side effects are not empty states. A retry after an unknown side effect must not duplicate submission. Deterministic Polymarket validation errors are typed rejections when code proves no venue order was created; they are not unknown side effects.

---

## 4. Lifecycle Phases

Canonical phases:

```text
pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled
```

Terminal/recovery phases:

```text
voided, quarantined, admin_closed, unknown
```

`architecture/money_path_objects.yaml` and `src/state/lifecycle_manager.py` own the phase vocabulary. Docs must not invent phase strings.

---

## 5. Exit Lifecycle

`src/execution/exit_lifecycle.py` owns live sell-order state transitions. Its golden rule is durable:

```text
confirmed sell fill -> economically_closed
settlement remains later harvester-owned transition
```

Exit module runtime states such as `exit_intent`, `sell_placed`, `sell_pending`, `retry_pending`, `backoff_exhausted`, and `sell_filled` are not replacements for canonical lifecycle phases.

Transient submit-channel gaps may retry without consuming bounded terminal retry budget where code classifies them as recoverable. Terminal/unsellable conditions keep fail-closed budget behavior.

---

## 6. Monitor And Chain Truth

Held-position monitor refresh may update belief and trigger exit intent. It cannot declare settlement and it cannot turn held-token quote observations into posterior-prior evidence.

Chain/CLOB truth outranks local cache:

```text
Polymarket chain/CLOB/user-channel facts
  -> event/command/fill DB truth
  -> projection/cache/export/report
```

Do not void on unknown/stale chain state. Chain-only or local/chain mismatch must follow reconciliation/quarantine/risk behavior in code.

---

## 7. Settlement And Learning

`src/execution/harvester.py` and settlement outcome paths own settlement/redeem follow-through. Settlement is a source/contract/bin-topology fact, not an exit-order fact.

Learning and attribution must consume fill and settlement truth only when provenance/training eligibility allow it. Backtest or replay results are diagnostic until parity/no-hindsight evidence promotes them.

---

## 8. Execution/Lifecycle Change Checklist

Before changing execution, exit, lifecycle, or settlement paths, prove:

1. command persistence precedes side effect;
2. idempotency key and unknown-side-effect behavior are safe;
3. final intent cannot recompute strategy;
4. JIT book/witness freshness is enforced;
5. collateral/inventory/balance gates run before SDK contact;
6. lifecycle phase grammar remains manifest/code-owned;
7. exit intent is not close/settlement;
8. chain/local mismatch behavior is not weakened;
9. settlement write path remains harvester/source-owned;
10. tests/manifests/docs registry are updated for route or state changes.
