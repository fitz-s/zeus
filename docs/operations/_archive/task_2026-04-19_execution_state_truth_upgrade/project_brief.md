# Task 2026-04-19 — Execution-State Truth Upgrade

Status: planning lock  
Branch: `data-improve`  
Baseline commit: `630a1e65945513d8bc32480af442cf56ae08cab9`  
Target landing path: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`

## 1. Executive ruling

This packet rules that Zeus **should prioritize Execution-State Truth Re-Architecture** as the main path.

That ruling is not a statement that model quality, market eligibility, or persistent alpha governance are unimportant. It is a statement about **failure ordering**. The repo already contains authority law that says canonical truth lives in repo-owned DB/event surfaces, derived JSON/status artifacts are not canonical truth, `CHAIN_UNKNOWN` is first-class, degraded authority must not blind monitoring, and `RED` must change behavior materially. The current branch has improved several surface-layer issues, but it still does not have a persisted pre-submit venue command journal, a venue command event spine, or a restart-safe command recovery contract. Until those exist, Zeus can still lose authority over whether it actually submitted, partially filled, cancelled, or failed an order.

The packet therefore locks the program around the following decision:

1. **P0 must harden current live behavior immediately**: no-new-entry gates, degraded truth fix, Polymarket CLOB V2 preflight/cutover readiness, and cleanup of stale authority artifacts that would mislead future work.
2. **P1 must introduce durable execution command truth**: `venue_commands`, `venue_command_events`, submit-before-side-effect discipline, and crash recovery.
3. **P2 must close the semantic loop**: unresolved commands become first-class `UNKNOWN` / `REVIEW_REQUIRED`, and `RED` becomes authoritative de-risk command emission rather than a local intent marker.
4. **P3 may then address outer decision law**: market eligibility / settlement containment and persistent alpha budget.

## 2. Why this packet supersedes the currently active one

`docs/operations/current_state.md` says the branch should continue the Dual-Track Metric Spine Refactor **unless a higher-priority governance or architecture blocker supersedes it**. This packet is that superseding blocker.

The reason is simple: the active refactor improves metric identity and data correctness, but this task changes canonical truth ownership, lifecycle semantics, DB/event authority, execution recovery, and control-plane behavior. Under current delivery law, that is architecture/governance/schema work and requires a frozen packet before implementation.

## 3. Cross-check outcome

The supplied review is directionally strong, but it is **not fully current**. Cross-checking against the repo and official venue docs yields the following:

### Confirmed already fixed on `data-improve`

- DB commit before derived JSON export is already implemented through `commit_then_export()` and the cycle runner’s commit-first write path.
- The FDR family split is already present via `make_hypothesis_family_id()` and `make_edge_family_id()`.
- Authority-loss no longer kills the whole cycle; the degraded path now keeps monitor / exit / reconciliation alive in read-only mode.
- `RED` is no longer pure entry-block-only in runtime reality; the cycle now performs a sweep by marking active positions for exit.

### Confirmed still incomplete or wrong

- `_TRUTH_AUTHORITY_MAP` still maps `"degraded"` to `"VERIFIED"`. That is an operator-authority bug and must be fixed in P0.
- There is still no durable `venue_commands` or `venue_command_events` truth layer in DB.
- Order submission still occurs before authoritative execution command persistence. The repo can therefore still create an orphan side-effect window across crash/restart boundaries.
- `pending_tracked` entry semantics still exist before confirmed fill resolution, so the local system can partially invent execution progress without a persisted command-event spine.
- `CHAIN_UNKNOWN` exists, but not yet as a fully integrated command-aware authority state machine. Some rescue/quarantine flows still fabricate sentinel timestamps such as `unknown_entered_at`.
- There is no explicit CLOB V2 preflight gate or cutover-readiness contract in the live order path.
- Multiple stale tests, comments, and authority-adjacent claims still describe pre-fixed behavior and would mislead future implementation if left untouched.

### Explicit conflict to carry into the plan

- The review states CLOB V2 cutover occurs on **2026-04-22**.
- Official Polymarket documentation currently states V2 go-live is **2026-04-28 (~11:00 UTC)**, with approximately one hour of downtime, open-order wipe, no backward compatibility for old SDK integrations, and final production service still hosted on `https://clob.polymarket.com`.

**Control source for that conflict:** official Polymarket docs, not the review.

