# SCAFFOLD — F14 + F16 cascade-liveness fix (PR-I)

**Status**: opus-architect draft + operator v2 revision for opus-critic review (Gate G1 per FIX_PLAN §1.2)
**Owner**: PR-I (Path A-clean, Karachi 5/17 T-0 = 2026-05-17 12:00 UTC)
**Author**: opus SCAFFOLD architect 2026-05-16; v2 §K + §I.1 amendment by main session per operator directive "选择架构上最干净和正确的解法" 2026-05-16

## Changelog v2 → v3 (2026-05-16, post-G2-critic revision)

G2 opus critic verdict on v2 was **G2-FAIL-REVISE** (5 SEV-1 + 4 SEV-2). Path A-clean direction confirmed sound; v2 added §K at intent level but did not propagate edits into v1 §I.2/§I.3/§I.4 and did not verify primitives. v3 addresses every probe:

- **P2 STATE MACHINE COMPLETENESS (SEV-1)**: §K.4 CLI gains `--force` flag for OPERATOR_REQUIRED→OPERATOR_REQUIRED re-record (covers operator-pasted-wrong-hash recovery while reconciler is web3-unwired). REDEEM_ABANDONED state NOT added in PR-I (would require SLA-timeout poller + abandoned-cleanup policy; deferred to PR-I.5 follow-up with §K.6 row-age guard). Aging beyond 24h surfaced via §K.6 contract row and §I.4 trigger.
- **P3 IDEMPOTENCY (SEV-1)**: §K.3 + §K.4 spec uses SQLite native atomic conditional UPDATE (`WHERE state='REDEEM_OPERATOR_REQUIRED'`, `cursor.rowcount==1` as success signal). Scheduler tick and CLI cannot race because submit_redeem only operates on `_SUBMITTABLE_STATES` (INTENT_CREATED, RETRYING) while CLI only operates on OPERATOR_REQUIRED — no state overlap.
- **P5 SEMANTIC DRIFT (SEV-1)**: §K.10 softened — "completes per designed operator-required transition" instead of "per design". Explicitly notes v2 semantic reframe of operator policy.
- **P8 RISK REGISTER (SEV-1)**: §K.9 adds RISK 6 (SQLite CHECK migration cost), RISK 7 (CLI-vs-daemon-restart race — mitigated by atomic UPDATE), RISK 8 (`_emit_heartbeat_alert` does not exist).
- **P10 ATOMICITY (SEV-1)**: §K.3 contract: `_transition` returns cursor.rowcount; alert fires ONLY on rowcount>0. Logger.warning is best-effort post-commit (no DB write, no rollback cascade). False-alert + silent-stuck both impossible.
- **P1 antibody row-age (SEV-2)**: §K.6 contract row gains `max_age_hours_with_operator_action` field; CI assertion fails if any row exceeds. Specced; row-aging poller deferred to PR-I.5 (acceptable since OPERATOR_REQUIRED rows are operator-attended).
- **P4 F16 operator gate (SEV-2)**: §E.2 unchanged; §K.9 RISK 3 (pUSD abandonment) gains explicit "operator decision required before code freeze" gate listed in G3 PR-open checklist.
- **P6 document conflict (SEV-2)**: §I.2 + §I.3 rewritten inline to reflect §K state machine.
- **P7 SCAFFOLD-to-code TBD (SEV-2)**: §K.8 resolves `src/state/db.py or wherever` to `src/execution/settlement_commands.py:31-34` (CHECK literal-set) + `:71-78` (SettlementState Enum). Adds migration script row.
- **P9 Path-C trigger (SEV-2)**: §I.4 amended — OPERATOR_REQUIRED ≤24h = expected; >24h with no CLI invocation = Path C trigger.

**Verification evidence used**: G3-prep haiku scout 2026-05-16 (DB schema, line numbers, position existence). Karachi position `c30f28a5-d4e` CONFIRMED present at `state/zeus_trades.db.position_current` phase=day0_window, market=Karachi 2026-05-17, shares=1.5873, exposure=$0.59. T-0 deadline holds.

**Author of v3**: main session (executor role, opus model) per `feedback_long_opus_revision_briefs_timeout.md` (do not dispatch opus revision briefs).

---

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

