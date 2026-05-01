# Reality Calibration Evidence

Created: 2026-04-27
Authority basis: live SQL probes + external WebFetch (polymarket.com, ecmwf.int) on 2026-04-27
Status: evidence appendix to packet plan.md

---

## 1. Disk probes (state/zeus-world.db, state/zeus_trades.db)

All counts probed at 2026-04-27 ~14:00 UTC.

### state/zeus-world.db

| Table | Rows | Note |
|---|---|---|
| settlements | 1,561 | 1469 VERIFIED + 92 QUARANTINED, 100% temperature_metric=high |
| observations | 42,749 | 42,743 VERIFIED, 39,431 with empty provenance_metadata (99% of WU rows) |
| observation_instants | 873,561 | legacy hourly |
| observation_instants_v2 | 1,813,662 | INV-14 spine columns 100% NULL |
| ensemble_snapshots | 0 | legacy |
| ensemble_snapshots_v2 | 0 | TIGGE not ingested |
| calibration_pairs | 0 | legacy |
| calibration_pairs_v2 | 0 | gated on P4 |
| platt_models | 0 | |
| platt_models_v2 | 0 | |
| settlements_v2 | 0 | |
| market_events | 0 | F13 |
| market_events_v2 | 0 | F13 |
| market_price_history | 0 | F13 |
| historical_forecasts | 0 | |
| historical_forecasts_v2 | 0 | |
| forecasts | 23,466 | NEW since handoff (was 0); k2_forecasts_daily succeeded |
| data_coverage | 350,088 | tracks legacy tables only |

### state/zeus_trades.db

All tables 0 rows (post-cutover, pre-restart).

### `forecasts` deeper structure

```
columns: id, city, target_date, source, forecast_basis_date, forecast_issue_time,
         lead_days, lead_time_hours, forecast_high, forecast_low, temp_unit,
         retrieved_at, imported_at, rebuild_run_id, data_source_version,
         source_id, raw_payload_hash, captured_at, authority_tier
```

| Source | Rows |
|---|---|
| openmeteo_previous_runs | 4,998 |
| gfs_previous_runs | 4,998 |
| ecmwf_previous_runs | 4,998 |
| icon_previous_runs | 4,284 |
| ukmo_previous_runs | 4,188 |

| Field | Sample value |
|---|---|
| forecast_issue_time | None (every row) |
| raw_payload_hash | None (every row) |
| captured_at | None (every row) |
| authority_tier | None (every row) |
| target_date range | 2026-04-21 → 2026-05-04 |
| forecast_basis_date range | 2026-04-14 → 2026-05-03 |
| distinct cities | 51 |

### `settlements.settlement_source` distribution (top 20)

```
60  https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA
59  https://www.wunderground.com/history/daily/us/tx/dallas/KDAL
59  https://www.wunderground.com/history/daily/us/ga/atlanta/KATL
59  https://www.wunderground.com/history/daily/kr/incheon/RKSI
59  https://www.wunderground.com/history/daily/ca/mississauga/CYYZ
58  https://www.wunderground.com/history/daily/us/wa/seatac/KSEA
58  https://www.wunderground.com/history/daily/ar/ezeiza/SAEZ
57  https://www.wunderground.com/history/daily/gb/london/EGLC
56  https://www.wunderground.com/history/daily/us/il/chicago/KORD
56  https://www.wunderground.com/history/daily/us/fl/miami/KMIA
56  https://www.wunderground.com/history/daily/nz/wellington/NZWN
56  https://www.wunderground.com/history/daily/fr/paris/LFPG
56  https://www.wunderground.com/history/daily/br/guarulhos/SBGR
55  https://www.wunderground.com/history/daily/tr/%C3%A7ubuk/LTAC
41  https://www.wunderground.com/history/daily/in/lucknow/VILK
41  https://www.wunderground.com/history/daily/de/munich/EDDM
36  https://www.wunderground.com/history/daily/jp/tokyo/RJTT
33  https://www.wunderground.com/history/daily/sg/singapore/WSSS
33  https://www.wunderground.com/history/daily/cn/shanghai/ZSPD
30  https://www.wunderground.com/history/daily/pl/warsaw/EPWA
```

→ **1400+ rows are WU URLs**, including all major US cities.

### `observations` empty-provenance distribution by source

```
wu_icao_history       : 39,437 rows, 39,431 empty (99%)
ogimet_metar_uuww     :    837 rows,      0 empty (0%)
ogimet_metar_llbg     :    837 rows,      0 empty (0%)
hko_daily_api         :    821 rows,      0 empty (0%)
ogimet_metar_ltfm     :    756 rows,      0 empty (0%)
ogimet_metar_vilk     :     59 rows,      0 empty (0%)
ogimet_metar_fact     :      2 rows,      0 empty (0%)
```

→ **The pattern is: WU writer never stamped provenance, others always did.** Mono-source defect.

### `raw/oracle_shadow_snapshots/` reality

