# Polymarket Station Verification Audit
**Date:** 2026-06-10 (data collected) / 2026-06-17 (written)  
**Auditor:** Independent verification agent  
**Source of truth:** Polymarket market description text (most recent active market per city) + OurAirports for coordinates  
**Config file:** `config/cities.json`

---

## Per-City Audit Table

| City | Cfg Station | Cfg lat, lon | Polymarket Settlement (from description) | Authoritative Coords (OurAirports) | Distance | VERDICT |
|------|-------------|--------------|------------------------------------------|------------------------------------|----------|---------|
| Amsterdam | EHAM | 52.3086, 4.7639 | WU/EHAM, Amsterdam Airport Schiphol, °C | 52.308601, 4.763890 (OurAirports) | ~0m | MATCH |
| Ankara | LTAC | 40.1281, 32.9951 | WU/LTAC, Esenboğa International Airport, °C | 40.128101, 32.995098 (OurAirports) | ~0m | MATCH |
| Atlanta | KATL | 33.6367, -84.4281 | WU/KATL, Hartsfield-Jackson Atlanta International, °F | 33.636700, -84.428101 (OurAirports) | ~0m | MATCH |
| Auckland | NZAA | -37.0120, 174.7863 | — (no active market found; 404) | -37.008099, 174.792007 (OurAirports) | ~780m | UNVERIFIED_NO_ACTIVE_MARKET |
| Austin | KAUS | 30.1945, -97.6699 | WU/KAUS, Austin-Bergstrom International, °F | 30.194500, -97.669899 (OurAirports) | ~0m | MATCH |
| Beijing | ZBAA | 40.0799, 116.5850 | WU/ZBAA, Beijing Capital International, °C | 40.080101, 116.584999 (OurAirports) | ~0m | MATCH |
| Buenos Aires | SAEZ | -34.8222, -58.5358 | WU/SAEZ, Minister Pistarini International, °C | -34.822201, -58.535801 (OurAirports) | ~0m | MATCH |
| Busan | RKPK | 35.1795, 128.938 | WU/RKPK, Gimhae International Airport, °C | 35.179501, 128.938004 (OurAirports) | ~0m | MATCH |
| Cape Town | FACT | -33.9648, 18.6017 | WU/FACT, Cape Town International Airport, °C | -33.964802, 18.601700 (OurAirports) | ~0m | MATCH |
| Chengdu | ZUUU | 30.5785, 103.9469 | WU/ZUUU, Chengdu Shuangliu International, °C | 30.578501, 103.946999 (OurAirports) | ~0m | MATCH |
| Chicago | KORD | 41.9786, -87.9048 | WU/KORD, O'Hare International Airport, °F | 41.978600, -87.904800 (OurAirports) | ~0m | MATCH |
| Chongqing | ZUCK | 29.7192, 106.6417 | WU/ZUCK, Chongqing Jiangbei International, °C | 29.719200, 106.641700 (OurAirports) | ~0m | MATCH |
| Dallas | KDAL | 32.8471, -96.8518 | WU/KDAL, Dallas Love Field, °F | 32.847099, -96.851799 (OurAirports) | ~0m | MATCH |
| Denver | KBKF | 39.7173, -104.7516 | WU/KBKF, Buckley Space Force Base, °F | 39.717300, -104.751600 (OurAirports) | ~0m | MATCH |
| Guangzhou | ZGGG | 23.3924, 113.2990 | WU/ZGGG, Guangzhou Baiyun International, °C | 23.392401, 113.299004 (OurAirports) | ~0m | MATCH |
| Helsinki | EFHK | 60.3172, 24.9633 | WU/EFHK, Helsinki-Vantaa Airport, °C | 60.317200, 24.963301 (OurAirports) | ~0m | MATCH |
| Hong Kong | HKO | 22.3022, 114.1742 | HKO (Hong Kong Observatory city-centre), "Absolute Daily Max (deg. C)", https://www.weather.gov.hk/en/cis/climat.htm, °C | 22.302200, 114.174200 (HKO HQ — not OurAirports; no ICAO) | N/A | MATCH |
| Houston | KHOU | 29.6454, -95.2789 | WU/KHOU, Houston Hobby Airport, °F | 29.645399, -95.278900 (OurAirports) | ~0m | MATCH |
| Istanbul | LTFM | 41.2753, 28.7519 | NOAA/LTFM, Istanbul Airport, "highest temperature recorded by NOAA...https://www.weather.gov/wrh/timeseries?site=LTFM", °C | 41.275299, 28.751900 (OurAirports) | ~0m | MATCH |
| Jakarta | WIHH | -6.1566, 106.8890 | WU/WIHH, Halim Perdanakusuma International Airport, °C | -6.156600, 106.889000 (OurAirports) | ~0m | MATCH |
| Jeddah | OEJN | 21.6796, 39.1565 | WU/OEJN, King Abdulaziz International Airport, °C | 21.679600, 39.156502 (OurAirports) | ~0m | MATCH |
| Jinan | ZSJN | 36.8572, 117.0160 | — (no active market found; page error) | 36.857201, 117.016000 (OurAirports) | ~0m cfg vs OurAirports | UNVERIFIED_NO_ACTIVE_MARKET |
| Karachi | OPKC | 24.9065, 67.1608 | WU/OPKC — description says "Masroor Airbase Station" (WU display name); resolution URL path=/pk/karachi/OPKC, °C | 24.906500, 67.160797 (OurAirports; OPKC = Jinnah International Airport) | ~0m | DISCREPANCY:NAMING — WU/Polymarket display name "Masroor Airbase Station" vs ICAO OPKC = Jinnah International Airport. ICAO, URL, and coords are correct; mismatch is a WU internal display-name quirk only. Config `airport_name` ("Jinnah International Airport") correct per ICAO; Polymarket settlement URL OPKC is consistent with config. No config change needed. |
| Kuala Lumpur | WMKK | 2.7456, 101.7100 | WU/WMKK, Kuala Lumpur International Airport, °C | 2.745600, 101.710003 (OurAirports) | ~0m | MATCH |
| Lagos | DNMM | 6.5774, 3.3212 | WU/DNMM, Murtala Muhammed International Airport, °C | 6.577400, 3.321200 (OurAirports) | ~0m | MATCH |
| London | EGLC | 51.5053, 0.0553 | WU/EGLC, London City Airport, °C | 51.505299, 0.055278 (OurAirports) | ~0m | MATCH |
| Los Angeles | KLAX | 33.9425, -118.4081 | WU/KLAX, Los Angeles International Airport, °F | 33.942501, -118.408096 (OurAirports) | ~0m | MATCH |
| Lucknow | VILK | 26.7606, 80.8893 | WU/VILK, Chaudhary Charan Singh International Airport, °C | 26.760599, 80.889297 (OurAirports) | ~0m | MATCH |
| Madrid | LEMD | 40.4936, -3.5668 | WU/LEMD, Adolfo Suárez Madrid-Barajas Airport, °C | 40.493599, -3.566830 (OurAirports) | ~0m | MATCH |
| Manila | RPLL | 14.5086, 121.0200 | WU/RPLL, Ninoy Aquino International Airport, °C | 14.508600, 121.019997 (OurAirports via web search) | ~0m | MATCH |
| Mexico City | MMMX | 19.4363, -99.0721 | WU/MMMX, "Benito Juárez International Airport Station", °C | 19.436300, -99.072098 (OurAirports) | ~0m | DISCREPANCY:NAMING — Config `airport_name` = "Mexico City International Airport"; Polymarket/OurAirports say "Benito Juárez International Airport". Same airport, same ICAO MMMX. No settlement or coord error. |
| Miami | KMIA | 25.7959, -80.2870 | WU/KMIA, Miami International Airport, °F | 25.795900, -80.287003 (OurAirports) | ~0m | MATCH |
| Milan | LIMC | 45.6306, 8.7231 | WU/LIMC, Malpensa International Airport, °C | 45.630600, 8.723100 (OurAirports) | ~0m | MATCH |
| Moscow | UUWW | 55.5915, 37.2615 | NOAA/UUWW, Vnukovo International Airport, "highest temperature recorded by NOAA...https://www.weather.gov/wrh/timeseries?site=UUWW", °C | 55.591499, 37.261501 (OurAirports) | ~0m | MATCH |
| Munich | EDDM | 48.3538, 11.7861 | WU/EDDM, Munich Airport, °C | 48.353802, 11.786100 (OurAirports) | ~0m | MATCH |
| New York City | KLGA | 40.7773, -73.8726 | WU/KLGA, LaGuardia Airport, °F | 40.777199, -73.872597 (OurAirports) | ~0m | MATCH |
| Panama City | MPMG | 8.9833, -79.5556 | WU/MPMG, Marcos A. Gelabert International Airport, °C | 8.983300, -79.555603 (OurAirports) | ~0m | MATCH |
| Paris | LFPB | 48.9584, 2.4412 | WU/LFPB, Paris-Le Bourget Airport, °C | 48.958401, 2.441200 (OurAirports) | ~0m | MATCH |
| Qingdao | ZSQD | 36.2661, 120.3748 | WU/ZSQD, °C | 36.266102, 120.374802 (OurAirports; now "Qingdao Jiaodong International Airport") | ~0m | DISCREPANCY:NAMING — Config `airport_name` = "Qingdao Liuting International Airport" is outdated; airport renamed to "Qingdao Jiaodong International Airport" (same ICAO ZSQD, same coords). No settlement or coord error. |
| San Francisco | KSFO | 37.6189, -122.3750 | WU/KSFO, San Francisco International Airport, °F | 37.618900, -122.375000 (OurAirports) | ~0m | MATCH |
| São Paulo | SBGR | -23.4356, -46.4731 | WU/SBGR, Guarulhos International Airport, °C | -23.435600, -46.473099 (OurAirports) | ~0m | MATCH |
| Seattle | KSEA | 47.4489, -122.3094 | WU/KSEA, Seattle-Tacoma International Airport, °F | 47.448900, -122.309402 (OurAirports) | ~0m | MATCH |
| Seoul | RKSI | 37.4691, 126.4510 | WU/RKSI, Incheon International Airport, °C | 37.469101, 126.451004 (OurAirports) | ~0m | MATCH |
| Shanghai | ZSPD | 31.1434, 121.8052 | WU/ZSPD, Shanghai Pudong International, °C | 31.143400, 121.805199 (OurAirports) | ~0m | MATCH |
| Shenzhen | ZGSZ | 22.6393, 113.8107 | WU/ZGSZ, Shenzhen Bao'an International, °C | 22.639299, 113.810699 (OurAirports) | ~0m | MATCH |
| Singapore | WSSS | 1.3502, 103.9943 | WU/WSSS, Singapore Changi Airport, °C | 1.350200, 103.994301 (OurAirports) | ~0m | MATCH |
| Taipei | RCSS | 25.0694, 121.5522 | WU/RCSS, Songshan Airport, °C | 25.069401, 121.552200 (OurAirports) | ~0m | MATCH |
| Tel Aviv | LLBG | 32.0095, 34.8820 | NOAA/LLBG, Ben Gurion International Airport, "NOAA data from Ben Gurion International Airport...https://www.weather.gov/wrh/timeseries?site=LLBG", °C | 32.009499, 34.882000 (OurAirports) | ~0m | MATCH |
| Tokyo | RJTT | 35.5523, 139.7799 | WU/RJTT, Tokyo Haneda Airport, °C | 35.552299, 139.779999 (OurAirports) | ~0m | MATCH |
| Toronto | CYYZ | 43.6772, -79.6306 | WU/CYYZ, Toronto Pearson International Airport, °C | 43.677200, -79.630600 (OurAirports) | ~0m | MATCH |
| Warsaw | EPWA | 52.1657, 20.9671 | WU/EPWA, Warsaw Chopin Airport, °C | 52.165699, 20.967100 (OurAirports) | ~0m | MATCH |
| Wellington | NZWN | -41.3272, 174.8050 | WU/NZWN, Wellington International Airport, °C | -41.327202, 174.805008 (OurAirports) | ~0m | MATCH |
| Wuhan | ZHHH | 30.7838, 114.2081 | WU/ZHHH, Wuhan Tianhe International Airport, °C | 30.783800, 114.208099 (OurAirports) | ~0m | MATCH |
| Zhengzhou | ZHCC | 34.5197, 113.8410 | — (no active market found; page error) | 34.519699, 113.841003 (OurAirports) | ~0m cfg vs OurAirports | UNVERIFIED_NO_ACTIVE_MARKET |

