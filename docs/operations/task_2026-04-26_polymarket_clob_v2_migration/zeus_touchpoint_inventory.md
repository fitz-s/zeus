# Zeus Polymarket CLOB Touchpoint Inventory

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: grep over `src/`, `scripts/`, `tests/`, `requirements.txt`, `architecture/*.yaml` against `data-improve` HEAD on 2026-04-26.

This file enumerates every Zeus integration site with the Polymarket CLOB. It is the source of truth for "files to touch" decisions in the V2 migration plan.

Ordering principle: production code → tests → architecture registry → external scripts → operations / state.

---

## 1. Single chokepoint module

`src/data/polymarket_client.py` is the only module that owns the `ClobClient` instance. All other modules consume the `PolymarketClient` wrapper.

| Line | Symbol / call | Notes |
|---|---|---|
| 17 | `CLOB_BASE = "https://clob.polymarket.com"` | V1 host hardcoded — V2 must change to `clob-v2.polymarket.com` |
| 18 | `DATA_API_BASE = "https://data-api.polymarket.com"` | data API likely unchanged in V2; verify |
| 25-44 | macOS Keychain credential resolution (private_key + funder_address) | unchanged across V2 |
| 60 | `from py_clob_client.client import ClobClient` | V2 swap point |
| 63-69 | `ClobClient(host=CLOB_BASE, signature_type=2, funder=...)` | sig_type=2 = GNOSIS_SAFE; pUSD swap requires Gnosis Safe to hold pUSD |
| 75-100 | `get_orderbook(token_id)` | mixed: SDK + direct httpx — broken abstraction |
| 81 | `httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=15.0)` | direct REST, bypasses SDK; V2 endpoint shape needs verification |
| 116-128 | `get_fee_rate(token_id)` | direct httpx GET `/fee-rate`; V2 may move to `getClobMarketInfo` (deletable) |
| 148 | `from py_clob_client.clob_types import OrderArgs` | V2 swap point |
| 149 | `from py_clob_client.order_builder.constants import BUY, SELL` | V2 swap point |
| 155 | `OrderArgs(price=..., size=..., side=..., token_id=...)` | V2 adds `metadata`, `builder_code`, `defer_exec`, `timestamp`; removes `taker`, `nonce`, `fee_rate_bps` |
| 160-161 | `signed = self._clob_client.create_order(args)` then `result = self._clob_client.post_order(signed)` | two-step pattern; verify V2 SDK preserves it |
| 167 | `cancel_order(order_id)` → `self._clob_client.cancel(order_id)` | V2 SDK method name verified post-Phase 0 |
| 200 | `from py_clob_client.clob_types import OpenOrderParams` | V2 swap point |
| 213-265 | position fetching path with `funder_address` | unchanged across V2 |
| 266-275 | USDC balance + redemption helpers | **pUSD swap point** — most operationally sensitive |
| 268 | `from py_clob_client.clob_types import AssetType, BalanceAllowanceParams` | V2 swap point |

---

## 2. PolymarketClient consumers (12 sites)

Identified via `import` of `from src.data.polymarket_client import PolymarketClient` and equivalent usages.

| Site | File:line | Usage |
|---|---|---|
| Engine | `src/engine/evaluator.py:34` | imports `PolymarketClient` — fee/price reads |
| Engine | `src/engine/cycle_runner.py:18` | imports — passed into cycle |
| Engine | `src/engine/monitor_refresh.py:25` | imports — Layer 1 probability recompute |
| Engine | `src/engine/cycle_runtime.py:185` | `clob.cancel_order(order_id)` direct call |
| Boot | `src/main.py:379` | `Startup wallet check: $%.2f USDC available` — pUSD relabel |
| Execution | `src/execution/executor.py:227,371` | imports — entry path |
| Execution | `src/execution/exit_lifecycle.py:355` | `clob.cancel_order(...)` |
| Execution | `src/execution/fill_tracker.py:346,348` | `cancel_order` polymorphic check via `hasattr` |
| Execution | `src/execution/harvester.py:1244-1264` | T2-G redemption path (claim winning USDC; pUSD swap) |
| Script | `scripts/live_smoke_test.py:51,67,102` | live smoke test |
| Script | `scripts/capture_replay_artifact.py:22` | replay artifact capture |

