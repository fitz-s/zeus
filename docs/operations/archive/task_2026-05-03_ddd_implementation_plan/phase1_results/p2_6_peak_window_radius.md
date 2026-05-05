# §2.6 Peak Window Radius — v2

Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C5

> Paris pending workstream A resync; rerun for Paris after A completes.

## Definition

Miss rate = fraction of days where achieved-extremum hour fell OUTSIDE peak ± radius.
Threshold: > 5% miss rate at ± 3 → try ± 4, ± 5.

## Results

| city | metric | season | n_days | miss_r3 | miss_r4 | miss_r5 | rec_radius |
|---|---|---|---|---|---|---|---|
| Amsterdam | high | DJF | 240 | 0.4708 | 0.4167 | 0.3917 | expand_beyond_5 |
| Amsterdam | high | JJA | 184 | 0.0870 | 0.0435 | 0.0272 | 4 |
| Amsterdam | high | MAM | 247 | 0.1417 | 0.0931 | 0.0769 | expand_beyond_5 |
| Amsterdam | high | SON | 182 | 0.2033 | 0.1703 | 0.1593 | expand_beyond_5 |
| Amsterdam | low | DJF | 240 | 0.6083 | 0.5625 | 0.2708 | expand_beyond_5 |
| Amsterdam | low | JJA | 184 | 0.4022 | 0.3424 | 0.2011 | expand_beyond_5 |
| Amsterdam | low | MAM | 247 | 0.3846 | 0.3077 | 0.1862 | expand_beyond_5 |
| Amsterdam | low | SON | 182 | 0.5769 | 0.5165 | 0.3132 | expand_beyond_5 |
| Ankara | high | DJF | 240 | 0.1208 | 0.0833 | 0.0750 | expand_beyond_5 |
| Ankara | high | JJA | 184 | 0.0109 | 0.0054 | 0.0000 | 3 |
| Ankara | high | MAM | 247 | 0.0810 | 0.0526 | 0.0445 | 5 |
| Ankara | high | SON | 182 | 0.0220 | 0.0000 | 0.0000 | 3 |
| Ankara | low | DJF | 240 | 0.3583 | 0.3042 | 0.2042 | expand_beyond_5 |
| Ankara | low | JJA | 184 | 0.0435 | 0.0435 | 0.0380 | 3 |
| Ankara | low | MAM | 247 | 0.2146 | 0.1741 | 0.1215 | expand_beyond_5 |
| Ankara | low | SON | 182 | 0.1868 | 0.1538 | 0.1099 | expand_beyond_5 |
| Atlanta | high | DJF | 240 | 0.6417 | 0.3042 | 0.1917 | expand_beyond_5 |
| Atlanta | high | JJA | 184 | 0.4239 | 0.1576 | 0.0489 | 5 |
| Atlanta | high | MAM | 247 | 0.3077 | 0.1660 | 0.1174 | expand_beyond_5 |
| Atlanta | high | SON | 182 | 0.5440 | 0.2692 | 0.1264 | expand_beyond_5 |
| Atlanta | low | DJF | 240 | 0.3375 | 0.3125 | 0.2833 | expand_beyond_5 |
| Atlanta | low | JJA | 184 | 0.2826 | 0.1957 | 0.1522 | expand_beyond_5 |
| Atlanta | low | MAM | 247 | 0.2551 | 0.1984 | 0.1700 | expand_beyond_5 |
| Atlanta | low | SON | 182 | 0.2473 | 0.1868 | 0.1758 | expand_beyond_5 |
| Auckland | high | DJF | 240 | 0.0667 | 0.0333 | 0.0250 | 4 |
| Auckland | high | JJA | 184 | 0.1467 | 0.0707 | 0.0598 | expand_beyond_5 |
| Auckland | high | MAM | 247 | 0.1134 | 0.0688 | 0.0486 | 5 |
| Auckland | high | SON | 182 | 0.1319 | 0.0714 | 0.0440 | 5 |
| Auckland | low | DJF | 240 | 0.3958 | 0.2917 | 0.2500 | expand_beyond_5 |
| Auckland | low | JJA | 184 | 0.4783 | 0.4076 | 0.3533 | expand_beyond_5 |
| Auckland | low | MAM | 247 | 0.4008 | 0.3482 | 0.2794 | expand_beyond_5 |
| Auckland | low | SON | 182 | 0.5000 | 0.4505 | 0.4011 | expand_beyond_5 |
| Austin | high | DJF | 240 | 0.6458 | 0.3083 | 0.1792 | expand_beyond_5 |
| Austin | high | JJA | 184 | 0.3424 | 0.1141 | 0.0652 | expand_beyond_5 |
| Austin | high | MAM | 247 | 0.3482 | 0.1700 | 0.1053 | expand_beyond_5 |
| Austin | high | SON | 182 | 0.4780 | 0.1484 | 0.0604 | expand_beyond_5 |
| Austin | low | DJF | 240 | 0.5250 | 0.4500 | 0.4208 | expand_beyond_5 |
| Austin | low | JJA | 184 | 0.2446 | 0.2011 | 0.1685 | expand_beyond_5 |
| Austin | low | MAM | 247 | 0.4534 | 0.3846 | 0.3117 | expand_beyond_5 |
| Austin | low | SON | 182 | 0.2747 | 0.2308 | 0.1813 | expand_beyond_5 |
| Beijing | high | DJF | 240 | 0.0833 | 0.0500 | 0.0417 | 4 |
| Beijing | high | JJA | 184 | 0.1033 | 0.0707 | 0.0598 | expand_beyond_5 |
| Beijing | high | MAM | 247 | 0.1053 | 0.0810 | 0.0688 | expand_beyond_5 |
| Beijing | high | SON | 182 | 0.1154 | 0.0604 | 0.0385 | 5 |
| Beijing | low | DJF | 240 | 0.3417 | 0.2708 | 0.1708 | expand_beyond_5 |
| Beijing | low | JJA | 184 | 0.4022 | 0.3370 | 0.1630 | expand_beyond_5 |
| Beijing | low | MAM | 247 | 0.2146 | 0.1822 | 0.1498 | expand_beyond_5 |
| Beijing | low | SON | 182 | 0.3901 | 0.3407 | 0.2418 | expand_beyond_5 |
| Buenos Aires | high | DJF | 240 | 0.0667 | 0.0375 | 0.0292 | 4 |
| Buenos Aires | high | JJA | 184 | 0.1522 | 0.0924 | 0.0815 | expand_beyond_5 |
| Buenos Aires | high | MAM | 247 | 0.1862 | 0.0972 | 0.0688 | expand_beyond_5 |
| Buenos Aires | high | SON | 182 | 0.1923 | 0.1099 | 0.0879 | expand_beyond_5 |
| Buenos Aires | low | DJF | 240 | 0.3167 | 0.2250 | 0.1792 | expand_beyond_5 |
| Buenos Aires | low | JJA | 184 | 0.3859 | 0.3207 | 0.2772 | expand_beyond_5 |
| Buenos Aires | low | MAM | 247 | 0.4494 | 0.3806 | 0.3077 | expand_beyond_5 |
| Buenos Aires | low | SON | 182 | 0.3626 | 0.3132 | 0.2308 | expand_beyond_5 |
| Busan | high | DJF | 240 | 0.0667 | 0.0500 | 0.0500 | 4 |
| Busan | high | JJA | 184 | 0.0924 | 0.0435 | 0.0217 | 4 |
| Busan | high | MAM | 247 | 0.0931 | 0.0648 | 0.0526 | expand_beyond_5 |
| Busan | high | SON | 182 | 0.0934 | 0.0604 | 0.0440 | 5 |
| Busan | low | DJF | 240 | 0.3875 | 0.3125 | 0.2458 | expand_beyond_5 |
| Busan | low | JJA | 184 | 0.4130 | 0.2609 | 0.1630 | expand_beyond_5 |
| Busan | low | MAM | 247 | 0.3117 | 0.2551 | 0.2024 | expand_beyond_5 |
| Busan | low | SON | 182 | 0.3846 | 0.2857 | 0.2143 | expand_beyond_5 |
| Cape Town | high | DJF | 217 | 0.0415 | 0.0046 | 0.0000 | 3 |
| Cape Town | high | JJA | 184 | 0.0978 | 0.0652 | 0.0489 | 5 |
| Cape Town | high | MAM | 247 | 0.0526 | 0.0324 | 0.0283 | 4 |
| Cape Town | high | SON | 182 | 0.0604 | 0.0110 | 0.0000 | 4 |
| Cape Town | low | DJF | 217 | 0.3733 | 0.2719 | 0.1705 | expand_beyond_5 |
| Cape Town | low | JJA | 184 | 0.4511 | 0.4076 | 0.3261 | expand_beyond_5 |
| Cape Town | low | MAM | 247 | 0.3927 | 0.3239 | 0.2429 | expand_beyond_5 |
| Cape Town | low | SON | 182 | 0.3901 | 0.3132 | 0.2473 | expand_beyond_5 |
| Chengdu | high | DJF | 240 | 0.1542 | 0.1333 | 0.1333 | expand_beyond_5 |
| Chengdu | high | JJA | 184 | 0.1304 | 0.1196 | 0.1196 | expand_beyond_5 |
| Chengdu | high | MAM | 247 | 0.1377 | 0.1174 | 0.1174 | expand_beyond_5 |
| Chengdu | high | SON | 182 | 0.1593 | 0.1154 | 0.1099 | expand_beyond_5 |
| Chengdu | low | DJF | 240 | 0.3208 | 0.2292 | 0.1792 | expand_beyond_5 |
| Chengdu | low | JJA | 184 | 0.2772 | 0.1957 | 0.1467 | expand_beyond_5 |
| Chengdu | low | MAM | 247 | 0.2794 | 0.1903 | 0.1619 | expand_beyond_5 |
| Chengdu | low | SON | 182 | 0.3846 | 0.2527 | 0.1978 | expand_beyond_5 |
| Chicago | high | DJF | 240 | 0.4417 | 0.3458 | 0.2625 | expand_beyond_5 |
| Chicago | high | JJA | 184 | 0.2500 | 0.1413 | 0.0652 | expand_beyond_5 |
| Chicago | high | MAM | 247 | 0.3239 | 0.2591 | 0.2146 | expand_beyond_5 |
| Chicago | high | SON | 182 | 0.3571 | 0.2033 | 0.1319 | expand_beyond_5 |
| Chicago | low | DJF | 240 | 0.5333 | 0.4875 | 0.2708 | expand_beyond_5 |
| Chicago | low | JJA | 184 | 0.3478 | 0.2826 | 0.2011 | expand_beyond_5 |
| Chicago | low | MAM | 247 | 0.4615 | 0.4089 | 0.2874 | expand_beyond_5 |
| Chicago | low | SON | 182 | 0.3352 | 0.2802 | 0.2143 | expand_beyond_5 |
| Chongqing | high | DJF | 240 | 0.2375 | 0.2125 | 0.2083 | expand_beyond_5 |
| Chongqing | high | JJA | 184 | 0.1304 | 0.0924 | 0.0870 | expand_beyond_5 |
| Chongqing | high | MAM | 247 | 0.1943 | 0.1538 | 0.1457 | expand_beyond_5 |
| Chongqing | high | SON | 182 | 0.2033 | 0.1758 | 0.1538 | expand_beyond_5 |
| Chongqing | low | DJF | 240 | 0.4333 | 0.3542 | 0.2917 | expand_beyond_5 |
| Chongqing | low | JJA | 184 | 0.3533 | 0.2609 | 0.2065 | expand_beyond_5 |
| Chongqing | low | MAM | 247 | 0.3077 | 0.2429 | 0.1741 | expand_beyond_5 |
| Chongqing | low | SON | 182 | 0.4066 | 0.2967 | 0.2527 | expand_beyond_5 |
| Dallas | high | DJF | 240 | 0.6167 | 0.3500 | 0.1792 | expand_beyond_5 |
| Dallas | high | JJA | 184 | 0.3424 | 0.1359 | 0.0598 | expand_beyond_5 |
| Dallas | high | MAM | 247 | 0.4170 | 0.2267 | 0.1619 | expand_beyond_5 |
| Dallas | high | SON | 182 | 0.4780 | 0.2088 | 0.0769 | expand_beyond_5 |
| Dallas | low | DJF | 240 | 0.3458 | 0.2958 | 0.2750 | expand_beyond_5 |
| Dallas | low | JJA | 184 | 0.1467 | 0.1250 | 0.1087 | expand_beyond_5 |
| Dallas | low | MAM | 247 | 0.3036 | 0.2470 | 0.2186 | expand_beyond_5 |
| Dallas | low | SON | 182 | 0.1868 | 0.1703 | 0.1593 | expand_beyond_5 |
| Denver | high | DJF | 240 | 0.5000 | 0.2625 | 0.1208 | expand_beyond_5 |
| Denver | high | JJA | 182 | 0.1593 | 0.0549 | 0.0220 | 5 |
| Denver | high | MAM | 247 | 0.1984 | 0.1255 | 0.0729 | expand_beyond_5 |
| Denver | high | SON | 181 | 0.2873 | 0.1381 | 0.1050 | expand_beyond_5 |
| Denver | low | DJF | 240 | 0.4792 | 0.4167 | 0.2917 | expand_beyond_5 |
| Denver | low | JJA | 182 | 0.1868 | 0.1484 | 0.0934 | expand_beyond_5 |
| Denver | low | MAM | 247 | 0.3441 | 0.2591 | 0.1781 | expand_beyond_5 |
| Denver | low | SON | 181 | 0.3149 | 0.2707 | 0.1934 | expand_beyond_5 |
| Guangzhou | high | DJF | 240 | 0.0958 | 0.0833 | 0.0833 | expand_beyond_5 |
| Guangzhou | high | JJA | 184 | 0.1033 | 0.0543 | 0.0326 | 5 |
| Guangzhou | high | MAM | 247 | 0.1538 | 0.1134 | 0.0891 | expand_beyond_5 |
| Guangzhou | high | SON | 182 | 0.0549 | 0.0495 | 0.0385 | 4 |
| Guangzhou | low | DJF | 240 | 0.3042 | 0.2500 | 0.1792 | expand_beyond_5 |
| Guangzhou | low | JJA | 184 | 0.5489 | 0.4674 | 0.3859 | expand_beyond_5 |
| Guangzhou | low | MAM | 247 | 0.5425 | 0.4615 | 0.3684 | expand_beyond_5 |
| Guangzhou | low | SON | 182 | 0.3681 | 0.3022 | 0.2473 | expand_beyond_5 |
| Helsinki | high | DJF | 240 | 0.5833 | 0.5375 | 0.5000 | expand_beyond_5 |
| Helsinki | high | JJA | 184 | 0.0870 | 0.0326 | 0.0217 | 4 |
| Helsinki | high | MAM | 247 | 0.1296 | 0.1134 | 0.1012 | expand_beyond_5 |
| Helsinki | high | SON | 182 | 0.3516 | 0.3187 | 0.2967 | expand_beyond_5 |
| Helsinki | low | DJF | 240 | 0.7250 | 0.4292 | 0.3000 | expand_beyond_5 |
| Helsinki | low | JJA | 184 | 0.2935 | 0.1250 | 0.0326 | 5 |
| Helsinki | low | MAM | 247 | 0.3927 | 0.2024 | 0.1093 | expand_beyond_5 |
| Helsinki | low | SON | 182 | 0.5714 | 0.3407 | 0.2527 | expand_beyond_5 |
| Houston | high | DJF | 240 | 0.5125 | 0.2792 | 0.1625 | expand_beyond_5 |
| Houston | high | JJA | 184 | 0.4402 | 0.2663 | 0.1304 | expand_beyond_5 |
| Houston | high | MAM | 247 | 0.4211 | 0.2672 | 0.1215 | expand_beyond_5 |
| Houston | high | SON | 182 | 0.4780 | 0.1703 | 0.0659 | expand_beyond_5 |
| Houston | low | DJF | 240 | 0.4542 | 0.3875 | 0.3542 | expand_beyond_5 |
| Houston | low | JJA | 184 | 0.3370 | 0.2228 | 0.1630 | expand_beyond_5 |
| Houston | low | MAM | 247 | 0.4413 | 0.3603 | 0.2834 | expand_beyond_5 |
| Houston | low | SON | 182 | 0.3022 | 0.2308 | 0.1648 | expand_beyond_5 |
| Jakarta | high | DJF | 240 | 0.1083 | 0.0333 | 0.0042 | 4 |
| Jakarta | high | JJA | 184 | 0.0815 | 0.0163 | 0.0000 | 4 |
| Jakarta | high | MAM | 247 | 0.0972 | 0.0121 | 0.0081 | 4 |
| Jakarta | high | SON | 182 | 0.1264 | 0.0275 | 0.0165 | 4 |
| Jakarta | low | DJF | 240 | 0.3958 | 0.3417 | 0.1375 | expand_beyond_5 |
| Jakarta | low | JJA | 184 | 0.1630 | 0.1630 | 0.1087 | expand_beyond_5 |
| Jakarta | low | MAM | 247 | 0.3117 | 0.2753 | 0.1134 | expand_beyond_5 |
| Jakarta | low | SON | 182 | 0.2967 | 0.2692 | 0.1484 | expand_beyond_5 |
| Jeddah | high | DJF | 240 | 0.0208 | 0.0083 | 0.0042 | 3 |
| Jeddah | high | JJA | 184 | 0.0326 | 0.0217 | 0.0217 | 3 |
| Jeddah | high | MAM | 247 | 0.0445 | 0.0121 | 0.0081 | 3 |
| Jeddah | high | SON | 182 | 0.0330 | 0.0055 | 0.0055 | 3 |
| Jeddah | low | DJF | 240 | 0.1500 | 0.0833 | 0.0667 | expand_beyond_5 |
| Jeddah | low | JJA | 184 | 0.1576 | 0.1304 | 0.0815 | expand_beyond_5 |
| Jeddah | low | MAM | 247 | 0.1579 | 0.1134 | 0.0931 | expand_beyond_5 |
| Jeddah | low | SON | 182 | 0.1429 | 0.0714 | 0.0275 | 5 |
| Karachi | high | DJF | 240 | 0.0208 | 0.0125 | 0.0083 | 3 |
| Karachi | high | JJA | 184 | 0.2011 | 0.0652 | 0.0326 | 5 |
| Karachi | high | MAM | 247 | 0.0810 | 0.0162 | 0.0121 | 4 |
| Karachi | high | SON | 182 | 0.0824 | 0.0275 | 0.0055 | 4 |
| Karachi | low | DJF | 240 | 0.0917 | 0.0542 | 0.0250 | 5 |
| Karachi | low | JJA | 184 | 0.6522 | 0.5109 | 0.4293 | expand_beyond_5 |
| Karachi | low | MAM | 247 | 0.2955 | 0.2065 | 0.1619 | expand_beyond_5 |
| Karachi | low | SON | 182 | 0.3352 | 0.2857 | 0.2198 | expand_beyond_5 |
| Kuala Lumpur | high | DJF | 240 | 0.0208 | 0.0125 | 0.0125 | 3 |
| Kuala Lumpur | high | JJA | 184 | 0.0217 | 0.0109 | 0.0109 | 3 |
| Kuala Lumpur | high | MAM | 247 | 0.0121 | 0.0040 | 0.0000 | 3 |
| Kuala Lumpur | high | SON | 182 | 0.0495 | 0.0220 | 0.0110 | 3 |
| Kuala Lumpur | low | DJF | 240 | 0.4917 | 0.3708 | 0.3000 | expand_beyond_5 |
| Kuala Lumpur | low | JJA | 184 | 0.4837 | 0.3750 | 0.2283 | expand_beyond_5 |
| Kuala Lumpur | low | MAM | 247 | 0.4615 | 0.3806 | 0.2551 | expand_beyond_5 |
| Kuala Lumpur | low | SON | 182 | 0.4451 | 0.3297 | 0.2747 | expand_beyond_5 |
| Lagos | high | DJF | 236 | 0.0254 | 0.0127 | 0.0127 | 3 |
| Lagos | high | JJA | 183 | 0.0820 | 0.0437 | 0.0328 | 4 |
| Lagos | high | MAM | 241 | 0.0996 | 0.0788 | 0.0581 | expand_beyond_5 |
| Lagos | high | SON | 182 | 0.0330 | 0.0220 | 0.0110 | 3 |
| Lagos | low | DJF | 236 | 0.1992 | 0.1271 | 0.0720 | expand_beyond_5 |
| Lagos | low | JJA | 183 | 0.4590 | 0.3661 | 0.2787 | expand_beyond_5 |
| Lagos | low | MAM | 241 | 0.4149 | 0.3195 | 0.2282 | expand_beyond_5 |
| Lagos | low | SON | 182 | 0.4011 | 0.3132 | 0.2418 | expand_beyond_5 |
| London | high | DJF | 240 | 0.3917 | 0.3167 | 0.2750 | expand_beyond_5 |
| London | high | JJA | 184 | 0.0598 | 0.0272 | 0.0163 | 4 |
| London | high | MAM | 247 | 0.0810 | 0.0364 | 0.0364 | 4 |
| London | high | SON | 182 | 0.2527 | 0.1868 | 0.1593 | expand_beyond_5 |
| London | low | DJF | 240 | 0.6167 | 0.5292 | 0.3042 | expand_beyond_5 |
| London | low | JJA | 184 | 0.1848 | 0.1304 | 0.0598 | expand_beyond_5 |
| London | low | MAM | 247 | 0.3482 | 0.2713 | 0.1377 | expand_beyond_5 |
| London | low | SON | 182 | 0.4780 | 0.3901 | 0.1923 | expand_beyond_5 |
| Los Angeles | high | DJF | 240 | 0.9583 | 0.8875 | 0.7792 | expand_beyond_5 |
| Los Angeles | high | JJA | 184 | 0.9348 | 0.8098 | 0.6087 | expand_beyond_5 |
| Los Angeles | high | MAM | 247 | 0.9636 | 0.8866 | 0.6802 | expand_beyond_5 |
| Los Angeles | high | SON | 182 | 0.9890 | 0.9286 | 0.7637 | expand_beyond_5 |
| Los Angeles | low | DJF | 240 | 0.2375 | 0.1708 | 0.1042 | expand_beyond_5 |
| Los Angeles | low | JJA | 184 | 0.5380 | 0.4239 | 0.1630 | expand_beyond_5 |
| Los Angeles | low | MAM | 247 | 0.3401 | 0.2632 | 0.1417 | expand_beyond_5 |
| Los Angeles | low | SON | 182 | 0.3132 | 0.2198 | 0.1209 | expand_beyond_5 |
| Lucknow | high | DJF | 222 | 0.0991 | 0.0856 | 0.0631 | expand_beyond_5 |
| Lucknow | high | JJA | 184 | 0.1359 | 0.0707 | 0.0435 | 5 |
| Lucknow | high | MAM | 247 | 0.0364 | 0.0202 | 0.0081 | 3 |
| Lucknow | high | SON | 182 | 0.2253 | 0.0989 | 0.0385 | 5 |
| Lucknow | low | DJF | 222 | 0.2748 | 0.2072 | 0.1261 | expand_beyond_5 |
| Lucknow | low | JJA | 184 | 0.4511 | 0.3533 | 0.1576 | expand_beyond_5 |
| Lucknow | low | MAM | 247 | 0.1296 | 0.0729 | 0.0445 | 5 |
| Lucknow | low | SON | 182 | 0.2637 | 0.2088 | 0.1044 | expand_beyond_5 |
| Madrid | high | DJF | 240 | 0.0792 | 0.0333 | 0.0250 | 4 |
| Madrid | high | JJA | 184 | 0.0217 | 0.0109 | 0.0109 | 3 |
| Madrid | high | MAM | 247 | 0.0688 | 0.0526 | 0.0486 | 5 |
| Madrid | high | SON | 182 | 0.0824 | 0.0330 | 0.0220 | 4 |
| Madrid | low | DJF | 240 | 0.3542 | 0.2875 | 0.2458 | expand_beyond_5 |
| Madrid | low | JJA | 184 | 0.0543 | 0.0435 | 0.0380 | 4 |
| Madrid | low | MAM | 247 | 0.1903 | 0.1579 | 0.1134 | expand_beyond_5 |
| Madrid | low | SON | 182 | 0.2198 | 0.1868 | 0.1484 | expand_beyond_5 |
| Manila | high | DJF | 240 | 0.0833 | 0.0375 | 0.0083 | 4 |
| Manila | high | JJA | 184 | 0.1848 | 0.0707 | 0.0326 | 5 |
| Manila | high | MAM | 247 | 0.0283 | 0.0121 | 0.0040 | 3 |
| Manila | high | SON | 182 | 0.1758 | 0.1154 | 0.0604 | expand_beyond_5 |
| Manila | low | DJF | 240 | 0.4083 | 0.2833 | 0.1625 | expand_beyond_5 |
| Manila | low | JJA | 184 | 0.6304 | 0.4783 | 0.4185 | expand_beyond_5 |
| Manila | low | MAM | 247 | 0.2915 | 0.2105 | 0.1417 | expand_beyond_5 |
| Manila | low | SON | 182 | 0.5879 | 0.4231 | 0.3022 | expand_beyond_5 |
| Mexico City | high | DJF | 240 | 0.0833 | 0.0208 | 0.0083 | 4 |
| Mexico City | high | JJA | 184 | 0.1902 | 0.0652 | 0.0163 | 5 |
| Mexico City | high | MAM | 247 | 0.1215 | 0.0324 | 0.0040 | 4 |
| Mexico City | high | SON | 182 | 0.1429 | 0.0604 | 0.0055 | 5 |
| Mexico City | low | DJF | 240 | 0.0667 | 0.0333 | 0.0250 | 4 |
| Mexico City | low | JJA | 184 | 0.4130 | 0.3424 | 0.2989 | expand_beyond_5 |
| Mexico City | low | MAM | 247 | 0.0850 | 0.0526 | 0.0364 | 5 |
| Mexico City | low | SON | 182 | 0.2582 | 0.1484 | 0.0989 | expand_beyond_5 |
| Miami | high | DJF | 240 | 0.7667 | 0.5333 | 0.2708 | expand_beyond_5 |
| Miami | high | JJA | 184 | 0.7228 | 0.5109 | 0.2663 | expand_beyond_5 |
| Miami | high | MAM | 247 | 0.6437 | 0.3968 | 0.1903 | expand_beyond_5 |
| Miami | high | SON | 182 | 0.8352 | 0.6099 | 0.4011 | expand_beyond_5 |
| Miami | low | DJF | 240 | 0.3958 | 0.3000 | 0.2292 | expand_beyond_5 |
| Miami | low | JJA | 184 | 0.5598 | 0.4457 | 0.3913 | expand_beyond_5 |
| Miami | low | MAM | 247 | 0.3401 | 0.2551 | 0.1781 | expand_beyond_5 |
| Miami | low | SON | 182 | 0.4835 | 0.4066 | 0.3297 | expand_beyond_5 |
| Milan | high | DJF | 240 | 0.1500 | 0.0958 | 0.0583 | expand_beyond_5 |
| Milan | high | JJA | 184 | 0.0272 | 0.0109 | 0.0054 | 3 |
| Milan | high | MAM | 247 | 0.1255 | 0.0891 | 0.0729 | expand_beyond_5 |
| Milan | high | SON | 182 | 0.1429 | 0.0824 | 0.0659 | expand_beyond_5 |
| Milan | low | DJF | 240 | 0.5375 | 0.4333 | 0.3708 | expand_beyond_5 |
| Milan | low | JJA | 184 | 0.2989 | 0.2174 | 0.1467 | expand_beyond_5 |
| Milan | low | MAM | 247 | 0.3644 | 0.2996 | 0.2227 | expand_beyond_5 |
| Milan | low | SON | 182 | 0.4231 | 0.3681 | 0.3132 | expand_beyond_5 |
| Munich | high | DJF | 240 | 0.3250 | 0.2208 | 0.1917 | expand_beyond_5 |
| Munich | high | JJA | 184 | 0.0870 | 0.0380 | 0.0326 | 4 |
| Munich | high | MAM | 247 | 0.1457 | 0.0850 | 0.0769 | expand_beyond_5 |
| Munich | high | SON | 182 | 0.2198 | 0.1484 | 0.1154 | expand_beyond_5 |
| Munich | low | DJF | 240 | 0.6125 | 0.5417 | 0.2750 | expand_beyond_5 |
| Munich | low | JJA | 184 | 0.3098 | 0.2174 | 0.1359 | expand_beyond_5 |
| Munich | low | MAM | 247 | 0.3117 | 0.2632 | 0.1538 | expand_beyond_5 |
| Munich | low | SON | 182 | 0.5330 | 0.4396 | 0.2527 | expand_beyond_5 |
| NYC | high | DJF | 240 | 0.4292 | 0.2958 | 0.2542 | expand_beyond_5 |
| NYC | high | JJA | 184 | 0.2120 | 0.0870 | 0.0489 | 5 |
| NYC | high | MAM | 247 | 0.3117 | 0.2632 | 0.2065 | expand_beyond_5 |
| NYC | high | SON | 182 | 0.2253 | 0.1374 | 0.1209 | expand_beyond_5 |
| NYC | low | DJF | 240 | 0.5208 | 0.4542 | 0.2917 | expand_beyond_5 |
| NYC | low | JJA | 184 | 0.2989 | 0.2772 | 0.1630 | expand_beyond_5 |
| NYC | low | MAM | 247 | 0.4251 | 0.3765 | 0.2267 | expand_beyond_5 |
| NYC | low | SON | 182 | 0.3297 | 0.2857 | 0.1868 | expand_beyond_5 |
| Panama City | high | DJF | 235 | 0.0468 | 0.0170 | 0.0085 | 3 |
| Panama City | high | JJA | 184 | 0.1250 | 0.0435 | 0.0109 | 4 |
| Panama City | high | MAM | 247 | 0.0729 | 0.0121 | 0.0121 | 4 |
| Panama City | high | SON | 182 | 0.1484 | 0.0440 | 0.0165 | 4 |
| Panama City | low | DJF | 235 | 0.4043 | 0.2979 | 0.1957 | expand_beyond_5 |
| Panama City | low | JJA | 184 | 0.6467 | 0.5272 | 0.4185 | expand_beyond_5 |
| Panama City | low | MAM | 247 | 0.4413 | 0.3117 | 0.1619 | expand_beyond_5 |
| Panama City | low | SON | 182 | 0.6044 | 0.5055 | 0.3791 | expand_beyond_5 |
| San Francisco | high | DJF | 240 | 0.4708 | 0.2417 | 0.1333 | expand_beyond_5 |
| San Francisco | high | JJA | 184 | 0.6576 | 0.3261 | 0.0924 | expand_beyond_5 |
| San Francisco | high | MAM | 247 | 0.5870 | 0.3158 | 0.1417 | expand_beyond_5 |
| San Francisco | high | SON | 182 | 0.5385 | 0.3132 | 0.0934 | expand_beyond_5 |
| San Francisco | low | DJF | 240 | 0.2833 | 0.1917 | 0.1250 | expand_beyond_5 |
| San Francisco | low | JJA | 184 | 0.3152 | 0.2609 | 0.1033 | expand_beyond_5 |
| San Francisco | low | MAM | 247 | 0.2632 | 0.1984 | 0.1174 | expand_beyond_5 |
| San Francisco | low | SON | 182 | 0.2967 | 0.1978 | 0.1538 | expand_beyond_5 |
| Sao Paulo | high | DJF | 240 | 0.0792 | 0.0375 | 0.0167 | 4 |
| Sao Paulo | high | JJA | 184 | 0.0815 | 0.0598 | 0.0598 | expand_beyond_5 |
| Sao Paulo | high | MAM | 247 | 0.0567 | 0.0364 | 0.0324 | 4 |
| Sao Paulo | high | SON | 182 | 0.1429 | 0.0989 | 0.0604 | expand_beyond_5 |
| Sao Paulo | low | DJF | 240 | 0.5167 | 0.3917 | 0.3000 | expand_beyond_5 |
| Sao Paulo | low | JJA | 184 | 0.4185 | 0.3478 | 0.2554 | expand_beyond_5 |
| Sao Paulo | low | MAM | 247 | 0.4251 | 0.3198 | 0.2348 | expand_beyond_5 |
| Sao Paulo | low | SON | 182 | 0.5220 | 0.4231 | 0.3407 | expand_beyond_5 |
| Seattle | high | DJF | 240 | 0.1833 | 0.1542 | 0.1333 | expand_beyond_5 |
| Seattle | high | JJA | 184 | 0.0326 | 0.0054 | 0.0054 | 3 |
| Seattle | high | MAM | 247 | 0.0810 | 0.0486 | 0.0486 | 4 |
| Seattle | high | SON | 182 | 0.1099 | 0.0714 | 0.0604 | expand_beyond_5 |
| Seattle | low | DJF | 240 | 0.4917 | 0.4042 | 0.2250 | expand_beyond_5 |
| Seattle | low | JJA | 184 | 0.1359 | 0.0761 | 0.0326 | 5 |
| Seattle | low | MAM | 247 | 0.2348 | 0.2186 | 0.1417 | expand_beyond_5 |
| Seattle | low | SON | 182 | 0.3681 | 0.3022 | 0.1923 | expand_beyond_5 |
| Seoul | high | DJF | 240 | 0.2417 | 0.1958 | 0.1708 | expand_beyond_5 |
| Seoul | high | JJA | 184 | 0.2337 | 0.1033 | 0.0489 | 5 |
| Seoul | high | MAM | 247 | 0.2389 | 0.1215 | 0.0931 | expand_beyond_5 |
| Seoul | high | SON | 182 | 0.2418 | 0.1648 | 0.1264 | expand_beyond_5 |
| Seoul | low | DJF | 240 | 0.4750 | 0.3792 | 0.3333 | expand_beyond_5 |
| Seoul | low | JJA | 184 | 0.5489 | 0.4511 | 0.3315 | expand_beyond_5 |
| Seoul | low | MAM | 247 | 0.5142 | 0.4049 | 0.3320 | expand_beyond_5 |
| Seoul | low | SON | 182 | 0.4725 | 0.3901 | 0.3242 | expand_beyond_5 |
| Shanghai | high | DJF | 240 | 0.3375 | 0.2333 | 0.1667 | expand_beyond_5 |
| Shanghai | high | JJA | 184 | 0.3152 | 0.1848 | 0.1141 | expand_beyond_5 |
| Shanghai | high | MAM | 247 | 0.4089 | 0.2267 | 0.1255 | expand_beyond_5 |
| Shanghai | high | SON | 182 | 0.5989 | 0.3681 | 0.1868 | expand_beyond_5 |
| Shanghai | low | DJF | 240 | 0.3875 | 0.2292 | 0.1667 | expand_beyond_5 |
| Shanghai | low | JJA | 184 | 0.4022 | 0.1413 | 0.1087 | expand_beyond_5 |
| Shanghai | low | MAM | 247 | 0.5182 | 0.2429 | 0.1498 | expand_beyond_5 |
| Shanghai | low | SON | 182 | 0.5055 | 0.2363 | 0.1813 | expand_beyond_5 |
| Shenzhen | high | DJF | 240 | 0.0583 | 0.0500 | 0.0500 | 4 |
| Shenzhen | high | JJA | 184 | 0.1304 | 0.0870 | 0.0598 | expand_beyond_5 |
| Shenzhen | high | MAM | 247 | 0.1498 | 0.1093 | 0.0810 | expand_beyond_5 |
| Shenzhen | high | SON | 182 | 0.0879 | 0.0769 | 0.0549 | expand_beyond_5 |
| Shenzhen | low | DJF | 240 | 0.2417 | 0.1958 | 0.1083 | expand_beyond_5 |
| Shenzhen | low | JJA | 184 | 0.5489 | 0.4457 | 0.1576 | expand_beyond_5 |
| Shenzhen | low | MAM | 247 | 0.3279 | 0.2753 | 0.1377 | expand_beyond_5 |
| Shenzhen | low | SON | 182 | 0.3187 | 0.2253 | 0.1154 | expand_beyond_5 |
| Singapore | high | DJF | 240 | 0.0625 | 0.0292 | 0.0250 | 4 |
| Singapore | high | JJA | 184 | 0.1359 | 0.0815 | 0.0543 | expand_beyond_5 |
| Singapore | high | MAM | 247 | 0.1296 | 0.0607 | 0.0324 | 5 |
| Singapore | high | SON | 182 | 0.1374 | 0.0989 | 0.0330 | 5 |
| Singapore | low | DJF | 240 | 0.6417 | 0.5458 | 0.4417 | expand_beyond_5 |
| Singapore | low | JJA | 184 | 0.4783 | 0.3859 | 0.2880 | expand_beyond_5 |
| Singapore | low | MAM | 247 | 0.5061 | 0.4130 | 0.3198 | expand_beyond_5 |
| Singapore | low | SON | 182 | 0.5714 | 0.4780 | 0.3352 | expand_beyond_5 |
| Taipei | high | DJF | 240 | 0.3250 | 0.2125 | 0.1417 | expand_beyond_5 |
| Taipei | high | JJA | 184 | 0.3207 | 0.1196 | 0.0543 | expand_beyond_5 |
| Taipei | high | MAM | 247 | 0.3077 | 0.2024 | 0.1174 | expand_beyond_5 |
| Taipei | high | SON | 182 | 0.4011 | 0.2253 | 0.1374 | expand_beyond_5 |
| Taipei | low | DJF | 240 | 0.5667 | 0.5375 | 0.2542 | expand_beyond_5 |
| Taipei | low | JJA | 184 | 0.5543 | 0.4022 | 0.2391 | expand_beyond_5 |
| Taipei | low | MAM | 247 | 0.5628 | 0.4777 | 0.2470 | expand_beyond_5 |
| Taipei | low | SON | 182 | 0.5934 | 0.5165 | 0.2253 | expand_beyond_5 |
| Tokyo | high | DJF | 240 | 0.2875 | 0.1583 | 0.1208 | expand_beyond_5 |
| Tokyo | high | JJA | 184 | 0.3478 | 0.2065 | 0.1304 | expand_beyond_5 |
| Tokyo | high | MAM | 247 | 0.3603 | 0.2429 | 0.1862 | expand_beyond_5 |
| Tokyo | high | SON | 182 | 0.4231 | 0.2967 | 0.2033 | expand_beyond_5 |
| Tokyo | low | DJF | 240 | 0.3500 | 0.2708 | 0.1708 | expand_beyond_5 |
| Tokyo | low | JJA | 184 | 0.5815 | 0.4728 | 0.1196 | expand_beyond_5 |
| Tokyo | low | MAM | 247 | 0.4251 | 0.3522 | 0.1984 | expand_beyond_5 |
| Tokyo | low | SON | 182 | 0.5055 | 0.4286 | 0.1923 | expand_beyond_5 |
| Toronto | high | DJF | 240 | 0.5208 | 0.4125 | 0.3583 | expand_beyond_5 |
| Toronto | high | JJA | 184 | 0.1522 | 0.0924 | 0.0435 | 5 |
| Toronto | high | MAM | 247 | 0.3198 | 0.2186 | 0.1781 | expand_beyond_5 |
| Toronto | high | SON | 182 | 0.2802 | 0.1593 | 0.0934 | expand_beyond_5 |
| Toronto | low | DJF | 240 | 0.6125 | 0.5708 | 0.5208 | expand_beyond_5 |
| Toronto | low | JJA | 184 | 0.2826 | 0.1902 | 0.1793 | expand_beyond_5 |
| Toronto | low | MAM | 247 | 0.4372 | 0.3725 | 0.2915 | expand_beyond_5 |
| Toronto | low | SON | 182 | 0.3846 | 0.3407 | 0.2912 | expand_beyond_5 |
| Warsaw | high | DJF | 240 | 0.4792 | 0.3833 | 0.3208 | expand_beyond_5 |
| Warsaw | high | JJA | 184 | 0.0815 | 0.0435 | 0.0326 | 4 |
| Warsaw | high | MAM | 247 | 0.0972 | 0.0648 | 0.0526 | expand_beyond_5 |
| Warsaw | high | SON | 182 | 0.2418 | 0.1923 | 0.1538 | expand_beyond_5 |
| Warsaw | low | DJF | 240 | 0.6375 | 0.5667 | 0.3167 | expand_beyond_5 |
| Warsaw | low | JJA | 184 | 0.2283 | 0.1630 | 0.1359 | expand_beyond_5 |
| Warsaw | low | MAM | 247 | 0.2632 | 0.1741 | 0.1215 | expand_beyond_5 |
| Warsaw | low | SON | 182 | 0.4615 | 0.4286 | 0.2363 | expand_beyond_5 |
| Wellington | high | DJF | 240 | 0.2083 | 0.1458 | 0.1083 | expand_beyond_5 |
| Wellington | high | JJA | 184 | 0.2935 | 0.2446 | 0.2120 | expand_beyond_5 |
| Wellington | high | MAM | 247 | 0.1943 | 0.1336 | 0.1134 | expand_beyond_5 |
| Wellington | high | SON | 182 | 0.2308 | 0.1648 | 0.1374 | expand_beyond_5 |
| Wellington | low | DJF | 240 | 0.5500 | 0.4333 | 0.3750 | expand_beyond_5 |
| Wellington | low | JJA | 184 | 0.6630 | 0.5870 | 0.5109 | expand_beyond_5 |
| Wellington | low | MAM | 247 | 0.6640 | 0.5951 | 0.5182 | expand_beyond_5 |
| Wellington | low | SON | 182 | 0.6538 | 0.5714 | 0.4725 | expand_beyond_5 |
| Wuhan | high | DJF | 240 | 0.1667 | 0.1417 | 0.1333 | expand_beyond_5 |
| Wuhan | high | JJA | 184 | 0.1033 | 0.0707 | 0.0543 | expand_beyond_5 |
| Wuhan | high | MAM | 247 | 0.1741 | 0.1417 | 0.1336 | expand_beyond_5 |
| Wuhan | high | SON | 182 | 0.1374 | 0.0879 | 0.0769 | expand_beyond_5 |
| Wuhan | low | DJF | 240 | 0.3792 | 0.3417 | 0.2875 | expand_beyond_5 |
| Wuhan | low | JJA | 184 | 0.4891 | 0.3696 | 0.2609 | expand_beyond_5 |
| Wuhan | low | MAM | 247 | 0.4615 | 0.3563 | 0.2551 | expand_beyond_5 |
| Wuhan | low | SON | 182 | 0.3956 | 0.3571 | 0.3022 | expand_beyond_5 |

