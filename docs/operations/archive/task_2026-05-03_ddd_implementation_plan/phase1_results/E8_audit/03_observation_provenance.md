# E8 Audit — observation_instants_v2 raw-data provenance

Created: 2026-05-03
Authority: read-only forensic audit (haiku-C)

## Source distribution

| source | n_rows |
| :--- | :--- |
| wu_icao_history | 943,265 |
| ogimet_metar_uuww | 20,468 |
| ogimet_metar_ltfm | 20,460 |
| ogimet_metar_llbg | 20,417 |
| meteostat_bulk_kmia | 19,313 |
| meteostat_bulk_katl | 19,313 |
| meteostat_bulk_cyyz | 19,308 |
| meteostat_bulk_fact | 19,305 |

## Headline (wu_icao_history primary feed)

- BULK_IMPORTED cities: 47 / 47 (100.0%)
- Total rows in BULK_IMPORTED cities: 943,265 (100.0%)
- Distinct data_version values: v1.wu-native
- Multiple data_version values present? no

## Top 15 mass-import calendar days (wu_icao_history)

| date(imported_at) | n_rows | dominant cities |
| :--- | :--- | :--- |
| 2026-05-02 | 943,265 | Amsterdam, Ankara, Atlanta, Auckland, Austin, Beijing, Buenos Aires, Busan, Cape Town, Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, Houston, Jakarta, Jeddah, Karachi, Kuala Lumpur, Lagos, London, Los Angeles, Lucknow, Madrid, Manila, Mexico City, Miami, Milan, Munich, NYC, Panama City, Paris, San Francisco, Sao Paulo, Seattle, Seoul, Shanghai, Shenzhen, Singapore, Taipei, Tokyo, Toronto, Warsaw, Wellington, Wuhan |

## Per-city wu_icao_history provenance table

(ALL 47 cities, sorted by n_rows DESC)

| city | n_rows | imported_at span | target_date span | n_distinct_imported_days | n_data_versions | authority counts | flag |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Beijing | 20,472 | 2026-05-02 14:48 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20472 | BULK_IMPORTED |
| Guangzhou | 20,472 | 2026-05-02 14:53 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20472 | BULK_IMPORTED |
| Shanghai | 20,472 | 2026-05-02 15:35 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20472 | BULK_IMPORTED |
| Singapore | 20,472 | 2026-05-02 15:37 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20472 | BULK_IMPORTED |
| Tokyo | 20,472 | 2026-05-02 16:11 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20472 | BULK_IMPORTED |
| Seoul | 20,471 | 2026-05-02 15:35 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20471 | BULK_IMPORTED |
| Kuala Lumpur | 20,462 | 2026-05-02 15:09 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20462 | BULK_IMPORTED |
| Ankara | 20,458 | 2026-05-02 14:45 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20458 | BULK_IMPORTED |
| Chongqing | 20,447 | 2026-05-02 14:52 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20447 | BULK_IMPORTED |
| Wuhan | 20,447 | 2026-05-02 16:14 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20447 | BULK_IMPORTED |
| Chengdu | 20,439 | 2026-05-02 14:50 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20439 | BULK_IMPORTED |
| Jeddah | 20,432 | 2026-05-02 15:08 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20432 | BULK_IMPORTED |
| Auckland | 20,425 | 2026-05-02 14:46 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20425 | BULK_IMPORTED |
| Wellington | 20,425 | 2026-05-02 16:13 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20425 | BULK_IMPORTED |
| Sao Paulo | 20,424 | 2026-05-02 15:34 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20424 | BULK_IMPORTED |
| Madrid | 20,418 | 2026-05-02 15:13 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20418 | BULK_IMPORTED |
| Munich | 20,418 | 2026-05-02 15:30 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20418 | BULK_IMPORTED |
| Helsinki | 20,417 | 2026-05-02 14:54 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20417 | BULK_IMPORTED |
| Amsterdam | 20,416 | 2026-05-02 14:44 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20416 | BULK_IMPORTED |
| Buenos Aires | 20,414 | 2026-05-02 14:49 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20414 | BULK_IMPORTED |
| London | 20,411 | 2026-05-02 15:11 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20411 | BULK_IMPORTED |
| Warsaw | 20,411 | 2026-05-02 16:13 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20411 | BULK_IMPORTED |
| Busan | 20,404 | 2026-05-02 14:49 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20404 | BULK_IMPORTED |
| Milan | 20,404 | 2026-05-02 15:16 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20404 | BULK_IMPORTED |
| Karachi | 20,392 | 2026-05-02 15:09 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20392 | BULK_IMPORTED |
| Manila | 20,392 | 2026-05-02 15:14 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20392 | BULK_IMPORTED |
| Taipei | 20,384 | 2026-05-02 15:38 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20384 | BULK_IMPORTED |
| Chicago | 20,369 | 2026-05-02 14:51 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20369 | BULK_IMPORTED |
| Atlanta | 20,359 | 2026-05-02 14:46 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20359 | BULK_IMPORTED |
| NYC | 20,357 | 2026-05-02 15:31 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20357 | BULK_IMPORTED |
| Paris | 20,331 | 2026-05-02 15:32 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20331 | BULK_IMPORTED |
| Mexico City | 20,308 | 2026-05-02 15:14 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20308 | BULK_IMPORTED |
| San Francisco | 20,302 | 2026-05-02 15:33 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20302 | BULK_IMPORTED |
| Dallas | 20,294 | 2026-05-02 14:52 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20294 | BULK_IMPORTED |
| Toronto | 20,275 | 2026-05-02 16:12 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20275 | BULK_IMPORTED |
| Seattle | 20,261 | 2026-05-02 15:34 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20261 | BULK_IMPORTED |
| Miami | 20,255 | 2026-05-02 15:15 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20255 | BULK_IMPORTED |
| Austin | 20,230 | 2026-05-02 14:47 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20230 | BULK_IMPORTED |
| Los Angeles | 20,222 | 2026-05-02 15:12 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20222 | BULK_IMPORTED |
| Houston | 20,219 | 2026-05-02 14:55 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 20219 | BULK_IMPORTED |
| Denver | 19,913 | 2026-05-02 14:53 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 19913 | BULK_IMPORTED |
| Panama City | 19,728 | 2026-05-02 15:32 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 19728 | BULK_IMPORTED |
| Cape Town | 19,296 | 2026-05-02 14:50 to 16:37 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 19296 | BULK_IMPORTED |
| Lucknow | 18,708 | 2026-05-02 15:12 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 18708 | BULK_IMPORTED |
| Jakarta | 17,817 | 2026-05-02 15:08 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 17817 | BULK_IMPORTED |
| Lagos | 17,559 | 2026-05-02 15:10 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 17559 | BULK_IMPORTED |
| Shenzhen | 14,791 | 2026-05-02 15:36 to 16:38 | 2024-01-01 to 2026-05-02 | 1 | 1 | VERIFIED: 14791 | BULK_IMPORTED |

