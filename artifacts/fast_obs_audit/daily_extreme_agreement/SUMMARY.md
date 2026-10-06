
### MAIN

| Candidate / source | type | metric | n_days | dropped (coverage) | eq | eq % | dangerous side (high: over, low: under) | other side | diff hist (cand - settled) | WRH-era n / eq / dangerous | reveal lead vs AWC, min p10 / p50 / p90 (live window; + = earlier) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Helsinki EFHK - FMI WFS 10-min (fmi_airport_temperature) | archive 10 min | high | 177 | 0 | 137 | 77.4% | **over=38** | under=2 | -1:2 0:137 1:37 2:1 | 43 / 36 / 6 | - |
| Helsinki EFHK - FMI WFS 10-min (fmi_airport_temperature) | archive 10 min | low | 46 | 0 | 41 | 89.1% | **under=5** | over=0 | -1:5 0:41 | 43 / 38 / 5 | - |
| Munich EDDM - DWD CDC TT_10 10-min (dwd_cdc_temperature) | archive 10 min | high | 203 | 2 | 146 | 71.9% | **over=7** | under=50 | -1:50 0:146 1:7 | 41 / 27 / 0 | - |
| Munich EDDM - DWD CDC TT_10 10-min (dwd_cdc_temperature) | archive 10 min | low | 44 | 2 | 39 | 88.6% | **under=5** | over=0 | -1:5 0:39 | 41 / 36 / 5 | - |
| Warsaw EPWA - IMGW synop hourly (imgw_synop_temperature) | archive hourly | high | 188 | 5 | 132 | 70.2% | **over=15** | under=41 | -1:41 0:132 1:15 | 38 / 28 / 5 | - |
| Warsaw EPWA - IMGW synop hourly (imgw_synop_temperature) | archive hourly | low | 41 | 5 | 18 | 43.9% | **under=2** | over=21 | -1:2 0:18 1:19 2:2 | 38 / 16 / 2 | - |
| Amsterdam EHAM - KNMI 10-min files, Tx12/Tn12 true extreme | archive extreme | high | 176 | 0 | 103 | 58.5% | **over=73** | under=0 | 0:103 1:73 | 43 / 26 / 17 | - |
| Amsterdam EHAM - KNMI 10-min files, Tx12/Tn12 true extreme | archive extreme | low | 46 | 0 | 29 | 63.0% | **under=17** | over=0 | -1:16 -2:1 0:29 | 43 / 29 / 14 | - |
| London EGLC - Met Office gcpvj0 hourly spot, 48 h window | api 48 h | high | 1 | 1 | 1 | 100.0% | **over=0** | under=0 | 0:1 | 1 / 1 / 0 | - |
| London EGLC - Met Office gcpvj0 hourly spot, 48 h window | api 48 h | low | 1 | 1 | 0 | 0.0% | **under=1** | over=0 | -1:1 | 1 / 0 / 1 | - |
| Tokyo RJTT - JMA daily table Haneda 44166 | archive daily | high | 202 | 0 | 131 | 64.9% | **over=71** | under=0 | 0:131 1:71 | 43 / 33 / 10 | - |
| Tokyo RJTT - JMA daily table Haneda 44166 | archive daily | low | 159 | 0 | 132 | 83.0% | **under=26** | over=1 | -1:26 0:132 5:1 | 43 / 37 / 6 | - |
| Tokyo RJTT - JMA AMeDAS 10-min files (live endpoint, 10 d) | live endpoint 10 min | high | 8 | 1 | 8 | 100.0% | **over=0** | under=0 | 0:8 | 8 / 8 / 0 | - |
| Tokyo RJTT - JMA AMeDAS 10-min files (live endpoint, 10 d) | live endpoint 10 min | low | 8 | 1 | 7 | 87.5% | **under=1** | over=0 | -1:1 0:7 | 8 / 7 / 1 | - |
| Toronto CYYZ - ECCC hourly archive (METAR-grade) | archive hourly | high | 288 | 0 | 278 | 96.5% | **over=1** | under=9 | -1:9 0:278 1:1 | 44 / 43 / 0 | - |
| Toronto CYYZ - ECCC hourly archive (METAR-grade) | archive hourly | low | 46 | 0 | 46 | 100.0% | **under=0** | over=0 | 0:46 | 44 / 44 / 0 | - |
| Toronto CYYZ - ECCC daily Max/Min (LST day) | archive daily | high | 288 | 0 | 170 | 59.0% | **over=106** | under=12 | -1:8 -2:2 -3:2 0:170 1:102 2:4 | 44 / 22 / 22 | - |
| Toronto CYYZ - ECCC daily Max/Min (LST day) | archive daily | low | 46 | 0 | 23 | 50.0% | **under=23** | over=0 | -1:18 -2:3 -3:2 0:23 | 44 / 23 / 21 | - |
| Madrid LEMD - AEMET public XML diario (7 d) | web 7 d | high | 7 | 0 | 7 | 100.0% | **over=0** | under=0 | 0:7 | 7 / 7 / 0 | - |
| Madrid LEMD - AEMET public XML diario (7 d) | web 7 d | low | 7 | 0 | 6 | 85.7% | **under=0** | over=1 | 0:6 1:1 | 7 / 6 / 0 | - |
| Singapore WSSS - NEA S24 1-min (data.gov.sg) | archive 1 min | high | 199 | 1 | 104 | 52.3% | **over=90** | under=5 | -1:5 0:104 1:86 2:4 | 43 / 19 / 23 | - |
| Singapore WSSS - NEA S24 1-min (data.gov.sg) | archive 1 min | low | 35 | 0 | 20 | 57.1% | **under=9** | over=6 | -1:9 0:20 1:6 | 32 / 18 / 9 | - |
| Helsinki EFHK - fmi_airport_temperature (live window) | live window | high | 8 | 1 | 8 | 100.0% | **over=0** | under=0 | 0:8 | 8 / 8 / 0 | -521 / +9 / +37 (n=8, earlier 6) |
| Helsinki EFHK - fmi_airport_temperature (live window) | live window | low | 8 | 1 | 6 | 75.0% | **under=2** | over=0 | -1:2 0:6 | 8 / 6 / 2 | -40 / 0 / +20 (n=6, earlier 3) |
| Munich EDDM - dwd_cdc_temperature (live window) | live window | high | 5 | 1 | 3 | 60.0% | **over=0** | under=2 | -1:2 0:3 | 5 / 3 / 0 | -86 / -85 / -85 (n=3, earlier 0) |
| Munich EDDM - dwd_cdc_temperature (live window) | live window | low | 5 | 1 | 5 | 100.0% | **under=0** | over=0 | 0:5 | 5 / 5 / 0 | -27 / -27 / -27 (n=5, earlier 1) |
| Warsaw EPWA - imgw_synop_temperature (live window) | live window | high | 4 | 1 | 2 | 50.0% | **over=1** | under=1 | -1:1 0:2 1:1 | 4 / 2 / 1 | -14 / -14 / -14 (n=2, earlier 0) |
| Warsaw EPWA - imgw_synop_temperature (live window) | live window | low | 4 | 1 | 0 | 0.0% | **under=1** | over=3 | -1:1 2:3 | 4 / 0 / 1 | - |
| Tokyo RJTT - jma_amedas_temperature (live window) | live window | high | 4 | 1 | 4 | 100.0% | **over=0** | under=0 | 0:4 | 4 / 4 / 0 | -2 / +1 / +2 (n=4, earlier 2) |
| Tokyo RJTT - jma_amedas_temperature (live window) | live window | low | 4 | 1 | 3 | 75.0% | **under=1** | over=0 | -1:1 0:3 | 4 / 3 / 1 | +2 / +2 / +2 (n=3, earlier 3) |
| Toronto CYYZ - eccc_swob_temperature (live window) | live window | high | 5 | 1 | 5 | 100.0% | **over=0** | under=0 | 0:5 | 5 / 5 / 0 | +4 / +6 / +6 (n=5, earlier 5) |
| Toronto CYYZ - eccc_swob_temperature (live window) | live window | low | 5 | 1 | 5 | 100.0% | **under=0** | over=0 | 0:5 | 5 / 5 / 0 | 0 / +5 / +5 (n=5, earlier 4) |
| Lucknow VILK - imd_olbs_metar_temperature (live window) | live window | high | 4 | 1 | 4 | 100.0% | **over=0** | under=0 | 0:4 | 4 / 4 / 0 | -2 / -2 / -2 (n=4, earlier 1) |
| Lucknow VILK - imd_olbs_metar_temperature (live window) | live window | low | 4 | 1 | 4 | 100.0% | **under=0** | over=0 | 0:4 | 4 / 4 / 0 | 0 / +2 / +2 (n=4, earlier 4) |
| Ankara LTAC - mgm_metar_temperature (live window) | live window | high | 4 | 1 | 4 | 100.0% | **over=0** | under=0 | 0:4 | 4 / 4 / 0 | +1 / +1 / +2 (n=4, earlier 4) |
| Ankara LTAC - mgm_metar_temperature (live window) | live window | low | 4 | 1 | 4 | 100.0% | **under=0** | over=0 | 0:4 | 4 / 4 / 0 | 0 / +1 / +1 (n=4, earlier 4) |
| Istanbul LTFM - mgm_metar_temperature (live window) | live window | high | 2 | 3 | 2 | 100.0% | **over=0** | under=0 | 0:2 | 2 / 2 / 0 | -3 / -1 / -3 (n=2, earlier 1) |
| Istanbul LTFM - mgm_metar_temperature (live window) | live window | low | 2 | 3 | 2 | 100.0% | **under=0** | over=0 | 0:2 | 2 / 2 / 0 | 0 / +1 / 0 (n=2, earlier 2) |
| Moscow UUWW - metaviatelecom_metar_temperature (live window) | live window | high | 0 | 0 | 0 | - | **over=0** | under=0 | - | 0 / 0 / 0 | - |
| Moscow UUWW - metaviatelecom_metar_temperature (live window) | live window | low | 0 | 0 | 0 | - | **under=0** | over=0 | - | 0 / 0 / 0 | - |

