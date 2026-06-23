# Runtime error audit — all live daemon logs (2026-06-23)

Created: 2026-06-23
Last audited: 2026-06-23
Authority basis: operator request "investigate all existing runtime error in log". Recency-classified
against today (2026-06-23) vs historical; severity by money-path impact.

Logs: /Users/leofitz/zeus/logs/*.{log,err}. stdout (.log) mostly clean; errors live in stderr (.err).
Cumulative ERROR/Traceback counts are since Jun 11 — what matters is ACTIVE TODAY.

## A. ACTIVE — money-path relevant

1. **Forecast coverage degradation — `insufficient Open-Meteo hourly samples inside target local day`**
   (zeus-forecast-live.err, ValueError, ACTIVE today). Many cities fail materialization: Beijing 74,
   Chengdu 46, Busan 46, Miami 44, Mexico City 44, Helsinki, London, … → NO posterior → NO belief →
   those cities produce NO entry candidates. Directly throttles "discovery across 1000+ markets".
   Root: Open-Meteo lacks enough hourly samples for the target local day for these cities/dates
   (external data gap or query-window mismatch). NEEDS INVESTIGATION (fallback provider or window fix).

2. **DB-lock contention dropping real work** (retryable but LOSSY, active today):
   - zeus-substrate-observer.err: `Executable market substrate refresh inserted no snapshots …
     coverage NONE … error: database is locked` (some ticks insert 0 snapshots → freshness gaps).
   - zeus-price-channel-ingest.err: `fill-bridge: could not persist settled disposition … database
     is locked` (96+ today) → settled fill dispositions not persisted (audit/identity gaps).
   - zeus-ingest.err: hourly observation insert `database is locked`.
   - zeus-live.err: `DAY0_ORACLE_ANOMALY_PERSIST_FAILED … db_writer_lock contended … pause holds
     in-process; will NOT survive a restart` (7). 
   WAL checkpoints fine; these are write contention on the 3 bloated DBs (~160GB). Mostly retried,
   but the substrate/fill-disposition drops have real downstream effect.

## B. ACTIVE — noise on STUCK GHOST positions (not active capital loss, but error spam + unhandled state)

3. **POISON projection rows — ChainState ENUM GAP** (riskguard-live.err 153×/load, post-trade-capital
   94×/load, active). `_position_from_projection_row` (src/state/portfolio.py:2639) coerces
   `chain_state` with an enum that LACKS `chain_absent_confirmed_position_unattributed`:
   - `src/state/chain_state.py` ChainState = {CHAIN_SYNCED, CHAIN_EMPTY, CHAIN_UNKNOWN} — missing it.
   - `src/contracts/semantic_types.py` ChainState HAS it (CHAIN_ABSENT_CONFIRMED_UNATTRIBUTED).
   - `src/state/chain_reconciliation.py` WRITES it.
   So every chain-absent-confirmed row poison-quarantines on EVERY load. Affected rows are ghosts /
   chain-absent: Dallas buy_yes 1184.57sh @0.002 (~$2.4 deep-OTM, chain-absent), Denver buy_no 17sh,
   Helsinki buy_no 5sh, Hong Kong / Seoul / Lucknow. Net effect: these are correctly EXCLUDED from
   risk/exit (they are not on-chain), but via a noisy ERROR rather than clean handling. FIX: align the
   loader's enum to recognize the chain-absent state and handle it as closed (then reconcile/clear the
   ghosts so they stop spamming). Hygiene + correctness, not a capital leak.

4. **BELIEF_AUTHORITY_FAULT — exit organ blind** (zeus-live.err 168× today, ONE position:
   Dallas 151c4e10-834 buy_yes; Milan 987d1b3c 6×). "stale belief for N cycles while market price is
   fresh — exit organ is blind on a live position." The Dallas one is the SAME deep-OTM ghost as (3)
   (chain-absent YES @0.002) — the monitor still treats it as live and cannot fetch fresh belief
   (Dallas forecast also degraded, see A1). Effect: error spam on a ghost; if it were a real live
   position it would be true exit-blindness. Resolve by clearing the ghost + the A1 forecast gap.

5. **DAY0_ORACLE_ANOMALY_COMPARISON_INCONCLUSIVE — wu_side_insufficient_coverage** (zeus-live.err,
   Lagos 157, Auckland 106, Wellington 103, Busan 58). Day0 settlement-anomaly check cannot get
   Weather-Underground observation coverage for these cities (NO_WU_SIDE) → inconclusive, retries.
   Settlement-verification data gap for specific cities.

6. **obs_live_tick WU ingest errors** (zeus-ingest.err: Lucknow/Toronto/Singapore/Shenzhen/Panama/
   Sao Paulo). Traceback → observation_instants_writer.insert_rows → _insert_revision (DB write
   contention class). WU observation rows not written for these ticks.

## C. RETRYABLE NOISE (handled, self-recovering)
- zeus-price-channel-ingest.err: `websockets ConnectionClosedError: keepalive ping timeout` (12) —
  EDLI market-channel disconnects and reconnects.
- zeus-post-trade-capital.err / venue: `py_clob_client request error: handshake/read timed out` —
  Polymarket API network timeouts, retried.

## D. RESOLVED / HISTORICAL (NOT active today)
- **ECMWF ENS extract `ModuleNotFoundError: tigge_local_calendar_day_common`** (215 historical,
  **0 today**). PYTHONSAFEPATH=1 stripped the script dir → sibling import failed in
  extract_open_ens_localday.py. Healed by the PYTHONPATH-injection deploy + forecast-live restart
  (~06-22 22:55). Confirm it stays at 0.
- **`OSError: [Errno 28] No space left on device`** (venue-heartbeat, 06-22). Disk now 86% used,
  128 GiB free — not currently critical, but the 3 split DBs are ~160 GB; WATCH (prune organ).
- **Venue heartbeat LOST/`service not ready` storm** (774+434, venue-heartbeat.err) — last entry
  06-22 15:58; daemon alive (pid 1379), no recent failures → venue API recovered.

## Priority for fixing
1. A1 forecast coverage (insufficient Open-Meteo) — money-path: restores belief/discovery for missing cities.
2. B3 ChainState enum gap + ghost reconcile — stops 250+/load error spam, cleans B4 too.
3. A2 DB-lock contention — DB prune/WAL tuning to stop substrate/fill-disposition drops.
4. D disk watch.
