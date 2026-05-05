# E8 Audit — calibration_pairs_v2 bulk-regeneration provenance

Created: 2026-05-03
Authority: read-only forensic audit (haiku-A)

## Headline

- BULK_REGENERATED bins: 100 / 102 (98.0% of cities × metrics)
- PARTIALLY_BULK bins:   0 / 102
- Total rows in BULK_REGENERATED bins: 40,373,380 (97.9%) of total
- The Tokyo HIGH case is **NOT** isolated. This is a system-wide phenomenon.

## Top 10 mass-write calendar days

| date(recorded_at) | n_rows | dominant cities |
| :--- | :--- | :--- |
| 2026-04-29 | 36,524,756 | Beijing, Buenos Aires, Busan, Cape Town, Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, Houston, Istanbul, Jakarta, Jeddah, Karachi, Kuala Lumpur, Lagos, London, Los Angeles, Lucknow, Madrid, Manila, Mexico City, Miami, Milan, Moscow, Munich, NYC, Panama City, Paris, San Francisco, Sao Paulo, Seattle, Seoul, Shanghai, Shenzhen, Singapore, Taipei, Tel Aviv, Tokyo, Toronto, Warsaw, Wellington, Wuhan, Amsterdam, Ankara, Atlanta, Auckland, Austin |
| 2026-04-28 | 3,816,698 | Amsterdam, Ankara, Atlanta, Auckland, Austin, Beijing |
| 2026-05-01 | 870,672 | Hong Kong, Paris |

## Per-(city, metric) provenance table