**T+1h+5min to T+1h+10min** — `redeem_submitter` fires (current adapter state + v3 Path A-clean state machine):
```
zeus-live.log: redeem_submitter: submitted=1 failed=0
zeus-live.log:   command_id=<...> state=REDEEM_OPERATOR_REQUIRED  # adapter returned REDEEM_DEFERRED_TO_R1
zeus-live.err: WARNING [REDEEM_OPERATOR_REQUIRED] command_id=<...> condition_id=c30f28a5-d4e action=run_operator_record_redeem  # picked up by heartbeat-sensor per Finding #10 path
```

**T+1h+10min to T+24h (operator action window)** — operator claims via Polymarket UI + records via CLI:
```
zeus-live.log: operator_record_redeem: command_id=<...> condition_id=c30f28a5-d4e tx_hash=0x<...> actor=operator
zeus-live.log: state transition REDEEM_OPERATOR_REQUIRED → REDEEM_TX_HASHED
```

**Post-operator-record** — `redeem_reconciler` fires (current web3-unwired state):
```
zeus-live.log: redeem_reconciler: results=0   # web3 unwired; row sits in TX_HASHED until PR-I.5 (cascade complete per design v3 semantics)
```

Post-PR-I.5 (adapter + web3 wired, future state) the same windows would show `REDEEM_TX_HASHED` (auto, not operator-recorded) → `REDEEM_CONFIRMED` instead. The operator CLI path remains available as recovery for adapter failures even after PR-I.5.

### I.3 What to monitor

| Surface | Healthy signal (post-PR-I, current adapter state) | Anomaly signal |
|---|---|---|
| `state/scheduler_jobs_health.json` | `redeem_submitter.status=OK` last 5 min, `redeem_reconciler.status=OK` last 10 min, `wrap_unwrap_liveness_guard.status=OK` last 30 min | Any FAILED → operator review |
| `settlement_commands` row state (v3 Path A-clean) | Transition INTENT → SUBMITTED → OPERATOR_REQUIRED within ~1h+10min post-Polymarket-settle (stub-deferred); then OPERATOR_REQUIRED → TX_HASHED after CLI invocation | Stuck at `REDEEM_INTENT_CREATED` >30 min after enqueue → cascade plumbing dead (PR-I antibody broken) |
| OPERATOR_REQUIRED aging | Row ≤24h post-OPERATOR_REQUIRED entry: EXPECTED designed-state (operator action pending). CLI invocation transitions out within minutes once invoked. | Row >24h with no CLI invocation → **Path C trigger** (operator missed alert; manual fallback runbook §3). Track via `MAX(julianday('now') - julianday(<entry_at>))` against settlement_command_events |
| Stuck-row visibility | Per §G antibody + §K.6 row-age guard: row in non-terminal `REDEEM_SUBMITTED` >30 min surfaces via `scheduler_jobs_health.json` (adapter call hung — Path C trigger) | Any row in `REDEEM_SUBMITTED` aged >30 min |
| `logs/zeus-live.err` | Single `[REDEEM_OPERATOR_REQUIRED]` WARNING per row when stub-detected; no further errors until CLI invoked | Repeated `submit_redeem` traceback → unexpected adapter error code → REVIEW_REQUIRED → Path C trigger |
| `logs/zeus-live.log` | `redeem_submitter: submitted=N failed=0` heartbeat every 5 min | `failed>0` for 3+ consecutive ticks → Path C |
| Polymarket UI balance | Karachi $1.5873 claimable balance appears post-Polymarket-settle. Operator claims via UI → records via `operator_record_redeem.py` CLI. | After PR-I.5 (adapter wired), this row updates to auto-claimed; CLI remains as recovery for adapter failures. |

### I.4 Path C escalation triggers (v3 Path A-clean semantics)

Per FIX_PLAN §1.2 + §9, any G1-G5 failure or:

**Pre-CLI cascade-plumbing failures** (PR-I antibody not holding):
- Karachi `REDEEM_INTENT_CREATED` row stuck for >30 min post-enqueue with `redeem_submitter` showing FAILED
- `_redeem_submitter_cycle` raises uncaught (would surface as `scheduler_jobs_health.json` FAILED)
- `PolymarketV2Adapter.redeem` returns hard error code other than `REDEEM_DEFERRED_TO_R1` (would land in REVIEW_REQUIRED, not OPERATOR_REQUIRED)

