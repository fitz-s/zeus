# Region-Down Solo Context Boot — proponent-down

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Down (CLOB v2 migration + downstream gates)
Author: proponent-down
Status: pre-R1, awaiting greenlight before A2A with opponent-down

---

## 1. Files read (path:line ranges)

### V2 packet
| File | Range | Purpose |
|---|---|---|
| `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/AGENTS.md` | 1-52 | packet entry-point + map-maintenance hook |
| `.../plan.md` | 1-518 | full 5-phase plan (Phase 0 through Phase 4) |
| `.../v2_system_impact_report.md` | 1-381 | V2 capability inventory + Zeus impact analysis |
| `.../zeus_touchpoint_inventory.md` | 1-147 | grep-verified file:line registry of every Zeus CLOB integration site |
| `.../open_questions.md` | 1-142 | Q1-Q7 + S1-S3 + F1-F2 question registry |
| `.../work_log.md` | (existed; not yet read line-by-line — will read at L3 if needed) | running log |

### Zeus integration surface
| File | Range read | Key signals |
|---|---|---|
| `src/data/polymarket_client.py` | 1-367 (full) | V1 SDK only (`from py_clob_client.client import ClobClient`, line 69); HOSTS V1: `clob.polymarket.com` (line 17); `V2PreflightError` class + `v2_preflight()` method (lines 21-108) calling `self._clob_client.get_ok()` — preparing for V2 swap with V1 SDK; direct httpx for `/book` (line 116) and `/fee-rate` (line 153); `cancel` (line 249), `redeem` (line 361), `get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))` (lines 348-350) |
| `src/main.py` | 1-150 + grep results to line 642 | DAEMON: apscheduler `BlockingScheduler`; `_cycle_lock = threading.Lock()` (line 30); `_run_mode` is decorated by `@_scheduler_job("run_mode")` and runs under `_cycle_lock.acquire(blocking=False)` (lines 70-101); ALREADY-EXISTS daemon heartbeat job: `_write_heartbeat` writes `state/daemon-heartbeat.json` every 60s (lines 338-367, scheduler.add_job at 579); 3-strikes failure → `logger.critical("FATAL: Heartbeat failed 3 consecutive times. Halting daemon...")` at line 367 |
| `src/engine/cycle_runner.py` | 1-100 | `run_cycle(mode)` synchronous; `KNOWN_STRATEGIES` enum (line 50); `_TERMINAL_POSITION_STATES_FOR_SWEEP` references INV-19 RED-related code; `_execute_force_exit_sweep` sets `pos.exit_reason="red_force_exit"` but does NOT emit durable cancel commands (cross-cut X1 / F-010 evidence) |
| `src/engine/monitor_refresh.py` | 1-10 head | grep `asyncio|websocket|WebSocket` returns EMPTY → confirmed pure synchronous REST |
| `src/execution/executor.py` | 1-150 + grep results | `idempotency_key` is a load-bearing field on `OrderResult` (line 66), `ExitOrderIntent` (line 84); pre-submit lookup `find_command_by_idempotency_key` (lines 456, 795); `_orderresult_from_existing` ack-state gate (lines 87-168); `v2_preflight` is invoked at line 873 inside `_live_order` (Phase 4 / INV-25 / K5 evidence) |
| `src/execution/exit_triggers.py` | 1-100 | exit-trigger evaluation — separate concern; not V2 transport |
| `src/execution/fill_tracker.py` | 1-120 + grep | `FILL_STATUSES = frozenset({"FILLED", "MATCHED"})` (line 24); `CANCEL_STATUSES = frozenset({"CANCELLED","CANCELED","EXPIRED","REJECTED"})` (line 25); `_normalize_status` defined at line 390; status reads in `check_pending_entries` at lines 330, 352; **NO** `DELAYED` handling today — confirms plan slice 2.A is real gap |
| `src/execution/harvester.py` | 1-100 head + grep | `run_harvester()` at line 293; `harvester_live` flag-OFF gate via `ZEUS_HARVESTER_LIVE_ENABLED` (line 305 area); pUSD redemption boundary lives here (lines 1244-1264 per inventory) |
| `architecture/invariants.yaml` | 178-310 grep slice | INV-23..INV-32 + INV-25 (V2 preflight blocks placement, statement at line 198, tests at 202-204) — relevant: ALREADY a V2-preflight invariant on `main` |

