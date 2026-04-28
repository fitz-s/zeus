# py-clob-client-v2 surface excerpt — 2026-04-27

Authority type: captured external SDK/reference evidence for R3 Z2. External
facts remain current-fact evidence, not durable Zeus law.

## Sources checked

- PyPI `py-clob-client-v2` 1.0.0: released 2026-04-17; project owned by
  Polymarket; PyPI provenance links to `Polymarket/py-clob-client-v2@v1.0.0`.
  Source: https://pypi.org/project/py-clob-client-v2/
- Polymarket V2 migration guide: V2 package name is `py-clob-client-v2`, V2
  test host is `https://clob-v2.polymarket.com`, and the legacy Python package
  stops working after cutover. Source: https://docs.polymarket.com/v2-migration
- Polymarket Clients & SDKs page: official Python package is
  `py-clob-client-v2` and source repository is
  `github.com/Polymarket/py-clob-client-v2`. Source:
  https://docs.polymarket.com/api-reference/clients-sdks
- Wheel inspection via `python3 -m pip download --no-deps
  py-clob-client-v2==1.0.0` on 2026-04-27.

## Verified package facts from wheel

- Import package path is `py_clob_client_v2`, not `py_clob_client`.
- `py_clob_client_v2.client.ClobClient` exposes:
  - `get_ok()`
  - `post_heartbeat(heartbeat_id: str = "")`
  - `get_clob_market_info(condition_id)`
  - `get_fee_rate_bps(token_id)`
  - `get_tick_size(token_id)`
  - `get_neg_risk(token_id)`
  - `get_balance_allowance(params)`
  - `create_order(order_args, options=None)`
  - `post_order(order, order_type=OrderType.GTC, post_only=False,
    defer_exec=False)`
  - `create_and_post_order(order_args, options=None, order_type=OrderType.GTC,
    post_only=False, defer_exec=False)`
  - `get_order(order_id)`
- `py_clob_client_v2.clob_types` exposes `OrderArgsV1`, `OrderArgsV2`,
  `OrderArgs` aliasing V2, `PartialCreateOrderOptions`, `OrderType`,
  `FeeDetails`, `FeeInfo`, `BuilderFeeRate`, `OpenOrderParams`, and
  `BalanceAllowanceParams`.
- V2 wheel still uses a positional Python constructor signature
  `(host, chain_id, key=None, creds=None, signature_type=None, funder=None,
  builder_config=None, use_server_time=False, retry_on_error=False)` despite
  TypeScript-oriented docs showing an options object. Z2 code must tolerate the
  wheel source, not assume the TS shape.
- `cancel` was not found as a direct `ClobClient.cancel` method in the wheel
  inspection; Z2 cancel should remain adapter-surfaced but must not overclaim a
  verified SDK cancel implementation until a method is confirmed.

## Z2 implementation consequence

Z2 should pin provenance around `VenueSubmissionEnvelope`, support both one-step
`create_and_post_order` and two-step `create_order` + `post_order` SDK paths in
unit tests, and keep live preflight fail-closed when Q1-zeus-egress evidence is
absent. Do not write tests that assert the TypeScript constructor shape for the
Python wheel.
