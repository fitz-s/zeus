# Karachi 2026-05-17 — Manual Settlement Fallback Runbook

Date prepared: 2026-05-16
Worktree: `.claude/worktrees/zeus-deep-alignment-audit-skill`
Scope: READ-ONLY plan, **no DB writes performed by this session**. All write
steps below are operator-gated. Pre-condition for invocation: auto-cascade
(`harvester_truth_writer → harvester_pnl_resolver → _settle_positions →
clob.redeem`) has failed to settle Karachi 2026-05-17 by **2026-05-18 04:00
UTC** (T+9 h after Polymarket endDate).

---

## 1. Position Snapshot

| Field | Value |
| --- | --- |
| position_id | `c30f28a5-d4e` |
| city | Karachi |
| target_date | 2026-05-17 (Asia/Karachi, UTC+5) |
| bin_label | "Will the highest temperature in Karachi be 37°C or higher on May 17?" |
| direction | buy_yes |
| shares | **1.5873 YES** |
| cost_basis_usd | **$0.5873** |
| entry_price | 0.37 |
| p_posterior | 0.8808 |
| phase | active |
| chain_state | synced |
| condition_id | `0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae` |
| YES token_id | `53911939967084927688315552226298819187226280922490512356766442155641045757884` |
| NO token_id  | `28680599703412232019829080383667165289158013485134429355774085136330386508715` |
| Polymarket event id | 486870 |
| Polymarket slug | `highest-temperature-in-karachi-on-may-17-2026` |
| Polymarket endDate | 2026-05-17T12:00:00Z (=17:00 PKT) |
| Live market YES price | 0.415 (probed 2026-05-16 @ Gamma) |
| **Max payoff if YES wins** | $1.5873 |
| **Loss if YES loses** | $0.5873 |
| Sibling voided position | `7211b1c5-d3b`, shares=0, status canceled 2026-05-15 18:48 UTC |

Dollar exposure is **negligible** ($0.59). This runbook's value is procedural,
not capital-protective.

---

## 2. Canonical Truth Source

`config/cities.json` Karachi entry:

```json
{
  "name": "Karachi",
  "wu_station": "OPKC",
  "lat": 24.9065, "lon": 67.1608,
  "airport_name": "Jinnah International Airport",
  "country_code": "PK",
  "settlement_source": "https://www.wunderground.com/history/daily/pk/karachi/OPKC",
  "timezone": "Asia/Karachi",
  "unit": "C",
  "noaa": null,
  "historical_peak_hour": 15.0
}
```

→ Canonical source family: **`wu_icao`**
   (`src/ingest/harvester_truth_writer.py:_HARVESTER_LIVE_DATA_VERSION["wu_icao"] = "wu_icao_history_v1"`).
→ Stored `settlement_source` text for VERIFIED rows:
   `https://www.wunderground.com/history/daily/pk/karachi/OPKC`.
→ Observation row must satisfy `observations.source` matching the `wu_icao`
   family AND `authority='VERIFIED'` AND `station_id='OPKC'`
   (`_source_matches_settlement_family`, `_station_matches_city`).

NOAA is `null` for Karachi → cannot cross-check via NOAA. Open-Meteo
archive at `lat=24.9065,lon=67.1608` is used by the ingest daemon for
calibration/diagnostics (not for settlement authority).

---

## 3. T-X Timeline Runbook

All times UTC unless suffixed PKT (PKT = UTC+5).

### T-12 h — 2026-05-17 00:00 UTC (= 05:00 PKT)

Read-only health checks (no writes):

```bash
# 1. confirm position is still active
sqlite3 -readonly state/zeus_trades.db "
  SELECT position_id, phase, shares, chain_state, order_status, updated_at
  FROM position_current WHERE position_id='c30f28a5-d4e';"

# 2. confirm both daemons live and HARVESTER flag ON
launchctl list | grep -E 'com.zeus.(data-ingest|live-trading)'
/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:ZEUS_HARVESTER_LIVE_ENABLED" \
  ~/Library/LaunchAgents/com.zeus.data-ingest.plist

# 3. confirm settlements_v2 has recent VERIFIED rows for other cities
sqlite3 -readonly state/zeus-forecasts.db "
  SELECT MAX(settled_at), COUNT(*) FROM settlements_v2
  WHERE authority='VERIFIED' AND settled_at >= datetime('now','-24 hours');"
```

Gate: if (1) shares != 1.5873 OR (2) flag != 1 OR (3) zero recent VERIFIED
rows → **escalate before doing anything else**.