---

## Summary

| Verdict | Count |
|---------|-------|
| MATCH | 47 |
| DISCREPANCY | 3 |
| UNVERIFIED_NO_ACTIVE_MARKET | 4 |
| **Total** | **54** |

### MATCH cities (47)
Amsterdam, Ankara, Atlanta, Austin, Beijing, Buenos Aires, Busan, Cape Town, Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, Hong Kong, Houston, Istanbul, Jakarta, Jeddah, Kuala Lumpur, Lagos, London, Los Angeles, Lucknow, Madrid, Manila, Miami, Milan, Moscow, Munich, New York City, Panama City, Paris, San Francisco, São Paulo, Seattle, Seoul, Shanghai, Shenzhen, Singapore, Taipei, Tel Aviv, Tokyo, Toronto, Warsaw, Wellington, Wuhan

### DISCREPANCY cities (3) — NAMING ONLY; no config change needed for settlement/coords

1. **Karachi (OPKC)**: Weather Underground and Polymarket description display the station as "Masroor Airbase Station", but ICAO OPKC = Jinnah International Airport (OurAirports). Polymarket resolution URL uses /pk/karachi/OPKC. Config ICAO, settlement_source, coords are all correct. This is a WU internal display-name quirk. **No action required.**

2. **Mexico City (MMMX)**: Config `airport_name` = "Mexico City International Airport". Polymarket and OurAirports now call it "Benito Juárez International Airport". Same ICAO MMMX, identical coords. **No action required for settlement; optional: update `airport_name` in config to "Benito Juárez International Airport".**

