# SCAFFOLD — F14 + F16 cascade-liveness fix (PR-I)

**Status**: opus-architect draft + operator v2 revision for opus-critic review (Gate G1 per FIX_PLAN §1.2)
**Owner**: PR-I (Path A-clean, Karachi 5/17 T-0 = 2026-05-17 12:00 UTC)
**Author**: opus SCAFFOLD architect 2026-05-16; v2 §K + §I.1 amendment by main session per operator directive "选择架构上最干净和正确的解法" 2026-05-16

## Changelog v1 → v2 (2026-05-16, operator-directed)

SCAFFOLD v1 (sections A through J as written by opus architect) honestly disclosed in §I.0 that `PolymarketV2Adapter.redeem` is a hard stub returning `REDEEM_DEFERRED_TO_R1` and that PR-I plumbing alone does not enable end-to-end on-chain settlement. v1 framed three operator paths (A-as-scoped / A-extended / C) and recommended A-as-scoped (Karachi $0.59 manually claimed via Polymarket UI; PR-I.5 wires adapter post-Karachi).

Operator override 2026-05-16: pick the architecturally cleanest solution, not the menu. The v2 revision adds **§K "Operator-completion as first-class state"** which captures the missing fourth path:

- Stub-after-cascade is not an exception (don't park in REVIEW_REQUIRED). It is a *designed state* (`REDEEM_OPERATOR_REQUIRED`) until R1 phase wires `PolymarketV2Adapter.redeem` for real.
- A dedicated `scripts/operator_record_redeem.py` CLI advances `REDEEM_OPERATOR_REQUIRED → REDEEM_TX_HASHED` post-manual-UI-claim (no signing, no gas, no web3-write — record-only).
- Distinct from generic REVIEW_REQUIRED: dedicated alert path, dedicated runbook, dedicated state-machine semantics.
- F14 category-antibody still holds (cascade_liveness_contract.yaml unchanged); on top, the operator-completion step becomes auditable + first-class instead of "outside the system".

§I.1 amended to add **Path A-clean** as the chosen path. v1 §I.0-I.4 retained as analytical history (do not delete — critic should see the reasoning chain). §H files-changed manifest amended to reflect §K additions.

Net scope add over v1: 1 new state enum value, 1 new state-transition in submit_redeem when stub detected, 1 new CLI script (~80 LOC), 1 new test for state transition, 1 new test for CLI smoke, KARACHI runbook §1 rewrite. **No web3 dependency**; CLI is record-only.

**Author**: opus SCAFFOLD architect, 2026-05-16
**Authority basis**:
- `docs/operations/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md` §1, §1.1, §1.2, §4.1
- `docs/operations/task_2026-05-16_deep_alignment_audit/RUN_4_findings.md` §F14
- `docs/operations/task_2026-05-16_deep_alignment_audit/RUN_5_findings.md` §F16, §1 (state-machine inventory)
- `docs/operations/task_2026-05-16_deep_alignment_audit/FINDINGS_REFERENCE.md` rows F14 (line 79) + F16 (line 81)
- `~/.claude/CLAUDE.md` Fitz Methodology #1 (structural decisions) + Universal Methodology #3 (immune system / antibody)
- `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md`

---

## A. Architecture summary

Two cascade-liveness defects share a single root pattern: **a complete state machine with no scheduled driver wiring the second hop.** F14 is active (rows will be enqueued on Karachi settlement); F16 is latent (no enqueue path either). The fix is one structural antibody, not two patches.

### A.1 F14 redeem cascade — current vs. fixed state diagram

**Current (broken at L4):**

```
Polymarket endDate  ──>  harvester_truth_writer  ──>  forecasts.settlements (VERIFIED row)
                                                                │
                                                                ▼   [APScheduler hourly "harvester" job, src/main.py:927]
                                              harvester_pnl_resolver  ──>  _settle_positions  ──>  decision_log + ledger
                                                                │
                                                                ▼
                                            enqueue_redeem_command(harvester.py:555)
                                                                │
                                                                ▼
                                            request_redeem  ──>  settlement_commands row
                                                                  state = REDEEM_INTENT_CREATED
                                                                │
                                                                ▼
                                                              ✗ DEAD ✗   ← no production caller of submit_redeem
                                                                │
                                                                ▼
                                            (manual operator REPL invocation required)
```

**Fixed:** insert two APScheduler interval jobs in `src/main.py` (next to the existing `harvester` job at L927) that poll `settlement_commands` and drive the transitions.

```
                                            settlement_commands row  state=REDEEM_INTENT_CREATED
                                                                │
                                                                ▼   [NEW: APScheduler interval=5min "redeem_submitter"]
                                            submit_redeem  ──>  adapter.redeem  ──>  state=REDEEM_TX_HASHED
                                                                                              │
                                                                                              ▼   [NEW: APScheduler interval=10min "redeem_reconciler"]
                                                                       reconcile_pending_redeems  ──>  web3 receipt
                                                                                              │
                                                                                              ▼
                                                                  state ∈ {REDEEM_CONFIRMED, REDEEM_FAILED, REDEEM_REVIEW_REQUIRED}
                                                                                              │
                                                                                              ▼
                                                                                          TERMINAL
```

### A.2 F16 wrap/unwrap state machine — current

```
wrap_unwrap_commands.request_wrap()  ──>  WRAP_REQUESTED row
                                                  │
                                                  ▼   ← ✗ no enqueue caller anywhere ✗
                                                  │   ← ✗ no submit driver ✗
                                                  │   ← ✗ no reconcile loop (reconcile_pending_wraps_against_chain is a stub returning None) ✗
                                                  ▼
                                              UNREACHABLE
```

Table is empty (`SELECT COUNT(*) FROM wrap_unwrap_commands = 0`) — F16 is the classic "complete state machine, missing operator" shape. F14 has at least an enqueue caller; F16 does not.

### A.3 The unifying structural decision

A *cascade-liveness contract* (FIX_PLAN §2 theme T2): every state-machine table with `*_INTENT_CREATED` / `*_REQUESTED` rows MUST have a registered APScheduler poller, asserted at boot and in CI. F14 is fixed by adding the pollers; F16 is fixed by either deleting the module (§E) or admitting it has no scheduler entry and refusing it under the contract.

The antibody (§G) makes the entire category of "any *_INTENT_CREATED row with no scheduled poller" permanently impossible — not just for F14 and F16, but for every future state machine added to `src/execution/`.

---

## B. APScheduler job registration spec

### B.1 Insertion site

`src/main.py`, between the existing `harvester` job at L927 and the `heartbeat` job at L928. Mirror the same call-form as `_harvester_cycle`. Wrap each tick with the existing `@_scheduler_job(name)` decorator (defined at L57) so success/failure flows into `_write_scheduler_health` (B047 observability invariant — already wired for every other job).

### B.2 Exact edit (additive, surgical)

After existing `src/main.py:927`:

```python
scheduler.add_job(_harvester_cycle, "interval", hours=1, id="harvester")
```

Insert these three blocks **before** the `heartbeat` job at L928:

```python
# F14 cascade-liveness antibody (PR-I, 2026-05-16 SCAFFOLD §B).
# Polls settlement_commands.state='REDEEM_INTENT_CREATED' → submit_redeem.
# 5-min interval matches "post-harvester" cadence: harvester fires hourly,
# so an enqueued row is at most 5 min behind harvester completion.
scheduler.add_job(
    _redeem_submitter_cycle, "interval",
    minutes=5, id="redeem_submitter",
    next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 60.0),
    max_instances=1, coalesce=True,
)

# F14 part-2 reconciler: tx_hash → terminal via web3 receipt.
# 10-min interval — Polygon receipts settle in seconds but we want to
# allow chain re-orgs to stabilise before declaring CONFIRMED/FAILED.
scheduler.add_job(
    _redeem_reconciler_cycle, "interval",
    minutes=10, id="redeem_reconciler",
    next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 120.0),
    max_instances=1, coalesce=True,
)
```

### B.3 Tick body sketches (PSEUDOCODE — not implementation)

```python
@_scheduler_job("redeem_submitter")
def _redeem_submitter_cycle() -> None:
    """Drive REDEEM_INTENT_CREATED → REDEEM_TX_HASHED via submit_redeem.

    Idempotency: SettlementState.submit_redeem only operates on rows in
    _SUBMITTABLE_STATES = {REDEEM_INTENT_CREATED, REDEEM_RETRYING}; any
    racing tick that re-selects an already-transitioned row will hit
    SettlementCommandStateError and skip (see §D).
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import (
        submit_redeem, SettlementState, SettlementCommandStateError,
    )
    from src.state.db import get_trade_connection_with_world
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter
    from src.state.collateral_ledger import CollateralLedger

    with acquire_lock("redeem_submitter") as acquired:
        if not acquired:
            logger.info("redeem_submitter skipped_lock_held")
            return
        conn = get_trade_connection_with_world()
        try:
            rows = conn.execute(
                """
                SELECT command_id FROM settlement_commands
                 WHERE state IN (?, ?)
                 ORDER BY requested_at, command_id
                 LIMIT 32  -- bounded per tick; tail catches up next interval
                """,
                (
                    SettlementState.REDEEM_INTENT_CREATED.value,
                    SettlementState.REDEEM_RETRYING.value,
                ),
            ).fetchall()
            if not rows:
                return
            adapter = PolymarketV2Adapter()  # canonical construction per src/venue/AGENTS.md
            ledger = CollateralLedger(conn)
            submitted = failed = 0
            for row in rows:
                try:
                    submit_redeem(row["command_id"], adapter, ledger, conn=conn)
                    submitted += 1
                except SettlementCommandStateError:
                    # Racing tick already transitioned this row — benign.
                    continue
                except Exception as exc:
                    logger.warning(
                        "redeem_submitter: command_id=%s failed: %s",
                        row["command_id"], exc,
                    )
                    failed += 1
                    # Row state already updated to REDEEM_RETRYING / REDEEM_FAILED
                    # by submit_redeem's exception handler — see §C.
            conn.commit()
            logger.info("redeem_submitter: submitted=%d failed=%d", submitted, failed)
        finally:
            conn.close()


@_scheduler_job("redeem_reconciler")
def _redeem_reconciler_cycle() -> None:
    """Drive REDEEM_TX_HASHED → terminal via web3 receipt lookup.

    Under current adapter state (§I.0), no row reaches REDEEM_TX_HASHED
    because adapter.redeem returns REDEEM_DEFERRED_TO_R1 (terminal at
    REDEEM_REVIEW_REQUIRED). This tick is therefore a no-op in practice
    today; it ships so that when PR-I.5 wires a real adapter, the
    reconciler is already in place. The tick must short-circuit cleanly
    if web3 is unavailable (no RPC plumbed).
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import reconcile_pending_redeems
    from src.state.db import get_trade_connection_with_world

    with acquire_lock("redeem_reconciler") as acquired:
        if not acquired:
            return
        conn = get_trade_connection_with_world()
        try:
            # Fast-exit if no candidate rows — avoids spinning on web3 init
            # when adapter is unwired (current state).
            n_pending = conn.execute(
                "SELECT COUNT(*) FROM settlement_commands "
                "WHERE state = 'REDEEM_TX_HASHED' AND tx_hash IS NOT NULL"
            ).fetchone()[0]
            if n_pending == 0:
                return
            web3 = _build_web3()  # see §B.5 — new helper, scope add
            if web3 is None:
                logger.warning(
                    "redeem_reconciler: %d pending rows but web3 unavailable; "
                    "PR-I.5 adapter wiring required for chain reconcile", n_pending
                )
                return
            results = reconcile_pending_redeems(web3, conn)
            conn.commit()
            logger.info("redeem_reconciler: results=%d", len(results))
        finally:
            conn.close()
```

### B.5 `_build_web3()` helper (new — scope-disclosure)

`grep -rn "from web3\|Web3(" src/` returned **zero hits**. There is no existing web3 client factory in `src/`. PR-I therefore needs a minimal new helper:

```python
def _build_web3():
    """Return a web3.Web3 client against Polygon RPC, or None if unconfigured.

    Returns None — NOT raises — when RPC URL is unset, so the reconciler
    short-circuits cleanly rather than crashing the daemon. This matches
    the existing scheduler-job fail-open contract (@_scheduler_job).

    PR-I.5 (adapter wiring) replaces this with a fully-resolved
    Web3+contract+signer chain; today, only the read-receipt path is needed.
    """
    try:
        from web3 import Web3
    except ImportError:
        return None
    from src.config import settings
    rpc_url = settings.get("polygon_rpc_url") or os.environ.get("POLYGON_RPC_URL")
    if not rpc_url:
        return None
    return Web3(Web3.HTTPProvider(rpc_url))
```

**Scope honesty**: PR-I adds `web3` to `requirements.txt` if not already present (likely is — Polymarket SDK pulls it transitively). Implementer to confirm with `pip show web3` before commit; if absent, this is a 1-line `requirements.txt` add, not a Tier-0 risk.

### B.4 Job-registration semantics

| Property | redeem_submitter | redeem_reconciler |
|---|---|---|
| Trigger | `interval`, minutes=5 | `interval`, minutes=10 |
| `max_instances` | 1 (no concurrent ticks) | 1 |
| `coalesce` | True (drop missed runs, single catch-up) | True |
| `next_run_time` | T+90s post-boot (stagger off harvester) | T+150s post-boot |
| Job ID | `"redeem_submitter"` | `"redeem_reconciler"` |
| Decorator | `@_scheduler_job("redeem_submitter")` | `@_scheduler_job("redeem_reconciler")` |

These mirror **exactly** the patterns at `src/main.py:921-929` (Day0 capture + harvester + heartbeat). No new APScheduler primitives.

---

## C. Per-job polling spec

### C.1 redeem_submitter SQL

```sql
SELECT command_id FROM settlement_commands
 WHERE state IN ('REDEEM_INTENT_CREATED', 'REDEEM_RETRYING')
 ORDER BY requested_at, command_id
 LIMIT 32
```

- Filter by `state` enum values (NOT by `tx_hash IS NULL` — those are not equivalent under `REDEEM_RETRYING`).
- `LIMIT 32` is a defensive backpressure cap; Karachi adds 1 row, so this is dormant in practice. Catches up over multiple ticks if a backlog ever forms.

### C.2 redeem_reconciler SQL

Already implemented inside `reconcile_pending_redeems(web3, conn)` at `src/execution/settlement_commands.py:442-453`:

```sql
SELECT * FROM settlement_commands
 WHERE state = 'REDEEM_TX_HASHED' AND tx_hash IS NOT NULL
 ORDER BY requested_at, command_id
```

No new SQL — the tick wrapper simply calls the existing function.

### C.3 Error handling, bounded retry, idempotency

| Source of failure | Behavior |
|---|---|
| DB lock (busy_timeout) | `get_trade_connection_with_world()` already sets WAL + busy_timeout per K1 fix. Single retry inside SQLite, then SqliteOperationalError surfaces and is logged + counted under `scheduler_jobs_health.json[redeem_submitter].FAILED`. |
| Adapter raises mid-`submit_redeem` | `submit_redeem` catches and transitions row to `REDEEM_RETRYING` (line 374-387). Next tick re-picks the row (it's still in `_SUBMITTABLE_STATES`). **Bounded retry**: see §C.4. |
| Adapter returns `{"errorCode": "REDEEM_DEFERRED_TO_R1"}` (currently the only return path from `PolymarketV2Adapter.redeem` at line 611-623) | `submit_redeem` transitions to `REDEEM_REVIEW_REQUIRED` (terminal). Operator escalation triggered on next critic review — see §I. |
| Adapter returns success without tx_hash | Transitions to `REDEEM_REVIEW_REQUIRED` (existing line 413). |
| web3 unavailable | `reconcile_pending_redeems` continues to next row when `_get_receipt(web3, tx_hash)` returns None. No transition, no error. Persistent unavailability surfaces via `scheduler_jobs_health.json[redeem_reconciler].FAILED` if `build_web3()` itself raises. |
| `build_web3` lookup | `PolymarketV2Adapter` already needs web3 for other paths; reuse the existing factory. If no factory exists today, the implementer adds `build_web3()` returning the canonical client per `src/venue/AGENTS.md` (a single-function shim; not new architecture). |

### C.4 Bounded retry decision

`REDEEM_RETRYING` is currently a transient state with no per-row attempt counter. **The SCAFFOLD does NOT add an attempt counter** in PR-I. Rationale:

- The existing settlement_commands schema does not carry `attempt_count`; adding it is out-of-scope for Karachi T-0.
- Per row: if `REDEEM_RETRYING` persists across >3 reconciler windows (>30 min) without transition to `REDEEM_TX_HASHED` or `REDEEM_FAILED`, that surfaces as an operator-visible Karachi anomaly per §I.
- For PR-I, the SLA is: "the redeem cascade fires programmatically OR a stuck-state row appears in `settlement_commands` for operator review within 30 min." Stuck-state visibility is the antibody (§G); bounded automatic retry can be a follow-up.
- If the critic flags this as insufficient, the simplest extension is `LIMIT 32` + a `requested_at < now() - INTERVAL '1 hour'` cutoff to stop pestering an adapter that keeps failing; this is a 2-line addition, not a re-architecture.

---

## D. Idempotency / racing-tick proof

This is the section most likely to hide a SEV-1. Treat it as the load-bearing claim of the SCAFFOLD.

### D.1 Claim

Two concurrent `_redeem_submitter_cycle` invocations (or one invocation racing a partially-completed prior tick that wrote `REDEEM_SUBMITTED` but crashed before commit) **cannot** result in two adapter.redeem() calls for the same `condition_id`.

### D.2 Mechanism (three layers)

**Layer 1 — APScheduler `max_instances=1` + `coalesce=True`.** APScheduler enforces single-tick-at-a-time per job id. A 5-min-interval tick that takes 6 min skips the next tick rather than running two concurrently. This is the same guarantee every other Zeus job relies on (mirroring L912/L925 patterns).

**Layer 2 — process-level advisory lock (`acquire_lock("redeem_submitter")`).** Even in the unlikely event of two daemon processes (e.g. accidental double-`launchctl load`), `src/data/dual_run_lock.acquire_lock` uses an OS file lock to serialize. Returns False if held; the second process logs `skipped_lock_held` and exits the tick.

**Layer 3 — state-machine guard inside `submit_redeem`.** Even if Layers 1+2 both fail (impossible per design, but the antibody must hold against incorrect operator action like manual REPL invocation), `submit_redeem` at line 342 raises `SettlementCommandStateError` if `state not in _SUBMITTABLE_STATES`. A racing call to a row already in `REDEEM_SUBMITTED` / `REDEEM_TX_HASHED` / terminal cannot complete the adapter side-effect.

Layer 3 alone is sufficient for the correctness invariant; Layers 1+2 are operational reliability.

### D.3 SAVEPOINT-during-adapter-call hazard

The existing implementation has a subtle pattern at `src/execution/settlement_commands.py:356-368`: a SAVEPOINT writes `REDEEM_SUBMITTED` *before* the adapter call at line 373. The `own_conn.commit()` at line 369-370 flushes the durable SUBMITTED state to disk **before** the adapter is contacted. This means:

- Crash mid-adapter → row state is `REDEEM_SUBMITTED`, no tx_hash. Next tick picks it up? **NO** — `REDEEM_SUBMITTED ∉ _SUBMITTABLE_STATES`, so the row is stuck.
- This is by design (the docstring at L321-323 calls it the "recovery anchor"), but it means a crashed adapter call requires operator intervention (the row will sit at `REDEEM_SUBMITTED` forever without a tx_hash).
- **SCAFFOLD note**: this is a pre-existing design property, not something PR-I changes. The critic may flag it as a concern; the response is "this is the existing contract; widening it to auto-recover from crashed SUBMITTEDs is out of scope for the Karachi window."
- **Stuck-row visibility**: surfaces via the §G antibody's row-state monitoring path — operator sees `REDEEM_SUBMITTED` aged >30 min in §I.3 monitoring table. Per §I.0 the current adapter state means rows terminate at `REDEEM_REVIEW_REQUIRED` rather than getting stuck at SUBMITTED, so this hazard is latent until PR-I.5 lands.

### D.4 Why not SELECT … FOR UPDATE?

SQLite doesn't support row-level locking — `BEGIN IMMEDIATE` is the closest equivalent. Layer 1 (`max_instances=1`) already provides per-job serialization, so adding `BEGIN IMMEDIATE` inside the tick adds no correctness; it only adds lock-storm risk against the harvester. **Decision: no BEGIN IMMEDIATE.** Connection-level WAL + busy_timeout (already applied by `get_trade_connection_with_world()`) is the canonical Zeus pattern.

### D.5 What CAN go wrong (and what's left for the critic to catch)

- If `PolymarketV2Adapter.redeem()` is non-idempotent on retry (e.g. submits the same on-chain tx twice), the adapter side has to deduplicate by `condition_id`. Today it always returns `REDEEM_DEFERRED_TO_R1` so this is moot, but if a future commit wires a real redeem the adapter MUST be idempotent on `condition_id`. **Flag for follow-up PR** (cited in §J risk register).
- `_savepoint` context manager at L356 — if it raises mid-block, the SAVEPOINT is released without commit. Need to confirm with critic that the existing primitive is exception-safe. Reading the codebase: `_savepoint` is used in 4+ other places in the same module, so this is established practice.

---

## E. F16 DECISION

### E.1 Z-phase context (verbatim from source)

- `src/execution/wrap_unwrap_commands.py` module docstring (line 1-5):
  > Durable USDC.e ↔ pUSD wrap/unwrap command states for R3 Z4. Z4 models request/tx/confirmation/failure state only. It does not submit live chain transactions; later operator-gated phases may attach a submitter.
- `src/execution/AGENTS.md` line 21:
  > `wrap_unwrap_commands.py` | Durable USDC.e↔pUSD command state | HIGH — no live chain side effects in Z4
- `reconcile_pending_wraps_against_chain` body (line 117-124):
  > Z4 intentionally does not perform live chain reads/writes here. R1/G1 can wire a concrete reconciler once operator gates and chain semantics are set.

### E.2 DECISION: **reject the binary framing; pick option (2-modified): keep the module, register a liveness-guard poller. Do NOT delete; do NOT wire real behavior.**

**Reframe of the binary**: the SCAFFOLD task posed F16 as (1) delete vs (2) wire-pollers. Both options as stated are wrong for the Karachi window. Option (1) destroys design intent under operational pressure; option (2) as literally posed ("wire pollers + enqueue stub") is the Z5-planned commit, which is months of pUSD migration work and not Karachi-window-fittable. The honest answer is a third shape: **keep the module + dormant table, register a no-op liveness-guard poller that asserts the table stays empty until Z5 actually lands**. This satisfies the cascade-liveness contract (§G) without committing to either the delete-the-design-intent path or the wire-the-pUSD-migration path. The DECISION must own this reframe rather than silently substituting it.

**Justification for the reframe**:

1. **The author explicitly anticipates future wiring** ("later operator-gated phases may attach a submitter", "R1/G1 can wire a concrete reconciler"). Deleting the module destroys design intent that survives ~20% across sessions per Universal Methodology #2 — the durable artifacts (schema + module skeleton) are valuable cross-session signals about the pUSD migration plan.
2. **Cost of deletion is asymmetric.** If we delete and Z5 later needs wrap/unwrap, the work to re-derive the state machine + schema is hours; the cost of keeping an empty table for 6-12 months is zero (the antibody at §G catches drift). Per Fitz Methodology #1 (structural decisions), the structural decision is "Zeus's pUSD migration uses a wrap/unwrap state machine"; deleting because it's currently dormant is reversing that decision under operational pressure.
3. **The antibody can subsume F16 without wiring real behavior.** §G's cascade-liveness contract requires every state-machine table to have a *registered scheduler entry*. For wrap/unwrap, the entry is a no-op poller that asserts the table stays empty until the Z5 cutover lands. This converts F16 from "latent SEV-0" to "actively guarded latent state" — strictly safer than today, weaker than full wiring, but Karachi-window appropriate.
4. **Option (1) — delete now — risks creating a precedent ("when in doubt, delete dormant modules") that conflicts with `feedback_live_alpha_overrides_legacy_design.md` ("live alpha overrides legacy design loyalty") — the wrap/unwrap module IS a legacy design choice, but it's an active design choice tied to the pUSD migration roadmap, not dead code. The right "delete it" trigger is: pUSD migration was abandoned. That's an operator decision, not a SCAFFOLD decision.
5. **Option (3) — wire full enqueue + submit + reconcile in PR-I — is out of scope**. PR-I is Karachi-blocking, NOT a pUSD migration kickoff.

### E.3 Concretely

- Add a third APScheduler job in `src/main.py`: `wrap_unwrap_liveness_guard`, interval 30 min, calls `_wrap_unwrap_liveness_guard_cycle()` which:
  - Queries `SELECT COUNT(*) FROM wrap_unwrap_commands` and asserts == 0.
  - If > 0, logs CRITICAL `wrap_unwrap_commands has rows but no production driver wired — F16 antibody triggered, operator review required`, sets `scheduler_jobs_health.json[wrap_unwrap_liveness_guard].status = FAILED`.
  - Returns without raising (consistent with `@_scheduler_job` fail-open contract).
- This satisfies the §G antibody: F16 module has a registered scheduler entry; the entry asserts the dormant contract; any future commit that enqueues a wrap without also wiring the rest of the cascade will surface as a loud operator-visible failure within 30 min.
- F16 is *NOT* split out into a separate PR (FIX_PLAN §4.4 lists PR-K but explicitly defers it). PR-I claims both F14 + F16 because the antibody is the same; PR-K becomes redundant and can be closed without merge — the FIX_PLAN should be updated accordingly in implementation (or PR-K issue is closed as duplicate of PR-I post-merge).

---

## F. Smoke test spec

### F.1 F14 smoke tests (in `tests/test_redeem_cascade_liveness.py`)

1. **`test_f14_karachi_synthetic_cascade`**: insert a `REDEEM_INTENT_CREATED` row via `request_redeem` with Karachi's condition_id `0xc5faddf4...`. Monkey-patch `src.main.PolymarketV2Adapter` with a mock returning `{"success": True, "tx_hash": "0xdeadbeef", "block_number": 1234}`. Invoke `_redeem_submitter_cycle()` directly. Assert row transitions to `REDEEM_TX_HASHED`.
2. **`test_f14_karachi_unwired_adapter_cascade`** (current-state regression): same setup but use the real `PolymarketV2Adapter` stub returning `REDEEM_DEFERRED_TO_R1`. Assert row transitions to `REDEEM_REVIEW_REQUIRED` (terminal). Documents the §I.0 behavior under current adapter state.
3. **`test_f14_scheduler_registration`**: mirror `tests/test_runtime_guards.py:9481`'s fake-scheduler boot pattern. Assert `{"redeem_submitter", "redeem_reconciler", "wrap_unwrap_liveness_guard"} ⊂ {j.id for j in scheduler.get_jobs()}`.
4. **`test_f14_idempotency_racing_tick`**: insert one row, invoke `_redeem_submitter_cycle()` twice back-to-back. Assert adapter.redeem called exactly once (per §D Layer 3 — second tick hits `SettlementCommandStateError` and skips).

### F.2 F16 smoke

5. **`test_f16_wrap_unwrap_liveness_guard_empty_table`**: empty table → tick exits silently, `scheduler_jobs_health.json[wrap_unwrap_liveness_guard].status=OK`.
6. **`test_f16_wrap_unwrap_liveness_guard_drift_alarm`**: insert synthetic `WRAP_REQUESTED` row (bypassing the unwired enqueue path) → tick logs CRITICAL, writes `FAILED` health status. Asserts the antibody fires on drift.

### F.3 Cascade-liveness CI assertion

`tests/test_cascade_liveness_contract.py` — see §G.3. Three tests: (a) every contract entry has a registered poller, (b) every state-machine poller appears in the contract (drift in the other direction), (c) the `mode` discriminator is consumed correctly per §G.2 inspect.getsource gates.

---

## G. Cascade-liveness ANTIBODY (non-negotiable)

### G.1 Pattern chosen: **(c) CI test enumerating cascade tables and confirming each has a scheduler entry**, augmented by **(a) startup registry assertion**.

Pattern (b) — "startup smoke that fails if any cascade table has INTENT rows older than N minutes with no recorded poll attempt" — is rejected because it's a runtime guard, not a structural guarantee. The instant a row is created the system has already lost; we want a guarantee BEFORE the first row.

### G.2 New file: `architecture/cascade_liveness_contract.yaml`

Single source of truth listing every state-machine table + its required scheduler job IDs:

```yaml
# Cascade-liveness contract — every state-machine table with *_INTENT_CREATED
# or *_REQUESTED rows MUST have at least one registered APScheduler job
# polling it. Enforced by tests/test_cascade_liveness_contract.py and by
# a boot-time assertion in src/main.py.
#
# Adding a new state machine: add an entry here, register the job in
# src/main.py, and the CI test self-validates.
#
# `db` field semantics: matches architecture/db_table_ownership.yaml's
# logical identity ("world" = the trade/world boundary, physically resident
# on state/zeus_trades.db after K1). Cross-reference db_table_ownership.yaml
# for authoritative table→physical_db mapping; cascade_liveness_contract
# never overrides that mapping, only annotates which tables are state machines.

state_machines:
  - table: settlement_commands
    db: world  # matches db_table_ownership.yaml:588-593
    intent_states: [REDEEM_INTENT_CREATED, REDEEM_RETRYING]
    required_pollers:
      - id: redeem_submitter
        watched_states: [REDEEM_INTENT_CREATED, REDEEM_RETRYING]
        owner: src/main.py:_redeem_submitter_cycle
        mode: submitter  # picks rows up and drives state transitions
      - id: redeem_reconciler
        watched_states: [REDEEM_TX_HASHED]
        owner: src/main.py:_redeem_reconciler_cycle
        mode: reconciler  # follows chain receipts to terminal state

  - table: wrap_unwrap_commands
    db: world  # matches db_table_ownership.yaml:711-716
    intent_states: [WRAP_REQUESTED, UNWRAP_REQUESTED]
    required_pollers:
      - id: wrap_unwrap_liveness_guard
        watched_states: [WRAP_REQUESTED, UNWRAP_REQUESTED]
        owner: src/main.py:_wrap_unwrap_liveness_guard_cycle
        mode: liveness_only  # counts rows, asserts table stays empty until Z5; not a state-transition driver
```

**Mode discriminator (consumed by §G.3 test, not just documented)**: the antibody test enforces:
- `mode=submitter`: poller must call a function with name pattern `submit_*` or `_*_submitter_cycle` operating on `watched_states`.
- `mode=reconciler`: poller must call a function with name pattern `reconcile_*` or `_*_reconciler_cycle`.
- `mode=liveness_only`: poller must NOT perform state transitions; asserts row count or aging only. The test enforces this by asserting the tick function's source does not call any settlement_commands/wrap_unwrap_commands `_transition` or `mark_*` helper.

Implementer enforces these via simple `inspect.getsource` checks in the antibody test. This converts the "documented but not consumed" footnote into a load-bearing structural guard.

### G.3 New CI test: `tests/test_cascade_liveness_contract.py`

Three tests, mirroring the existing `tests/test_runtime_guards.py:9468-9481` fake-scheduler boot pattern:

1. **`test_every_state_machine_in_contract_has_a_registered_poller`**: load `architecture/cascade_liveness_contract.yaml`. Boot `src.main.main()` with fake scheduler. For each `state_machines[*].required_pollers[*].id`, assert present in `{j.id for j in scheduler.get_jobs()}`. Failure message names the missing `(table, poller_id)` pair and points operator at "either register the job in src/main.py or remove the contract entry".
2. **`test_every_scheduler_poller_is_listed_in_contract`** (inverse drift guard): allow-list known non-state-machine jobs (`opening_hunt`, `update_reaction_*`, `day0_capture`, `harvester`, `heartbeat`, `venue_heartbeat`); every other scheduler job ID must appear in the contract. Prevents orphan pollers polling untracked tables.
3. **`test_poller_mode_discriminator_enforced`**: per §G.2 mode spec, use `inspect.getsource(owner_func)` to assert: `mode=liveness_only` pollers contain no calls to settlement_commands/wrap_unwrap_commands `_transition` or `mark_*` helpers; `mode=submitter` pollers must call `submit_*`; `mode=reconciler` pollers must call `reconcile_*`.

### G.4 Boot-time fail-closed assertion (optional second layer)

In `src/main.py` after `scheduler.add_job` calls and before `scheduler.start()`:

```python
def _assert_cascade_liveness_contract(scheduler):
    """Boot-time mirror of tests/test_cascade_liveness_contract.py.

    Fail-closed: refuses to start the daemon if any required poller is
    missing. This guards against accidental git-conflict resolution that
    deletes a job registration without updating the contract.
    """
    import yaml
    contract = yaml.safe_load(open("architecture/cascade_liveness_contract.yaml"))
    job_ids = {j.id for j in scheduler.get_jobs()}
    missing = []
    for sm in contract["state_machines"]:
        for poller in sm["required_pollers"]:
            if poller["id"] not in job_ids:
                missing.append((sm["table"], poller["id"]))
    if missing:
        raise SystemExit(
            f"FATAL: cascade_liveness_contract violation: missing pollers "
            f"{missing!r}. Refusing to boot."
        )

_assert_cascade_liveness_contract(scheduler)
```

### G.5 Why this is the right shape

- **CI catches the category at PR time** (test_cascade_liveness_contract.py).
- **Boot-time catches stale main on operator workstation** (the assertion).
- **Operationally observable** (scheduler_jobs_health.json shows each poller's status).
- **Generic**: any future state machine added to `src/execution/` inherits the antibody by adding 4 lines to the YAML. F17 (validated_calibration_transfers) and any new redeem/wrap/transfer machine plugs in identically.
- **Patching just submit_redeem alone is INSUFFICIENT.** That would close F14 instance without closing the category. The next time a developer adds `src/execution/transfer_commands.py` with `TRANSFER_INTENT_CREATED` and no scheduler, we're back to F14 shape on a different table.

---

## H. Files-changed manifest

| File | Range | Change | Rationale |
|---|---|---|---|
| `src/main.py` | L927-928 (insert before heartbeat job) | Add 3 `scheduler.add_job` blocks: `redeem_submitter`, `redeem_reconciler`, `wrap_unwrap_liveness_guard` (with `_assert_cascade_liveness_contract` call after them) | F14 + F16 scheduler registration |
| `src/main.py` | After `_harvester_cycle` def (~L160) | Add `_redeem_submitter_cycle`, `_redeem_reconciler_cycle`, `_wrap_unwrap_liveness_guard_cycle`, `_assert_cascade_liveness_contract`, `_build_web3` function defs | Tick bodies + boot assertion + web3 helper |
| `architecture/cascade_liveness_contract.yaml` | NEW | YAML registry of state-machine tables and required pollers | §G antibody data |
| `tests/test_redeem_cascade_liveness.py` | NEW | F14 smoke tests: scheduler registration + synthetic cascade against Karachi condition_id | §F.1, §F.2 |
| `tests/test_cascade_liveness_contract.py` | NEW | CI antibody asserting every contract entry has a registered poller and vice versa | §G.3 |
| `docs/operations/task_2026-05-16_deep_alignment_audit/KARACHI_2026_05_17_MANUAL_FALLBACK.md` | §1 cascade description | Correct erroneous "auto-cascade includes clob.redeem" wording (per FINDINGS_REFERENCE F14 evidence and RUN_4 §6) | Doc accuracy; the runbook still describes a half-true cascade |
| `requirements.txt` | NEW LINE (pin required — verified absent) | Add explicit `web3==<latest-stable>` pin. **Empirical verification 2026-05-16 in this SCAFFOLD session**: `pip show web3` in zeus `.venv` returned "Package(s) not found: web3" — NOT installed transitively. The `_build_web3` helper's `try: from web3 import Web3 except ImportError: return None` path means the reconciler tick logs a WARN and exits cleanly without web3 installed, but installing the pin is required for PR-I.5 to function downstream. Implementer should add the pin in PR-I so reconciler shipping isn't deferred. | `_build_web3` import dependency |
| `docs/operations/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md` | §4.4 PR-K entry | Annotate: "subsumed into PR-I; close without merge" — implementer should NOT edit FIX_PLAN per audit-branch read-only rule, but flag for operator | Doc consistency |

**File count: 6 files modified or created** (`src/main.py`, `architecture/cascade_liveness_contract.yaml` NEW, `tests/test_redeem_cascade_liveness.py` NEW, `tests/test_cascade_liveness_contract.py` NEW, `KARACHI_2026_05_17_MANUAL_FALLBACK.md`, `requirements.txt`). The FIX_PLAN annotation is operator-mediated. `src/main.py` consolidates the scheduler + tick + helper changes into a single file edit; the web3 helper does NOT touch `src/venue/polymarket_v2_adapter.py` (which is reserved for PR-I.5).

---

## I. Karachi T-0 contingency

### I.0 SCOPE HONESTY — adapter surface is unwired (SEV-1 disclosure)

> **This section re-opens the operator's Path A decision** because FIX_PLAN §1.1's framing assumed adapter wiring that empirical evidence (this SCAFFOLD's grep) confirms does not exist. Operator must re-confirm or re-decide Path A given the evidence below.


**Verified 2026-05-16 (this SCAFFOLD session) by grep**: `def redeem` exists at exactly 3 locations in `src/`:

- `src/venue/polymarket_v2_adapter.py:611-623` — returns `{"success": False, "errorCode": "REDEEM_DEFERRED_TO_R1"}` unconditionally
- `src/data/polymarket_client.py:700-724` — same hardcoded deferred return + DeprecationWarning
- `src/venue/polymarket_v2_adapter.py:160` — Protocol signature only

No `from web3` import exists anywhere under `src/`. **There is no production redeem code path that can produce a real tx_hash today.** Even with PR-I's scheduler wiring landed, Karachi's `REDEEM_INTENT_CREATED` row will:

1. Be picked up by `redeem_submitter` within 5 min of `harvester` enqueue
2. Transition `REDEEM_SUBMITTED` → adapter returns `REDEEM_DEFERRED_TO_R1` → terminal `REDEEM_REVIEW_REQUIRED`
3. **The $0.59 on-chain claim still requires manual operator action via Polymarket UI**.

This SCAFFOLD therefore distinguishes two antibodies, and PR-I owns only the first:

- **PR-I antibody (THIS scaffold)**: *cascade-plumbing liveness*. Every state-machine table has a registered scheduler driver. The Karachi row transitions OUT of `REDEEM_INTENT_CREATED` programmatically (into terminal `REDEEM_REVIEW_REQUIRED`), surfacing the unwired-adapter limitation as a loud, operator-visible signal instead of a silent stuck row. This is what makes the F14 *category* (intent rows enqueued with no driver) permanently impossible — verified by §G's CI test + boot assertion.
- **PR-I.5 (NOT in this PR)**: *adapter surface*. Replace `PolymarketV2Adapter.redeem` stub with a real ConditionalTokens contract call via web3. Requires: RPC URL plumbed from settings, contract ABI, wallet signing path, gas budgeting, tx confirmation handling. **Tier-0 venue-surface work, big scope add, NOT Karachi-window-fittable** per the empirical evidence above.

### I.1 Implication for FIX_PLAN §1.1 Path A framing

FIX_PLAN §1.1 motivates Path A by stating "If this one completes manually, the cascade-liveness antibody is never forced into existence." The current SCAFFOLD's honest read: **PR-I forces the cascade-plumbing antibody into existence**; it does NOT force the on-chain settlement to happen programmatically because the adapter surface is unwired. Karachi will still require manual UI claim of $0.59.

Two paths for operator (decision required before G3):

- **Path A-as-scoped** (v1 default): merge PR-I as drafted. Cascade-plumbing antibody lands; CI + boot assertion makes F14 category permanently impossible. Operator manually claims Karachi $0.59 via Polymarket UI per existing fallback runbook §3. **PR-I.5 follow-up tracks adapter wiring as a non-Karachi-blocking next step.** This is the most honest read of "Path A under empirical adapter state": you ship the structural antibody now, you ship the adapter wiring after Karachi when the time window allows Tier-0 venue-surface work.
- **Path A-extended**: expand PR-I scope to include a real `redeem()` implementation. Tier-0 venue-surface work. SCAFFOLD's verdict: NOT FEASIBLE in remaining Karachi window (T-24h → T-6h) per `feedback_long_opus_revision_briefs_timeout.md` (Tier-0 venue work over a short window is the exact shape that timed out 3/4 times in 2026-05-15 session). Recommending against.
- **Path C** (FIX_PLAN §1.2 fallback): manual fallback armed as primary. PR-I deferred. SCAFFOLD's verdict: REJECT — the cascade-plumbing antibody is valuable regardless of adapter state, and ships without venue risk.
- **Path A-clean (v2 CHOSEN, operator directive 2026-05-16)**: see §K. Adds `REDEEM_OPERATOR_REQUIRED` first-class state + `scripts/operator_record_redeem.py` record-only CLI. Cascade plumbing antibody same as Path A-as-scoped, but the operator-completion step is *designed* (alerted, recorded, idempotent) rather than *parked in REVIEW_REQUIRED*. Karachi $0.59 still requires operator action this cycle — but the action is a one-line CLI invocation that advances the state machine cleanly, not a silent out-of-system UI claim. R1 phase later replaces only the `REDEEM_OPERATOR_REQUIRED → REDEEM_TX_HASHED` transition with adapter call; all other state-machine + antibody work survives.

**SCAFFOLD v2 recommendation: Path A-clean per §K.** v1 Path A-as-scoped recommendation retained above for traceability of architectural reasoning.

### I.2 Expected log lines (UTC, post-merge — under current adapter state)

**T-0 to T+5 min** — Polymarket settles:
```
zeus-ingest.log: harvester_truth_writer settlements_written=1 condition_id=c5faddf4...
```

**T+1h** — hourly `harvester` job fires:
```
zeus-live.log: Harvester: {'status': 'ok', 'positions_settled': 1, ...}
zeus-live.log: pUSD redemption for <trade_id> (condition=c5faddf4...) recorded in R1 settlement command ledger: <command_id>
```

**T+1h+5min to T+1h+10min** — `redeem_submitter` fires (current adapter state):
```
zeus-live.log: redeem_submitter: submitted=1 failed=0
zeus-live.log:   command_id=<...> state=REDEEM_REVIEW_REQUIRED  # adapter returned REDEEM_DEFERRED_TO_R1
```

**T+1h+15min to T+1h+20min** — `redeem_reconciler` fires:
```
zeus-live.log: redeem_reconciler: results=0   # no row in TX_HASHED state because adapter is unwired
```

Post-PR-I.5 (adapter wired, future state) the same windows would show `REDEEM_TX_HASHED` then `REDEEM_CONFIRMED` instead.

### I.3 What to monitor

| Surface | Healthy signal (post-PR-I, current adapter state) | Anomaly signal |
|---|---|---|
| `state/scheduler_jobs_health.json` | `redeem_submitter.status=OK` last 5 min, `redeem_reconciler.status=OK` last 10 min, `wrap_unwrap_liveness_guard.status=OK` last 30 min | Any FAILED → operator review |
| `settlement_commands` row state | Transition INTENT → SUBMITTED → REVIEW_REQUIRED within ~1h+10min post-Polymarket-settle (adapter unwired, terminal at REVIEW_REQUIRED) | Stuck at `REDEEM_INTENT_CREATED` >30 min after enqueue → cascade plumbing still dead (PR-I antibody broken) |
| Stuck-row visibility | Per §G antibody: row in non-terminal `REDEEM_SUBMITTED` >30 min surfaces via `scheduler_jobs_health.json` (no auto-recovery for crashed-adapter; operator-driven from this signal) | Any row in `REDEEM_SUBMITTED` aged >30 min |
| `logs/zeus-live.err` | No new ERROR lines from `_redeem_submitter_cycle` or `_redeem_reconciler_cycle` | Repeated `submit_redeem` traceback → unexpected adapter error code — Path C trigger |
| `logs/zeus-live.log` | `redeem_submitter: submitted=N failed=0` heartbeat every 5 min | `failed>0` for 3+ consecutive ticks → Path C |
| Polymarket UI balance | (Manual claim required this cycle — see §I.0.) Karachi $1.5873 claimable balance appears post-manual-claim. | Adapter wiring would change this row in PR-I.5; not in scope for PR-I. |

### I.4 Path C escalation triggers

Per FIX_PLAN §1.2 + §9, any G1-G5 failure or:
- Karachi `REDEEM_INTENT_CREATED` row stuck for >30 min post-enqueue with `redeem_submitter` showing FAILED
- `_redeem_submitter_cycle` raises uncaught (would surface as `scheduler_jobs_health.json` FAILED)
- `PolymarketV2Adapter.redeem` returns hard error code other than `REDEEM_DEFERRED_TO_R1`

→ Trigger Path C: arm manual fallback per KARACHI_2026_05_17_MANUAL_FALLBACK.md §3 starting T+9h. **Note**: under current adapter state, manual claim of the $0.59 is the expected outcome regardless of Path A/C; what Path C-vs-A-as-scoped controls is whether the PR-I plumbing antibody ships before T-0.

---

## J. Risk register

### J.1 Top 3 ways this SCAFFOLD could be wrong

**RISK 1 — PR-I does NOT make Karachi's $0.59 settle programmatically; only the cascade plumbing does.**

This is the SCAFFOLD's load-bearing scope honesty (now disclosed up-front in §I.0). Empirically verified 2026-05-16: every `def redeem` path in `src/` returns the `REDEEM_DEFERRED_TO_R1` stub. PR-I's scheduler wiring transitions Karachi's row INTENT → SUBMITTED → REVIEW_REQUIRED programmatically; on-chain claim still requires manual operator action.

This may read as failing FIX_PLAN §1.1's intent ("the cascade-liveness antibody is never forced into existence"). The reconcile:

- §1.1 conflates two antibodies: (a) cascade-plumbing — every state-machine table has a registered scheduler driver; (b) full programmatic settlement — the adapter actually claims on-chain. PR-I as scoped delivers (a). (b) requires PR-I.5 venue-surface work (out of Karachi window per §I.0).
- The first-row-of-Zeus-history precedent argument in §1.1 was written before this SCAFFOLD's adapter grep surfaced the stub state. The post-evidence read is: the precedent is set when *cascade-plumbing* antibodies are enforced, not when the adapter surface is complete. Adapter completeness is a separable Tier-0 venue surface.
- The alternative — expanding PR-I to include adapter wiring — is a real Tier-0 venue PR (Polymarket SDK contract calls, gas, signing, receipt confirmation) that does not fit Karachi T-6h. Forcing it into the window risks both PR-I and the venue surface.

**SCAFFOLD recommendation**: ship PR-I as scoped (cascade plumbing). Open PR-I.5 to wire `PolymarketV2Adapter.redeem` as the next Tier-0 venue surface PR after Karachi window closes. Operator manually claims Karachi $0.59 via Polymarket UI per existing fallback runbook §3.

**Decision deferred to operator** (G2 critic + operator joint review): is "cascade-plumbing antibody now + manual UI claim this cycle + adapter wiring next PR" acceptable as the precedent shape? If not, fall back to Path C (FIX_PLAN §1.2) — do NOT bolt adapter wiring into PR-I under time pressure.

**Mitigation if critic insists on PR-I including adapter wiring**: see §I.0 Path A-extended verdict — SCAFFOLD's empirical assessment is NOT FEASIBLE in remaining window. If the critic disagrees, that's the conflict to surface to operator before G3, not to paper over in the SCAFFOLD.

**RISK 2 — `_assert_cascade_liveness_contract` fail-closed at boot creates a new boot-fail mode.**
A future PR that adds a state machine to the YAML but forgets to register the job (or vice versa) will brick the daemon at restart. This is the *intended* behavior, but it could fire during a partial deployment and block the trading daemon from coming up.

**Mitigation**: (a) The CI test catches the mismatch at PR time, before merge. (b) The error message in §G.4 names exactly which tables and pollers are missing — operator can comment out the offending YAML entry in <30s to recover. (c) The boot-fail mode is strictly safer than the silent-cascade-halt mode it replaces; F14 became operator-visible only after 5 audit runs. Per Universal Methodology #3, this is the antibody trade-off: explicit fail vs silent decay.

**SCAFFOLD DECISION**: hard fail-closed (`raise SystemExit`) per §G.4 code. No operator override in PR-I. Rationale:

- The error message names exactly which contract entry is broken — recovery is "comment out the offending YAML entry, restart" in <30s. This is faster than diagnosing a silent silent-degraded mode.
- CI catches it before merge anyway (§G.3). Boot assertion only fires if the YAML and src/main.py diverge between merge and deploy, which is a tiny window.
- Universal Methodology #3 explicitly endorses fail-closed antibodies over silent decay. A degraded-but-running daemon hides the antibody trigger and is the exact failure mode F14 occupied for 5 audit runs.

(Earlier SCAFFOLD draft floated a degrade-to-WARN alternative; that was prose inconsistency, retracted. The SCAFFOLD ships fail-closed.)

**RISK 3 — F16 decision (keep + no-op poller) is wrong if pUSD migration is abandoned.**
The SCAFFOLD assumes Z5 pUSD migration is roadmapped. If operator policy has moved away from pUSD entirely (e.g. "USDC.e is permanent settlement asset"), keeping the wrap/unwrap module is dead weight that bloats the antibody contract.

**Mitigation**: §E.2 reasoning #4 explicitly cites this as the right delete trigger. The SCAFFOLD does not have access to current pUSD migration policy as of 2026-05-16; if operator confirms abandonment, the F16 portion of PR-I downgrades to: delete `src/execution/wrap_unwrap_commands.py`, drop both tables, remove the YAML entry, remove the `wrap_unwrap_liveness_guard` job. This is mechanical and reversible; the antibody pattern survives unchanged. **Operator decision required before code freeze.** Default in PR-I: keep + no-op poller.

### J.2 Lower-tier risks (acknowledged, not Karachi-blocking)

- `acquire_lock("redeem_submitter")` — confirm `src/data/dual_run_lock` lock-name namespace doesn't collide with existing locks. Implementer to grep.
- `LIMIT 32` is arbitrary; for Karachi (1 row) it's massively over-provisioned. Could lower to LIMIT 8.
- (Resolved upstream in §G.2.) The `mode` discriminator is now load-bearing — the antibody test consumes it via `inspect.getsource` to enforce that liveness-only pollers never call state-transition helpers. Implementer follows the tri-state spec.

---

---

## K. Operator-completion as first-class state (v2, chosen Path A-clean)

### K.1 Architectural rationale

SCAFFOLD v1 §I.0-I.4 honestly disclosed that under current adapter state, the cascade ends at `REDEEM_REVIEW_REQUIRED` when `PolymarketV2Adapter.redeem` returns the stub. That treatment uses `REVIEW_REQUIRED` as the catch-all bucket for "anything the cascade couldn't auto-complete". v2 rejects this because:

1. **`REVIEW_REQUIRED` conflates two distinct failure modes**: (a) genuinely unexpected adapter error (e.g. network blip, wrong contract), which is an exception, and (b) known-stub-deferred-to-R1, which is *designed behavior until the R1 phase wires the adapter*. Bucketing both as REVIEW_REQUIRED forces operator to diagnose each occurrence to know whether action is "fix the adapter" or "claim via UI".
2. **The stub-completion step is a real design surface**, not "outside the system". Until R1 ships, every redeem will hit this state. Designing it as a first-class transition makes the operator-required step auditable, idempotent, alertable, and runbook-driven — instead of out-of-band UI activity that nothing records.
3. **F14 antibody (cascade-plumbing) and adapter completeness are separable concerns** (per §I.1). v2 keeps them separable but adds explicit semantics for the gap, so first-live-precedent doesn't rest on "operator quietly claims via UI" — it rests on "cascade hits a designed state, fires an alert, operator runs a recorded CLI command".

### K.2 New state: `REDEEM_OPERATOR_REQUIRED`

Add to whatever enum/literal set `settlement_commands.state` uses. Distinct from `REDEEM_REVIEW_REQUIRED` (which remains for unexpected adapter errors only).

State machine deltas:

```
… REDEEM_INTENT_CREATED  ──>  [redeem_submitter tick]  ──>  PolymarketV2Adapter.redeem()
                                                                  │
                                          ┌───────────────────────┼───────────────────────┐
                                          ▼                       ▼                       ▼
                                 success=True              errorCode =                 unexpected
                                tx_hash returned       REDEEM_DEFERRED_TO_R1            error
                                          │                       │                       │
                                          ▼                       ▼                       ▼
                              REDEEM_TX_HASHED      REDEEM_OPERATOR_REQUIRED       REDEEM_REVIEW_REQUIRED
                                          │                       │                       │
                              [reconciler tick]   [scripts/operator_record_redeem]  [operator triage]
                                          │                       │
                                          ▼                       ▼
                              REDEEM_CONFIRMED          REDEEM_TX_HASHED  ──>  [same reconciler tick]
                              REDEEM_FAILED                                           │
                                                                                      ▼
                                                                          REDEEM_CONFIRMED
```

### K.3 `submit_redeem` transition delta

In the function that wraps `adapter.redeem()` and updates the row state (likely `src/execution/settlement_commands.py` near line 393 where the error-code check already exists):

```python
result = adapter.redeem(condition_id=row["condition_id"])
if result.get("success"):
    _transition(conn, row["id"], "REDEEM_TX_HASHED", tx_hash=result["tx_hash"])
    return
err = result.get("errorCode")
if err == "REDEEM_DEFERRED_TO_R1":
    _transition(conn, row["id"], "REDEEM_OPERATOR_REQUIRED")
    _emit_heartbeat_alert(
        severity="WARN",
        message=(
            f"Redeem requires operator UI claim + record: "
            f"condition={row['condition_id']} command_id={row['id']}. "
            f"Run scripts/operator_record_redeem.py after UI claim."
        ),
    )
    return
# unexpected error code
_transition(conn, row["id"], "REDEEM_REVIEW_REQUIRED", error_code=err)
```

Heartbeat alert path reuses existing infrastructure (do not create new alert channel); WARN severity is appropriate (designed-state-requiring-action, not a system fault).

### K.4 `scripts/operator_record_redeem.py` (new, record-only CLI)

```python
"""Record a Polymarket-UI-completed redeem against a REDEEM_OPERATOR_REQUIRED row.

Usage:
    python -m scripts.operator_record_redeem <condition_id> <tx_hash> [--notes "..."]

Authority basis: 2026-05-16 SCAFFOLD §K — operator-completion as first-class
state while PolymarketV2Adapter.redeem is stubbed at REDEEM_DEFERRED_TO_R1.

Behavior (record-only; no web3 write, no signing, no gas):
1. Verify exactly one settlement_commands row in REDEEM_OPERATOR_REQUIRED for condition_id.
2. Verify tx_hash matches 0x-prefixed 64-hex regex.
3. SAVEPOINT-transition state to REDEEM_TX_HASHED with tx_hash recorded + occurred_at + actor='operator'.
4. Append audit-log row (existing infrastructure).
5. Emit operator_action heartbeat event for traceability.
6. Print 4-line summary: condition_id, old_state → new_state, tx_hash, command_id.

Reconciler picks up REDEEM_TX_HASHED in normal flow → CONFIRMED via web3 receipt
(once web3 is wired; until then reconciler logs the row and leaves it for the
next reconciler+web3 PR — current behavior unchanged from v1).

Idempotency: if already in REDEEM_TX_HASHED with matching tx_hash → no-op success.
If in REDEEM_TX_HASHED with different tx_hash → reject (operator must reconcile manually).
"""
```

~80 LOC including argparse + DB transaction + audit log + heartbeat emit. No new dependencies.

### K.5 Tests added (extend SCAFFOLD §F)

| Test | Location | Asserts |
|---|---|---|
| `test_submit_redeem_transitions_to_operator_required_on_stub` | `tests/test_redeem_cascade_liveness.py` (extend) | Mock adapter returns `REDEEM_DEFERRED_TO_R1`; assert state → `REDEEM_OPERATOR_REQUIRED`; assert heartbeat alert emitted |
| `test_submit_redeem_transitions_to_review_required_on_unexpected_error` | same file | Mock adapter returns `errorCode=NETWORK_TIMEOUT`; assert state → `REDEEM_REVIEW_REQUIRED` (not OPERATOR_REQUIRED — semantic distinction load-bearing) |
| `test_operator_record_redeem_advances_state_to_tx_hashed` | `tests/test_operator_record_redeem.py` NEW | Seed `REDEEM_OPERATOR_REQUIRED` row; invoke CLI; assert state → `REDEEM_TX_HASHED` + tx_hash recorded + audit log row appended |
| `test_operator_record_redeem_rejects_wrong_state` | same NEW file | Seed `REDEEM_INTENT_CREATED`; invoke CLI; assert raises + state unchanged |
| `test_operator_record_redeem_rejects_malformed_tx_hash` | same NEW file | Invoke CLI with `0xabc` (too short); assert raises + state unchanged |
| `test_operator_record_redeem_is_idempotent_with_same_hash` | same NEW file | Seed `REDEEM_TX_HASHED` already with hash X; invoke CLI with same hash; assert no-op success |
| `test_operator_record_redeem_rejects_conflicting_hash` | same NEW file | Seed `REDEEM_TX_HASHED` with hash X; invoke CLI with hash Y; assert raises |

### K.6 cascade_liveness_contract.yaml addendum

Add a `terminal_states_with_operator_action` array per entry. The antibody test (`tests/test_cascade_liveness_contract.py`) extends to assert that for each contract entry:

- Every non-terminal state has a poller that can transition out of it
- Every state in `terminal_states_with_operator_action` is reachable from a known transition AND has a documented operator runbook step (the runbook reference is a yaml field on the entry)

For F14: `terminal_states_with_operator_action: [REDEEM_OPERATOR_REQUIRED]` with runbook ref pointing to KARACHI runbook §1 (rewritten per §K.7).

This keeps the antibody contract complete: no state can sit silently with no defined progression.

### K.7 KARACHI_2026_05_17_MANUAL_FALLBACK.md §1 rewrite

The v1 SCAFFOLD §H already calls for correcting the misleading "auto-cascade includes clob.redeem" wording. v2 expands this rewrite to walk through the v2 state machine:

```
1. Polymarket settles (T-0)
2. harvester writes settlements_v2 VERIFIED row (T-0 → T+~1h)
3. harvester_pnl_resolver enqueues REDEEM_INTENT_CREATED (T+~1h)
4. redeem_submitter tick fires (T+~1h+5min)
5. PolymarketV2Adapter.redeem returns REDEEM_DEFERRED_TO_R1 (current adapter state)
6. row transitions to REDEEM_OPERATOR_REQUIRED + heartbeat WARN alert emitted
7. Operator (notified by alert): claim $0.59 via Polymarket UI → copy tx_hash
8. Operator runs: python -m scripts.operator_record_redeem c30f28a5... 0x<tx_hash>
9. CLI transitions row to REDEEM_TX_HASHED + records tx_hash + writes audit log
10. reconciler tick picks up REDEEM_TX_HASHED → marks REDEEM_CONFIRMED once web3 receipt path lands
```

Until step 9 the cascade is deterministic and machine-driven; step 7+8 is the designed operator action, not silent UI tinkering. Until R1 ships, this is "complete per design".

### K.8 Files-changed manifest amendment (over §H)

| File | Range | Change | Rationale |
|---|---|---|---|
| `src/execution/settlement_commands.py` | ~L380-410 (where `errorCode == REDEEM_DEFERRED_TO_R1` is already checked) | Add `REDEEM_OPERATOR_REQUIRED` state transition + heartbeat WARN emit per §K.3 | Stub-detected designed-state transition |
| `src/state/db.py` or wherever `state` literal-set lives | Add `"REDEEM_OPERATOR_REQUIRED"` to allowed states | Schema literal | New state |
| `scripts/operator_record_redeem.py` | NEW (~80 LOC) | Record-only CLI per §K.4 | Operator-completion entrypoint |
| `tests/test_redeem_cascade_liveness.py` | EXTEND (already created per §H) | Add 2 tests per §K.5 | Coverage for state-transition logic |
| `tests/test_operator_record_redeem.py` | NEW | 5 tests per §K.5 | CLI coverage |
| `architecture/cascade_liveness_contract.yaml` | EXTEND (already created per §H) | Add `terminal_states_with_operator_action` field per §K.6 | Antibody completeness |
| `KARACHI_2026_05_17_MANUAL_FALLBACK.md` | §1 (already to be rewritten per §H) | Rewrite per §K.7 walkthrough (supersedes §H bullet — same file, expanded scope) | Operator runbook |

**Updated file count: 8 files modified or created** (over §H's 6 — adds `src/execution/settlement_commands.py` and `scripts/operator_record_redeem.py` and `tests/test_operator_record_redeem.py`; KARACHI runbook is same file as §H, rewrite scope expanded; cascade_liveness_contract.yaml extended in place). The `requirements.txt` web3 pin from §H is still added for reconciler path (no v2 change).

### K.9 Risk register addendum (extend §J.1)

**RISK 4 — alert fatigue / silent CLI failure.** If the heartbeat alert path is noisy or operators ignore WARN events, the row sits in REDEEM_OPERATOR_REQUIRED indefinitely.

**Mitigation**: cascade_liveness_contract antibody (§K.6) asserts every entry has runbook ref; reconciler observability can be extended to surface "row aged >24h in REDEEM_OPERATOR_REQUIRED" as a higher-severity alert in a follow-up PR. For Karachi the row is the first one ever — operator attention is guaranteed.

**RISK 5 — CLI typo / wrong tx_hash recorded.** Operator pastes wrong tx_hash; cascade records it; reconciler later fails to find on-chain receipt.

**Mitigation**: K.5 tests cover idempotency + conflicting-hash rejection. CLI validates 0x + 64-hex format before commit. Wrong-but-format-valid hash surfaces at reconciler stage as REDEEM_FAILED — operator can re-run CLI after correcting (reconciler ticket carries the bad hash, audit log preserved).

### K.10 Why this is "architecturally cleanest"

- **F14 category antibody unchanged**: cascade_liveness_contract.yaml + tests + boot assertion still ship. Any future *_INTENT_CREATED table without a poller still bricks boot. v1 §G unchanged.
- **Stub-after-cascade gets first-class semantics**: not parked in REVIEW_REQUIRED catch-all; has dedicated state, alert, CLI, runbook, tests, antibody-contract coverage.
- **No web3 dependency added**: PR-I stays Tier-1 implementation cost; no signing-code-under-time-pressure risk per `feedback_long_opus_revision_briefs_timeout.md`.
- **R1 forward-compatible**: when adapter is wired, change `submit_redeem`'s success path to write tx_hash directly (no state-machine refactor needed). `REDEEM_OPERATOR_REQUIRED` becomes a legacy state recorded in the YAML but never re-entered.
- **Operator action is auditable**: every UI claim has a CLI invocation record, tx_hash recorded, audit log row, heartbeat trace. v1's "manual UI claim" had no DB footprint.
- **First-live-precedent**: the cascade contract reaches its designed terminal state programmatically. The operator-completion step is a designed transition, not an out-of-system rescue. Per operator policy 2026-05-16, this is what "完成 per design" means under current adapter state.

---

## End SCAFFOLD

**Critic-budget hint** (per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md`): the three sections most likely to harbor SEV-1 issues are §D (idempotency / racing-tick proof), §E (F16 decision), and §G (antibody approach). If these three pass first-round critic, PR-I clears Gate G2.