### T-2 h — 2026-05-17 10:00 UTC (= 15:00 PKT) — peak heat already past

Karachi historical_peak_hour = 15:00 PKT = 10:00 UTC. By this point the
observed daily high is known to ground stations but Wunderground may not yet
have published the closed daily summary.

```bash
# Probe Wunderground page — manual eyeball; no API key required
open https://www.wunderground.com/history/daily/pk/karachi/OPKC/date/2026-5-17

# Probe Polymarket (read-only)
python3 - <<'PY'
import httpx, json
r = httpx.get("https://gamma-api.polymarket.com/events",
              params={"slug": "highest-temperature-in-karachi-on-may-17-2026"}, timeout=20.0)
ev = r.json()[0]
for m in ev["markets"]:
    if "37" in m["question"]:
        print(m["question"], "outcomePrices=", m["outcomePrices"],
              "closed=", m["closed"], "uma=", m.get("umaResolutionStatus"))
PY
```

Gate: if YES outcomePrice ≥ 0.95 → winning bin almost certainly 37°C
or higher. If YES ≤ 0.05 → losing. Mixed → wait for endDate.

### T-0 — 2026-05-17 12:00 UTC (= 17:00 PKT) — Polymarket endDate

Market enters UMA resolution; typical resolution lag 1-3 h. **Do nothing
yet** — the auto cascade should run inside this window.

### T+1 h — 2026-05-17 13:00 UTC

```bash
# auto-cascade probe
sqlite3 -readonly state/zeus-forecasts.db "
  SELECT settlement_id, winning_bin, settlement_value, settled_at, authority
  FROM settlements_v2
  WHERE city='Karachi' AND target_date='2026-05-17';"
```

If a row exists with `authority='VERIFIED'` → auto path worked. Verify
position transition:

```bash
sqlite3 -readonly state/zeus_trades.db "
  SELECT event_type, occurred_at, json_extract(payload_json,'$.phase_after') AS phase
  FROM position_events
  WHERE position_id='c30f28a5-d4e'
  ORDER BY rowid DESC LIMIT 5;"
```

Look for a `SETTLED` event after T-0. Done — stop here.

### T+3 h — 2026-05-17 15:00 UTC

Still no settlements_v2 row? Investigate before falling back:

```bash
# 1. last harvester tick result
grep harvester_truth_writer_tick logs/zeus-ingest.err | tail -5
# 2. wall-cap warnings in last hour
grep -E 'wall-cap|database is locked' logs/zeus-ingest.err | tail -20
# 3. confirm Polymarket has resolved (umaResolutionStatus != None)
python3 - <<'PY'
import httpx
r=httpx.get("https://gamma-api.polymarket.com/events",
            params={"slug":"highest-temperature-in-karachi-on-may-17-2026"},timeout=20)
ev=r.json()[0]; print("closed=",ev["closed"],"endDate=",ev["endDate"])
for m in ev["markets"]:
    if "37" in m["question"]:
        print("  ", m["question"], "umaResolutionStatus=",m.get("umaResolutionStatus"),
              "outcomePrices=",m["outcomePrices"])
PY
```

If Polymarket has NOT resolved yet (umaResolutionStatus pending) → continue
to wait. If resolved but settlements_v2 still empty → proceed to T+9 fallback.

### T+9 h — 2026-05-18 04:00 UTC (= 09:00 PKT) — manual fallback

Pre-flight (all read-only, all four must pass):

```bash
# 1. Polymarket has resolved
python3 -c "import httpx; ev=httpx.get('https://gamma-api.polymarket.com/events',
  params={'slug':'highest-temperature-in-karachi-on-may-17-2026'}).json()[0];
  assert ev['closed'] is True, 'Polymarket not yet closed'; print('OK closed')"

# 2. forecasts.db not currently locked
sqlite3 -readonly state/zeus-forecasts.db "BEGIN IMMEDIATE; ROLLBACK;" \
  && echo "OK forecasts.db writable" || echo "LOCK HELD — abort"

# 3. observations table has a wu_icao Karachi 2026-05-17 row, authority=VERIFIED
sqlite3 -readonly state/zeus-forecasts.db "
  SELECT source, station_id, authority, high_temp, fetched_at
  FROM observations
  WHERE city='Karachi' AND target_date='2026-05-17'
    AND station_id='OPKC'
  ORDER BY fetched_at DESC LIMIT 5;"

# 4. position is still 'active' (not already settled by a concurrent path)
sqlite3 -readonly state/zeus_trades.db "
  SELECT phase FROM position_current WHERE position_id='c30f28a5-d4e';"
# expected: 'active'
```