| city | metric | n_rows | recorded_at span | target_date span | n_distinct_recorded_days | n_distinct_data_versions | n_verified | n_unverified | flag |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Amsterdam | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Amsterdam | low | 76,704 | 0.03h | 836d | 1 | 1 | 76,704 | 0 | BULK |
| Ankara | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Ankara | low | 137,496 | 0.05h | 836d | 1 | 1 | 137,496 | 0 | BULK |
| Atlanta | high | 614,100 | 0.24h | 839d | 1 | 1 | 614,100 | 0 | BULK |
| Atlanta | low | 153,272 | 0.06h | 837d | 1 | 1 | 153,272 | 0 | BULK |
| Auckland | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Auckland | low | 357,612 | 0.14h | 838d | 1 | 1 | 357,612 | 0 | BULK |
| Austin | high | 615,572 | 0.25h | 839d | 1 | 1 | 615,572 | 0 | BULK |
| Austin | low | 377,108 | 0.15h | 837d | 1 | 1 | 377,108 | 0 | BULK |
| Beijing | high | 682,482 | 0.26h | 839d | 2 | 1 | 682,482 | 0 | BULK |
| Beijing | low | 149,124 | 0.06h | 837d | 1 | 1 | 149,124 | 0 | BULK |
| Buenos Aires | high | 680,850 | 0.25h | 839d | 1 | 1 | 680,850 | 0 | BULK |
| Buenos Aires | low | 81,498 | 0.03h | 836d | 1 | 1 | 81,498 | 0 | BULK |
| Busan | high | 682,482 | 0.25h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Busan | low | 88,638 | 0.03h | 834d | 1 | 1 | 88,638 | 0 | BULK |
| Cape Town | high | 660,450 | 0.25h | 839d | 1 | 1 | 660,450 | 0 | BULK |
| Cape Town | low | 135,456 | 0.05h | 838d | 1 | 1 | 135,456 | 0 | BULK |
| Chengdu | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Chengdu | low | 135,762 | 0.05h | 838d | 1 | 1 | 135,762 | 0 | BULK |
| Chicago | high | 614,836 | 0.24h | 839d | 1 | 1 | 614,836 | 0 | BULK |
| Chicago | low | 304,244 | 0.12h | 831d | 1 | 1 | 304,244 | 0 | BULK |
| Chongqing | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Chongqing | low | 115,566 | 0.04h | 838d | 1 | 1 | 115,566 | 0 | BULK |
| Dallas | high | 614,836 | 0.24h | 839d | 1 | 1 | 614,836 | 0 | BULK |
| Dallas | low | 379,316 | 0.15h | 839d | 1 | 1 | 379,316 | 0 | BULK |
| Denver | high | 612,628 | 0.24h | 839d | 1 | 1 | 612,628 | 0 | BULK |
| Denver | low | 379,500 | 0.15h | 837d | 1 | 1 | 379,500 | 0 | BULK |
| Guangzhou | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Guangzhou | low | 87,414 | 0.03h | 838d | 1 | 1 | 87,414 | 0 | BULK |
| Helsinki | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Helsinki | low | 48,858 | 0.02h | 832d | 1 | 1 | 48,858 | 0 | BULK |
| Hong Kong | high | 689,622 | 0.49h | 849d | 1 | 1 | 689,622 | 0 | BULK |
| Hong Kong | low | 89,454 | 0.07h | 843d | 1 | 1 | 89,454 | 0 | BULK |
| Houston | high | 614,836 | 0.24h | 839d | 1 | 1 | 614,836 | 0 | BULK |
| Houston | low | 330,188 | 0.13h | 839d | 1 | 1 | 330,188 | 0 | BULK |
| Istanbul | high | 614,040 | 0.23h | 836d | 1 | 1 | 614,040 | 0 | BULK |
| Istanbul | low | 47,634 | 0.02h | 836d | 1 | 1 | 47,634 | 0 | BULK |
| Jakarta | high | 682,482 | 0.25h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Jakarta | low | 23,256 | 0.01h | 829d | 1 | 1 | 23,256 | 0 | BULK |
| Jeddah | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Jeddah | low | 163,506 | 0.06h | 837d | 1 | 1 | 163,506 | 0 | BULK |
| Karachi | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Karachi | low | 42,228 | 0.02h | 835d | 1 | 1 | 42,228 | 0 | BULK |
| Kuala Lumpur | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Kuala Lumpur | low | 12,240 | 0.00h | 794d | 1 | 1 | 12,240 | 0 | BULK |
| Lagos | high | 672,690 | 0.25h | 839d | 1 | 1 | 672,690 | 0 | BULK |
| Lagos | low | 199,512 | 0.07h | 834d | 1 | 1 | 199,512 | 0 | BULK |
| London | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| London | low | 404,532 | 0.15h | 839d | 1 | 1 | 404,532 | 0 | BULK |
| Los Angeles | high | 615,572 | 0.24h | 839d | 1 | 1 | 615,572 | 0 | BULK |
| Los Angeles | low | 65,228 | 0.03h | 837d | 1 | 1 | 65,228 | 0 | BULK |
| Lucknow | high | 667,794 | 0.26h | 839d | 1 | 1 | 667,794 | 0 | BULK |
| Lucknow | low | 23,460 | 0.01h | 822d | 1 | 1 | 23,460 | 0 | BULK |
| Madrid | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Madrid | low | 292,026 | 0.11h | 838d | 1 | 1 | 292,026 | 0 | BULK |
| Manila | high | 681,666 | 0.27h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Manila | low | 49,878 | 0.02h | 831d | 1 | 1 | 49,878 | 0 | BULK |
| Mexico City | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Mexico City | low | 682,482 | 0.25h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Miami | high | 614,100 | 0.24h | 839d | 1 | 1 | 614,100 | 0 | BULK |
| Miami | low | 92,368 | 0.04h | 838d | 1 | 1 | 92,368 | 0 | BULK |
| Milan | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Milan | low | 133,518 | 0.05h | 837d | 1 | 1 | 133,518 | 0 | BULK |
| Moscow | high | 680,136 | 0.26h | 836d | 1 | 1 | 680,136 | 0 | BULK |
| Moscow | low | 52,632 | 0.02h | 830d | 1 | 1 | 52,632 | 0 | BULK |
| Munich | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Munich | low | 128,316 | 0.05h | 837d | 1 | 1 | 128,316 | 0 | BULK |
| NYC | high | 614,100 | 0.25h | 839d | 1 | 1 | 614,100 | 0 | BULK |
| NYC | low | 97,796 | 0.04h | 834d | 1 | 1 | 97,796 | 0 | BULK |
| Panama City | high | 678,402 | 0.26h | 839d | 1 | 1 | 678,402 | 0 | BULK |
| Panama City | low | 67,014 | 0.02h | 836d | 1 | 1 | 67,014 | 0 | BULK |
| Paris | high | 690,132 | 58.53h | 851d | 2 | 1 | 72,012 | 0 | - |
| Paris | low | 148,614 | 52.35h | 851d | 2 | 1 | 19,584 | 0 | - |
| San Francisco | high | 615,572 | 0.25h | 839d | 1 | 1 | 615,572 | 0 | BULK |
| San Francisco | low | 17,480 | 0.01h | 836d | 1 | 1 | 17,480 | 0 | BULK |
| Sao Paulo | high | 682,482 | 0.27h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Sao Paulo | low | 67,524 | 0.02h | 830d | 1 | 1 | 67,524 | 0 | BULK |
| Seattle | high | 614,836 | 0.26h | 839d | 1 | 1 | 614,836 | 0 | BULK |
| Seattle | low | 16,008 | 0.01h | 815d | 1 | 1 | 16,008 | 0 | BULK |
| Seoul | high | 681,666 | 0.28h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Seoul | low | 91,596 | 0.03h | 839d | 1 | 1 | 91,596 | 0 | BULK |
| Shanghai | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Shanghai | low | 44,982 | 0.02h | 832d | 1 | 1 | 44,982 | 0 | BULK |
| Shenzhen | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Shenzhen | low | 89,148 | 0.03h | 836d | 1 | 1 | 89,148 | 0 | BULK |
| Singapore | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Singapore | low | 20,298 | 0.01h | 812d | 1 | 1 | 20,298 | 0 | BULK |
| Taipei | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Taipei | low | 26,112 | 0.01h | 819d | 1 | 1 | 26,112 | 0 | BULK |
| Tel Aviv | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Tel Aviv | low | 64,668 | 0.02h | 835d | 1 | 1 | 64,668 | 0 | BULK |
| Tokyo | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Tokyo | low | 37,332 | 0.01h | 831d | 1 | 1 | 37,332 | 0 | BULK |
| Toronto | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Toronto | low | 81,804 | 0.03h | 826d | 1 | 1 | 81,804 | 0 | BULK |
| Warsaw | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Warsaw | low | 99,858 | 0.04h | 839d | 1 | 1 | 99,858 | 0 | BULK |
| Wellington | high | 682,482 | 0.26h | 839d | 1 | 1 | 682,482 | 0 | BULK |
| Wellington | low | 340,986 | 0.13h | 838d | 1 | 1 | 340,986 | 0 | BULK |
| Wuhan | high | 681,666 | 0.26h | 839d | 1 | 1 | 681,666 | 0 | BULK |
| Wuhan | low | 116,178 | 0.04h | 835d | 1 | 1 | 116,178 | 0 | BULK |

