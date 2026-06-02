<!--
Created: 2026-06-01
Last reused/audited: 2026-06-01
Authority basis: Adversarial READ-ONLY critic review of #95 SEV-2.1 (Tier-0 live-execution-path change).
  Reviews UNCOMMITTED worktree fix-95 (base 9b47b5f301) src/events/reactor.py split + antibody.
  All citations grep-verified in the worktree on 2026-06-01. No code edits, no git ops performed.
-->

# CRITIC — SEV-2.1 (#95): world-DB write mutex held across network I/O — split fix

**VERDICT: MERGE (shadow) — with two REQUIRED follow-ups gated BEFORE arming `real_order_submit_enabled=true`.**

## 5-line verdict
1. The split correctly removes the lock-across-HTTP violation; the antibody pins it; disposition parity is exact; INV-37 untouched; events suite is `2 failed, 259 passed, 2 xfailed` and the 2 failures are a verified pre-existing tick_size cert defect, not a #95 regression.
2. PRIMARY crash-window double-submit is REAL but PRE-EXISTING and equally exposed/mitigated before and after this diff — the split does not introduce the category; the executor's durable command ledger + deterministic idempotency key is the real protection in both versions.
3. The one severity-material residual: the idempotency key includes JIT-book-derived `price`/`size`, so a crash-recovery re-submit against a MOVED book mints a NEW key → dedup MISS → duplicate live order. This is unchanged by #95 but must be closed before arming (MAJOR, armed-only).
4. Current posture is SHADOW (`config/settings.json:112 real_order_submit_enabled=false`) → the live-money hazards are INERT today; merging the contention fix now is safe and net-positive.
5. No mutex/savepoint leaks on any exception path (probed live); Window-A premature-commit concern is OK (no pre-submit world write persists a fill-conditional row).

---

## Pre-commitment predictions vs findings
- Predicted: crash-window double-submit REAL, venue POST not idempotent even if ledgers are. **Partially wrong** — the executor DOES have a durable command ledger keyed by a deterministic idempotency key (`executor.py:3152,3275-3341`, `command_bus.py:182-218`), committed BEFORE the venue POST. The residual is narrower than predicted: only a MOVED-book re-derivation breaks the key.
- Predicted: BEGIN IMMEDIATE can raise "database is locked". **Mostly wrong** — Window B holds the process mutex before BEGIN IMMEDIATE, so no in-process writer contends; busy_timeout=30000ms (`db.py:2071`) absorbs cross-process contention.
- Predicted: disposition double-count. **Wrong** — `_finalize_disposition` is called exactly once per event (Window A xor Window B); parity is exact.

---

## Hazard-by-hazard

### 1. Crash-window double-submit (PRIMARY) — MAJOR (armed-only); not introduced by #95
**Real, pre-existing, narrower than the brief frames it.**

Evidence chain (all grep-verified):
- New flow commits the claim in Window A BEFORE submit (`reactor.py:241-242` RELEASE+commit), runs `self._submit` with no mutex / no open txn (`reactor.py:252-253`), marks processed only in Window B (`reactor.py:283-287` → `_finalize_disposition` → `mark_processed`, `reactor.py:338`). This is structurally at-least-once.
- BUT the durable dedup is downstream in the executor, not in `mark_processed`:
  - `submit_event_bound_final_intent_via_existing_executor` passes `decision_id=str(execution_command_cert.payload["execution_command_id"])` (`event_bound_final_intent.py:134`).
  - `execution_command_id` is DETERMINISTIC on the stable event_id: `f"edli_exec_cmd:{event_id}:{final_intent_id}:{token_id}:{direction}"` (`event_reactor_adapter.py:1385-1388`).
  - The entry idempotency key = `IdempotencyKey.from_inputs(decision_id, token_id, side, price, size, intent_kind)` (`executor.py:3044-3051`), sha256-deterministic (`command_bus.py:182-218`).
  - `_live_order` does a pre-submit `find_command_by_idempotency_key` fast-path that SKIPS submit if a row exists (`executor.py:3152-3176`), and `insert_command`+`SUBMIT_REQUESTED`+`conn.commit()` (`executor.py:3275-3341`) DURABLY persist the command row to zeus_trades.db BEFORE `PolymarketClient()` is even instantiated (`executor.py:3445`) or the venue POST fires (after `:3559`). Race belt: IntegrityError → existing-command (`executor.py:3389-3422`).
- Therefore a crash AFTER the POST but BEFORE the executor's own commit, or before the reactor Window-B mark, is deduped on recovery **iff the re-derived idempotency key is identical**.