## 4. Authority order for this packet

This packet uses the following control order and treats any conflict according to this stack:

1. **Runtime code / current tests / actual behavior**
2. **Machine-checkable manifests** (`architecture/*.yaml`, invariants, negative constraints, topology)
3. **Current authority docs**
4. **Current operations pointer**
5. **The supplied review file as evidence input**
6. **Historical or stale tests/comments/docs**

For venue migration dates, fee semantics, order lifecycle, websocket/user-channel behavior, and cutover requirements, **official Polymarket documentation outranks repo comments and the supplied review**.

## 5. Live-readiness verdict

`data-improve` is **not unrestricted live-entry ready**.

Immediate operating posture from this packet:

- **Now:** `NO_NEW_ENTRIES`
- **Before end of P0:** remain `NO_NEW_ENTRIES`; monitoring and exit lanes may continue if authority is explicit
- **After P0 but before P1/P2:** at most `EXIT_ONLY` or `MONITOR_ONLY`
- **Unrestricted live entry:** blocked until P1 command journal + P2 unknown/de-risk semantics are verified

This is not because the branch has no good work. It is because execution-state truth is still incomplete, and Polymarket’s V2 migration creates a near-term venue contract change that must be gated before live order placement.

## 6. Why Path 1 beats the alternatives

### Path selected

**Execution-State Truth Re-Architecture** is the chosen main path.

### Competing path with the strongest case against it

The strongest argument against Path 1 is that **settlement containment and CLOB V2 migration readiness are more immediate and lower blast radius**. A team could argue that the fastest de-risk move is to shrink market eligibility, freeze boundary-day exposure, and ship V2 preflight/cutover gates without reopening the execution-state architecture.

### Why that argument does not change the ruling

That argument **changes phase ordering, not the main ruling**.

It is strong enough to force the following decision:

- **P0 must include V2 preflight / cutover readiness and degraded-authority hardening immediately.**

It is **not** strong enough to replace Path 1 because market eligibility and cutover prep do not solve the deeper system-kill class:

- venue accepted an order but Zeus has no durable command record
- Zeus restarts and cannot deterministically recover submit intent vs accepted vs unknown
- partial fill authority cannot be reconstructed from an append-first command-event spine
- operators see “VERIFIED” while the system is degraded
- `RED` can still stop new mistakes without yet owning explicit de-risk command truth

In other words: settlement containment and migration prep are urgent, but they are still downstream of a missing durable execution truth model.

## 7. Phase summary

## P0 — Immediate hardening

Objective: stop authority inflation and block unsafe new risk immediately.

Scope:

- degraded export can never be labeled `VERIFIED`
- unresolved/unknown/review-required command conditions block new entries
- V2 preflight failure blocks live order placement
- stale authority-facing tests/docs/comments are updated or demoted
- direct order-placement surface gets AST/CI guard coverage
- branch operating posture remains `NO_NEW_ENTRIES`

## P1 — Durable command truth

Objective: introduce a persisted venue command journal and event spine.

Scope:

- `venue_commands`
- `venue_command_events`
- submit-before-side-effect discipline
- execution gateway / command bus
- crash-recovery worker
- idempotency + replay keys

## P2 — Semantic closure

Objective: make `UNKNOWN` / `REVIEW_REQUIRED` and `RED` truly authoritative.

Scope:

- unresolved command state feeds reconciliation truth
- `UNKNOWN` and `REVIEW_REQUIRED` become first-class lifecycle-adjacent blocking states
- `RED` emits cancel / de-risk / exit commands
- fabricated timestamps and heuristic truth folding are removed or quarantined
- projection-only surfaces are explicitly demoted

## P3 — Outer containment and decision-law governance

Objective: reduce venue/settlement and repeated-test risk once command truth is stable.

Scope:

- market eligibility / settlement containment
- boundary-day controls
- station/finalization contracts
- persistent alpha budget across time, not just per snapshot

## 8. Global invariants this packet locks

The implementation packet that follows this plan must preserve all existing active law and add the following as non-negotiable behavior:

