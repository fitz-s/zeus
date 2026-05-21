# Live Entry Order Management Plan

Created: 2026-05-21
Last reused or audited: 2026-05-21
Authority basis: AGENTS.md money path, topology navigation, live runtime evidence 2026-05-21

## Objective

Fix the broken relationship between live entry selection and existing entry
orders. A same-token open order must not silently block a fresh executable edge
as `ALREADY_HELD_SAME_TOKEN`; it must be classified as exposure, keep-existing,
or cancel-replace-needed using canonical command/order truth.

## Current Facts

- Live code is clean and loaded from `origin/main` at
  `f1b08be94caeaf3e724f7ef91ff20ee23ff21cbb`.
- Current open entry command truth is in `state/zeus_trades.db`; unresolved
  chain/local reconciliation findings are zero.
- Open commands include:
  - `e22f472189764500`, Tokyo YES, `ACKED`, order price `0.008`, latest book
    bid/ask `0.009/0.010`;
  - `1e6071451c1a4a2e`, Kuala Lumpur YES, `PARTIAL`, matched `1.123333`;
  - `ba136ee156c64258`, Jeddah YES, `ACKED`, order price `0.003`.
- Recent `opening_hunt` candidates are rejected as `already_held_same_token`
  while these orders remain live. The evaluator currently checks
  `position_current` non-terminal phases, including `pending_entry`, before
  executable snapshot repricing runs.
- Existing cancel/replace safety is exit-oriented. Entry has orphan cleanup and
  duplicate-submit guards, but no entry-order manager that compares current
  executable book truth against an existing live entry order.
- A backup-DB dry-run of the new entry-order manager identified the stale Tokyo
  order, but `request_cancel_for_command()` raised `NORMAL:CANCEL`. Live entry
  submission is operating in CutoverGuard `NORMAL`, while the durable cancel
  grammar still only allows cancel in `LIVE_ENABLED` or `PRE_CUTOVER_FREEZE`.
  That is a relationship break between live entry order management and the old
  CLOB-v2 cutover state machine.

## Structural Decision

Split same-token handling into two relationships:

1. Exposure dedup remains fail-closed for active, day0, pending-exit,
   partial-filled, or exit-in-flight exposure.
2. No-fill pending entry order management is a command-management decision:
   keep the existing order, request cancel before replacement, or block as
   unsafe. It must not be represented as held exposure.

The evaluator may identify that same-token entry management is required, but it
must not directly perform venue side effects. Any cancel path must go through
durable `venue_commands` and the existing typed cancel grammar.

The cancel grammar must distinguish ordinary live order management from
redeem/cutover enablement. Allowing a durable cancel for an existing venue
command must not enable new submits or autonomous redemption. Cancels reduce
exposure and are required for maintaining stale entry orders; redemption remains
operator-gated by its separate settlement command state.

Second structural finding: passive maker sizing and taker liquidity sizing are
different relationships. A post-only GTC bid does not consume best-ask depth and
must not inherit the FAK/taker haircut bucket. Taker FOK/FAK paths still use the
microstructure haircut and depth checks.

## Relationship Tests

Write tests before implementation that prove:

- a no-fill `pending_entry`/`ACKED` same-token order with a newer executable
  snapshot does not become a terminal `ALREADY_HELD_SAME_TOKEN` evaluator result;
- the system does not submit a duplicate same-token entry while the prior order
  remains live;
- partial-filled same-token exposure remains a hard dedup block;
- cancel/replacement, if selected, appends durable cancel intent before any new
  submit and blocks replacement on cancel-unknown.
- live `NORMAL` permits durable cancel for an existing command without enabling
  redemption or bypassing identity/state/capability proof;
- passive maker repricing remains submit-safe when only ask-side depth is
  shallow; taker/crossing paths continue to apply ask-depth liquidity checks.

## Non-Goals

- Do not weaken duplicate-exposure protection.
- Do not submit market orders or enable autonomous spread crossing in this
  slice.
- Do not mutate production DB rows manually.
- Do not claim live stability from tests, PR merge, daemon liveness, or a single
  order.

## Acceptance

The slice is acceptable only when all are true:

- topology admits the changed files or planning lock accepts this packet;
- relationship tests pass for no-fill pending entry, partial fill, duplicate
  prevention, and cancel-unknown safety;
- live deploy on `main` is clean and the daemon loaded commit equals
  `origin/main`;
- a subsequent live cycle either manages stale same-token entry orders
  explicitly or records economically legitimate no-trade reasons without hiding
  an executable edge behind `ALREADY_HELD_SAME_TOKEN`.
