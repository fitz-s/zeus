# Live Smoke Test — Findings & Resolutions

**Date**: 2026-05-01
**Smoke window**: 11:30Z – 12:02Z
**Initial state**: branch `main` @ 157192d9, no zeus daemons running, no sentinels.

## F1 — `py-clob-client-v2` not installed in venv

| Aspect | Detail |
|---|---|
| Severity | LIVE BLOCKER |
| Root cause | requirements.txt declares `py-clob-client-v2==1.0.0` but `.venv/lib/python3.14/site-packages/` only contained `py_clob_client` (v1 SDK). Earlier env was never re-synced after the v2 dep was added. |
| Symptom | `ModuleNotFoundError: No module named 'py_clob_client_v2'` repeating every 5s on the venue heartbeat job. Trading daemon stayed up, but venue heartbeat stuck in `LOST`, blocking all GTC/GTD orders. |
| Detection | First Phase 3 boot — visible in `logs/zeus-live.err.phase3-firstrun-2026-05-01`. |
| Fix | `.venv/bin/pip install "py-clob-client-v2==1.0.0"` (single command; deps were already satisfied). |
| Antibody | Add a CI/preflight step: `python -m venv` reuses requirement.txt; introduce a startup probe that imports critical SDKs (`py_clob_client_v2`, sqlite3, polymarket-related) and exits non-zero if any are missing. Tracked as follow-up. |

## F2 — `heartbeat_sensor.py` rejects new plist args