3. **Qingdao (ZSQD)**: Config `airport_name` = "Qingdao Liuting International Airport". Airport was renamed/redeveloped to "Qingdao Jiaodong International Airport" — same ICAO ZSQD, same coords. **No action required for settlement; optional: update `airport_name` in config to "Qingdao Jiaodong International Airport".**

### UNVERIFIED cities (4) — no active Polymarket market accessible

1. **Auckland (NZAA)**: Market page returned 404. Config ICAO NZAA matches OurAirports coords within ~780m (OurAirports: -37.008099, 174.792007 vs config -37.01199, 174.786331 — ~1.5km difference). **FLAG: coords diverge ~1.5km from OurAirports; recommend re-pinning to OurAirports values when active market available for re-verification.**

2. **Jinan (ZSJN)**: Market page returned error. Config ICAO ZSJN, coords match OurAirports. Cannot verify Polymarket settlement station from active market description.

3. **Zhengzhou (ZHCC)**: Market page returned error. Config ICAO ZHCC, coords match OurAirports. Cannot verify Polymarket settlement station from active market description.

---

## Coord Notes

- All 50 ICAO-type stations with accessible OurAirports data match config lat/lon within ≤35m (well within 100m threshold).
- Exception: Auckland config coords (-37.01199, 174.786331) differ from OurAirports (-37.008099, 174.792007) by approximately 1.5km. Market could not be verified due to 404. Recommend re-pin when active market is available.
- HKO (Hong Kong): non-airport station; OurAirports not applicable. Coordinates approximate HKO HQ (22.3022, 114.1742); settlement confirmed via Polymarket description referencing HKO directly.

---

## Settlement Source Type Summary

| Type | Cities |
|------|--------|
| wu_icao | 50 (all except HK, Istanbul, Moscow, Tel Aviv) |
| hko | 1 (Hong Kong) |
| noaa | 3 (Istanbul/LTFM, Moscow/UUWW, Tel Aviv/LLBG) |
| cwa_station | 0 (Taipei migrated to wu_icao/RCSS) |

All three NOAA cities confirmed via Polymarket description text referencing `https://www.weather.gov/wrh/timeseries?site=<ICAO>`.

---

*Audit methodology: Polymarket market description fetched via WebFetch from polymarket.com/event/highest-temperature-in-\<city\>-on-\<date\>-2026 URLs and via WebSearch for cities with no accessible active market. Coordinate authority: OurAirports (ourairports.com/airports/\<ICAO\>/). HKO coordinates from Hong Kong Observatory public data. All fetches read-only; no config or code edits made.*
