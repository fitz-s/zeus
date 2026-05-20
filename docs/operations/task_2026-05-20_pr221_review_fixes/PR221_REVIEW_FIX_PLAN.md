# PR 221 Review Fix Plan

Created: 2026-05-20
Last reused or audited: 2026-05-20
Authority basis: PR 221 automated review threads at src/data/market_scanner.py:2914 and src/main.py:1218.

## Scope

Fix two P1 live-runtime review findings before PR 221 can merge:

1. Background executable snapshot capture must preserve the actual outcome side. A buy_no substrate row must select the NO token, not hard-code buy_yes/YES.
2. User-channel WS auto-derive must not latch the daemon into a process-lifetime disabled state when fresh persisted condition IDs are absent at boot.

## Structural Decisions

- Snapshot side authority comes from the market outcome being captured, not from a default decision shim.
- WS condition-ID discovery treats empty persisted IDs at boot as stale input and runs the scanner fallback by default before returning an empty subscription set. Operators can still explicitly disable this boot fallback with `ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN=0`.

## Verification

- Add/extend relationship tests for persisted buy_no snapshot substrate token identity.
- Add/extend relationship tests for user-channel auto-derive fallback behavior when persisted rows are empty.
- Run focused tests for market scanner provenance, user-channel ingest/main boot behavior, cascade/live gates touched by PR 221, and command recovery regression gates.