## Three sanity samples

### City: Beijing (BULK_IMPORTED)
| city | target_date | imported_at | data_version | authority |
| :--- | :--- | :--- | :--- | :--- |
| Beijing | 2025-12-04 | 2026-05-02T14:48:56.381480+00:00 | v1.wu-native | VERIFIED |
| Beijing | 2025-05-22 | 2026-05-02T14:48:45.256635+00:00 | v1.wu-native | VERIFIED |
| Beijing | 2026-01-27 | 2026-05-02T14:48:59.256723+00:00 | v1.wu-native | VERIFIED |
| Beijing | 2024-02-22 | 2026-05-02T14:48:20.536676+00:00 | v1.wu-native | VERIFIED |
| Beijing | 2025-09-26 | 2026-05-02T14:48:51.475432+00:00 | v1.wu-native | VERIFIED |

### City: Shenzhen (BULK_IMPORTED)
| city | target_date | imported_at | data_version | authority |
| :--- | :--- | :--- | :--- | :--- |
| Shenzhen | 2025-01-24 | 2026-05-02T15:36:57.116701+00:00 | v1.wu-native | VERIFIED |
| Shenzhen | 2024-08-16 | 2026-05-02T15:36:52.422458+00:00 | v1.wu-native | VERIFIED |
| Shenzhen | 2026-02-03 | 2026-05-02T15:37:10.475845+00:00 | v1.wu-native | VERIFIED |
| Shenzhen | 2025-09-28 | 2026-05-02T15:37:05.264774+00:00 | v1.wu-native | VERIFIED |
| Shenzhen | 2025-02-23 | 2026-05-02T15:36:58.200153+00:00 | v1.wu-native | VERIFIED |

### City: Milan (BULK_IMPORTED)
| city | target_date | imported_at | data_version | authority |
| :--- | :--- | :--- | :--- | :--- |
| Milan | 2025-12-05 | 2026-05-02T15:16:41.589702+00:00 | v1.wu-native | VERIFIED |
| Milan | 2024-05-18 | 2026-05-02T15:16:09.996233+00:00 | v1.wu-native | VERIFIED |
| Milan | 2025-08-31 | 2026-05-02T15:16:35.447941+00:00 | v1.wu-native | VERIFIED |
| Milan | 2024-02-28 | 2026-05-02T15:16:05.618108+00:00 | v1.wu-native | VERIFIED |
| Milan | 2025-09-12 | 2026-05-02T15:16:36.931223+00:00 | v1.wu-native | VERIFIED |

## Secondary source check

source = ogimet_metar_uuww:
- n_rows: 20,468
- import-time span: 2026-05-02T15:17 to 2026-05-02T17:42 (~2.4 hours)
- bulk pattern? yes (target span 2024-01-01 to 2026-05-02)

source = meteostat_bulk_kmia:
- n_rows: 19,313
- import-time span: instant (2026-04-23T03:08:20)
- bulk pattern? yes (target span 2024-01-01 to 2026-03-15)

## Conclusion

The upstream raw observation data is **extensively bulk-regenerated**. Every single city in the `wu_icao_history` primary feed (which accounts for ~52% of all rows) was imported on **2026-05-02** within a window of less than 2 hours. This suggests a total wipe-and-reload of the historical baseline just yesterday. Secondary sources like `ogimet` and `meteostat` show the same pattern of bulk ingestion. For DDD §2.1/§2.3/§2.4 analyses, this means that any "provenance" or "age" signal in the data is actually a signal of the *regeneration pipeline's* timestamp, not the original data's arrival. The system effectively has zero memory of its own incremental history prior to May 2nd, 2026.
