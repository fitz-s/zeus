---
applyTo: "src/execution/**/*.py,src/venue/**/*.py,scripts/*redeem*.py,scripts/*reconcile*.py"
---

# Zeus execution + settlement review

These paths touch live venue side effects and settlement redeem paths.
Default severity is Critical or Important — Nit findings should be
suppressed when any higher-severity finding exists.

## Venue command journaling

Every venue side effect must be preceded by a `venue_commands` row write
(INV-28, INV-30). The pattern is: persist INTENT → submit → update
status. Finding a `place_limit_order` call without an immediately
preceding insert/update to `venue_commands` is Critical.

`place_limit_order` is gateway-only (INV-24). Any call outside the
gateway object is Critical.

## Preflight and limit-order discipline

V2 preflight failure → no `place_limit_order` (INV-25). Market orders
are forbidden; every order submission must set limit_price. BUY orders
round limit price UP; SELL orders round DOWN. Check rounding direction
is enforced in `execution_price.py`, not ad-hoc at call sites.

## Fail-closed paths

RED state: must cancel all pending orders and sweep active positions
before halting (INV-19). Missing either step is Critical.

Void on CHAIN_UNKNOWN is forbidden (INV-18). Void requires CHAIN_EMPTY.
Authority-loss degrades to read-only mode, not RuntimeError (INV-20).

## Cycle scan

Cycle start must scan for unresolved venue_command states (INV-31).
A new cycle that submits without checking for UNKNOWN/REVIEW_REQUIRED
rows from prior cycles risks double-submitting.

## Redeem paths

Redeem is gated behind position being in CHAIN_EMPTY state, not
CHAIN_UNKNOWN or live. Settlement commands must follow the monotonic
state sequence (REDEEM_INTENT_CREATED → ... → REDEEM_CONFIRMED or
terminal). REDEEM_OPERATOR_REQUIRED requires errorCode + operator_action_required
+ autoretry_eligible fields; check all three are present when that
state is written.

## Transaction boundaries

Event append + projection fold must be in one transaction (INV-08).
No commit between append and fold. DB COMMIT must precede derived JSON
export (INV-17). Cross-DB writes (world + trades) must use ATTACH +
SAVEPOINT, never independent connections (INV-37).
