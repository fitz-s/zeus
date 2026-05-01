# R1L1 External Grounding — Down Region

Created: 2026-04-26
Authority basis: WebFetch of upstream Polymarket sources, no file:line claims (L1)
Authors: proponent-down (shared with opponent-down)

## V1 SDK (py-clob-client) snapshot

- Source: https://github.com/Polymarket/py-clob-client (README + tag list)
- Latest tag: **v0.34.6**
- Latest tag date: **Feb 19, 2026**
- No `v2` branch / version notice present
- No deprecation / EOL notice present
- OrderArgs surface (README example): `token_id, price, size, side`
- MarketOrderArgs surface: `token_id, amount, side, order_type`
- No clientOrderId / idempotency_key in README
- No heartbeat method (closest is `client.get_ok()`)

## V2 SDK (py-clob-client-v2) snapshot

- Source: https://github.com/Polymarket/py-clob-client-v2 (repo confirmed exists)
- Latest tag: **v1.0.0**
- OrderArgs surface (README example): `token_id, price, side, size`
- Authentication: L1 (EIP-712 wallet sig) → derive API creds → L2 HMAC for orders
- New combined methods: `create_and_post_order(...)`, `create_and_post_market_order(...)`
- New helper class: `PartialCreateOrderOptions(tick_size="0.01")`
- OrderType enum exposed: `GTC`, `FOK`, `FAK` (READMEs only show GTC / FOK / FAK explicitly)
- No clientOrderId / idempotency_key in README
- No heartbeat / keepalive / session method in README (we did NOT find evidence of mandatory heartbeat in V2 README — flagged for Phase 0.B deeper source check)
- Market order amount labeling: `# USDC` (no pUSD wording in README)
- Host: configurable placeholder `<polymarket-clob-host>` (no V2-specific URL pinned in README)
- Source-tree subpages (`/blob/main/py_clob_client/clob_types.py`, `/tree/main/py_clob_client`) returned 404 via WebFetch — likely package name renamed in V2 (e.g. `polymarket_clob` or `clob_v2`) or sources are not under that path. Phase 0.B operator clone-and-grep is required.

## Polymarket public docs snapshot

- Source: https://docs.polymarket.com/
- No CLOB V2 migration timeline page surfaced
- No EOL announcement surfaced
- No clientOrderId / idempotency / heartbeat / WS keepalive wording surfaced
- No pUSD wording surfaced (Builder Program mentioned but no required-builder-code wording for V2 specifically)

## Implications for L1 architecture debate

1. V2 is a **separate Python package**, not a config flag inside V1 — confirms transport-paradigm shift (Plan §2 Paradigm A) is real, not synthetic.
2. Mandatory 10s heartbeat claim in plan §1 / §2 is **NOT yet externally evidenced** in WebFetched README. This needs Phase 0.B deeper source / Discord / support channel before the plan's "mandatory 10s" wording is locked.
3. Idempotency surface gap is **identical** in V1 v0.34.6 and V2 v1.0.0 README. F-003 likely upstream-neutral pending Phase 0.B addendum on HTTP-header layer.
4. pUSD swap (Plan §2 Paradigm B) is **not yet externally evidenced** in README — only "USDC" is labelled. This is the highest-uncertainty paradigm in the plan; Phase 0.D operator inquiry is correctly required.

## Limitations

- WebFetch returned 404 on speculative source-tree paths; no clone-and-grep was performed (operator-only step per Phase 0.B).
- README is a marketing surface, not source of truth for full method signatures.
- "Mandatory 10s heartbeat" claim should be promoted from plan-asserted to Phase 0.B-evidenced before Phase 1 entry.