**Post-stub-detect, pre-CLI states** (v3 designed flow):
- Row in `REDEEM_OPERATOR_REQUIRED` ≤24h with no CLI invocation → **EXPECTED designed-state, NOT a Path C trigger**. Operator action pending; alert is the cue.
- Row in `REDEEM_OPERATOR_REQUIRED` >24h with no CLI invocation → **Path C trigger** (operator missed alert OR cannot complete UI claim). Arm fallback runbook §3.

**Post-CLI states** (operator has recorded a tx_hash):
- Row in `REDEEM_TX_HASHED` indefinitely with reconciler `results=0` → EXPECTED until PR-I.5 (web3 wired). Not a Path C trigger.
- Row in `REDEEM_TX_HASHED` where post-PR-I.5 reconciler finds receipt missing → execute recovery via `operator_record_redeem.py --force` (per §K.4 spec); if recovery exhausts (3 tries), escalate to REVIEW_REQUIRED + Path C.

→ Trigger Path C: arm manual fallback per KARACHI_2026_05_17_MANUAL_FALLBACK.md §3 starting T+9h. **Note (v3)**: under Path A-clean + current adapter state, "operator runs CLI" IS the designed completion; what Path C controls is whether to abandon CLI flow entirely when the cascade-plumbing antibody is broken or operator unavailability exceeds SLA.

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

### K.3 `submit_redeem` transition delta (v3 — atomicity + real alert primitive)

In the function that wraps `adapter.redeem()` and updates the row state (`src/execution/settlement_commands.py` — exact insertion site is the existing error-code check; scout-verified location L31-34 = CHECK literal-set, L71-78 = SettlementState Enum, near L380-410 = state-transition path):

```python
import logging
logger = logging.getLogger(__name__)

result = adapter.redeem(condition_id=row["condition_id"])
if result.get("success"):
    transitioned = _atomic_transition(
        conn, row["command_id"],
        from_state="REDEEM_SUBMITTED",
        to_state="REDEEM_TX_HASHED",
        tx_hash=result["tx_hash"],
    )
    return

err = result.get("errorCode")
if err == "REDEEM_DEFERRED_TO_R1":
    transitioned = _atomic_transition(
        conn, row["command_id"],
        from_state="REDEEM_SUBMITTED",
        to_state="REDEEM_OPERATOR_REQUIRED",
        error_payload=result,
    )
    if transitioned:  # row state guard passed; cursor.rowcount == 1
        logger.warning(
            "[REDEEM_OPERATOR_REQUIRED] command_id=%s condition_id=%s "
            "action=run_operator_record_redeem details='Polymarket UI claim + "
            "scripts/operator_record_redeem.py <condition_id> <tx_hash>'",
            row["command_id"], row["condition_id"],
        )
    return

# unexpected error code (NOT REDEEM_DEFERRED_TO_R1)
_atomic_transition(
    conn, row["command_id"],
    from_state="REDEEM_SUBMITTED",
    to_state="REDEEM_REVIEW_REQUIRED",
    error_payload=result,
)
```

