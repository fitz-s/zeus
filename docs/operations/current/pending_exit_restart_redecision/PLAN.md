# pending_exit_restart_redecision -- Plan

Date: 2026-07-11
Branch: `p2-pending-exit-restart-redecision`
Status: active

## Background

The live Wellington exit intent never reached the venue. Sell preflight rejected
it before command creation because current CTF collateral authority reported zero.
The durable projection is `pending_exit/retry_pending` with a non-empty retry
deadline and a canonical `EXIT_ORDER_REJECTED` event, but `retry_count=0`.
Restart preflight recognizes this recovery shape only when retry_count is positive,
so it blocks the daemon that owns cooldown release and fresh redecision.

## Scope

Treat a commandless pre-submit rejection as restart-recoverable when canonical
state proves `retry_pending` plus a retry deadline, regardless of whether the
counter is zero. This authorizes only boot-time recovery into fresh redecision;
it does not approve an exit, create a command, or bypass the preflight.

## Deliverables
- Restart preflight recognizes the zero-count pre-submit retry shape.
- Existing command-backed retry and unsafe pending-exit cases remain unchanged.

## Verification
- focused restart-preflight regression tests
- full `tests/test_check_live_restart_preflight.py`
- read-only live preflight no longer reports `pending_exit_restart_risk`