---

## 3. Typed contracts (CLOB-shaped)

`src/contracts/` — Zeus internal abstractions. **All retained across V2.**

| File | Purpose | V2 impact |
|---|---|---|
| `tick_size.py:110` | `POLYMARKET_WEATHER_TICK = TickSize(value=0.01, ...)` | none — V2 retains 0.01 tick for weather |
| `execution_price.py:130` | `polymarket_fee(price, fee_rate=0.05)` formula `fee_rate × p × (1-p)` | none — V2 formula identical |
| `execution_price.py:95-107` | `ExecutionPrice.with_taker_fee` | none — uses formula, V2 SDK may change where the rate comes from |
| `slippage_bps.py` | T5.d `SlippageBps` typed atom | none |
| `realized_fill.py` | T5.d `RealizedFill` composite | none |
| `execution_intent.py` | `ExecutionIntent` frozen dataclass | none |
| `decision_evidence.py` | T4.1a evidence record | none |
| `settlement_semantics.py` | `assert_settlement_value()` invariant | none |

---

## 4. Tests

| File:line | Type | V2 impact |
|---|---|---|
| `tests/test_neg_risk_passthrough.py:66-83` | SDK contract antibody | **must replicate for V2 SDK** — early-warning signal preserved |
| `tests/test_polymarket_error_matrix.py:39` | `from py_clob_client.exceptions import PolyApiException` | V2 swap point in import; assert behavior parity |

Existing antibody pattern is the model for new V2 antibodies (M1-M5, A1-A3 in `v2_system_impact_report.md` §6).

---

## 5. Architecture registry

| File:line | Entry | V2 impact |
|---|---|---|
| `architecture/source_rationale.yaml:271` | `polymarket_client.py` listed under writers | refresh metadata when V2 swap lands |
| `architecture/source_rationale.yaml:606-608` | `polymarket_client.py: authority_role: clob_boundary` | V2 swap should refresh `last_audited` + add `protocol_version: v1|v2` field |
| `architecture/test_topology.yaml:134` | `tests/test_polymarket_error_matrix.py` registered | new V2 antibody tests must be registered here |

---

## 6. Operations / state

| Path | Existing role | V2 interaction |
|---|---|---|
| `state/auto_pause_failclosed.tombstone` | application-level kill switch | **target of automatic write on V2 heartbeat failure** |
| `state/daemon-heartbeat.json` | daemon liveness file | independent of V2 protocol heartbeat (different layer) |
| `scripts/check_daemon_heartbeat.py:30` | external staleness check | independent |
| `scripts/deep_heartbeat.py:14` | Layer 1 diagnostic | model for V2 heartbeat coroutine, but not a driver |
| `HEARTBEAT.md` (workspace doc) | operator documentation | informational |

---

## 7. Dependency pin

| File:line | Entry |
|---|---|
| `requirements.txt:14` | `py-clob-client>=0.25` (V1) |

Phase 1 adds dual pin:
```
py-clob-client>=0.25
py-clob-client-v2>=1.0.0
```
Phase 4 removes V1 pin.

---

## 8. Files NOT to touch (anti-targets)

- `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/` — frozen evidence, do not modify
- `state/zeus-world.db` — runtime DB, no schema changes from V2 migration
- `state/*.tombstone` (other than `auto_pause_failclosed.tombstone`) — different fail-closure axes
- `src/data/observation_instants_v2_writer.py` — naming collision with "v2" but unrelated to CLOB V2

---

## 9. Summary

| Category | Count |
|---|---|
| Single chokepoint modules | 1 |
| Direct `ClobClient` instantiation sites | 1 |
| `PolymarketClient` consumer sites | 12 |
| Direct httpx CLOB calls bypassing SDK | 2 (`/book`, `/fee-rate`) |
| Typed contracts | 8 (all retained) |
| Test files | 2 (both need V2 mirrors) |
| Architecture registry entries | 3 |
| Operations / state files | 2 (`tombstone` + daemon heartbeat) |
| Dependency pins | 1 (becomes 2 in Phase 1) |

**Total touchpoint count**: ~30 sites across ~20 files. Bounded blast radius — single-chokepoint module + single SDK swap covers most.