### VERDICTS

| Candidate | metric | all VERIFIED days: n / eq% / dangerous | WRH-era only: n / eq% / dangerous | live lead p50 (min) | 95 % upper bound on dangerous rate (all days) | verdict |
|---|---|---|---|---|---|---|
| Helsinki EFHK - FMI WFS 10-min (fmi_airport_temperature) | high | 177 / 77.4% / 38 | 43 / 83.7% / 6 | n/a (no live prints) | 27.2% | FAIL |
| Helsinki EFHK - FMI WFS 10-min (fmi_airport_temperature) | low | 46 / 89.1% / 5 | 43 / 88.4% / 5 | n/a (no live prints) | 21.5% | FAIL |
| Munich EDDM - DWD CDC TT_10 10-min (dwd_cdc_temperature) | high | 203 / 71.9% / 7 | 41 / 65.9% / 0 | n/a (no live prints) | 6.4% | FAIL |
| Munich EDDM - DWD CDC TT_10 10-min (dwd_cdc_temperature) | low | 44 / 88.6% / 5 | 41 / 87.8% / 5 | n/a (no live prints) | 22.4% | FAIL |
| Warsaw EPWA - IMGW synop hourly (imgw_synop_temperature) | high | 188 / 70.2% / 15 | 38 / 73.7% / 5 | n/a (no live prints) | 12.0% | FAIL |
| Warsaw EPWA - IMGW synop hourly (imgw_synop_temperature) | low | 41 / 43.9% / 2 | 38 / 42.1% / 2 | n/a (no live prints) | 14.6% | FAIL |
| Amsterdam EHAM - KNMI 10-min files, Tx12/Tn12 true extreme | high | 176 / 58.5% / 73 | 43 / 60.5% / 17 | n/a (no live prints) | 47.9% | FAIL |
| Amsterdam EHAM - KNMI 10-min files, Tx12/Tn12 true extreme | low | 46 / 63.0% / 17 | 43 / 67.4% / 14 | n/a (no live prints) | 50.1% | FAIL |
| London EGLC - Met Office gcpvj0 hourly spot, 48 h window | high | 1 / 100.0% / 0 | 1 / 100.0% / 0 | n/a (no live prints) | 95.0% | INSUFFICIENT n (1 d) |
| London EGLC - Met Office gcpvj0 hourly spot, 48 h window | low | 1 / 0.0% / 1 | 1 / 0.0% / 1 | n/a (no live prints) | 100.0% | INSUFFICIENT n (1 d) |
| Tokyo RJTT - JMA daily table Haneda 44166 | high | 202 / 64.9% / 71 | 43 / 76.7% / 10 | n/a (no live prints) | 41.1% | FAIL |
| Tokyo RJTT - JMA daily table Haneda 44166 | low | 159 / 83.0% / 26 | 43 / 86.0% / 6 | n/a (no live prints) | 22.0% | FAIL |
| Tokyo RJTT - JMA AMeDAS 10-min files (live endpoint, 10 d) | high | 8 / 100.0% / 0 | 8 / 100.0% / 0 | n/a (no live prints) | 31.2% | INSUFFICIENT n (8 d) |
| Tokyo RJTT - JMA AMeDAS 10-min files (live endpoint, 10 d) | low | 8 / 87.5% / 1 | 8 / 87.5% / 1 | n/a (no live prints) | 47.1% | INSUFFICIENT n (8 d) |
| Toronto CYYZ - ECCC hourly archive (METAR-grade) | high | 288 / 96.5% / 1 | 44 / 97.7% / 0 | n/a (no live prints) | 1.6% | accuracy PASS (WRH era), lead unmeasured |
| Toronto CYYZ - ECCC hourly archive (METAR-grade) | low | 46 / 100.0% / 0 | 44 / 100.0% / 0 | n/a (no live prints) | 6.3% | accuracy PASS (WRH era), lead unmeasured |
| Toronto CYYZ - ECCC daily Max/Min (LST day) | high | 288 / 59.0% / 106 | 44 / 50.0% / 22 | n/a (no live prints) | 41.7% | FAIL |
| Toronto CYYZ - ECCC daily Max/Min (LST day) | low | 46 / 50.0% / 23 | 44 / 52.3% / 21 | n/a (no live prints) | 62.9% | FAIL |
| Madrid LEMD - AEMET public XML diario (7 d) | high | 7 / 100.0% / 0 | 7 / 100.0% / 0 | n/a (no live prints) | 34.8% | INSUFFICIENT n (7 d) |
| Madrid LEMD - AEMET public XML diario (7 d) | low | 7 / 85.7% / 0 | 7 / 85.7% / 0 | n/a (no live prints) | 34.8% | INSUFFICIENT n (7 d) |
| Singapore WSSS - NEA S24 1-min (data.gov.sg) | high | 199 / 52.3% / 90 | 43 / 44.2% / 23 | n/a (no live prints) | 51.3% | FAIL |
| Singapore WSSS - NEA S24 1-min (data.gov.sg) | low | 35 / 57.1% / 9 | 32 / 56.2% / 9 | n/a (no live prints) | 40.6% | FAIL |
| Helsinki EFHK - fmi_airport_temperature (live window) | high | 8 / 100.0% / 0 | 8 / 100.0% / 0 | +9 | 31.2% | INSUFFICIENT n (8 d) |
| Helsinki EFHK - fmi_airport_temperature (live window) | low | 8 / 75.0% / 2 | 8 / 75.0% / 2 | -0 | 60.0% | INSUFFICIENT n (8 d) |
| Munich EDDM - dwd_cdc_temperature (live window) | high | 5 / 60.0% / 0 | 5 / 60.0% / 0 | -85 | 45.1% | INSUFFICIENT n (5 d) |
| Munich EDDM - dwd_cdc_temperature (live window) | low | 5 / 100.0% / 0 | 5 / 100.0% / 0 | -27 | 45.1% | INSUFFICIENT n (5 d) |
| Warsaw EPWA - imgw_synop_temperature (live window) | high | 4 / 50.0% / 1 | 4 / 50.0% / 1 | -14 | 75.1% | INSUFFICIENT n (4 d) |
| Warsaw EPWA - imgw_synop_temperature (live window) | low | 4 / 0.0% / 1 | 4 / 0.0% / 1 | n/a (no live prints) | 75.1% | INSUFFICIENT n (4 d) |
| Tokyo RJTT - jma_amedas_temperature (live window) | high | 4 / 100.0% / 0 | 4 / 100.0% / 0 | +1 | 52.7% | INSUFFICIENT n (4 d) |
| Tokyo RJTT - jma_amedas_temperature (live window) | low | 4 / 75.0% / 1 | 4 / 75.0% / 1 | +2 | 75.1% | INSUFFICIENT n (4 d) |
| Toronto CYYZ - eccc_swob_temperature (live window) | high | 5 / 100.0% / 0 | 5 / 100.0% / 0 | +6 | 45.1% | INSUFFICIENT n (5 d) |
| Toronto CYYZ - eccc_swob_temperature (live window) | low | 5 / 100.0% / 0 | 5 / 100.0% / 0 | +5 | 45.1% | INSUFFICIENT n (5 d) |
| Lucknow VILK - imd_olbs_metar_temperature (live window) | high | 4 / 100.0% / 0 | 4 / 100.0% / 0 | -2 | 52.7% | INSUFFICIENT n (4 d) |
| Lucknow VILK - imd_olbs_metar_temperature (live window) | low | 4 / 100.0% / 0 | 4 / 100.0% / 0 | +2 | 52.7% | INSUFFICIENT n (4 d) |
| Ankara LTAC - mgm_metar_temperature (live window) | high | 4 / 100.0% / 0 | 4 / 100.0% / 0 | +1 | 52.7% | INSUFFICIENT n (4 d) |
| Ankara LTAC - mgm_metar_temperature (live window) | low | 4 / 100.0% / 0 | 4 / 100.0% / 0 | +1 | 52.7% | INSUFFICIENT n (4 d) |
| Istanbul LTFM - mgm_metar_temperature (live window) | high | 2 / 100.0% / 0 | 2 / 100.0% / 0 | -1 | 77.6% | INSUFFICIENT n (2 d) |
| Istanbul LTFM - mgm_metar_temperature (live window) | low | 2 / 100.0% / 0 | 2 / 100.0% / 0 | +1 | 77.6% | INSUFFICIENT n (2 d) |
| Moscow UUWW - metaviatelecom_metar_temperature (live window) | high | 0 / - / 0 | 0 / - / 0 | n/a (no live prints) | - | INSUFFICIENT n (0 d) |
| Moscow UUWW - metaviatelecom_metar_temperature (live window) | low | 0 / - / 0 | 0 / - / 0 | n/a (no live prints) | - | INSUFFICIENT n (0 d) |

