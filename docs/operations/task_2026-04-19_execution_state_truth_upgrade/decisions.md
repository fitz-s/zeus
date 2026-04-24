# Decision Log — Execution-State Truth Upgrade

## D-001 — Main path selection

Status: accepted

### Decision
Choose **Execution-State Truth Re-Architecture** as the main path.

### Why
The highest-severity remaining failure class is not statistical edge quality. It is loss of authoritative control over order/position truth across asynchronous submit/fill/cancel/restart boundaries.

### Consequence
P1/P2 become the main structural work. P3 remains secondary.

---

## D-002 — Live readiness ruling

Status: accepted

### Decision
`data-improve` is **not unrestricted live-entry ready**.

### Why
The branch still lacks durable pre-submit command truth, restart-safe command recovery, and fully integrated unknown/de-risk semantics.

### Consequence
Immediate posture is `NO_NEW_ENTRIES`. At most `EXIT_ONLY` after P0, and unrestricted live entry remains blocked until P1/P2 verification.

---

## D-003 — Source of control for repo/review conflicts

Status: accepted

### Decision
Use the following precedence when sources disagree:

1. runtime code/tests/behavior
2. machine manifests
3. current authority docs
4. current operations pointer
5. supplied review
6. stale/historical docs/tests/comments

### Why
Current delivery law explicitly places code/tests/runtime behavior above docs and historical rationale.

### Consequence
The review is evidence, not authority.

---

## D-004 — Specific stale-claim resolutions

Status: accepted

### Decision
Treat the following review/test claims as stale and update/demote them:

- “commit-then-export helper missing”
- “cycle still writes JSON before DB”
- “family split helper missing”
- “degraded loader still kills the whole cycle”
- any authority-facing text that describes those as present-tense truth

### Why
Current repo reality already contradicts them.

### Consequence
P0 includes targeted stale authority cleanup.

---

## D-005 — RED behavior interpretation

Status: accepted

### Decision
Acknowledge that current runtime has improved beyond pure entry-block-only `RED`, but still treat command-authoritative de-risk as unfinished.

### Why
The branch now sweeps positions toward exit by marking exit intent, which is real progress. But target law still requires cancel/de-risk/exit work as durable command truth, not only local annotation.

### Consequence
Do not restate the stale claim that RED is still entirely entry-block-only. Instead, describe current behavior as **partially remediated but not final**.

---

## D-006 — Degraded export semantics

Status: accepted

### Decision
A degraded export can never be labeled `VERIFIED`.

### Why
That label inflates operator confidence exactly when authority is reduced.

### Consequence
This is a P0 must-fix, not a later cleanup item.

---

## D-007 — New durable truth surfaces

Status: accepted

### Decision
Add `venue_commands` and `venue_command_events` rather than trying to overload `trade_decisions`, `execution_report`, or JSON files.

### Why
Those existing surfaces are either telemetry, decision history, or derived projections. They are not a durable command journal/event spine designed for crash recovery.

### Consequence
P1 includes schema + repo API + recovery flow.

---

## D-008 — Unknown/review-required semantics

Status: accepted

### Decision
`UNKNOWN` and `REVIEW_REQUIRED` become first-class execution-truth states that block new entry.

### Why
A system that cannot prove command truth must not continue opening new risk.

### Consequence
P2 integrates unresolved commands into reconciliation and operator workflow.

---

## D-009 — Gateway-only live placement

Status: accepted

### Decision
All live order placement must go through the approved execution gateway / command boundary. Direct `place_limit_order` outside that boundary is forbidden.

### Why
Without a single choke point, submit-before-side-effect law cannot be enforced.

### Consequence
P0 adds guardrails; P1 completes the gateway architecture.

---

## D-010 — Official Polymarket docs control venue migration facts

Status: accepted

### Decision
Official Polymarket documentation controls the plan for cutover date, open-order wipe, SDK compatibility, and fee/migration semantics.

### Why
Repo comments and the supplied review may be stale. Venue docs define actual external behavior.

### Consequence
The packet uses:
- V2 go-live: 2026-04-28 (~11:00 UTC)
- open orders wiped at cutover
- no backward compatibility for old SDK integrations
- production URL remains `https://clob.polymarket.com` after cutover

---

## D-011 — P0 includes V2 preflight

Status: accepted

### Decision
V2 preflight is a P0 hard gate, not a later nice-to-have.

### Why
The venue migration is near-term and directly affects live order safety.

### Consequence
No live placement without passing preflight.

---

## D-012 — Preserve unrelated dirty work

Status: accepted

### Decision
This packet forbids recommendations to reset/checkout/revert or otherwise discard unrelated dirty work.

### Why
Current repo law and operator safety both require preserving in-flight work unless explicit human approval says otherwise.

### Consequence
All rollback notes are behavioral and scoped, not destructive.

## Blocking questions

These questions block safe implementation of P1/P2:

1. final schema ownership/location for `venue_commands` and `venue_command_events`
2. frozen command-event grammar
3. approved V2 Python client/version and auth surface
4. source-of-truth precedence during recovery conflicts
5. durable idempotency key format

## Non-blocking questions for P0

These do not block immediate hardening:

1. rename vs relabel `positions.json`
2. exact operator wording for degraded/unverified/review-required
3. exact representation of nullable unresolved timestamps
4. exact P3 alpha-ledger table design
5. dashboard/UI polish for review queues

## First recommended implementation packet after planning

`P0 hardening — degraded authority fix + V2 preflight + stale authority cleanup + no-new-entry gates`

Reason:
- smallest blast radius
- immediately reduces live risk
- prepares the repo for the P1 schema packet
- avoids mixing venue migration hardening with the larger command-journal refactor