**Atomicity contract (v3, P10 critic fix)**:
- `_atomic_transition` issues a single SQLite UPDATE with `WHERE command_id=? AND state=?` — `cursor.rowcount == 1` is the success signal; `0` means another writer changed state since selection (rollback no-op).
- The `logger.warning` call fires ONLY when `transitioned == True`. Cannot false-alert (no rowcount > 0 means no alert) and cannot silent-transition (if logger.warning's file-handle fails, row is still observable via DB query + scheduler_jobs_health.json).
- `logger.warning` does NOT write to DB. No transaction concern. Best-effort log emit; failure mode is rare-OS-error → row state remains correct + observable.

**Alert primitive (v3, P8.3 critic fix)**:
- `_emit_heartbeat_alert` referenced in v2 does **not exist** in the codebase (G2 critic grep + G3-prep scout confirm both: grep `_emit_heartbeat_alert` = 0 hits; closest find `_emit_signal` in `src/execution/live_executor.py:69` is the ANTI_DRIFT_CHARTER ritual-signal helper, wrong domain).
- v3 alert path: structured `logger.warning` with `[REDEEM_OPERATOR_REQUIRED]` prefix in `logs/zeus-live.err`. The existing heartbeat-sensor (per Finding #10 architecture) scans `.err` files for severity-tagged lines and feeds the dispatcher. PR-D (alarm-channel-bridge, FIX_PLAN §4.2) is the dedicated push-notification path; PR-I emits the prefix that PR-D picks up. No new alert infrastructure created in PR-I.
- Operator-pageable channel is PR-D scope. Until PR-D ships, operator picks up the WARNING via heartbeat-dispatcher status reports + manual `.err` log grep.

### K.4 `scripts/operator_record_redeem.py` (v3 — atomic CLI with recovery flag)

```python
"""Record a Polymarket-UI-completed redeem against a REDEEM_OPERATOR_REQUIRED row.

Usage:
    python -m scripts.operator_record_redeem <condition_id> <tx_hash> [--notes "..."]
    python -m scripts.operator_record_redeem <condition_id> <tx_hash> --force [--notes "..."]

Authority basis: 2026-05-16 SCAFFOLD §K — operator-completion as first-class
state while PolymarketV2Adapter.redeem is stubbed at REDEEM_DEFERRED_TO_R1.

Behavior (record-only; no web3 write, no signing, no gas):

NORMAL MODE (no --force):
1. SELECT exactly one settlement_commands row in REDEEM_OPERATOR_REQUIRED for condition_id.
2. Verify tx_hash matches 0x-prefixed 64-hex regex (rejects malformed before DB touch).
3. Atomic transition: UPDATE settlement_commands SET state='REDEEM_TX_HASHED', tx_hash=?, submitted_at=?
   WHERE command_id=? AND state='REDEEM_OPERATOR_REQUIRED'. Assert cursor.rowcount == 1.
4. INSERT settlement_command_events row (event_type='operator_record', payload=tx_hash + actor + notes).
5. logger.info("[OPERATOR_RECORD] command_id=... old=OPERATOR_REQUIRED new=TX_HASHED tx_hash=...").
6. Print 4-line summary to stdout: command_id, old → new state, tx_hash, condition_id.

FORCE MODE (--force, recovery use):
- Allowed source states: REDEEM_OPERATOR_REQUIRED (re-record over prior fail), REDEEM_TX_HASHED (overwrite).
- Same atomicity (single UPDATE), but WHERE clause widens to `state IN ('REDEEM_OPERATOR_REQUIRED','REDEEM_TX_HASHED')`.
- INSERT audit event with extra payload field actor_override=true + prior_tx_hash field if overwriting.
- Emits WARNING (not info) because forced overwrite is operator-acknowledged exception.

REJECTIONS:
- Wrong state (NOT in allowed set): exit 2, log REJECT, no DB write.
- Malformed tx_hash (not 0x + 64 hex): exit 3, log REJECT, no DB write.
- Multiple matching rows: exit 4, log REJECT, no DB write (data integrity violation; never expected because UNIQUE INDEX on (condition_id, market_id, payout_asset) WHERE state NOT IN (CONFIRMED, FAILED) — settlement_commands.py:53-55).
- Zero matching rows: exit 5, log REJECT.

IDEMPOTENCY:
- NORMAL mode + row already TX_HASHED with matching tx_hash: detect via SELECT before UPDATE; exit 0, log "already_recorded_no_op".
- NORMAL mode + row already TX_HASHED with DIFFERENT tx_hash: reject (exit 6); operator must use --force to overwrite.

RACE CONTRACT (v3 P3 critic fix):
- scheduler `redeem_submitter` only operates on `_SUBMITTABLE_STATES = {REDEEM_INTENT_CREATED, REDEEM_RETRYING}` (settlement_commands.py:87-90). CLI only operates on REDEEM_OPERATOR_REQUIRED (NORMAL) or {OPERATOR_REQUIRED, TX_HASHED} (FORCE). No state overlap → no race.
- SQLite WAL serializes writes per row anyway. Conditional UPDATE is the atomic primitive; cursor.rowcount==1 is the success signal.
- CLI-during-daemon-restart: harmless. If CLI commits TX_HASHED while daemon is down, daemon restart picks up TX_HASHED → reconciler operates normally (or no-ops if web3 unwired).

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

### K.6 cascade_liveness_contract.yaml addendum (v3 — row-age guard)

Add per-entry fields to the YAML schema:

```yaml
- table: settlement_commands
  poller_job_id: redeem_submitter
  reconciler_job_id: redeem_reconciler
  submittable_states: [REDEEM_INTENT_CREATED, REDEEM_RETRYING]
  terminal_states: [REDEEM_CONFIRMED, REDEEM_FAILED, REDEEM_REVIEW_REQUIRED]
  terminal_states_with_operator_action:
    - state: REDEEM_OPERATOR_REQUIRED
      max_age_hours: 24                    # P1 critic fix: row-age guard
      operator_runbook: docs/operations/task_2026-05-16_deep_alignment_audit/KARACHI_2026_05_17_MANUAL_FALLBACK.md#1
      escalation_action: "Path C trigger per §I.4 if exceeded; arm manual fallback runbook §3"
      cli_invocation: "python -m scripts.operator_record_redeem <condition_id> <tx_hash>"
```

The antibody test (`tests/test_cascade_liveness_contract.py`) extends to assert:

- Every non-terminal state has a poller (existing v2 check).
- Every state in `terminal_states_with_operator_action` has: (a) a transition path INTO it from the state-machine code, (b) `max_age_hours` field set, (c) `operator_runbook` reference resolves to an existing file + section anchor, (d) `cli_invocation` is a runnable command string.
- **Row-age guard (v3 P1 critic fix)**: a new test `test_no_operator_required_row_exceeds_max_age` queries production-shape settlement_commands rows and asserts no row in OPERATOR_REQUIRED exceeds `max_age_hours`. Test is data-dependent (skipped if DB is empty); runs in CI against staging snapshot if available, runs in operator-invocable mode against live DB.
- **Aging poller (deferred to PR-I.5)**: scheduled job `redeem_age_guard` scans every 1h, raises ALERT for rows exceeding `max_age_hours`. PR-I ships the contract field + CI test; PR-I.5 ships the poller. Acceptable because:
  - For Karachi: position is the first ever — operator attention guaranteed; no need for aging-poller alert on top of the immediate WARN.
  - For future positions post-PR-I.5: the guard is automated.

This keeps the antibody contract complete: no state can sit silently with no defined progression; OPERATOR_REQUIRED has documented escalation if exceeded.

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

### K.8 Files-changed manifest amendment (v3 — TBDs resolved + migration added)

| File | Range | Change | Rationale |
|---|---|---|---|
| `src/execution/settlement_commands.py` | L31-34 (CHECK literal-set) | Add `'REDEEM_OPERATOR_REQUIRED'` to CHECK constraint. Since SQLite cannot ALTER CHECK in-place, the migration script (below) drops + recreates table. Production starting state: 0 rows in both `state/zeus_trades.db` and `state/zeus-live.db` per scout 2026-05-16 — migration is zero-cost data-wise. | New state in schema |
| `src/execution/settlement_commands.py` | L71-78 (SettlementState Enum) | Add `REDEEM_OPERATOR_REQUIRED = "REDEEM_OPERATOR_REQUIRED"` | Python-level literal-set parallel |
| `src/execution/settlement_commands.py` | ~L380-410 (existing `REDEEM_DEFERRED_TO_R1` check site) | Add `_atomic_transition` helper + transition logic per §K.3 (v3 atomicity + logger.warning alert primitive) | Stub-detected designed-state transition |
| `src/execution/settlement_commands.py` | adjacent to existing `_TERMINAL_STATES` (L81-85) | Update set membership: REDEEM_OPERATOR_REQUIRED is NOT terminal (CLI exits it); REDEEM_REVIEW_REQUIRED stays terminal | State-set bookkeeping |
| `scripts/migrations/202605_add_redeem_operator_required_state.py` | NEW (v3 P7 critic fix) | SQLite CHECK-constraint migration: BEGIN; CREATE TABLE settlement_commands_new with new CHECK; INSERT INTO settlement_commands_new SELECT * FROM settlement_commands; DROP TABLE settlement_commands; ALTER TABLE settlement_commands_new RENAME TO settlement_commands; recreate indexes; COMMIT. Idempotent (no-op if CHECK already includes REDEEM_OPERATOR_REQUIRED). | Schema migration on live DB |
| `scripts/operator_record_redeem.py` | NEW (~120 LOC including --force flag + atomic UPDATE + audit event) | Per §K.4 v3 spec | Operator-completion entrypoint |
| `src/main.py` | scout-verified line range L984-986 (existing harvester + heartbeat jobs) | Add 3 new `scheduler.add_job` blocks + `_assert_cascade_liveness_contract(scheduler)` call. Mirror existing pattern exactly per scout Q2. | Scheduler registration |
| `src/main.py` | after `_harvester_cycle` def (~L160-200 area; executor to find exact insertion site) | Add tick body functions: `_redeem_submitter_cycle`, `_redeem_reconciler_cycle`, `_wrap_unwrap_liveness_guard_cycle`, `_assert_cascade_liveness_contract`, `_atomic_transition` helper | Scheduler tick implementations |
| `tests/test_redeem_cascade_liveness.py` | EXTEND (already created per §H) | Add 2 tests per §K.5 v2 + 1 atomicity test per §K.3 v3 (assert `transitioned == False` → no logger.warning fires) | Coverage for state-transition logic |
| `tests/test_operator_record_redeem.py` | NEW | 7 tests per §K.5 v3 (NORMAL mode: 5 from v2; FORCE mode: 2 new — `test_force_overwrites_conflicting_hash`, `test_force_re_records_after_failure`) | CLI coverage |
| `architecture/cascade_liveness_contract.yaml` | EXTEND (already created per §H) | Add `terminal_states_with_operator_action` schema per §K.6 v3 (with `max_age_hours`, `operator_runbook`, `cli_invocation` fields) | Antibody completeness |
| `tests/test_cascade_liveness_contract.py` | EXTEND | Add `test_no_operator_required_row_exceeds_max_age` (data-dependent; skip-if-empty) | Row-age guard per §K.6 v3 |
| `KARACHI_2026_05_17_MANUAL_FALLBACK.md` | §1 (already to be rewritten per §H) | Rewrite per §K.7 walkthrough; correct misleading "auto-cascade includes clob.redeem" wording | Operator runbook |
| `requirements.txt` | NEW line (still applies from §H) | Add `web3==<latest-stable>` pin; reconciler path no-ops without it but installing keeps option open for PR-I.5 | Reconciler future-state |

**Updated file count: 10 files modified or created** (was 8 in v2; v3 adds `scripts/migrations/202605_add_redeem_operator_required_state.py` and `tests/test_cascade_liveness_contract.py` extension). `src/main.py` has TWO insertion sites (scheduler registration L984 + tick body functions ~L160).

### K.9 Risk register addendum (v3 — extends §J.1; adds RISK 6/7/8 per P8 critic fix)

**RISK 4 — alert fatigue / silent CLI failure.** If the heartbeat alert path is noisy or operators ignore WARN events, the row sits in REDEEM_OPERATOR_REQUIRED indefinitely.

**Mitigation**: cascade_liveness_contract antibody (§K.6 v3) asserts every entry has runbook ref + `max_age_hours`; aging-poller deferred to PR-I.5 is the automated mitigation. For Karachi the row is the first ever — operator attention is guaranteed.

**RISK 5 — CLI typo / wrong tx_hash recorded.** Operator pastes wrong tx_hash; cascade records it; reconciler later fails to find on-chain receipt.

**Mitigation**: K.5 tests cover idempotency + conflicting-hash rejection. CLI validates 0x + 64-hex format before commit. Wrong-but-format-valid hash surfaces at reconciler stage post-PR-I.5 as REDEEM_FAILED — operator can re-run CLI with `--force` after correcting (reconciler ticket carries the bad hash; audit log preserves prior recordings).

**RISK 6 (v3 P8.1 critic fix) — SQLite CHECK-constraint migration on live `settlement_commands`.** Adding REDEEM_OPERATOR_REQUIRED to the CHECK literal-set requires a table rebuild (CREATE new + INSERT copy + DROP old + RENAME), because SQLite cannot ALTER CHECK in place.

**Mitigation**: scout 2026-05-16 confirms `state/zeus_trades.db.settlement_commands` and `state/zeus-live.db.settlement_commands` are both 0-row tables. Migration is data-cost-zero. Migration script `scripts/migrations/202605_add_redeem_operator_required_state.py` (per §K.8 v3) wraps the rebuild in a single transaction; idempotent (no-op if CHECK already includes REDEEM_OPERATOR_REQUIRED). Test: migration must be runnable against a `:memory:` SQLite seeded with v1 schema + a non-OPERATOR_REQUIRED row, end state preserves row + accepts new state.

**RISK 7 (v3 P8.2 critic fix) — CLI invocation race with daemon restart mid-cascade.** Scenario: scheduler tick has read row in REDEEM_SUBMITTED + called adapter; daemon SIGKILL'd before commit; restart re-reads row in REDEEM_SUBMITTED; operator concurrently runs CLI.

**Mitigation**: CLI's atomic conditional UPDATE `WHERE state='REDEEM_OPERATOR_REQUIRED'` fails (cursor.rowcount==0) because row is still SUBMITTED post-daemon-restart. CLI exits cleanly with "wrong state" reject. Operator can re-run CLI once scheduler has had a tick to re-process. The state guard is the race-free primitive; no advisory lock needed. Test: `test_cli_rejects_during_submitted_state_after_daemon_crash_simulation` seeds row in SUBMITTED, runs CLI, asserts reject + state unchanged.

**RISK 8 (v3 P8.3 critic fix) — `_emit_heartbeat_alert` function does NOT exist.** v2 §K.3 referenced a function that grep finds zero hits for. Closest existing surface (`_emit_signal` in `src/execution/live_executor.py:69`) is the ANTI_DRIFT_CHARTER ritual-signal helper, wrong domain.

**Mitigation (v3)**: §K.3 replaced with `logger.warning("[REDEEM_OPERATOR_REQUIRED] ...")` writing to existing logger infrastructure (lands in `logs/zeus-live.err`). Heartbeat-sensor (per Finding #10) already scans `.err` for severity lines. PR-D (FIX_PLAN §4.2 alarm-channel-bridge) is the dedicated push-notification wiring; PR-I emits the prefix that PR-D picks up. No new alert infrastructure invented in PR-I.

### K.10 Why this is "architecturally cleanest" (v3 — softened per P5 critic fix)

- **F14 category antibody unchanged**: cascade_liveness_contract.yaml + tests + boot assertion still ship. Any future *_INTENT_CREATED table without a poller still bricks boot. v1 §G unchanged.
- **Stub-after-cascade gets first-class semantics**: not parked in REVIEW_REQUIRED catch-all; has dedicated state, alert, CLI, runbook, tests, antibody-contract coverage.
- **No web3 dependency added**: PR-I stays Tier-1 implementation cost; no signing-code-under-time-pressure risk per `feedback_long_opus_revision_briefs_timeout.md`.
- **R1 forward-compatible**: when adapter is wired, change `submit_redeem`'s success path to write tx_hash directly (no state-machine refactor needed). `REDEEM_OPERATOR_REQUIRED` becomes a legacy state recorded in the YAML but rarely re-entered (still available as recovery path for adapter failures).
- **Operator action is auditable**: every UI claim has a CLI invocation record, tx_hash recorded, audit log row, structured log trace. v1's "manual UI claim" had no DB footprint.

**On first-live-precedent semantic (v3 P5 critic-corrected reframe)**:

Operator policy memory (`feedback_first_live_order_no_manual_completion.md`) verbatim: *"目前我们唯一 live smoke test 成功的订单不能'手动'执行，必须要程序性的正确按照我们设计预期完成"*. v3 explicitly acknowledges:

- **Strict reading**: "程序性的正确按照我们设计预期完成" = "completes through the programmatic path designed by the system, end-to-end on-chain". Under THIS reading, Path A-clean does NOT satisfy the policy — operator still runs a CLI, which is operator action regardless of how well-designed it is.
- **v2 reframe** (the read v2 §K.10 implicitly adopted): "designed expectation" includes the operator-completion CLI as a first-class step of the design, given R1 phase is not yet implemented. Under this reading, Path A-clean DOES satisfy the policy.
- **v3 honest verdict**: this is a redefinition of the policy semantics under current adapter state. v3 does NOT silently adopt the redefinition. The operator should ratify which reading governs first-live-precedent.

Path A-clean's actual claim is precise: **the cascade contract reaches its designed terminal state programmatically; the operator-completion step is a designed CLI transition with full auditability; the strict on-chain-call layer is deferred to PR-I.5 because adapter wiring inside the Karachi window is unsafe**. Whether this counts as "完成 per design" depends on whether the policy's "design" refers to (a) end-to-end automation or (b) the system's current designed terminal-state semantics under partial-adapter conditions.

Per operator directive 2026-05-16 "选择架构上最干净和正确的解法", v3 selects Path A-clean as the architecturally correct response to current adapter state. Whether this fully honors the first-live-precedent policy is the operator's call to make explicit in G2-second-round review or in §1 of FIX_PLAN.

---

## End SCAFFOLD

**Critic-budget hint** (per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md`): the three sections most likely to harbor SEV-1 issues are §D (idempotency / racing-tick proof), §E (F16 decision), and §G (antibody approach). If these three pass first-round critic, PR-I clears Gate G2.
