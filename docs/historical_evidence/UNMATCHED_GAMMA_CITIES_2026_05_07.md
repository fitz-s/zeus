# Unmatched Gamma Cities — 2026-05-07

**Source**: `scripts/backfill_settlements_via_gamma_2026.py` dry-run, 2026-02-21..2026-05-07
**Total events scanned**: 2 709 temperature events
**Unmatched (`skipped_no_city`)**: 13

---

## Category 1 — Global anomaly markets (not city markets; do NOT add to cities.json)

These two events use the title "Temperature Increase (°C)" and ask about **global mean temperature
anomaly** (IPCC/NOAA monthly index). They have no city; `_match_city()` correctly returns None.
No action required.

| Slug | Metric | Date | Markets | Sample question |
|---|---|---|---|---|
| `february-2026-temperature-increase-c` | high | 2026-03-10 | 6 | Will global temperature increase by between 1.15°C and 1.19°C in February 2026? |
| `march-2026-temperature-increase-c` | high | 2026-04-10 | 6 | Will global temperature increase by between 1.10°C and 1.14°C in March 2026? |

**Operator decision**: these are global-anomaly markets; exclude permanently. No cities.json entry.

---

## Category 2 — Qingdao, China (11 HIGH markets, 2026-04-27..2026-05-07)

Polymarket launched Qingdao (青岛) HIGH temperature markets starting 2026-04-27. No alias/slug for
Qingdao exists in `config/cities.json` → all 11 dates are silently skipped.

| Slug | Metric | Date | Markets | Sample question |
|---|---|---|---|---|
| `highest-temperature-in-qingdao-on-april-27-2026` | high | 2026-04-27 | 11 | Will the highest temperature in Qingdao be 4°C or below on April 27? |
| `highest-temperature-in-qingdao-on-april-28-2026` | high | 2026-04-28 | 11 | Will the highest temperature in Qingdao be 14°C or below on April 28? |
| `highest-temperature-in-qingdao-on-april-29-2026` | high | 2026-04-29 | 11 | Will the highest temperature in Qingdao be 17°C or below on April 29? |
| `highest-temperature-in-qingdao-on-april-30-2026` | high | 2026-04-30 | 11 | Will the highest temperature in Qingdao be 20°C or below on April 30? |
| `highest-temperature-in-qingdao-on-may-1-2026` | high | 2026-05-01 | 11 | Will the highest temperature in Qingdao be 23°C or below on May 1? |
| `highest-temperature-in-qingdao-on-may-2-2026` | high | 2026-05-02 | 11 | Will the highest temperature in Qingdao be 21°C on May 2? |
| `highest-temperature-in-qingdao-on-may-3-2026` | high | 2026-05-03 | 11 | Will the highest temperature in Qingdao be 11°C or below on May 3? |
| `highest-temperature-in-qingdao-on-may-4-2026` | high | 2026-05-04 | 11 | Will the highest temperature in Qingdao be 19°C or below on May 4? |
| `highest-temperature-in-qingdao-on-may-5-2026` | high | 2026-05-05 | 11 | Will the highest temperature in Qingdao be 20°C or below on May 5? |
| `highest-temperature-in-qingdao-on-may-6-2026` | high | 2026-05-06 | 11 | Will the highest temperature in Qingdao be 27°C on May 6? |
| `highest-temperature-in-qingdao-on-may-7-2026` | high | 2026-05-07 | 11 | Will the highest temperature in Qingdao be 16°C or below on May 7? |

**Temperature unit**: °C (Celsius, inferred from bin labels).
**Slug pattern**: `highest-temperature-in-qingdao-on-*`

### RESOLVED 2026-05-07

Station ZSJG (Jiaodong International, candidate) rejected 400 by WU API.
Station ZSQD (Liuting Airport) confirmed live: 7/7 smoke days with real daily highs.

Actions taken:
- Added Qingdao to `config/cities.json` with `wu_station: "ZSQD"`, unit C, tier WU_ICAO
- WU daily backfill: 11 obs rows written (2026-04-27..2026-05-07)
- Gamma backfill: 11 settlements_v2 rows, all authority=VERIFIED
- Commit: 8fad09e7 (cities.json + test count updates)
