# M3 clarification — WS auth shape and gap threshold

Date: 2026-04-27
Phase: M3 User-channel WS ingest + REST fallback

## What the phase card says

- `PolymarketUserChannelIngestor(adapter, condition_ids, api_key)`
- WS gap detected → block new submit + force M5 sweep before unblocking.
- Open question in `CONFUSION_CHECKPOINTS.md`: what defines a gap?

## Current official-doc evidence

Official Polymarket user-channel docs require an `auth` object with three L2 credential fields: `apiKey`, `secret`, and `passphrase`. They also state that the user channel subscribes by condition IDs in `markets`, not asset IDs, and that market/user channels require `PING` every 10 seconds.

Evidence is summarized in `r3/reference_excerpts/polymarket_user_ws_2026-04-27.md` with official URLs.

## Localized M3 decision

1. Replace the phase-card shorthand `api_key` with a `WSAuth(api_key, secret, passphrase)` object. This is not a structural schema change; it aligns M3 with current official docs and avoids leaking wallet/private-key assumptions into websocket auth.
2. Define a stale-message gap as 3 missed heartbeat cadences: default `stale_after_seconds = 30`, derived from the official 10-second PING cadence.
3. A disconnect, auth failure, or market subscription mismatch is an immediate gap.
4. Reconnection alone does not clear the submit block if `m5_reconcile_required` is true; M5 or an explicit recovery-clear path must provide evidence later.
5. M3 records the M5 requirement as a guard/status fact and cycle summary marker only. It does not implement the M5 exchange reconciliation sweep.

## Why this is safe

- Conservative: false positives block new submit instead of allowing blind trading through missing venue truth.
- Bounded: no command enum/state expansion and no live cutover authority.
- Auditable: the threshold and auth-shape deviation are packet-local evidence, not hidden implementation drift.