### Cross-cut packets
| File | Range | Purpose |
|---|---|---|
| `docs/operations/task_2026-04-26_ultimate_plan/judge_ledger.md` | 1-80 (full minus tail) | judge state, forbid-rerun list, routing yaml summary |
| `.../ULTIMATE_PLAN.md` | 1-100 (full) | 3-region scope + cross-cuts X1-X4 |
| `.../evidence/apr26_findings_routing.yaml` | 30-160 (F-001..F-011 entries) | heuristic routing map |
| `.../evidence/up/_context_boot_proponent.md` | 1-60 | Region-Up's reading of F-001/F-003 (claims partially shipped via INV-28+INV-30) |
| `.../evidence/mid/_context_boot_proponent.md` | 100-160 | Region-Mid's reading of A1.5/A4.5 amendments + RED durable-cmd routing |
| `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` | 1-80 grep slice | Wave 2 mapping: B2/B4/B5 are DATA work; G7/G10-cutover are the live-readiness gates |
| `docs/operations/task_2026-04-26_live_readiness_completion/evidence/audit_2026-04-26.md` | 22-58 grep slice | confirms G7 (LIVE_SAFE_CITIES) and G10-cutover live-readiness items |
| `/Users/leofitz/Downloads/Zeus_Apr26_review.md` | 340-440 (F-002 / F-003 / F-004 / F-005 / F-006 / F-007) + grep across full file | F-003 fix design: "persist `command_id`, `client_order_id`, signed order hash, nonce, raw signed payload before post; retry only by reconcile-first policy" (line 368); confidence "high" (line 372) |

## 2. External URLs fetched (with timestamps + status)

All fetched 2026-04-26 between session start and pre-R1 boot.

| URL | HTTP status (inferred) | Notes / quotes |
|---|---|---|
| `https://github.com/Polymarket/py-clob-client` | 200 | "v0.34.6 Latest" + "Feb 19, 2026"; no V2 branch reference; no EOL notice; OrderArgs README example: `token_id, price, size, side`; closest-to-heartbeat is `client.get_ok()`; no `clientOrderId`/idempotency in README |
| `https://github.com/Polymarket/py-clob-client-v2` | 200 | repo EXISTS; "v1.0.0 Latest"; OrderArgs README example: `token_id, price, side, size`; `create_and_post_order(...)` and `create_and_post_market_order(...)` combined methods; `PartialCreateOrderOptions(tick_size="0.01")`; `OrderType.GTC|FOK|FAK`; auth `L1` EIP-712 + `L2` HMAC; no clientOrderId/heartbeat surface in README |
| `https://docs.polymarket.com/` | 200 | no V2 timeline / EOL / clientOrderId / heartbeat / pUSD wording surfaced in README excerpts; "Builder Program" mentioned (no required-for-V2 wording) |
| `https://github.com/Polymarket/py-clob-client-v2/blob/main/py_clob_client/clob_types.py` | **404** | speculative path — V2 package likely renamed (e.g. `polymarket_clob`); operator must clone-and-grep |
| `https://github.com/Polymarket/py-clob-client-v2/tree/main/py_clob_client` | **404** | same |
| `https://github.com/Polymarket/py-clob-client-v2/tree/main/examples` | 200 | only `__init__.py` listed |
| `https://github.com/Polymarket/py-clob-client-v2/blob/main/README.md` | 200 | full README — confirms structure above; reaffirms NO heartbeat/keepalive method documented in README |

