# Polymarket user WebSocket excerpt — 2026-04-27

Source URLs:
- https://docs.polymarket.com/market-data/websocket/overview
- https://docs.polymarket.com/market-data/websocket/user-channel

Facts used by R3 M3:
- User endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/user`.
- User-channel subscription sends `type: "user"`, `markets: [condition_id, ...]`, and an `auth` object with `apiKey`, `secret`, and `passphrase`.
- The user channel subscribes by condition IDs (`markets`), not token/asset IDs.
- User message families are order and trade updates.
- Trade statuses include `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`, and `FAILED`; `MATCHED` is not terminal finality.
- Order messages include placement, update, and cancellation events.
- Market/user channels require a client `PING` every 10 seconds and the server responds with `PONG`.
- A valid subscription must be sent immediately after connecting; user-channel credentials must not be exposed client-side.

M3 implementation implication:
- Zeus accepts all three L2 API credential fields for WS auth even though the original M3 card abbreviated this as `api_key`; this is a localized spec-vs-current-docs adjustment recorded in `_confusion/M3_gap_threshold_and_auth_2026-04-27.md`.
