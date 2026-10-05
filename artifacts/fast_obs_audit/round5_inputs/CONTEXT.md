# Round 5 context (operator-side, measured 2026-10-05 ~09:00Z, live = 4fc499647)

## What is on live now (verified read-only)
- Fast routes landed on live (commits d153a99af MGM Ankara/Istanbul, 38749c275+0a7159871 IMD Lucknow, 775eec60d role split, 6572c524d/d467fe087 registry speed-proof validation). Moscow UUWW route withdrawn (plaintext HTTP).
- live_route_prints_24h.json: every (station, source_channel) that wrote WORLD.observation_prints in the last 24h (165 rows). Non-reference channels present: mgm_metar_temperature, imd_olbs_metar_temperature, jma_amedas_temperature, eccc_swob_temperature, hko_current_1min_mean, hko_rhrread_spot, dwd_cdc_temperature, fmi_airport_temperature, imgw_synop_temperature, wu_station_current_temperature, wu_station_history_temperature, wu_icao_history.
- KMA (Seoul RKSI / Busan RKPK) does not write observation_prints; it writes WORLD.opportunity_events with payload observation_transport='kma_amo_raw_metar' (524 such events in the last 200k rows). It is live.

## Order lifecycle (read-only audit of live trades DB, this morning)
Tool: audit_order_lifecycle.py (yours from REQ-20260929-170443, restored from archive, unchanged), run with --source-ref 4fc499647. Outputs: summary.json, order_lifecycles.csv.gz (5,352 commands), read_only_queries.sql.
Counts: ENTRY CANCELLED 1900 / EXPIRED 634 / FILLED 1655 / REJECTED 115 / SUBMIT_REJECTED 52; EXIT CANCELLED 44 / EXPIRED 43 / FILLED 803 / REJECTED 100 / SUBMIT_REJECTED 6. Positions: settled 3456, voided 1718, NO_POSITION_RECORD 151, day0_window 16, active 4, admin_closed 4, economically_closed 3.
Flags: fill_evidence_conflicts 6, POSITION_PROJECTION_EVENT_PHASE_MISMATCH 3, BOUND_SNAPSHOT_MISSING 3.

Operator-side follow-up on the 6 conflicts (confirmed trade shares vs position_current shares):
| command | cmd state | confirmed trade shares | position phase | position shares | chain_shares |
|---|---|---:|---|---:|---:|
| 086d130a613546f2 | CANCELLED | 2.5 | voided | 0.0 | None |
| 37c227a8a0f24596 | CANCELLED | 5.0 | voided | 0.0 | None |
| 37e80adb681a416b | EXPIRED | 38.0 | voided | 0.0 | None |
| 79e6322ae8344a92 | FILLED | 39.6 | settled | 19.6 | 19.6 |
| 19a58c0a03d8416d | CANCELLED | 28.52 | settled | 28.52 | 28.52 |
| 5c25a7b16af841b6 | CANCELLED | 25.71 | settled | 25.71 | 25.71 |
Three VOIDED positions carry CONFIRMED fills with zero recorded shares, and one settled position records half its confirmed shares. Trade CONFIRMED facts for the June ones were observed 2026-07-13T22:02Z (late reconciliation). Phase mismatches: 0e0ac1edba2e4619 + cad0050955ae4cf7 (same position d3840f5b, voided), 59fc7867387b4f04. Snapshot-missing: three adopted_exit_* commands.

## Latency
Only the controlled harness exists (ROUND3.md: receipt→ACK 101–221 ms, single samples, fake venue). No production distribution has been measured.