**Authority note**: `v2_system_impact_report.md` cites richer evidence (V2 `getHeartbeat()` added in `py-clob-client-v2 v0.0.4` on 2026-04-16; pUSD contract `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`; mandatory ~10s heartbeat; EIP-712 v2; `clob-v2.polymarket.com` host) that my own WebFetch did not reproduce because GitHub's main README reflects the LATEST tag (v1.0.0), and the heartbeat detail lives in earlier release notes / source files / docs subpages. **The impact_report's pre-WebFetched evidence (2026-04-26 fetch) is the authority for these specific facts**; my WebFetches add the v1.0.0 README + USDC labelling + Polymarket docs landing-page negative.

## 3. VERIFIED-vs-ASSUMED V2 facts

| Claim | Status | Source |
|---|---|---|
| V2 SDK exists as `py-clob-client-v2` v1.0.0 (separate package, not config-toggle) | **VERIFIED** | my WebFetch of `github.com/Polymarket/py-clob-client-v2` |
| V1 SDK `py-clob-client` is at v0.34.6 (Feb 19, 2026), no EOL announced | **VERIFIED** | my WebFetch of `github.com/Polymarket/py-clob-client` |
| V2 host = `clob-v2.polymarket.com`, V1 host = `clob.polymarket.com` | **VERIFIED-via-impact-report** (rs-clob-client-v2 README citation) | impact_report §2.2 |
| V2 EIP-712 domain version `"2"` (V1 was `"1"`) | **VERIFIED-via-impact-report** | impact_report §2.2 |
| V2 collateral = pUSD on Polygon `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | **VERIFIED-via-impact-report** | impact_report §2.7 |
| V2 mandatory ~10s heartbeat; missing → server cancels all open orders | **PRELIMINARY-VERIFIED** (impact_report §2.4 cites `getHeartbeat` added in v0.0.4 2026-04-16); NOT in v1.0.0 README excerpt | impact_report §2.4 — re-confirm in Phase 0.B by reading `py-clob-client-v2` source |
| V2 OrderArgs ADDS `metadata`, `builder_code`, `defer_exec`, `timestamp`; REMOVES `taker`, `nonce`, `fee_rate_bps` | **PRELIMINARY-VERIFIED** (impact_report §2.3); v1.0.0 README example shows only `token_id/price/side/size`, so add-fields are likely OPTIONAL kwargs not in basic example | impact_report §2.3 — re-confirm in Phase 0.B |
| V2 adds `delayed`/`ORDER_DELAYED`/`DELAYING_ORDER_ERROR` transitional states | **PRELIMINARY-VERIFIED** (impact_report §2.5) | impact_report §2.5 — re-confirm in Phase 0.B |
| V2 `getClobMarketInfo(conditionID)` returns fee_rate + tick_size + neg_risk in one call | **ASSUMED** (impact_report §2.4 / Q3 OPEN) | gates Phase 0.C |
| V2 idempotency / `client_order_id` / `Idempotency-Key` HTTP-header surface | **ASSUMED-NOT-PRESENT-AT-SDK-LEVEL** (negative); Polymarket docs page did not affirm or deny header-level idempotency | needs Phase 0.B addendum |
| `CLOB_V1` and `CLOB_V2` are independent Python packages (no parallel install conflict) | **PRELIMINARY-VERIFIED**; plan §1.G dual-pin slice acknowledges potential conflicts | re-confirm in Phase 1.G |
| V1 EOL announced or imminent | **NEGATIVE-VERIFIED** (no announcement) | both my WebFetch and impact_report §1 |
| Builder code REQUIRED for V2 fee-share | **ASSUMED-NO-DEFAULT** (Q7 OPEN); only docs wording is "Builder Program" | gates Phase 2.H |
| pUSD ↔ USDC.e bridge path documented | **NEGATIVE-VERIFIED**; impact_report §4.5 explicitly says "no public document explains the bridge path" | Q5 BLOCKING |

### Round-2 boot additions (post lead path-correction + post opponent-down boot read)

#### R2.1 PyPI fetch (proponent's own, 2026-04-26)

`https://pypi.org/project/py-clob-client-v2/` → 200; package `py-clob-client-v2 1.0.0`; `Requires: Python >=3.9.10`; Upload date Apr 17, 2026; 4 published releases (1.0.0, 0.0.4, 0.0.2, 0.0.1). Slice 1.G dual-pin is mechanically installable. (Opponent already covered this in §7.1 with richer requires_dist.)