1. DB / event truth outranks JSON/status projections.
2. JSON/status projections are never canonical truth.
3. No venue order side effect may occur unless a persisted `VenueCommand` exists first.
4. No authoritative position state may change from order submission unless a `venue_command_event` exists.
5. A degraded export must never be labeled `VERIFIED`.
6. `UNKNOWN` or `REVIEW_REQUIRED` venue command state blocks new entries.
7. `RED` risk must produce cancel / de-risk / exit commands, not merely entry suppression.
8. `positions.json` is projection only.
9. Direct `place_limit_order` calls outside the execution gateway / command boundary must be blocked by AST/CI guard.
10. V2 preflight failure blocks live order placement.
11. Unrelated dirty work is preserved; no reset / checkout / revert is recommended by this packet.

## 9. Open questions that block implementation

These are genuine blockers for coding P1/P2 correctly:

1. **DB schema decision**
   - Should `venue_commands` and `venue_command_events` live in the main trading DB or a dedicated execution schema within the same DB file?
   - This must be answered before migration design.

2. **Command-event grammar**
   - Which exact external facts become authoritative events?
   - Proposed minimum: `SUBMIT_REQUESTED`, `SUBMIT_ACKED`, `SUBMIT_UNKNOWN`, `PARTIAL_FILL_OBSERVED`, `FILL_CONFIRMED`, `CANCEL_REQUESTED`, `CANCEL_ACKED`, `EXPIRED`, `REJECTED`, `REVIEW_REQUIRED`.
   - Final grammar must be frozen before implementation.

3. **CLOB V2 integration surface**
   - What exact V2 Python client package/version is approved for production?
   - Can the runtime environment authenticate the V2 user websocket and version endpoint?
   - This blocks the final P0/P1 live placement gate.

4. **Recovery source-of-truth order**
   - When command state, order lookup, websocket events, and chain data disagree after restart, which one wins at each stage?
   - This must be explicit before recovery code lands.

5. **Idempotency / replay contract**
   - What exact idempotency key format persists across restarts and operator replays?
   - A weak or unstable key defeats the entire command-journal purpose.

## 10. Open questions that do not block P0

These are real questions, but they should not delay immediate hardening:

1. Whether `positions.json` should be renamed or only stamped more aggressively as projection-only.
2. Whether operator UI terminology should say `DEGRADED`, `UNVERIFIED`, or both.
3. Whether `REVIEW_REQUIRED` lives as a dedicated enum or a state + reason pair.
4. Whether the future alpha budget table lands in P3 as a new table or as fields on an existing family ledger.
5. Whether `unknown_entered_at` is replaced by a nullable timestamp or a typed sentinel wrapper.

## 11. First recommended Codex implementation packet after planning

The first implementation packet after this planning lock should be a **narrow P0 hardening packet**, not the full P1 schema refactor.

### Packet name

`task_2026-04-19_execution_state_truth_upgrade_p0_hardening`

### Goal

Land the fastest no-new-risk hardening changes without opening the schema yet.

### Intended file scope

- `src/state/portfolio.py`
- `src/engine/cycle_runner.py`
- `src/data/polymarket_client.py`
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- authority-facing stale tests (`tests/test_dt1_commit_ordering.py`, `tests/test_fdr_family_scope.py`)
- new targeted P0 tests
- the smallest CI/AST rule surface required to block direct non-gateway order placement outside the approved boundary

### Required outputs

1. Fix degraded truth labeling
2. Add V2 preflight gate for live order placement
3. Block new entries on explicit unknown/review-required gate conditions
4. Update or demote stale authority-facing tests/comments
5. Add targeted P0 verification tests
6. Keep runtime in `NO_NEW_ENTRIES`

### Explicitly excluded from the first packet

- no `venue_commands` schema yet
- no broad cycle-runtime refactor yet
- no evaluator/Kelly redesign
- no market-eligibility redesign
- no persistent alpha ledger
- no destructive git cleanup

## 12. Operator posture until implementation completes

Until P0 and P1 evidence exists, operator policy from this packet is:

- do not re-enable unrestricted live entry
- do not trust any degraded export labeled as verified
- treat unresolved order/command truth as blocking
- treat V2 readiness as a hard gate, not an advisory note
- prefer manual review / cancel-all / rebuild over optimistic inference when authority is missing

## 13. What this packet is not

This is **not** a request to write implementation code in one pass.  
This is **not** permission to rewrite broad truth surfaces without packet evidence.  
This is **not** a reset/cleanup packet for unrelated dirty work.

It is a frozen architecture/governance plan that turns a review into a commit-ready implementation sequence.