Gate: all four must pass. If (3) returns 0 rows or authority != VERIFIED →
fall through to **3.A Manual observation fetch**, otherwise jump to **3.B
Invoke sanctioned backfill**.

#### 3.A Manual observation fetch (only if observations.observations is missing)

Operator action; **NOT** automated:

1. Open `https://www.wunderground.com/history/daily/pk/karachi/OPKC/date/2026-5-17`.
2. Read the "High" temperature from the day summary table (°C).
3. Record the snapshot: page URL, value, screen-shot, retrieval timestamp.
4. Do **NOT** hand-insert into `observations`. Instead, kick the ingest job
   that pulls WU history:
   ```bash
   # Operator-attended; runs in the ingest daemon's venv
   /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m \
     scripts.backfill_observations_from_settlements --city Karachi \
     --date 2026-05-17 --source wu_icao
   ```
   Wait ~30 s, re-run pre-flight check (3); if still empty, escalate.
   The reason for using the ETL path is that `observations` rows are
   authority-typed and provenance-tagged by the ETL; a bare INSERT would
   create UNVERIFIED data and `_lookup_settlement_obs` would still reject it.

#### 3.B Invoke sanctioned backfill

```bash
# Operator-attended, single shot; max wall-cap 900 s.
# This is the ONLY sanctioned unbounded paginator (PLAN §10 antibody).
cd /Users/leofitz/.openclaw/workspace-venus/zeus
ZEUS_HARVESTER_LIVE_ENABLED=1 \
  .venv/bin/python -m scripts.backfill_harvester_settlements --days 3
```

What happens:
1. Script paginates Gamma `/events?closed=true` ascending from now with no
   30-day cutoff, 900 s wall-cap.
2. For every event whose slug aliases to Karachi and target_date=2026-05-17,
   calls `write_settlement_truth_for_open_markets` (idempotent — `INSERT OR
   IGNORE` against `market_events_v2`; `_write_settlement_truth` writes
   only if no existing VERIFIED row).
3. Returns counts.

Verify the cascade fired:

```bash
sqlite3 -readonly state/zeus-forecasts.db "
  SELECT settlement_id, winning_bin, settlement_value, settled_at, authority
  FROM settlements_v2
  WHERE city='Karachi' AND target_date='2026-05-17';"

# expect: one row, authority='VERIFIED'

sqlite3 -readonly state/zeus_trades.db "
  SELECT phase, shares FROM position_current WHERE position_id='c30f28a5-d4e';"

# expect within 1-5 min: phase='settled' (driven by harvester_pnl_resolver
# inside live-trading daemon loop)
```

If `position_current.phase` does not transition to `settled` within
10 min, the live-trading daemon is not running the resolver loop. Force a
single resolver tick:

```bash
# Operator gate: confirm trade DB writable and live-trading daemon healthy
cd /Users/leofitz/.openclaw/workspace-venus/zeus
ZEUS_HARVESTER_LIVE_ENABLED=1 \
  .venv/bin/python -c "
from src.state.db import get_trade_connection, get_forecasts_connection
from src.execution.harvester_pnl_resolver import resolve_pnl_for_settled_markets
print(resolve_pnl_for_settled_markets(get_trade_connection(), get_forecasts_connection()))
"
```

### T+12 h — 2026-05-18 07:00 UTC — redemption verification

```bash
# Verify on-chain redeem fired (only if YES won)
sqlite3 -readonly state/zeus_trades.db "
  SELECT event_type, occurred_at,
         json_extract(payload_json,'$.tx_hash') AS tx,
         json_extract(payload_json,'$.gas_used') AS gas
  FROM position_events
  WHERE position_id='c30f28a5-d4e' AND event_type LIKE '%REDEEM%'
  ORDER BY rowid DESC LIMIT 3;"
```

If YES won and no REDEEM event: USDC is **claimable later** (per
`harvester.py:938` "Redeem failed for %s: %s (USDC still claimable later)").
Manual `clob.redeem` can be invoked from the live-trading shell at any
later operator-attended window — no time pressure.

---

## 4. Pre-Deadline Checkpoint Matrix