## Three sanity samples

### Sample 1: Hong Kong, high
  - recorded_at: 2026-05-01 17:16:11 → 2026-05-01 17:45:46 (span: 29.6 minutes)
  - forecast_available_at: 2024-01-01T00:00:00+00:00 → 2026-04-28T00:00:00+00:00 (span: 849 days)
  - Verdict: **BULK-REGEN CONFIRMED**

### Sample 2: Amsterdam, high
  - recorded_at: 2026-04-28 22:32:10 → 2026-04-28 22:47:33 (span: 15.4 minutes)
  - forecast_available_at: 2024-01-01T00:00:00+00:00 → 2026-04-18T00:00:00+00:00 (span: 839 days)
  - Verdict: **BULK-REGEN CONFIRMED**

### Sample 3: Beijing, high
  - recorded_at: 2026-04-28 23:47:44 → 2026-04-29 00:03:11 (span: 15.4 minutes)
  - forecast_available_at: 2024-01-01T00:00:00+00:00 → 2026-04-18T00:00:00+00:00 (span: 839 days)
  - Verdict: **BULK-REGEN CONFIRMED**

## Conclusion

The audit results confirm a **system-wide bulk-regeneration event**. 98% of all bins in `calibration_pairs_v2` exhibit the diagnostic signature: hundreds of thousands of rows written within minutes, yet covering over 800 days of history. 

This is not a Tokyo-only phenomenon; it is the near-universal state of the table. **The entire codebase must treat `calibration_pairs_v2` data with a `target_date` earlier than 2026-04-29 as potentially contaminated**, as its provenance was destroyed by this mass rewrite. Only 2 out of 102 bins (Paris High/Low) avoided the strict "BULK" flag, and even those show unusual write patterns. This confirms systematic E8 data leakage.
