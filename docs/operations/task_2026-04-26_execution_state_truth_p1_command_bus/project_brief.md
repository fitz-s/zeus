# P1 Project Brief — Durable Command Truth (`venue_commands` + `command_bus`)

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent task `docs/operations/task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` §4 P1; closed P0 packet `docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/`.

## 1. Why P1 starts now

P0 closed five of five structural decisions short of K4 (UNKNOWN composition + RED command emission). K4 cannot land without a durable command journal: chain × command UNKNOWN composition needs command rows to compose against, RED needs durable cancel/derisk/exit *commands* (not just position-local exit_reason marks), and removing the fabricated `unknown_entered_at` requires a command-event timestamp the system can lookup.

P1 builds that durable command journal. Once it exists, K4 becomes a small slice rather than a cross-cutting rewrite.

## 2. Goal of P1

Build a durable execution-state authority spine before any side effect:

```
decision → persisted VenueCommand → command event → venue side effect/recovery → position event/projection → JSON/status projection
```

Concretely:

- **Pre-side-effect persistence**: every order submit must be preceded by a durable row in `venue_commands` and a `SUBMIT_REQUESTED` event in `venue_command_events`.
- **Restart-safe**: a daemon crash between submit and ack must leave a recoverable `UNKNOWN` command row that the next cycle can reconcile.
- **Command-aware reconciliation**: chain × command lookup must compose so flat-after-pending-fill no longer requires fabricating `entered_at`.
- **Idempotency**: every submit carries a deterministic `idempotency_key` so retries do not double-place.

P1 itself does NOT change market eligibility, model sophistication, or execution capabilities. Those remain P3.

## 3. Authority order followed

1. Runtime code/tests (this is execution surface; runtime wins over docs).
2. `architecture/invariants.yaml` and `architecture/negative_constraints.yaml` (current law).
3. Original parent plan at `task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` §4.
4. P0 packet evidence at `task_2026-04-26_execution_state_truth_p0_hardening/`.
5. Operator decisions captured in this packet's `decisions.md`.

## 4. Slice plan — five micro-slices

P1 is intentionally sliced so each slice is independently reviewable and locks invariants before the next slice depends on them.

| Slice | Title | What lands | Touches | INV/NC additions |
|-------|-------|------------|---------|------------------|
| **P1.S1** | Schema + Repo | `venue_commands` + `venue_command_events` table DDL; new `src/state/venue_command_repo.py` with append/query API; INV anchoring "command journal exists and is append-first". No callers yet. | `src/state/db.py` (init_schema), new `src/state/venue_command_repo.py`, new tests | INV-28 |
| **P1.S2** | Command Bus types | `src/execution/command_bus.py` with frozen `VenueCommand` dataclass, `CommandState` enum, `CommandEventType` enum, `IdempotencyKey` newtype. No runtime caller; pure type contract. | New `src/execution/command_bus.py`, type tests | INV-29 |
| **P1.S3** | Executor split | `_live_order` factored into 4 phases: build → persist (writes command row + SUBMIT_REQUESTED event) → submit (calls SDK) → ack (writes SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN event). Side effect now strictly follows persisted intent. | `src/execution/executor.py`, tests for ordering invariants | INV-30 (no submit before persist) |
| **P1.S4** | Recovery loop | New `src/execution/command_recovery.py`. Cycle start scans for unresolved commands (`SUBMITTING`, `UNKNOWN`, `REVIEW_REQUIRED`); reconciles against venue REST + chain. | New `src/execution/command_recovery.py`, integration into `cycle_runner.run_cycle`, recovery tests | INV-31 |
| **P1.S5** | Discovery integration + idempotency | `cycle_runtime.execute_discovery_phase` materializes position only from durable command-event outcome. Add idempotency key contract: `H(decision_id, token_id, side, price_units)`. | `src/engine/cycle_runtime.py`, `src/execution/command_bus.py`, tests for idempotency stability | INV-32, NC-18 (no duplicate submit on retry) |

K4 (chain × command UNKNOWN composition; RED → durable command emission; remove fabricated `unknown_entered_at`) lands in **P2** as a small follow-up once P1 closes.

## 5. Operator decisions required before P1.S1 starts

See [decisions.md](docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/decisions.md). Five decisions (D-P1-1 through D-P1-5) gate the schema design.

## 6. Non-goals (explicit deferrals)

- No execution capability expansion (iceberg, dynamic peg, etc.) — already deleted in K3.
- No Bayesian/model upgrade.
- No market eligibility/source-validity work — P3.
- No persistent alpha budget — P3.
- No K4 work in P1; K4 lands in P2 only after P1.S1–S5 close.
- No production data migration — P1 only adds NEW tables; existing tables untouched.
- No live-execution mode change — runtime posture remains `NO_NEW_ENTRIES` throughout P1.

## 7. Verification posture

Same gate as P0: every slice passes
- `pytest tests/<slice-tests> -v` green;
- wide-suite regression at parity with `2a8902c` baseline (no new failures);
- manifest pointers all resolve via `pytest --collect-only`;
- critic pass after each slice closes.

## 8. Rollback posture

- P1 only ADDS surfaces (tables, modules, types). Nothing existing is replaced or deleted.
- If a slice fails review, revert the slice; remaining slices continue independently.
- If P1.S3 (executor split) hits a blocker, fall back to P0 posture: runtime stays `NO_NEW_ENTRIES`, no live entries proceed, recovery loop runs read-only.

## 9. Branch / worktree

- Continue on `claude/pr18-execution-state-truth-fix-plan-2026-04-26` (P0 + P1 in same branch).
- Local commits only until operator approves push.
- Co-tenant worktrees active: stage files explicitly, never `git add -A`.