- 480 files = 48 cities × 10 dates
- Date range: **2026-04-15 to 2026-04-26**
- Each file: `{city, target_date, captured_at_utc, station_id, source, daily_high_f, observation_count, data_version, wu_raw_payload}`
- `wu_raw_payload` contains full WU API response with `observations[]` array (40 obs/day typical), full station metadata
- This IS a viable provenance source going forward (matches forensic Fix 4.3.B requirements: payload hash + source URL + parser version derivable)

### Settlements vs Oracle-Shadow temporal overlap

```
settlements:    2025-12-30 to 2026-04-16  (61 distinct dates)
observations:   2023-12-27 to 2026-04-19
oracle_shadow:  2026-04-15 to 2026-04-26  (10 distinct dates)
```

→ Overlap with empty-provenance WU rows: **~96 rows (2 dates × 48 cities)** = 0.24% of 39,431.

---

## 2. External probes

### 2.1 Polymarket (polymarket.com, docs.polymarket.com)

Verified 2026-04-27 via WebFetch.

- 361 live temperature prediction markets
- Each market has resolution source/criteria in Rules section, **fixed at market creation**
- ⚠ ~~US markets: NOAA stations are most common resolution source (NOT WU)~~ — **RETRACTED 2026-04-27**: 4 US markets verified verbatim (NYC/Chicago/Miami/LA) all use **Wunderground** (KLGA/KORD/KMIA/KLAX). See [04 §C3](04_corrections_2026-04-27.md#c3-polymarket-us-weather-market-resolution-source) for verbatim quotes.
- CLOB API has `getTrades` / `getTradesPaginated` for trade history
- WebSocket "Market Channel" provides real-time orderbook + price + lifecycle updates
- Trade objects include: id, size, price, status, match_time, transaction_hash, trader_side (TAKER/MAKER), maker_orders[], `fee_rate_bps`
- `neg_risk` field exists on multi-outcome markets; uses separate "Neg Risk CTF Exchange" contract
- ⚠ ~~No public archive API for orderbook snapshots~~ — **CORRECTED 2026-04-27**: 4 layers exist (Gamma API, public Subgraph with 6 sub-subgraphs incl. `orderbook-subgraph`, Data API REST `/trades`, WebSocket Market Channel). Trade history retrievable via subgraph and Data API. Orderbook-snapshot retention at arbitrary timestamps still unverified (04 §3 U4). See [04 §C4](04_corrections_2026-04-27.md#c4-polymarket-no-public-historical-archive-api).
- Settlement via Conditional Token Framework (CTF) split/merge/redeem; resolution oracle is documented per-market
- Maker rebate program exists; daily USDC earnings; "Get current rebated fees for a maker" endpoint

### 2.2 ECMWF ENS (www.ecmwf.int)

Verified 2026-04-27 via WebFetch.

- ENS dissemination lag is approximately **40-41 minutes** after base time (00 / 06 / 12 / 18 UTC)
- Derived products (long lead, steps 246-360) add ~20 minute delay
- Total members: **50 perturbed + 1 control + 1 HRES = 52** (Zeus assumes 51 in places — minor discrepancy worth checking)
- Authoritative `available_at` is the dissemination time, not the base time
- No deterministic release-time formula published, but the empirical 40-min lag is industry-standard for downstream consumers

---

## 3. Code probes

### `src/engine/replay.py` (line counts as of 2026-04-27)

```
Total: 2382 lines
Public functions/classes:
  L38   BACKTEST_AUTHORITY_SCOPE = "diagnostic_non_promotion"
  L42   DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({...})
  L49   class ReplayPreflightError
  L57   class ReplayDecision
  L77   class ReplayOutcome
  L101  class ReplaySummary
  L124  def _market_price_linkage_limitations
  L156  def _missing_parity_dimensions    # hardcoded False for sizing+selection
  L167  def _replay_provenance_limitations
  L200  class TradeSubjectCandidate
  L208  class TradeHistorySubject
  L224  class ReplayContext
  L661  def get_market_price          # returns nan
  L1622 def _assert_market_events_ready_for_replay  # F13 antibody
  L1721 def run_wu_settlement_sweep   # WU_SWEEP_LANE
  L1992 def run_trade_history_audit
  L2145 def run_replay
```

### Authority scope grep

```
src/state/db.py:1397       authority_scope = 'diagnostic_non_promotion'
src/state/db.py:1438-1439  CHECK constraint enforcing diagnostic_non_promotion
src/engine/replay.py:38    BACKTEST_AUTHORITY_SCOPE = "diagnostic_non_promotion"
src/engine/replay.py:1521  _insert_backtest_run uses BACKTEST_AUTHORITY_SCOPE
src/engine/replay.py:1590  _insert_backtest_outcome uses BACKTEST_AUTHORITY_SCOPE
src/engine/replay.py:1767  WU_SWEEP_LANE limitations.authority_scope
src/engine/replay.py:2004  TRADE_HISTORY_LANE limitations.authority_scope
tests/test_backtest_outcome_comparison.py:193,204  asserts diagnostic_non_promotion
tests/test_topology_doctor.py:1381,2863,2883  topology rules
tests/test_run_replay_cli.py:855,856  asserts active_sizing_parity / selection_family_parity in missing
```

→ The scope is consistently enforced; the design upgrade keeps it in place.