## Entries Needing Expanded Radius

| key | miss_r3 | recommended_radius |
|---|---|---|
| Amsterdam_high_DJF | 0.4708 | expand_beyond_5 |
| Amsterdam_high_JJA | 0.0870 | 4 |
| Amsterdam_high_MAM | 0.1417 | expand_beyond_5 |
| Amsterdam_high_SON | 0.2033 | expand_beyond_5 |
| Amsterdam_low_DJF | 0.6083 | expand_beyond_5 |
| Amsterdam_low_JJA | 0.4022 | expand_beyond_5 |
| Amsterdam_low_MAM | 0.3846 | expand_beyond_5 |
| Amsterdam_low_SON | 0.5769 | expand_beyond_5 |
| Ankara_high_DJF | 0.1208 | expand_beyond_5 |
| Ankara_high_MAM | 0.0810 | 5 |
| Ankara_low_DJF | 0.3583 | expand_beyond_5 |
| Ankara_low_MAM | 0.2146 | expand_beyond_5 |
| Ankara_low_SON | 0.1868 | expand_beyond_5 |
| Atlanta_high_DJF | 0.6417 | expand_beyond_5 |
| Atlanta_high_JJA | 0.4239 | 5 |
| Atlanta_high_MAM | 0.3077 | expand_beyond_5 |
| Atlanta_high_SON | 0.5440 | expand_beyond_5 |
| Atlanta_low_DJF | 0.3375 | expand_beyond_5 |
| Atlanta_low_JJA | 0.2826 | expand_beyond_5 |
| Atlanta_low_MAM | 0.2551 | expand_beyond_5 |
| Atlanta_low_SON | 0.2473 | expand_beyond_5 |
| Auckland_high_DJF | 0.0667 | 4 |
| Auckland_high_JJA | 0.1467 | expand_beyond_5 |
| Auckland_high_MAM | 0.1134 | 5 |
| Auckland_high_SON | 0.1319 | 5 |
| Auckland_low_DJF | 0.3958 | expand_beyond_5 |
| Auckland_low_JJA | 0.4783 | expand_beyond_5 |
| Auckland_low_MAM | 0.4008 | expand_beyond_5 |
| Auckland_low_SON | 0.5000 | expand_beyond_5 |
| Austin_high_DJF | 0.6458 | expand_beyond_5 |
| Austin_high_JJA | 0.3424 | expand_beyond_5 |
| Austin_high_MAM | 0.3482 | expand_beyond_5 |
| Austin_high_SON | 0.4780 | expand_beyond_5 |
| Austin_low_DJF | 0.5250 | expand_beyond_5 |
| Austin_low_JJA | 0.2446 | expand_beyond_5 |
| Austin_low_MAM | 0.4534 | expand_beyond_5 |
| Austin_low_SON | 0.2747 | expand_beyond_5 |
| Beijing_high_DJF | 0.0833 | 4 |
| Beijing_high_JJA | 0.1033 | expand_beyond_5 |
| Beijing_high_MAM | 0.1053 | expand_beyond_5 |
| Beijing_high_SON | 0.1154 | 5 |
| Beijing_low_DJF | 0.3417 | expand_beyond_5 |
| Beijing_low_JJA | 0.4022 | expand_beyond_5 |
| Beijing_low_MAM | 0.2146 | expand_beyond_5 |
| Beijing_low_SON | 0.3901 | expand_beyond_5 |
| Buenos Aires_high_DJF | 0.0667 | 4 |
| Buenos Aires_high_JJA | 0.1522 | expand_beyond_5 |
| Buenos Aires_high_MAM | 0.1862 | expand_beyond_5 |
| Buenos Aires_high_SON | 0.1923 | expand_beyond_5 |
| Buenos Aires_low_DJF | 0.3167 | expand_beyond_5 |
| Buenos Aires_low_JJA | 0.3859 | expand_beyond_5 |
| Buenos Aires_low_MAM | 0.4494 | expand_beyond_5 |
| Buenos Aires_low_SON | 0.3626 | expand_beyond_5 |
| Busan_high_DJF | 0.0667 | 4 |
| Busan_high_JJA | 0.0924 | 4 |
| Busan_high_MAM | 0.0931 | expand_beyond_5 |
| Busan_high_SON | 0.0934 | 5 |
| Busan_low_DJF | 0.3875 | expand_beyond_5 |
| Busan_low_JJA | 0.4130 | expand_beyond_5 |
| Busan_low_MAM | 0.3117 | expand_beyond_5 |
| Busan_low_SON | 0.3846 | expand_beyond_5 |
| Cape Town_high_JJA | 0.0978 | 5 |
| Cape Town_high_MAM | 0.0526 | 4 |
| Cape Town_high_SON | 0.0604 | 4 |
| Cape Town_low_DJF | 0.3733 | expand_beyond_5 |
| Cape Town_low_JJA | 0.4511 | expand_beyond_5 |
| Cape Town_low_MAM | 0.3927 | expand_beyond_5 |
| Cape Town_low_SON | 0.3901 | expand_beyond_5 |
| Chengdu_high_DJF | 0.1542 | expand_beyond_5 |
| Chengdu_high_JJA | 0.1304 | expand_beyond_5 |
| Chengdu_high_MAM | 0.1377 | expand_beyond_5 |
| Chengdu_high_SON | 0.1593 | expand_beyond_5 |
| Chengdu_low_DJF | 0.3208 | expand_beyond_5 |
| Chengdu_low_JJA | 0.2772 | expand_beyond_5 |
| Chengdu_low_MAM | 0.2794 | expand_beyond_5 |
| Chengdu_low_SON | 0.3846 | expand_beyond_5 |
| Chicago_high_DJF | 0.4417 | expand_beyond_5 |
| Chicago_high_JJA | 0.2500 | expand_beyond_5 |
| Chicago_high_MAM | 0.3239 | expand_beyond_5 |
| Chicago_high_SON | 0.3571 | expand_beyond_5 |
| Chicago_low_DJF | 0.5333 | expand_beyond_5 |
| Chicago_low_JJA | 0.3478 | expand_beyond_5 |
| Chicago_low_MAM | 0.4615 | expand_beyond_5 |
| Chicago_low_SON | 0.3352 | expand_beyond_5 |
| Chongqing_high_DJF | 0.2375 | expand_beyond_5 |
| Chongqing_high_JJA | 0.1304 | expand_beyond_5 |
| Chongqing_high_MAM | 0.1943 | expand_beyond_5 |
| Chongqing_high_SON | 0.2033 | expand_beyond_5 |
| Chongqing_low_DJF | 0.4333 | expand_beyond_5 |
| Chongqing_low_JJA | 0.3533 | expand_beyond_5 |
| Chongqing_low_MAM | 0.3077 | expand_beyond_5 |
| Chongqing_low_SON | 0.4066 | expand_beyond_5 |
| Dallas_high_DJF | 0.6167 | expand_beyond_5 |
| Dallas_high_JJA | 0.3424 | expand_beyond_5 |
| Dallas_high_MAM | 0.4170 | expand_beyond_5 |
| Dallas_high_SON | 0.4780 | expand_beyond_5 |
| Dallas_low_DJF | 0.3458 | expand_beyond_5 |
| Dallas_low_JJA | 0.1467 | expand_beyond_5 |
| Dallas_low_MAM | 0.3036 | expand_beyond_5 |
| Dallas_low_SON | 0.1868 | expand_beyond_5 |
| Denver_high_DJF | 0.5000 | expand_beyond_5 |
| Denver_high_JJA | 0.1593 | 5 |
| Denver_high_MAM | 0.1984 | expand_beyond_5 |
| Denver_high_SON | 0.2873 | expand_beyond_5 |
| Denver_low_DJF | 0.4792 | expand_beyond_5 |
| Denver_low_JJA | 0.1868 | expand_beyond_5 |
| Denver_low_MAM | 0.3441 | expand_beyond_5 |
| Denver_low_SON | 0.3149 | expand_beyond_5 |
| Guangzhou_high_DJF | 0.0958 | expand_beyond_5 |
| Guangzhou_high_JJA | 0.1033 | 5 |
| Guangzhou_high_MAM | 0.1538 | expand_beyond_5 |
| Guangzhou_high_SON | 0.0549 | 4 |
| Guangzhou_low_DJF | 0.3042 | expand_beyond_5 |
| Guangzhou_low_JJA | 0.5489 | expand_beyond_5 |
| Guangzhou_low_MAM | 0.5425 | expand_beyond_5 |
| Guangzhou_low_SON | 0.3681 | expand_beyond_5 |
| Helsinki_high_DJF | 0.5833 | expand_beyond_5 |
| Helsinki_high_JJA | 0.0870 | 4 |
| Helsinki_high_MAM | 0.1296 | expand_beyond_5 |
| Helsinki_high_SON | 0.3516 | expand_beyond_5 |
| Helsinki_low_DJF | 0.7250 | expand_beyond_5 |
| Helsinki_low_JJA | 0.2935 | 5 |
| Helsinki_low_MAM | 0.3927 | expand_beyond_5 |
| Helsinki_low_SON | 0.5714 | expand_beyond_5 |
| Houston_high_DJF | 0.5125 | expand_beyond_5 |
| Houston_high_JJA | 0.4402 | expand_beyond_5 |
| Houston_high_MAM | 0.4211 | expand_beyond_5 |
| Houston_high_SON | 0.4780 | expand_beyond_5 |
| Houston_low_DJF | 0.4542 | expand_beyond_5 |
| Houston_low_JJA | 0.3370 | expand_beyond_5 |
| Houston_low_MAM | 0.4413 | expand_beyond_5 |
| Houston_low_SON | 0.3022 | expand_beyond_5 |
| Jakarta_high_DJF | 0.1083 | 4 |
| Jakarta_high_JJA | 0.0815 | 4 |
| Jakarta_high_MAM | 0.0972 | 4 |
| Jakarta_high_SON | 0.1264 | 4 |
| Jakarta_low_DJF | 0.3958 | expand_beyond_5 |
| Jakarta_low_JJA | 0.1630 | expand_beyond_5 |
| Jakarta_low_MAM | 0.3117 | expand_beyond_5 |
| Jakarta_low_SON | 0.2967 | expand_beyond_5 |
| Jeddah_low_DJF | 0.1500 | expand_beyond_5 |
| Jeddah_low_JJA | 0.1576 | expand_beyond_5 |
| Jeddah_low_MAM | 0.1579 | expand_beyond_5 |
| Jeddah_low_SON | 0.1429 | 5 |
| Karachi_high_JJA | 0.2011 | 5 |
| Karachi_high_MAM | 0.0810 | 4 |
| Karachi_high_SON | 0.0824 | 4 |
| Karachi_low_DJF | 0.0917 | 5 |
| Karachi_low_JJA | 0.6522 | expand_beyond_5 |
| Karachi_low_MAM | 0.2955 | expand_beyond_5 |
| Karachi_low_SON | 0.3352 | expand_beyond_5 |
| Kuala Lumpur_low_DJF | 0.4917 | expand_beyond_5 |
| Kuala Lumpur_low_JJA | 0.4837 | expand_beyond_5 |
| Kuala Lumpur_low_MAM | 0.4615 | expand_beyond_5 |
| Kuala Lumpur_low_SON | 0.4451 | expand_beyond_5 |
| Lagos_high_JJA | 0.0820 | 4 |
| Lagos_high_MAM | 0.0996 | expand_beyond_5 |
| Lagos_low_DJF | 0.1992 | expand_beyond_5 |
| Lagos_low_JJA | 0.4590 | expand_beyond_5 |
| Lagos_low_MAM | 0.4149 | expand_beyond_5 |
| Lagos_low_SON | 0.4011 | expand_beyond_5 |
| London_high_DJF | 0.3917 | expand_beyond_5 |
| London_high_JJA | 0.0598 | 4 |
| London_high_MAM | 0.0810 | 4 |
| London_high_SON | 0.2527 | expand_beyond_5 |
| London_low_DJF | 0.6167 | expand_beyond_5 |
| London_low_JJA | 0.1848 | expand_beyond_5 |
| London_low_MAM | 0.3482 | expand_beyond_5 |
| London_low_SON | 0.4780 | expand_beyond_5 |
| Los Angeles_high_DJF | 0.9583 | expand_beyond_5 |
| Los Angeles_high_JJA | 0.9348 | expand_beyond_5 |
| Los Angeles_high_MAM | 0.9636 | expand_beyond_5 |
| Los Angeles_high_SON | 0.9890 | expand_beyond_5 |
| Los Angeles_low_DJF | 0.2375 | expand_beyond_5 |
| Los Angeles_low_JJA | 0.5380 | expand_beyond_5 |
| Los Angeles_low_MAM | 0.3401 | expand_beyond_5 |
| Los Angeles_low_SON | 0.3132 | expand_beyond_5 |
| Lucknow_high_DJF | 0.0991 | expand_beyond_5 |
| Lucknow_high_JJA | 0.1359 | 5 |
| Lucknow_high_SON | 0.2253 | 5 |
| Lucknow_low_DJF | 0.2748 | expand_beyond_5 |
| Lucknow_low_JJA | 0.4511 | expand_beyond_5 |
| Lucknow_low_MAM | 0.1296 | 5 |
| Lucknow_low_SON | 0.2637 | expand_beyond_5 |
| Madrid_high_DJF | 0.0792 | 4 |
| Madrid_high_MAM | 0.0688 | 5 |
| Madrid_high_SON | 0.0824 | 4 |
| Madrid_low_DJF | 0.3542 | expand_beyond_5 |
| Madrid_low_JJA | 0.0543 | 4 |
| Madrid_low_MAM | 0.1903 | expand_beyond_5 |
| Madrid_low_SON | 0.2198 | expand_beyond_5 |
| Manila_high_DJF | 0.0833 | 4 |
| Manila_high_JJA | 0.1848 | 5 |
| Manila_high_SON | 0.1758 | expand_beyond_5 |
| Manila_low_DJF | 0.4083 | expand_beyond_5 |
| Manila_low_JJA | 0.6304 | expand_beyond_5 |
| Manila_low_MAM | 0.2915 | expand_beyond_5 |
| Manila_low_SON | 0.5879 | expand_beyond_5 |
| Mexico City_high_DJF | 0.0833 | 4 |
| Mexico City_high_JJA | 0.1902 | 5 |
| Mexico City_high_MAM | 0.1215 | 4 |
| Mexico City_high_SON | 0.1429 | 5 |
| Mexico City_low_DJF | 0.0667 | 4 |
| Mexico City_low_JJA | 0.4130 | expand_beyond_5 |
| Mexico City_low_MAM | 0.0850 | 5 |
| Mexico City_low_SON | 0.2582 | expand_beyond_5 |
| Miami_high_DJF | 0.7667 | expand_beyond_5 |
| Miami_high_JJA | 0.7228 | expand_beyond_5 |
| Miami_high_MAM | 0.6437 | expand_beyond_5 |
| Miami_high_SON | 0.8352 | expand_beyond_5 |
| Miami_low_DJF | 0.3958 | expand_beyond_5 |
| Miami_low_JJA | 0.5598 | expand_beyond_5 |
| Miami_low_MAM | 0.3401 | expand_beyond_5 |
| Miami_low_SON | 0.4835 | expand_beyond_5 |
| Milan_high_DJF | 0.1500 | expand_beyond_5 |
| Milan_high_MAM | 0.1255 | expand_beyond_5 |
| Milan_high_SON | 0.1429 | expand_beyond_5 |
| Milan_low_DJF | 0.5375 | expand_beyond_5 |
| Milan_low_JJA | 0.2989 | expand_beyond_5 |
| Milan_low_MAM | 0.3644 | expand_beyond_5 |
| Milan_low_SON | 0.4231 | expand_beyond_5 |
| Munich_high_DJF | 0.3250 | expand_beyond_5 |
| Munich_high_JJA | 0.0870 | 4 |
| Munich_high_MAM | 0.1457 | expand_beyond_5 |
| Munich_high_SON | 0.2198 | expand_beyond_5 |
| Munich_low_DJF | 0.6125 | expand_beyond_5 |
| Munich_low_JJA | 0.3098 | expand_beyond_5 |
| Munich_low_MAM | 0.3117 | expand_beyond_5 |
| Munich_low_SON | 0.5330 | expand_beyond_5 |
| NYC_high_DJF | 0.4292 | expand_beyond_5 |
| NYC_high_JJA | 0.2120 | 5 |
| NYC_high_MAM | 0.3117 | expand_beyond_5 |
| NYC_high_SON | 0.2253 | expand_beyond_5 |
| NYC_low_DJF | 0.5208 | expand_beyond_5 |
| NYC_low_JJA | 0.2989 | expand_beyond_5 |
| NYC_low_MAM | 0.4251 | expand_beyond_5 |
| NYC_low_SON | 0.3297 | expand_beyond_5 |
| Panama City_high_JJA | 0.1250 | 4 |
| Panama City_high_MAM | 0.0729 | 4 |
| Panama City_high_SON | 0.1484 | 4 |
| Panama City_low_DJF | 0.4043 | expand_beyond_5 |
| Panama City_low_JJA | 0.6467 | expand_beyond_5 |
| Panama City_low_MAM | 0.4413 | expand_beyond_5 |
| Panama City_low_SON | 0.6044 | expand_beyond_5 |
| San Francisco_high_DJF | 0.4708 | expand_beyond_5 |
| San Francisco_high_JJA | 0.6576 | expand_beyond_5 |
| San Francisco_high_MAM | 0.5870 | expand_beyond_5 |
| San Francisco_high_SON | 0.5385 | expand_beyond_5 |
| San Francisco_low_DJF | 0.2833 | expand_beyond_5 |
| San Francisco_low_JJA | 0.3152 | expand_beyond_5 |
| San Francisco_low_MAM | 0.2632 | expand_beyond_5 |
| San Francisco_low_SON | 0.2967 | expand_beyond_5 |
| Sao Paulo_high_DJF | 0.0792 | 4 |
| Sao Paulo_high_JJA | 0.0815 | expand_beyond_5 |
| Sao Paulo_high_MAM | 0.0567 | 4 |
| Sao Paulo_high_SON | 0.1429 | expand_beyond_5 |
| Sao Paulo_low_DJF | 0.5167 | expand_beyond_5 |
| Sao Paulo_low_JJA | 0.4185 | expand_beyond_5 |
| Sao Paulo_low_MAM | 0.4251 | expand_beyond_5 |
| Sao Paulo_low_SON | 0.5220 | expand_beyond_5 |
| Seattle_high_DJF | 0.1833 | expand_beyond_5 |
| Seattle_high_MAM | 0.0810 | 4 |
| Seattle_high_SON | 0.1099 | expand_beyond_5 |
| Seattle_low_DJF | 0.4917 | expand_beyond_5 |
| Seattle_low_JJA | 0.1359 | 5 |
| Seattle_low_MAM | 0.2348 | expand_beyond_5 |
| Seattle_low_SON | 0.3681 | expand_beyond_5 |
| Seoul_high_DJF | 0.2417 | expand_beyond_5 |
| Seoul_high_JJA | 0.2337 | 5 |
| Seoul_high_MAM | 0.2389 | expand_beyond_5 |
| Seoul_high_SON | 0.2418 | expand_beyond_5 |
| Seoul_low_DJF | 0.4750 | expand_beyond_5 |
| Seoul_low_JJA | 0.5489 | expand_beyond_5 |
| Seoul_low_MAM | 0.5142 | expand_beyond_5 |
| Seoul_low_SON | 0.4725 | expand_beyond_5 |
| Shanghai_high_DJF | 0.3375 | expand_beyond_5 |
| Shanghai_high_JJA | 0.3152 | expand_beyond_5 |
| Shanghai_high_MAM | 0.4089 | expand_beyond_5 |
| Shanghai_high_SON | 0.5989 | expand_beyond_5 |
| Shanghai_low_DJF | 0.3875 | expand_beyond_5 |
| Shanghai_low_JJA | 0.4022 | expand_beyond_5 |
| Shanghai_low_MAM | 0.5182 | expand_beyond_5 |
| Shanghai_low_SON | 0.5055 | expand_beyond_5 |
| Shenzhen_high_DJF | 0.0583 | 4 |
| Shenzhen_high_JJA | 0.1304 | expand_beyond_5 |
| Shenzhen_high_MAM | 0.1498 | expand_beyond_5 |
| Shenzhen_high_SON | 0.0879 | expand_beyond_5 |
| Shenzhen_low_DJF | 0.2417 | expand_beyond_5 |
| Shenzhen_low_JJA | 0.5489 | expand_beyond_5 |
| Shenzhen_low_MAM | 0.3279 | expand_beyond_5 |
| Shenzhen_low_SON | 0.3187 | expand_beyond_5 |
| Singapore_high_DJF | 0.0625 | 4 |
| Singapore_high_JJA | 0.1359 | expand_beyond_5 |
| Singapore_high_MAM | 0.1296 | 5 |
| Singapore_high_SON | 0.1374 | 5 |
| Singapore_low_DJF | 0.6417 | expand_beyond_5 |
| Singapore_low_JJA | 0.4783 | expand_beyond_5 |
| Singapore_low_MAM | 0.5061 | expand_beyond_5 |
| Singapore_low_SON | 0.5714 | expand_beyond_5 |
| Taipei_high_DJF | 0.3250 | expand_beyond_5 |
| Taipei_high_JJA | 0.3207 | expand_beyond_5 |
| Taipei_high_MAM | 0.3077 | expand_beyond_5 |
| Taipei_high_SON | 0.4011 | expand_beyond_5 |
| Taipei_low_DJF | 0.5667 | expand_beyond_5 |
| Taipei_low_JJA | 0.5543 | expand_beyond_5 |
| Taipei_low_MAM | 0.5628 | expand_beyond_5 |
| Taipei_low_SON | 0.5934 | expand_beyond_5 |
| Tokyo_high_DJF | 0.2875 | expand_beyond_5 |
| Tokyo_high_JJA | 0.3478 | expand_beyond_5 |
| Tokyo_high_MAM | 0.3603 | expand_beyond_5 |
| Tokyo_high_SON | 0.4231 | expand_beyond_5 |
| Tokyo_low_DJF | 0.3500 | expand_beyond_5 |
| Tokyo_low_JJA | 0.5815 | expand_beyond_5 |
| Tokyo_low_MAM | 0.4251 | expand_beyond_5 |
| Tokyo_low_SON | 0.5055 | expand_beyond_5 |
| Toronto_high_DJF | 0.5208 | expand_beyond_5 |
| Toronto_high_JJA | 0.1522 | 5 |
| Toronto_high_MAM | 0.3198 | expand_beyond_5 |
| Toronto_high_SON | 0.2802 | expand_beyond_5 |
| Toronto_low_DJF | 0.6125 | expand_beyond_5 |
| Toronto_low_JJA | 0.2826 | expand_beyond_5 |
| Toronto_low_MAM | 0.4372 | expand_beyond_5 |
| Toronto_low_SON | 0.3846 | expand_beyond_5 |
| Warsaw_high_DJF | 0.4792 | expand_beyond_5 |
| Warsaw_high_JJA | 0.0815 | 4 |
| Warsaw_high_MAM | 0.0972 | expand_beyond_5 |
| Warsaw_high_SON | 0.2418 | expand_beyond_5 |
| Warsaw_low_DJF | 0.6375 | expand_beyond_5 |
| Warsaw_low_JJA | 0.2283 | expand_beyond_5 |
| Warsaw_low_MAM | 0.2632 | expand_beyond_5 |
| Warsaw_low_SON | 0.4615 | expand_beyond_5 |
| Wellington_high_DJF | 0.2083 | expand_beyond_5 |
| Wellington_high_JJA | 0.2935 | expand_beyond_5 |
| Wellington_high_MAM | 0.1943 | expand_beyond_5 |
| Wellington_high_SON | 0.2308 | expand_beyond_5 |
| Wellington_low_DJF | 0.5500 | expand_beyond_5 |
| Wellington_low_JJA | 0.6630 | expand_beyond_5 |
| Wellington_low_MAM | 0.6640 | expand_beyond_5 |
| Wellington_low_SON | 0.6538 | expand_beyond_5 |
| Wuhan_high_DJF | 0.1667 | expand_beyond_5 |
| Wuhan_high_JJA | 0.1033 | expand_beyond_5 |
| Wuhan_high_MAM | 0.1741 | expand_beyond_5 |
| Wuhan_high_SON | 0.1374 | expand_beyond_5 |
| Wuhan_low_DJF | 0.3792 | expand_beyond_5 |
| Wuhan_low_JJA | 0.4891 | expand_beyond_5 |
| Wuhan_low_MAM | 0.4615 | expand_beyond_5 |
| Wuhan_low_SON | 0.3956 | expand_beyond_5 |
