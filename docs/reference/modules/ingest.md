# Ingest Module Authority Book

**Recommended repo path:** `docs/reference/modules/ingest.md`
**Current code path:** `src/ingest`
**Authority status:** Dense module reference for runtime venue/user event ingest. Executable code, tests, machine manifests, and active R3 phase cards outrank it.

## 1. Module purpose

Own runtime event-stream ingestion that converts external venue messages into Zeus append-only facts. R3 M3 starts this module with the authenticated Polymarket user WebSocket channel.

## 2. What this module is not

- Not command grammar authority.
- Not lifecycle transition authority.
- Not M5 exchange reconciliation sweep implementation.
- Not live cutover approval.

## 3. Runtime role

The ingestor subscribes to Polymarket user-channel order/trade updates, normalizes them, and writes U2 `venue_order_facts`, `venue_trade_facts`, and `position_lots` through `src/state/venue_command_repo.py` APIs.

## 4. Invariants

- User channel subscriptions use condition IDs in `markets`, not asset IDs.
- Source for user-channel facts is `WS_USER`.
- `MATCHED` trade facts may create `OPTIMISTIC_EXPOSURE`; only `CONFIRMED` creates `CONFIRMED_EXPOSURE` / canonical training eligibility.
- `FAILED` after `MATCHED` appends a quarantining/reversal lot rather than mutating optimistic history.
- WS gap/auth/mismatch states block new submit through the M3 guard and require future M5 reconciliation evidence before clearing.
- API key, secret, and passphrase are never included in raw fact payloads, logs, or operator evidence artifacts.

## 5. Verification commands

```bash
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_m3_user_channel_routes_to_m3_profile
```