### LIVE

| Live WORLD channel | metric | n_days | dropped | eq | dangerous | other side | diff hist | same-window AWC eq/n (dangerous) | lead all fetches p10/p50/p90 | lead no-backfill p10/p50/p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| Helsinki EFHK fmi_airport_temperature | high | 8 | 1 | 8 | **over=0** | under=0 | 0:8 | 9/9 (0) | -521 / +9 / +37 (n=8, earlier 6) | -521 / +9 / +37 (n=8, earlier 6) |
| Helsinki EFHK fmi_airport_temperature | low | 8 | 1 | 6 | **under=2** | over=0 | -1:2 0:6 | 9/9 (0) | -40 / 0 / +20 (n=6, earlier 3) | -2 / +1 / +20 (n=5, earlier 3) |
| Munich EDDM dwd_cdc_temperature | high | 5 | 1 | 3 | **over=0** | under=2 | -1:2 0:3 | 6/6 (0) | -86 / -85 / -85 (n=3, earlier 0) | -86 / -85 / -85 (n=3, earlier 0) |
| Munich EDDM dwd_cdc_temperature | low | 5 | 1 | 5 | **under=0** | over=0 | 0:5 | 6/6 (0) | -27 / -27 / -27 (n=5, earlier 1) | -27 / -27 / -27 (n=5, earlier 1) |
| Warsaw EPWA imgw_synop_temperature | high | 4 | 1 | 2 | **over=1** | under=1 | -1:1 0:2 1:1 | 5/5 (0) | -14 / -14 / -14 (n=2, earlier 0) | -14 / -14 / -14 (n=2, earlier 0) |
| Warsaw EPWA imgw_synop_temperature | low | 4 | 1 | 0 | **under=1** | over=3 | -1:1 2:3 | 5/5 (0) | - | - |
| Tokyo RJTT jma_amedas_temperature | high | 4 | 1 | 4 | **over=0** | under=0 | 0:4 | 5/5 (0) | -2 / +1 / +2 (n=4, earlier 2) | -2 / +1 / +2 (n=4, earlier 2) |
| Tokyo RJTT jma_amedas_temperature | low | 4 | 1 | 3 | **under=1** | over=0 | -1:1 0:3 | 5/5 (0) | +2 / +2 / +2 (n=3, earlier 3) | +2 / +2 / +2 (n=3, earlier 3) |
| Toronto CYYZ eccc_swob_temperature | high | 5 | 1 | 5 | **over=0** | under=0 | 0:5 | 6/6 (0) | +4 / +6 / +6 (n=5, earlier 5) | +4 / +6 / +6 (n=5, earlier 5) |
| Toronto CYYZ eccc_swob_temperature | low | 5 | 1 | 5 | **under=0** | over=0 | 0:5 | 6/6 (0) | 0 / +5 / +5 (n=5, earlier 4) | 0 / +5 / +5 (n=5, earlier 4) |
| Lucknow VILK imd_olbs_metar_temperature | high | 4 | 1 | 4 | **over=0** | under=0 | 0:4 | 5/5 (0) | -2 / -2 / -2 (n=4, earlier 1) | -20 / -2 / -2 (n=4, earlier 1) |
| Lucknow VILK imd_olbs_metar_temperature | low | 4 | 1 | 4 | **under=0** | over=0 | 0:4 | 5/5 (0) | 0 / +2 / +2 (n=4, earlier 4) | 0 / +2 / +2 (n=4, earlier 4) |
| Ankara LTAC mgm_metar_temperature | high | 4 | 1 | 4 | **over=0** | under=0 | 0:4 | 5/5 (0) | +1 / +1 / +2 (n=4, earlier 4) | +1 / +1 / +2 (n=4, earlier 4) |
| Ankara LTAC mgm_metar_temperature | low | 4 | 1 | 4 | **under=0** | over=0 | 0:4 | 5/5 (0) | 0 / +1 / +1 (n=4, earlier 4) | 0 / +1 / +1 (n=4, earlier 4) |
| Istanbul LTFM mgm_metar_temperature | high | 2 | 3 | 2 | **over=0** | under=0 | 0:2 | 5/5 (0) | -3 / -1 / -3 (n=2, earlier 1) | -9 / -4 / -9 (n=2, earlier 1) |
| Istanbul LTFM mgm_metar_temperature | low | 2 | 3 | 2 | **under=0** | over=0 | 0:2 | 5/5 (0) | 0 / +1 / 0 (n=2, earlier 2) | 0 / +1 / 0 (n=2, earlier 2) |
| Moscow UUWW metaviatelecom_metar_temperature | high | 0 | 0 | 0 | **over=0** | under=0 | - | - | - | - |
| Moscow UUWW metaviatelecom_metar_temperature | low | 0 | 0 | 0 | **under=0** | over=0 | - | - | - | - |