| Time | Confirm | Action if FAIL |
| --- | --- | --- |
| T-12 h | position phase=active, daemons up, flag=1 | escalate; do not advance |
| T-2 h | live market price reasonable, daemons still up | escalate |
| T-0 | (passive) Polymarket endDate passed | none |
| T+1 h | settlements_v2 Karachi row exists VERIFIED | wait until T+3 h |
| T+3 h | row exists OR umaResolutionStatus pending | continue waiting |
| T+9 h | row exists | execute 3.A / 3.B above |
| T+10 h | position phase=settled | force resolver tick |
| T+12 h | REDEEM event exists (if YES won) | manual redeem later |

---

## 5. Rollback / Double-Settlement Safety

Code-level guards that already exist (audit-confirmed):

- `settlements_v2 UNIQUE(city, target_date, temperature_metric)` (schema
  default; see `PRAGMA table_info`). Second write attempt raises
  `IntegrityError`; `write_settlement_truth_for_open_markets` catches and
  logs, **does not corrupt**.
- `_write_settlement_truth` reads existing VERIFIED row first; will not
  overwrite. Same row written twice = no-op.
- `_settle_positions` is keyed by `(city, target_date, winning_bin)` and
  checks `position_current.phase`. A position already in phase `settled`
  is skipped. Double-settle = no-op.
- `clob.redeem(condition_id)` is idempotent on-chain (Polymarket conditional
  token: only the first redeem of a given condition_id by a given holder
  burns the tokens; subsequent attempts revert harmlessly). The cost is
  ~$0.02 gas wasted.

Operator-level safety:

- **Do not** manually `INSERT` into `settlements_v2`. Always go through
  `write_settlement_truth_for_open_markets` or the backfill script —
  those set `authority`, `provenance_json`, and trigger the cascade.
- **Do not** manually `UPDATE position_current.phase`. Always let
  `_settle_positions` drive the transition so `position_events` audit log
  remains consistent.
- If a manual SQL write becomes unavoidable (e.g. settlement_value typo),
  perform it inside a sentinel transaction:
  ```sql
  BEGIN IMMEDIATE;
  -- inspect
  SELECT * FROM settlements_v2 WHERE city='Karachi' AND target_date='2026-05-17';
  -- only proceed after confirming
  ROLLBACK;   -- replace with COMMIT only after operator sign-off
  ```

Recovery if 3.B fires after auto-path has already written:

- `INSERT OR IGNORE` on `market_events_v2` makes the duplicate observation
  load harmless.
- `_write_settlement_truth` skip-on-existing-VERIFIED makes the settlements
  write harmless.
- `_settle_positions` skip-on-already-settled makes the position write
  harmless.
- Net effect of running 3.B unnecessarily: ~30-900 s of CPU and one extra
  Gamma API quota burn. **No correctness risk.**

---

## 6. Approval Gates Summary

| Gate | Required before … | Operator check |
| --- | --- | --- |
| G1 | running step 3.A | Wunderground HTML high temp recorded, screenshot taken |
| G2 | running step 3.B | pre-flight items (1)-(4) all OK; T ≥ +9 h confirmed |
| G3 | force resolver tick | live-trading daemon NOT actively writing; trade DB writable |
| G4 | manual redeem retry | accept ~$0.02 gas waste; condition_id matches position |
| G5 | any SQL write | sentinel BEGIN/ROLLBACK; operator sign-off required for COMMIT |

---

## 7. Files / Modules Touched (Read-Only Reference)

| Path | Role |
| --- | --- |
| `src/ingest/harvester_truth_writer.py` | live truth writer; `_fetch_open_settling_markets`, `_write_settlement_truth`, `write_settlement_truth_for_open_markets` |
| `src/execution/harvester_pnl_resolver.py` | trading-side P&L resolver; `resolve_pnl_for_settled_markets` |
| `src/execution/harvester.py` | `_settle_positions`, `clob.redeem` cascade |
| `src/data/market_scanner.py` | `_match_city`, `_parse_target_date`, `_parse_temp_range`, `GAMMA_BASE` |
| `scripts/backfill_harvester_settlements.py` | sanctioned operator backfill (PLAN §10) |
| `scripts/backfill_observations_from_settlements.py` | observation ETL refill |
| `config/cities.json` | Karachi station + URL definition |
| `state/zeus-forecasts.db` | `settlements`, `settlements_v2`, `observations`, `market_events_v2` |
| `state/zeus_trades.db` | `position_current`, `position_events` |
| `~/Library/LaunchAgents/com.zeus.data-ingest.plist` | data daemon; `ZEUS_HARVESTER_LIVE_ENABLED=1` |
| `logs/zeus-ingest.err` | harvester tick history (sole stdout/err target; the `.log` is empty) |

End of runbook.
