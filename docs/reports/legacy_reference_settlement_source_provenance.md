# Settlement Source Provenance Registry

> LEGACY REFERENCE SNAPSHOT - historical analytical/support material.
> Not a canonical reference. Not default-read.
> Use the canonical reference in `docs/reference/` first.

> **Purpose**: Durable reference record for which data source Polymarket uses
> to settle each city's daily temperature market. Polymarket can and does change
> sources without notice.
>
> **Last full audit**: 2026-04-17 (Gamma API tag_id=103040, 1721 events)
>
> Authority status: reference evidence only. Settlement code, current
> configuration, tests, and `docs/authority/**` win on disagreement. Compact
> routing lives in `docs/reference/zeus_market_settlement_reference.md`.

## Current Settlement Sources (as of 2026-04-17)

### Stable WU Cities (44 cities — no source changes observed)

All settle via Weather Underground ICAO station data.

| City | WU Station | Airport Name | Unit | First Event |
|------|-----------|--------------|------|-------------|
| Amsterdam | EHAM | Amsterdam Airport Schiphol | C | 2026-04-03 |
| Ankara | LTAC | Esenboğa Intl Airport | C | 2026-02-18 |
| Atlanta | KATL | Hartsfield-Jackson International Airport | F | 2025-12-30 |
| Austin | KAUS | Austin-Bergstrom International Airport | F | 2026-03-24 |
| Beijing | ZBAA | Beijing Capital International Airport | C | 2026-03-20 |
| Buenos Aires | SAEZ | Minister Pistarini Intl Airport | C | 2025-12-31 |
| Busan | RKPK | Gimhae Intl Airport | C | 2026-04-03 |
| Cape Town | FACT | Cape Town International Airport | C | 2026-04-09 |
| Chengdu | ZUUU | Chengdu Shuangliu International Airport | C | 2026-03-20 |
| Chicago | KORD | Chicago O'Hare Intl Airport | F | 2026-02-18 |
| Chongqing | ZUCK | Chongqing Jiangbei International Airport | C | 2026-03-20 |
| Dallas | KDAL | Dallas Love Field | F | 2025-12-30 |
| Denver | KBKF | Buckley Space Force Base | F | 2026-03-24 |
| Guangzhou | ZGGG | Guangzhou Baiyun International Airport | C | 2026-04-15 |
| Helsinki | EFHK | Helsinki Vantaa Airport | C | 2026-04-03 |
| Houston | KHOU | William P. Hobby Airport | F | 2026-03-24 |
| Jakarta | WIHH | Halim Perdanakusuma Intl Airport | C | 2026-04-03 |
| Jeddah | OEJN | King Abdulaziz International Airport | C | 2026-04-09 |
| Karachi | OPKC | Masroor Airbase | C | 2026-04-15 |
| Kuala Lumpur | WMKK | Kuala Lumpur Intl Airport | C | 2026-04-03 |
| Lagos | DNMM | Murtala Muhammad International Airport | C | 2026-04-09 |
| London | EGLC | London City Airport | C | 2025-12-31 |
| Los Angeles | KLAX | Los Angeles International Airport | F | 2026-03-24 |
| Lucknow | VILK | Chaudhary Charan Singh Intl Airport | C | 2026-03-05 |
| Madrid | LEMD | Adolfo Suárez Madrid-Barajas Airport | C | 2026-03-16 |
| Manila | RPLL | Ninoy Aquino International Airport | C | 2026-04-15 |
| Mexico City | MMMX | Benito Juárez International Airport | C | 2026-03-30 |
| Miami | KMIA | Miami Intl Airport | F | 2026-02-18 |
| Milan | LIMC | Malpensa Intl Airport | C | 2026-03-16 |
| Munich | EDDM | Munich Airport | C | 2026-03-05 |
| NYC | KLGA | LaGuardia Airport | F | 2025-12-30 |
| Panama City | MPMG | Marcos A. Gelabert Intl Airport | C | 2026-04-03 |
| Paris | LFPG | Charles de Gaulle Airport | C | 2026-02-18 |
| San Francisco | KSFO | San Francisco International Airport | F | 2026-03-24 |
| Sao Paulo | SBGR | Sao Paulo-Guarulhos International Airport | C | 2026-02-18 |
| Seattle | KSEA | Seattle-Tacoma International Airport | F | 2025-12-31 |
| Seoul | RKSI | Incheon Intl Airport | C | 2025-12-31 |
| Shanghai | ZSPD | Shanghai Pudong International Airport | C | 2026-03-13 |
| Singapore | WSSS | Singapore Changi Airport | C | 2026-03-13 |
| Tokyo | RJTT | Tokyo Haneda Airport | C | 2026-03-10 |
| Toronto | CYYZ | Toronto Pearson Intl Airport | C | 2025-12-31 |
| Warsaw | EPWA | Warsaw Chopin Airport | C | 2026-03-16 |
| Wellington | NZWN | Wellington Intl Airport | C | 2026-02-18 |
| Wuhan | ZHHH | Wuhan Tianhe International Airport | C | 2026-03-20 |

