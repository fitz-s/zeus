# EDLI Redemption Daemon Runbook

Created: 2026-05-24

## Status

REVIEW_REQUIRED: daemon restart is intentionally not part of PR331.

PR331 is a redemption proof package. It freezes EDLI live config off and proves
event-bound semantic construction without scheduler, executor, venue adapter,
websocket, live submit, or daemon restart.

## Relationship To PR328

PR328 is a failed spike/scaffold and must not be merged or rebooted. PR331 is
the replacement redemption branch. It does not patch PR328 in place; it
restarts from `origin/main` and imports only the redemption package and pure
proof-kernel contracts.

## Before Any Future Daemon Online PR

1. Verify `config/settings.json` keeps `edli_v1.enabled=false` until R9/R10
   acceptance evidence exists.
2. Run the event-bound proof tests.
3. Run the money-path semantic classifier and required selected tests.
4. Add a separate runtime PR that wires the reactor to daemon startup.
5. Only after that PR, perform operator-approved daemon restart smoke:
   - event writer starts
   - market channel connects only active weather tokens
   - user channel or explicit reconcile is available before submit
   - one synthetic event replay produces no submit
   - one Day0 live-authority dry-run produces no submit unless live cap PR is approved
   - reports run

No launchctl command is executed by this PR.