| Aspect | Detail |
|---|---|
| Severity | LIVE BLOCKER (sensor exited rc=2 on every load) |
| Root cause | Architect audit B-2 (executor #20) promoted `com.zeus.heartbeat-sensor.plist.proposed` to live, which passes `--heartbeat-files` and `--stale-threshold-seconds`. The script was never extended to accept those flags — argparse `parse_args()` failed with "unrecognized arguments". |
| Symptom | `launchctl list com.zeus.heartbeat-sensor` showed status `2` immediately after every `load`. Sensor never wrote `memory/heartbeat-brief.json`. |
| Detection | Phase 3 first load — `logs/heartbeat-sensor.err.phase3-firstrun-2026-05-01`. |
| Fix | `bin/heartbeat_sensor.py` now declares both flags via argparse with helpful descriptions stating they are advisory until full multi-heartbeat enforcement lands. Behaviour preserved (Layer 1 sensor reads heartbeats internally via `deep_heartbeat`); plist no longer crashes. |
| Antibody | Tracked: extend Layer 1 sensor (or a new wrapper script) to actually monitor BOTH `daemon-heartbeat.json` and `daemon-heartbeat-ingest.json` with the `--stale-threshold-seconds` argument honoured. |

## F3 — `world_schema_manifest.yaml` drifted from real columns

| Aspect | Detail |
|---|---|
| Severity | Phase 2 warn-only (Phase 3 promotion would FAIL boot) |
| Root cause | Manifest required columns that don't exist on the live tables: `solar_daily.date` (real column is `target_date`), `forecast_skill.brier` (real column is `error`), `observations.max_temp/min_temp` (real columns are `high_temp/low_temp`). |
| Symptom | `World schema validation FAILED (3 mismatches). Phase 3 will make this FATAL.` warning at every trading boot. |
| Detection | Phase 3 first boot warnings, then probed via `PRAGMA table_info(...)`. |
| Fix | `architecture/world_schema_manifest.yaml` updated for `solar_daily`, `forecast_skill`, and `observations` to match actual schema. `validate_world_schema_at_boot` now returns `True` (9 tables checked, 0 mismatches). |
| Antibody | Tracked: add a CI test that loads the manifest and `PRAGMA`s every column in the canonical zeus-world.db schema; fail the suite if either side drifts again. |

## F4 — Wallet read returned `$0.00` (real balance is `$199.40`)

| Aspect | Detail |
|---|---|
| Severity | LIVE BLOCKER |
| Root cause | TWO compounding gaps: (a) `_resolve_credentials()` resolved only signer + funder address; the v2 SDK's authenticated endpoints require L2 API creds (key/secret/passphrase) — without them every balance/order call returns `PolyException("API Credentials are needed to interact with this endpoint!")`. (b) `DEFAULT_V2_HOST = "https://clob-v2.polymarket.com"` was outdated; the host has been consolidated into `clob.polymarket.com` and now returns HTTP 301 (the v2 SDK does not follow redirects). |
| Sub-finding (cred drift) | The Keychain held a stale set of API creds (`api_key=019cdf86…`) that no longer matched what the active signer would produce (`derive_api_key` returns `ae7780b0…`). This is a data-provenance / second-source-of-truth hazard. |
| Symptom | Boot logged `Startup wallet check: $0.00 pUSD available` despite wallet holding $199.40. |
| Detection | User explicitly stated wallet held funds → escalated; reproduced via direct `_read_adapter_payload(adapter)` probe; ran `derive_api_key()` and compared against Keychain copy. |
| Fixes | (1) `DEFAULT_V2_HOST` → `https://clob.polymarket.com`. (2) `PolymarketV2Adapter._default_client_factory` now calls `client.set_api_creds(client.create_or_derive_api_key())` whenever no static creds are passed — deterministic derivation from the signer. (3) `_resolve_credentials()` deliberately stops resolving Keychain API creds; the auto-derive path is now canonical. (4) `get_collateral_payload` gracefully degrades when SDK lacks `get_positions` (which it does — positions live on the data-api host) so balance reads no longer fail closed on missing CTF enumeration. |
| Verification | Direct probe returned `pUSD balance: $199.40`. Phase 4 boot logged `Startup wallet check: $199.40 pUSD available`. |
| Antibody | Drift hazard removed at the structural level — Keychain L2 creds are no longer consulted, so they cannot drift away from the signer-derived creds again. Tracked: delete (or quarantine with a README) the now-unused `openclaw-polymarket-api-key/-secret/-passphrase` Keychain entries to remove the temptation of re-introducing them. |

## F5 — Venue heartbeat: `Invalid Heartbeat ID` (status 400) — RESOLVED

| Aspect | Detail |
|---|---|
| Severity | LIVE BLOCKER for resting orders (GTC/GTD); does NOT crash daemon |
| Root cause | Misuse of the Polymarket CLOB v2 chain-token heartbeat protocol. The supervisor was minting `uuid.uuid4().hex` once at construction and posting that fixed UUID forever. The actual protocol (verified live 2026-05-01 against `clob.polymarket.com`) is: `POST /v1/heartbeats` with `heartbeat_id=""` starts a session and the server returns its assigned UUID; each subsequent successful POST must echo the most recently returned id and the server **rotates** to a new id on the response. Any non-matching id is rejected with `400 Invalid Heartbeat ID` and the error body leaks the current canonical id. We therefore got perpetual rejection — our self-minted UUID never matched the server record. |
| Detection sequence | Direct probe: `post_heartbeat("")` → `{heartbeat_id: "dbbb2c0d-…"}`; `post_heartbeat("dbbb2c0d-…")` → `{heartbeat_id: "789be673-…"}`; `post_heartbeat("dbbb2c0d-…")` again → 400 (id rotated). Chain-token semantics not in py-clob-client-v2 docs but verbatim in the JS SDK (`@polymarket/clob-client` v5.8.2 `examples/postHeartbeat.ts`). |
| Symptom (pre-fix) | `_write_venue_heartbeat` raised `RuntimeError: venue heartbeat unhealthy: …Invalid Heartbeat ID` every 5s. APScheduler swallowed the exception so the daemon stayed alive, but heartbeat-supervised order paths (GTC/GTD) rejected. |
| Fix | `src/control/heartbeat_supervisor.py` initialises `_heartbeat_id = ""`, calls `adapter.post_heartbeat(self._heartbeat_id)`, captures `ack.raw["heartbeat_id"]` for the next tick on success, and resets to `""` on any failure so the next tick re-anchors the chain. |
| Antibody | `tests/test_heartbeat_supervisor.py::test_chain_token_protocol_rotation_and_failure_resets_to_empty` — asserts the exact submission sequence `["", "id-1", "id-2", ""]` across success/success/fail/recover. Locks the protocol contract at the unit-test layer. |
| Verification | (a) Standalone 5-tick run against live `clob.polymarket.com`: all HEALTHY, every tick rotated cleanly (`4c01a7e4 → 5bfcb192 → 44a30f62 → 3958ccd4 → d9c70ee0`), 0 failures. (b) Daemon load + 35 s observation window: 7 `_write_venue_heartbeat` jobs executed successfully, 0 `Invalid Heartbeat ID` errors, 0 `venue_heartbeat failed` errors. |

## Summary

| ID | Severity | Status | Antibody status |
|---|---|---|---|
| F1 | LIVE BLOCKER | FIXED (pip install) | TODO: import-probe at boot |
| F2 | LIVE BLOCKER | FIXED (argparse) | TODO: real multi-heartbeat enforcement |
| F3 | Phase 3 FATAL | FIXED (manifest sync) | TODO: CI manifest-vs-DB probe |
| F4 | LIVE BLOCKER | FIXED (auto-derive + host) | DONE structurally — keychain copy no longer consulted |
| F5 | LIVE BLOCKER (resting orders) | FIXED (chain-token protocol) | DONE — antibody test locks protocol contract |

After fixes, Phase 4 boot logged: wallet=$199.40 ✓, schema=passed ✓, freshness=STALE-degraded (expected, sources pre-existed staleness from prior session); only F5 noise remains in `logs/zeus-live.err`.