#### R2.2 docs.polymarket.com/quickstart (proponent's own, 2026-04-26)

`https://docs.polymarket.com/quickstart` → 200. CONTRADICTORY INTERNAL AUTHORITY:
- Install instruction: `pip install py-clob-client-v2` (V2 pip package)
- Import statement shown: `from py_clob_client.client import ClobClient` (V1 IMPORT PATH)
- Collateral wording: explicitly names pUSD: "Before trading, your funder address needs **pUSD** (for buying outcome tokens) and **POL** (for gas, if using EOA type 0)." and "BUY orders: need pUSD in your funder address."
- No heartbeat cadence requirement
- No V1 EOL / migration timeline

This is a SIXTH external inconsistency: V2-pip + V1-import on the SAME quickstart page. **Strong signal that Polymarket intends `py-clob-client-v2` to be installed AS the unified client and used via the V1 import path that existing code already has.** Compatible with opponent-down's "unified V1+V2 client" finding (their §3.1, §4.4, §6).

#### R2.3 Major findings from opponent-down's deeper boot (incorporated into my position)

opponent-down fetched the V2 SDK SOURCE (not just README) at commit `a2ec069f` 2026-04-17. Live-source findings BUST several `v2_system_impact_report.md` premises that my earlier "VERIFIED-via-impact-report" rows above LEAN ON. **Updated authority assessment** (these supersede the table above):

| Plan claim | Updated status | Authority |
|---|---|---|
| V2 collateral = pUSD on `0xC011a7…2DFB` | **CONTRADICTORY DUAL-AUTHORITY** | docs say pUSD; SDK config has the historical USDC.e address. Operator must read on-chain `symbol()` on Polygonscan. NEW Q-NEW-1 needed. |
| V2 EIP-712 domain version "2" deploy-time switch | **FALSIFIED** | SDK exports both `OrderArgsV1` and `OrderArgsV2`; `_resolve_version()` is per-token runtime resolution. The plan's `clob_protocol.eip712_domain_version: str` field encodes a non-existent decision. |
| V2 mandatory ~10s heartbeat; missing → server cancels all | **UNSOURCED** | `post_heartbeat()` exists but no internal cadence; not called by `create_and_post_order`; V1 v0.34.2 (2026-01-06) ALREADY had heartbeat support — heartbeat is not V2-new. Until Polymarket support / Discord URL produced, M1 / Phase 2.B should be RECOMMENDED, not MANDATORY. |
| V2 adds `delayed`/`ORDER_DELAYED` transitional states | **UNSOURCED in SDK** | Zero `delayed|delaying` hits in client.py + clob_types.py + endpoints.py per opponent's grep. The "latent capital-leak" §4.3 narrative needs a docs URL OR retraction. **Defensible narrower form**: `_normalize_status` (fill_tracker.py:390) should add an UNKNOWN_STATUS branch that calls reconcile-and-quarantine on ANY status the closed enum doesn't recognize — protocol-version-independent robustness. |
| V2 OrderArgs ADDS `metadata, builder_code, defer_exec, timestamp` | **PARTIALLY FALSIFIED** | `OrderArgsV2` (clob_types.py:75-97) has `token_id, price, size, side, expiration, builder_code, metadata`. `defer_exec` is a method-arg of `create_and_post_order`. `timestamp` is on the SIGNED order, not on `OrderArgsV2`. Plan slice 1.D antibody asserts on field names that DON'T EXIST on `OrderArgsV2` — would FAIL on first run. |
| V2 SDK is a separate-package alternative to V1 | **FALSIFIED** | V2 SDK is a UNIFIED V1+V2 client. The deploy-time `ZEUS_CLOB_PROTOCOL=v1\|v2` env var encodes a choice the SDK has eliminated. |
| V1 v0.34.6 released 2026-04-19 (2 days post-V2 GA) | **FALSIFIED — TRANSCRIPTION ERROR** | Actual V1 v0.34.6 release 2026-02-19. V1 has been DORMANT 2 months pre-V2 GA. Urgency-driver in plan rests on a transcription error. |
| `getClobMarketInfo` is a 1-call replacement for fee/tick/neg_risk | **PARTIALLY REDUNDANT** | V2 SDK provides dedicated `get_fee_rate_bps`, `get_fee_exponent`, `get_tick_size`, `get_neg_risk` AND internally caches `MarketDetails` via `__ensure_market_info_cached` (client.py:1061). Slice 2.F adds a Zeus cache layer parallel to SDK cache — redundant. |