### CONTROLS

| Control | metric | n_days | dropped | eq | eq % | dangerous | other side | diff hist |
|---|---|---|---|---|---|---|---|---|
| Helsinki FMI 10-min, only :20/:50 readings | high | 177 | 0 | 148 | 83.6% | **over=16** | under=13 | -1:13 0:148 1:16 |
| Helsinki FMI 10-min, only :20/:50 readings | low | 46 | 0 | 46 | 100.0% | **under=0** | over=0 | 0:46 |
| Munich DWD TT_10, only :20/:50 readings | high | 203 | 2 | 126 | 62.1% | **over=1** | under=76 | -1:76 0:126 1:1 |
| Munich DWD TT_10, only :20/:50 readings | low | 44 | 2 | 39 | 88.6% | **under=3** | over=2 | -1:3 0:39 1:2 |
| Munich DWD TX_10/TN_10 (10-min extrema columns) | high | 203 | 2 | 165 | 81.3% | **over=23** | under=15 | -1:15 0:165 1:23 |
| Munich DWD TX_10/TN_10 (10-min extrema columns) | low | 44 | 2 | 34 | 77.3% | **under=10** | over=0 | -1:10 0:34 |
| Singapore NEA S24, only :00/:30 readings | high | 199 | 1 | 137 | 68.8% | **over=37** | under=25 | -1:25 0:137 1:37 |
| Singapore NEA S24, only :00/:30 readings | low | 35 | 0 | 22 | 62.9% | **under=6** | over=7 | -1:6 0:22 1:7 |