### Cities with Source Changes

#### Hong Kong
| Period | Source | Station | Notes |
|--------|--------|---------|-------|
| 2026-03-13 → 2026-03-14 | WU (HK Airport) | VHHH | "Hong Kong International Airport Station" |
| 2026-03-16 → current | HKO | HK Observatory HQ | "Hong Kong Observatory" — NOT the airport |

**Impact**: Our HKO data matches from Mar 16+. Mar 13-14 mismatch (7-8°C gap) because airport ≠ observatory.

#### Taipei
| Period | Source | Station | Notes |
|--------|--------|---------|-------|
| 2026-03-16 → 2026-03-22 | CWA | 46692 | "Taipei's Central Weather Administration" |
| 2026-03-23 → 2026-04-04 | NOAA | RCTP (Taoyuan) | "NOAA at the Taiwan Taoyuan International Airport" |
| 2026-04-05 → current | WU | RCSS (Songshan) | "Taipei Songshan Airport Station" |

**Impact**: Our WU/RCSS data matches from Apr 5+. Before that, 16 mismatches (1-5°C) due to different stations/sources.

#### Shenzhen
| Period | Source | Station | Notes |
|--------|--------|---------|-------|
| 2026-03-20 → 2026-03-28 | WU | ZGSZ (Bao'an) | "Shenzhen Bao'an International Airport Station" |
| 2026-03-29 only | NOAA | ZGSZ (Bao'an) | "NOAA at the Shenzhen Baoan International Airport" |
| 2026-03-30 → current | WU | ZGSZ (Bao'an) | Back to WU |

**Impact**: Same station but different data provider for 1 day. Remaining ±1°C mismatches are rounding differences.

#### Tel Aviv
| Period | Source | Station | Notes |
|--------|--------|---------|-------|
| 2026-03-10 → 2026-03-22 | WU | LLBG | "Ben Gurion Intl Airport Station" |
| 2026-03-23 → current | NOAA | LLBG | "NOAA at the Ben Gurion International Airport" |

**Impact**: Same station, different provider. NOAA may report different values due to observation timing.

#### Istanbul
| Period | Source | Notes |
|--------|--------|-------|
| 2026-03-30 → current | NOAA | "NOAA at the Istanbul Airport" |

**Impact**: We use ogimet METAR (LTFM). Need to verify alignment.

#### Moscow
| Period | Source | Notes |
|--------|--------|-------|
| 2026-03-30 → current | NOAA | "NOAA at the Vnukovo International Airport" |

**Impact**: We use ogimet METAR (UUWW = Vnukovo). Same station, different provider.

#### Denver
| Period | Source | Notes |
|--------|--------|-------|
| 2026-03-24 → 2026-03-28 | WU | "Buckly Space Force Base" (typo in PM) |
| 2026-03-29 → current | WU | "Buckley Space Force Base" (corrected spelling) |

**Impact**: Same station, just a typo fix. No data impact.

## Known Data Quality Issues

### 2026-03-08: WU API Partial Data (6 cities QUARANTINED)
- **Affected**: Atlanta, Chicago, Dallas, Miami, NYC, Seattle
- **Problem**: WU API returned only 2-3 hourly observations instead of ~24
- **Result**: High temp is from early morning, not true daily max
- **DB status**: `authority = 'QUARANTINED'` with provenance_metadata explaining the issue
- **Cannot be fixed**: Re-fetching still returns only 2-3 observations

## Gamma API Date Mapping

**CRITICAL**: The Gamma API `endDate` field is NOT the weather observation date.
- `endDate` = market close date = weather date + 1 day
- The actual weather date must be extracted from the event `title` field
- Example: `endDate=2026-03-22`, `title="...Beijing on March 21?"` → weather date is March 21

## Settlement Source Audit Cadence

1. **Weekly**: Run `scripts/smoke_test_settlements.py` to detect new mismatches
2. **On any mismatch spike**: Check market descriptions via Gamma API for source changes
3. **Update this file** whenever a source transition is confirmed
4. **Update `config/cities.json`** fields: `wu_station`, `airport_name`, `settlement_source`, `settlement_source_type`