Legacy comparison (proves not-introduced): legacy `_process_one` ran `claim → gates → submit → ledgers → mark_processed` inside ONE uncommitted savepoint, single commit at end (base `reactor.py` lines 199-238). A crash during/after the legacy submit also left the event re-claimable and re-ran submit. Both versions are at-least-once at the reactor layer and rely on the SAME executor idempotency ledger. The split moves the recovery trigger from "rolled-back claim → immediate pending re-pickup" to "committed claim → lease-stale (300s) re-pickup" and durably advances `attempt_count` — a behavior change, not a new double-submit category.

Quantified exposure: INERT in shadow (`config/settings.json:112 real_order_submit_enabled=false`; armed submit additionally requires `durable_submit_outbox_enabled`, `event_reactor_adapter.py:303-310`). Live-money exposure exists only post-arm.

**Why this matters / required mitigation (gate before arming):** the idempotency key includes `price` and `size`, which the live order build re-derives from the JIT `/book` depth sweep at submit time (`event_reactor_adapter.py:1119-1180`: `_limit_price_d` from `_ask_for_limit`/`_reservation`, `_desired_shares` from `_depth_sweep`/`available_crossable_shares`). On a crash-recovery re-submit against a MOVED book, `price`/`size` differ → new idempotency key → `find_command_by_idempotency_key` MISS → a SECOND distinct live order. This is the unmitigated tail. Required before `real_order_submit_enabled=true`: EITHER (a) pin the executable `limit_price`/`size` to the durable command/final-intent certificate so the re-submit reuses the same economic inputs (key stable), OR (b) make `mark_processed` precede the durable submit via the already-scaffolded `durable_submit_outbox` (claim a submit-intent row keyed by event_id before the POST, so recovery sees "submit already attempted" independent of book movement). The `find_unknown_command_by_economic_intent` economic-intent fallback (`executor.py:2272`, exit path) is a partial belt but is price/size-exact and exit-scoped, so it does not cover a moved-book entry.

### 2. Disposition-semantics parity — OK
Exact reproduction verified by line-for-line comparison with base `reactor.py`:
- `_FSR_PARTIAL_DEAD_LETTER`: legacy released savepoint + committed, no mark; new `_finalize_disposition` early-returns (`reactor.py:312-315`). Identical (dead_lettered already incremented in `_process_one_pre_submit:443`).
- `_EXECUTABLE_SNAPSHOT_RETRY`: legacy attempt-count branch (dead-letter at cap else requeue) reproduced verbatim in `_finalize_disposition` (`reactor.py:316-333`), same `result.dead_lettered`/`retried` increments.
- Gate-reject (`None`): legacy fell through to `mark_processed` + `result.processed += 1`; new routes `(None, False)` → `_finalize_disposition(None)` → `mark_processed` + `result.processed += 1` (`reactor.py:338-339`). Identical.
- Gate-pass + post-submit accept/reject (`None`): single `_finalize_disposition` in Window B → `mark_processed` + processed++. `result.proof_accepted` still incremented inside `_process_one_post_submit:554`, matching legacy `_process_one`. No double counting: `_finalize_disposition` runs in Window A XOR Window B, never both.
- Live-probed: events suite `2 failed, 259 passed, 2 xfailed` (matches doc; base was 258 passed → +1 antibody).

### 3. BEGIN IMMEDIATE in Window B — OK (minor asymmetry noted)
- Window B re-acquires the process mutex (`reactor.py:263`) BEFORE `BEGIN IMMEDIATE` (`reactor.py:272-273`). With the mutex held, no other mutex-respecting in-process writer (ingestor, reactor next event) can hold the WAL write lock, so BEGIN IMMEDIATE does not contend in-process. `busy_timeout=30000ms` (`db.py:2071`) covers any cross-process fcntl writer. It will NOT raise immediate "database is locked" under the documented in-process model — it would only do so if a cross-process writer held the lock for >30s, the same bound as everywhere else.
- A successfully-submitted order is NOT lost to a Window-B lock failure: if `_process_one_post_submit` raises (incl. a hypothetical lock error), the `except` dead-letters UNKNOWN_REVIEW_REQUIRED (`reactor.py:288-292`) with `reconciliation_followup_required` semantics living in the executor receipt — the order's durable command row already exists in zeus_trades.db, so the fill is reconcilable, not orphaned. MINOR asymmetry: `_dead_letter_unknown` (`reactor.py:341-373`) does NOT issue its own BEGIN IMMEDIATE (unlike Window B), so its writes use SQLite implicit DEFERRED + lazy first-DML lock acquisition; acceptable because it is the terminal failure-soft path, but it is the one spot that could still hit a lazy "database is locked" under cross-process contention. Non-blocking.

### 4. Window A commit content — OK
Window A on the gate-PASS path commits ONLY the `claim()` UPDATE (`reactor.py:238-242` comment is accurate: "No world writes happened in the pre-submit gate-pass path beyond claim"). No reject/decision ledger that should be submit-conditional is persisted before submit. Gate-REJECT paths correctly write their reject/dead-letter ledgers inside Window A's savepoint via `_process_one_pre_submit` (`reactor.py:422,436-443,446,449,452,460`) and `_finalize_disposition`, then commit — these are terminal-without-submit by definition, so committing them in Window A is correct. No premature persistence of a fill-conditional row.