## 4. Q1-Q7 preliminary pre-answers (operator-confirm flagged)

Each cell flags my WebFetch finding + impact_report claim + recommended operator-action.

| Q | WebFetch | Impact_report | Status | Operator-action |
|---|---|---|---|---|
| Q1 (`clob-v2.polymarket.com` reachable from Zeus's egress) | did not probe (Phase 0.A is operator-only) | host name documented | OPEN | Phase 0.A `curl -I` from Zeus host |
| Q2 (V2 OrderArgs schema + per-method signature diff) | README v1.0.0 shows `token_id/price/side/size` minimal; `metadata/builder_code/defer_exec/timestamp` are NOT in basic README example so likely optional kwargs | claims explicit add/remove field list | **PRELIMINARY-RESOLVED** — operator should clone source and grep `OrderArgs` dataclass to confirm exact field set; my WebFetch insufficient because clob_types.py URL 404'd |
| Q3 (`getClobMarketInfo` 1-call) | README does not mention | claims "discoverable via `getClobMarketInfo(conditionID)`" §2.8 | OPEN | Phase 0.C operator-grep on V2 SDK |
| Q4 (V1 fee snapshot for ≥3 weather tokens) | n/a (live data) | n/a (live data) | OPEN | Phase 0.E live `polymarket_client.get_fee_rate` runs |
| Q5 (USDC.e → pUSD bridge path) | docs page silent on pUSD wording | impact_report §4.5: "no public document explains the bridge path" | **EXPLICITLY-OPEN** | Phase 0.D — Polymarket support inquiry |
| Q6 (V1 EOL) | no EOL notice on V1 README; V1 patched 2026-02-19 (v0.34.6); V2 GA 2026-04-17 → V1 has been patched AFTER V2 GA | impact_report §1: "No public V1 EOL date has been announced" | **NEGATIVE-PRELIMINARY-RESOLVED** — V1 likely safe for months; operator confirm via 0.D |
| Q7 (Builder code required) | "Builder Program" wording present, no V2-required wording | impact_report §2.2: "Builder code became native order field; HMAC also still works" | OPEN | Phase 0.D inquiry |

## 5. Working hypothesis for Layer 1

### Authority direction
Transport (V2 host + SDK + heartbeat + collateral) is UPSTREAM of execution-journal (`venue_commands`, `venue_command_events`, `idempotency_key`, `command_state`). Data flows transport → fill_tracker → journal. CORRECTNESS authority is the OTHER way: execution-journal invariants (INV-23..32, NC-16..19) are the laws transport must serve.

### V2 plan structural quality
Plan §2 three-paradigm framing (A. Transport, B. Collateral, C. State machine) is **structurally honest**: V1↔V2 are independent Python packages, so the "abstraction first, infrastructure second" sequencing (Phase 1 → Phase 2) is correctly ordered.

But two structural over-claims exist in the plan:

**Over-claim 1**: Plan §5.2.B describes heartbeat as "Zeus's first cross-cycle concurrent component" with "thread-safe design on `_cycle_lock`". This is wrong on the facts — main.py:579 already runs `_write_heartbeat` as a peer apscheduler job at 60s cadence, with 3-strikes fatal halt at line 367. Zeus's daemon is ALREADY concurrent-with-cycle. The V2 heartbeat coroutine is a **fourth scheduler job at 10s cadence with `clob.heartbeat()` as its body and tombstone-write as its failure path** — same shape as the existing daemon heartbeat, smaller scope than the plan implies. `heartbeat_supervisor.py` is OK as a separate module name, but the "first concurrency in daemon" framing should be retired.

**Heartbeat-supervisor plug-in seam (precise, per team-lead's path-correction request)**: in `src/main.py`, the exact insertion site is line 579 (immediately after `_write_heartbeat` registration), as a sibling `scheduler.add_job(...)` call:

```
scheduler.add_job(
    _clob_v2_heartbeat_tick, "interval", seconds=<phase0_confirmed>,  # ~10
    id="clob_v2_heartbeat", max_instances=1, coalesce=True,
)
```

`_clob_v2_heartbeat_tick` is the new function (or wrapper around a coroutine) imported from `src/engine/heartbeat_supervisor.py` per plan slice 2.B `Allowed files`. **No `_cycle_lock` interaction needed**: apscheduler's `BlockingScheduler` runs each job on its own thread-pool worker; `_run_mode` already acquires `_cycle_lock` for cycle-internal state writes (main.py:79), but the heartbeat tick is a pure boundary call (`clob.heartbeat()` → 200 OK or write tombstone). It does NOT read or mutate `portfolio` / `tracker` / `state/world.db`. The only shared state is `state/auto_pause_failclosed.tombstone`, which uses the same atomic-tmp-then-replace pattern already used by `_write_heartbeat` (lines 350-352). Thread-safety is achieved by atomic-write on the producer side + read-once pre-cycle entry on the consumer side — no new lock primitive needed.

**Conflict with cycle_runner's sequential model — there is none, with one consumer-side gate**: `cycle_runner.py::run_cycle` is sequential within a cycle, but the heartbeat tick fires INDEPENDENTLY of any run_cycle invocation (apscheduler is the orchestrator, not run_cycle). The single point where the cycle could see a stale-but-recently-failed heartbeat is the cycle entry — fix is a `_check_tombstone_and_short_circuit()` pre-check inside `_run_mode` (or its callee) BEFORE the cycle body runs. If tombstone exists with `reason="clob_v2_heartbeat_failure_*"`, abort the cycle (don't submit orders, don't cancel — just no-op and log). This is a SMALL extension to the existing `auto_pause_failclosed.tombstone` semantics (impact_report §4.1) — V2 heartbeat just adds a new reason value, not a new mechanism.

**RealizedFill (T5.d) impact**: read `src/contracts/realized_fill.py:1-105` (per team-lead path correction). `RealizedFill` is a frozen dataclass with `execution_price`, `expected_price`, `slippage`, `side`, `shares`, `trade_id`. Its `__post_init__` enforces currency parity between execution and expected prices and rejects non-finite/empty share/trade_id values. **V2 impact**: zero. Currency stays as `ExecutionPrice.currency` (probability-space — the price units, not the collateral asset), independent of collateral asset (USDC.e vs pUSD). pUSD swap (Phase 2.C) does NOT touch RealizedFill or its T5.a/T5.d siblings. Confirms touchpoint inventory §3 "all retained across V2".

**Over-claim 2**: Apr26 review F-003 ("local idempotency exists in name only", line 362) is partly addressed already. Zeus has `idempotency_key` field load-bearing on OrderResult/ExitOrderIntent + pre-submit lookup `find_command_by_idempotency_key` + `command_state` ack gate (INV-32 / NC-19). The F-003 residual gap is the durable JOURNAL of `signed_order_hash` + `client_order_id` + raw `post_order` response **before** post-side-effect, plus reconcile-first retry policy. This sits cleanly in **Region-Mid execution-journal slice (A1.5 amendment per Mid's context boot)**, NOT in V2 transport. F-003 STAYS OUT of V2 packet.

### Mapping V2-plan ↔ Apr26 findings (Down-region)

| Apr26 F-### | V2 packet absorbs? | Reasoning |
|---|---|---|
| F-001 (durable cmd before side effect) | NO | Region-Mid; partially shipped via INV-28+INV-30 |
| F-002 (typed exceptions UNKNOWN) | NO | A3 / Region-Mid |
| F-003 (no exchange-proven idempotency) | **NO** | Region-Mid execution-journal A1.5; V2 SDK does not change F-003's fix shape because `client_order_id` is not an SDK kwarg in v1.0.0 README and likely not at HTTP-header layer either |
| F-004 (partial-fill state machine) | NO | Region-Mid; V2 vs V1 shared gap |
| F-005 (cancel-failure first-class) | NO | Region-Mid A4.5 |
| F-006 (exchange reconciliation loop) | NO | Region-Mid |
| F-007 (Gamma/Data/CLOB boundary) | NO | Region-Up `ExecutableMarketSnapshot` |
| F-008 (YES/NO outcome-token identity) | NO | Region-Mid execution-journal A1.5 |
| F-009 (tick/min/negRisk discipline) | YES — partial | D1.D V2 SDK contract antibody + D1.E mirror — already routed |
| F-010 (RED authority drift) | NO | Region-Mid PR18-P2 A1 |
| F-011 (raw-payload persistence) | **PARTIAL — D2.D add-on** | SDK swap is the moment Zeus owns the wire envelope; capture raw `signed_order` + `post_order_response` + `getHeartbeat` raw at boundary. Cross-cut X4 verdict pending — defending this routing |
| F-012 (happy-path-only test) | NO | per-region acceptance criterion (X2) |

### Wave 2 routing (Down-adjacent items)

| Wave 2 item | V2-internal? | Reasoning |
|---|---|---|
| B2 (settlement backfill scripts) | NO | DATA work, scripts/backfill_*; pre-existing |
| B4 obs_v2 physical bounds | NO | DATA work |
| B5 DST flag writer + backfill | NO | DATA work |
| B6 G6+reason-fidelity | **ADJACENT** — execution correctness; the `_execute_force_exit_sweep` exit-reason setter (cycle_runner.py:60-100) overlaps F-010/A1 territory; touchpoint inventory does not list this as a V2 concern |
| G7 LIVE_SAFE_CITIES (HK in initial set per U1 verdict) | **ADJACENT** — strategy gate; touches `executor.py`; should NOT be sequenced inside V2 packet but should COORDINATE on `executor.py` edits to avoid co-tenant collisions during Phase 2.D SDK swap |
| G10-cutover (`src/main.py` scheduler removal + launchd plists) | **HIGH-COUPLING-ADJACENT** — both V2 Phase 2.B (heartbeat coroutine in main.py) and G10-cutover edit main.py scheduler; ordering matters. **Recommend G10-cutover lands BEFORE V2 Phase 2.B** so the heartbeat coroutine is added on top of stable scheduler, not racing a scheduler refactor. |

### Cross-cut positions (Down's stake)

- **X1 (PR18-P2 A1 ↔ D2.A delayed-status branch)**: SEQUENCE A1 BEFORE D2.A. Reasoning: A1 is the durable cancel-command journal (RED→cancel). When V2 D2.A introduces a `delayed` state with wall-clock timeout that voids positions, the void path needs A1's durable-cancel-command emission to avoid silently broken cancels. If D2.A lands first, you get capital-leak symmetry (delayed→void without durable cancel) that A1 has to fix afterward anyway.
- **X4 (F-011 raw-payload persistence — D2.D add-on vs independent NET_NEW packet)**: D2.D ADD-ON. Reasoning: SDK swap is when Zeus has full control of `OrderArgs` construction + post_order response (`polymarket_client.py:155+196`). Inserting raw-payload capture lines at the same boundary is ~30 LOC and a single column on `venue_commands`. Splitting into a separate packet creates two diff-windows on the same chokepoint module — collision risk per memory `feedback_no_git_add_all_with_cotenant`. Region-Up may push back wanting a broader `clob_market_snapshot` table; I will defend **D2.D add-on for the order-side raw payload** while conceding `clob_market_snapshot` (read-side / discovery-time snapshot) is a separate Region-Up F-007 concern.

### Slice cards I plan to mint after consensus
- `down-01` clob_protocol method-shape only (no field-list freeze pre-Q2)
- `down-02` heartbeat coroutine as 4th apscheduler job + tombstone fail-closure (NOT new threading model)
- `down-03` V2/F-### co-location appendix (which findings absorb vs stay)
- `down-04` Phase 0.B addendum: V2 idempotency surface deep-source check (negative result expected)
- `down-05` raw-payload capture at SDK boundary (D2.D add-on for X4)
- `down-06` G10-cutover sequencing precedes V2 Phase 2.B (cross-cut with live-readiness packet)
- `down-07` X1 sequencing: A1 before D2.A

## 6. Counter-position to anticipate from opponent-down

Opponent-down will likely argue:
- (Strong) Plan §5.2.B "first concurrency in daemon" framing is wrong (matches my own finding) — opponent will ALSO claim this, so we converge here.
- (Medium) F-003 might absorb into V2 IF V2 exposes a server-side idempotency key at HTTP-header layer that V1 lacked — opponent will demand Phase 0.B addendum to settle. I agree this is a fair gate; my position is "expected-negative but not certain" so we converge on adding the addendum.
- (Medium) X1 sequencing — opponent may argue D2.A can land first because `delayed→matched` doesn't need cancel-command emission, only `delayed→cancelled` does. Counter: the wall-clock-timeout-voids-position branch is what needs A1; if D2.A lands without the timeout-void branch, the slice is incomplete; if D2.A includes the timeout-void branch, A1 is a strict prerequisite.
- (Strong) X4 — opponent may argue F-011 deserves its own slice because raw-payload retention is a CROSS-flow concern (orders + orderbook + gamma snapshots), not just orders. Counter: agree on the cross-flow framing — defend `D2.D add-on for ORDER raw payload only`; concede `discovery-time snapshots` (`clob_market_snapshot` per F-007 fix design) belong to Region-Up.
- (Weak) Plan should ABSORB Wave 2 G10-cutover into V2 packet because both touch main.py. Counter: G10-cutover is a launchd-plist refactor with no V2 dependency; better to close it as Wave 2 item BEFORE V2 Phase 2.B opens.

## 7. ACK statement to judge

Solo context boot complete. Down region scope (V2 packet + F-003 + Wave 2 B2/B4/B5/B6 + cross-cuts X1/X4) verified against HEAD `874e00c`. V2 SDK exists at v1.0.0 (verified). V2 heartbeat as "first concurrency" claim in plan is wrong — `_write_heartbeat` apscheduler job already runs cross-cycle. F-003 belongs to Region-Mid execution-journal A1.5, not V2 transport. Recommended cross-cut verdicts: X1 = A1-before-D2.A; X4 = D2.D add-on for order-side raw payload, discovery-side raw to Region-Up. 7 candidate slice cards drafted (down-01..down-07). Ready for R1L1 A2A on greenlight.