### AWC

| City / station | metric | n_days | dropped | eq | dangerous | other side | diff hist | WRH-era n / eq / dangerous | pre-WRH n / eq / dangerous |
|---|---|---|---|---|---|---|---|---|---|
| Helsinki EFHK | high | 64 | 16 | 61 | **over=0** | under=3 | -1:3 0:61 | 43 / 42 / 0 | 21 / 19 / 0 |
| Helsinki EFHK | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Munich EDDM | high | 76 | 3 | 75 | **over=0** | under=1 | -1:1 0:75 | 43 / 43 / 0 | 33 / 32 / 0 |
| Munich EDDM | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Warsaw EPWA | high | 65 | 13 | 65 | **over=0** | under=0 | 0:65 | 43 / 43 / 0 | 22 / 22 / 0 |
| Warsaw EPWA | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Amsterdam EHAM | high | 77 | 2 | 77 | **over=0** | under=0 | 0:77 | 43 / 43 / 0 | 34 / 34 / 0 |
| Amsterdam EHAM | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| London EGLC | high | 74 | 5 | 74 | **over=0** | under=0 | 0:74 | 43 / 43 / 0 | 31 / 31 / 0 |
| London EGLC | low | 74 | 5 | 74 | **under=0** | over=0 | 0:74 | 43 / 43 / 0 | 31 / 31 / 0 |
| Tokyo RJTT | high | 78 | 4 | 77 | **over=0** | under=1 | -3:1 0:77 | 43 / 43 / 0 | 35 / 34 / 0 |
| Tokyo RJTT | low | 78 | 4 | 78 | **under=0** | over=0 | 0:78 | 43 / 43 / 0 | 35 / 35 / 0 |
| Toronto CYYZ | high | 78 | 3 | 77 | **over=0** | under=1 | -1:1 0:77 | 43 / 42 / 0 | 35 / 35 / 0 |
| Toronto CYYZ | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Ankara LTAC | high | 74 | 5 | 74 | **over=0** | under=0 | 0:74 | 43 / 43 / 0 | 31 / 31 / 0 |
| Ankara LTAC | low | 46 | 0 | 45 | **under=1** | over=0 | -1:1 0:45 | 43 / 42 / 1 | 3 / 3 / 0 |
| Istanbul LTFM | high | 67 | 13 | 66 | **over=0** | under=1 | -1:1 0:66 | 43 / 42 / 0 | 24 / 24 / 0 |
| Istanbul LTFM | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Lucknow VILK | high | 63 | 17 | 62 | **over=0** | under=1 | -2:1 0:62 | 43 / 42 / 0 | 20 / 20 / 0 |
| Lucknow VILK | low | 46 | 0 | 46 | **under=0** | over=0 | 0:46 | 43 / 43 / 0 | 3 / 3 / 0 |
| Moscow UUWW | high | 68 | 12 | 64 | **over=4** | under=0 | 0:64 1:4 | 43 / 43 / 0 | 25 / 21 / 4 |
| Moscow UUWW | low | 46 | 0 | 45 | **under=0** | over=1 | 0:45 1:1 | 43 / 42 / 0 | 3 / 3 / 0 |
| Madrid LEMD | high | 77 | 2 | 76 | **over=0** | under=1 | -1:1 0:76 | 43 / 43 / 0 | 34 / 33 / 0 |
| Madrid LEMD | low | 46 | 0 | 45 | **under=1** | over=0 | -2:1 0:45 | 43 / 42 / 1 | 3 / 3 / 0 |
| Singapore WSSS | high | 77 | 5 | 76 | **over=1** | under=0 | 0:76 1:1 | 43 / 42 / 1 | 34 / 34 / 0 |
| Singapore WSSS | low | 35 | 0 | 35 | **under=0** | over=0 | 0:35 | 32 / 32 / 0 | 3 / 3 / 0 |
| Sao Paulo SBGR | high | 62 | 18 | 62 | **over=0** | under=0 | 0:62 | 41 / 41 / 0 | 21 / 21 / 0 |
| Sao Paulo SBGR | low | 43 | 3 | 43 | **under=0** | over=0 | 0:43 | 41 / 41 / 0 | 2 / 2 / 0 |