### 5. Exception paths — OK (live-probed)
- Submit raises: `mutex.acquire()` → `_dead_letter_unknown` → `mutex.release()` (`reactor.py:254-260`). Live probe: event drains to `dead_letter`, `attempt_count=1`, mutex unlocked, `conn.in_transaction=False`, 1 dead-letter row. No leak.
- `_dead_letter_unknown` `ROLLBACK TO SAVEPOINT`/`RELEASE` are wrapped in `contextlib.suppress` (`reactor.py:355-357`), so calling it from the submit-raises path (where no `edli_reactor_event` savepoint exists) does not error. Safe from both Window A's open savepoint and a fresh mutex with no txn, as its docstring claims.
- Window A inner exception: `_dead_letter_unknown` then `finally: mutex.release()` (`reactor.py:243-247`). No double-release (each `mutex.acquire()` has exactly one matching release per path).
- Window B inner exception: ROLLBACK/RELEASE suppressed, then `_dead_letter_unknown`, then `finally: mutex.release()` (`reactor.py:288-294`). Clean.

### 6. INV-37 / world-only — OK
`src/events/reactor.py` creates no connection, no ATTACH, no zeus_trades reference (grep: zero hits beyond a docstring). All 20 store-conn touchpoints route through the single `self._store.conn`. The cross-DB trades write remains in the unchanged adapter `_run_live_order_build_savepoint`. Splitting the world write unit into two single-conn windows is orthogonal to INV-37.

---

## What's missing (gaps)
- **No test for the crash-window itself.** The antibody proves the lock is released across submit (the contention cure) but does NOT prove crash-recovery does not double-submit. A relationship test simulating "process dies between Window A commit and Window B" → re-run → assert exactly one venue command (by idempotency key, AND under a moved book) is absent. This is the test that would catch the MAJOR residual. Recommended before arming.
- **`durable_submit_outbox_enabled` is a hard arm-gate but unexercised by this change** (`event_reactor_adapter.py:303`). If the outbox is the chosen mitigation (option b above), this change should be paired with the outbox wiring before arming.
- Stale doc-rot: `reactor.py:119,121` comments still say "_process_one" (method renamed to the two split methods). Cosmetic.

## Ambiguity risks
- Doc step 9 says re-run "does not double-write" because writes are "idempotent by event_id". This is imprecise: idempotency is by `(decision_id, token_id, side, price, size)`, and price/size are book-derived, NOT event_id-stable. The doc's claim is true only under an unchanged book. Tighten the doc so a future arming session does not over-trust it.

## Multi-perspective notes
- Executor: a future arming engineer reading SEV21_MUTEX_HTTP_FIX step 9 would believe crash-recovery is fully idempotent. It is not under book movement. The brief must carry the moved-book caveat.
- Stakeholder: the stated problem (WAL starvation from lock-across-HTTP) IS solved and proven (antibody + events suite). Success criterion met for the shadow goal.
- Skeptic: strongest argument against = "you committed the claim before submit, durably advancing attempt_count and shifting recovery to the 300s lease path." Counter: the executor's pre-POST durable command commit already made the system at-least-once before this change; the reactor was never the idempotency authority. The split is honest about this (doc step 9 acknowledges the durability posture is unchanged). Sound.

## Verdict justification
Review ran in THOROUGH mode; did NOT escalate to ADVERSARIAL — the PRIMARY hazard, on verification, proved pre-existing and equally mitigated, and no CRITICAL or 3+ MAJOR cluster emerged. Realist Check: the double-submit finding was held at MAJOR (not BLOCKER) because (a) the system is in shadow → inert today, (b) the executor's durable command ledger + deterministic key already deduplicates the common (unmoved-book) crash-recovery case, and (c) the residual moved-book tail is identical in the pre-#95 code, so this diff is not the thing that introduces live-money loss. It earns MAJOR (not MINOR) because it is real, unmitigated, and live-money on the armed path. **MERGE for the shadow contention fix; the two REQUIRED follow-ups (pin price/size to cert OR wire durable_submit_outbox; add a crash-window+moved-book relationship test) gate arming, not merging.**

## Open questions (unscored)
- Does the production `final_intent` certificate already carry a frozen `limit_price`/`size` that the executor COULD use instead of the JIT re-derivation? If so, option (a) may be a one-line change at `executor.py:3044-3051` (derive idem from cert price/size, not re-fetched). Worth a planner pass.
- Is there a reconciliation job that sweeps `processing`-stuck-then-dead-lettered events against on-chain fills? If yes, the moved-book duplicate would be caught post-hoc; if no, the duplicate is silent until balance reconcile. Out of scope for this diff but relevant to the arm gate.
