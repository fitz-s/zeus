# §2.5 Small Sample Floor — v2

Created: 2026-05-03  Authority: RERUN_PLAN_v2.md C4

> Paris pending workstream A resync; rerun for Paris after A completes.

## Definition

N = unique training target_dates (not raw pair rows).
N* = smallest N where ECE std over a 100-date sliding window < 0.02.
When N < N*: force DDD multiplier = curve_max (0.91× Kelly).

## Results

| city | metric | total_N_dates | N_star | final_brier | final_ece | status |
|---|---|---|---|---|---|---|
| Amsterdam | high | 840 | 123 | 0.0601 | 0.1972 | OK |
| Amsterdam | low | 357 | 112 | 0.1704 | 0.3379 | OK |
| Ankara | high | 839 | 125 | 0.0323 | 0.1419 | OK |
| Ankara | low | 408 | 110 | 0.0732 | 0.1698 | OK |
| Atlanta | high | 838 | 110 | 0.0365 | 0.1555 | OK |
| Atlanta | low | 481 | 110 | 0.0560 | 0.1529 | OK |
| Auckland | high | 839 | 123 | 0.0320 | 0.1135 | OK |
| Auckland | low | 584 | 110 | 0.0564 | 0.1745 | OK |
| Austin | high | 840 | 110 | 0.0454 | 0.1763 | OK |
| Austin | low | 669 | 110 | 0.0478 | 0.1316 | OK |
| Beijing | high | 840 | 110 | 0.0331 | 0.1401 | OK |
| Beijing | low | 419 | 137 | 0.0473 | 0.1399 | OK |
| Buenos Aires | high | 838 | 110 | 0.0315 | 0.1335 | OK |
| Buenos Aires | low | 309 | 110 | 0.0564 | 0.1423 | OK |
| Busan | high | 840 | 198 | 0.0472 | 0.1304 | OK |
| Busan | low | 320 | 110 | 0.0126 | 0.0311 | OK |
| Cape Town | high | 813 | 110 | 0.0511 | 0.1820 | OK |
| Cape Town | low | 450 | 110 | 0.1025 | 0.2217 | OK |
| Chengdu | high | 840 | 134 | 0.0383 | 0.1517 | OK |
| Chengdu | low | 399 | 115 | 0.0587 | 0.1598 | OK |
| Chicago | high | 839 | 144 | 0.0347 | 0.1543 | OK |
| Chicago | low | 548 | 110 | 0.0431 | 0.1503 | OK |
| Chongqing | high | 840 | 113 | 0.0346 | 0.1457 | OK |
| Chongqing | low | 368 | 130 | 0.0862 | 0.2094 | OK |
| Dallas | high | 839 | 110 | 0.0344 | 0.1497 | OK |
| Dallas | low | 677 | 116 | 0.0745 | 0.2251 | OK |
| Denver | high | 836 | 110 | 0.0181 | 0.0964 | OK |
| Denver | low | 528 | 110 | 0.0246 | 0.1012 | OK |
| Guangzhou | high | 840 | 110 | 0.0213 | 0.0902 | OK |
| Guangzhou | low | 316 | 180 | 0.0290 | 0.0599 | OK |
| Helsinki | high | 840 | 110 | 0.0540 | 0.1934 | OK |
| Helsinki | low | 314 | 118 | 0.1129 | 0.2212 | OK |
| Hong Kong | high | 850 | 110 | 0.0666 | 0.1945 | OK |
| Hong Kong | low | 304 | 111 | 0.0219 | 0.0397 | OK |
| Houston | high | 839 | 115 | 0.0145 | 0.0509 | OK |
| Houston | low | 612 | 189 | 0.0883 | 0.2231 | OK |
| Istanbul | high | 756 | 172 | 0.0526 | 0.1318 | OK |
| Istanbul | low | 307 | 113 | 0.0705 | 0.1345 | OK |
| Jakarta | high | 840 | 110 | 0.0115 | 0.0425 | OK |
| Jakarta | low | 131 | 110 | 0.0019 | 0.0061 | OK |
| Jeddah | high | 840 | 110 | 0.0012 | 0.0114 | OK |
| Jeddah | low | 439 | 174 | 0.0911 | 0.1820 | OK |
| Karachi | high | 839 | 110 | 0.0828 | 0.2312 | OK |
| Karachi | low | 207 | 114 | 0.0662 | 0.1580 | OK |
| Kuala Lumpur | high | 840 | 110 | 0.0146 | 0.0401 | OK |
| Kuala Lumpur | low | 95 | not found | 0.0653 | 0.1258 | N_STAR_NOT_FOUND |
| Lagos | high | 828 | 110 | 0.0172 | 0.0452 | OK |
| Lagos | low | 500 | 150 | 0.1781 | 0.3304 | OK |
| London | high | 840 | 110 | 0.0556 | 0.1991 | OK |
| London | low | 654 | 110 | 0.0476 | 0.1512 | OK |
| Los Angeles | high | 840 | 110 | 0.0595 | 0.1899 | OK |
| Los Angeles | low | 287 | 144 | 0.0899 | 0.1964 | OK |
| Lucknow | high | 822 | 161 | 0.0701 | 0.2016 | OK |
| Lucknow | low | 117 | 113 | 0.1071 | 0.2411 | OK |
| Madrid | high | 840 | 110 | 0.0524 | 0.1902 | OK |
| Madrid | low | 608 | 127 | 0.0874 | 0.2400 | OK |
| Manila | high | 839 | 110 | 0.0179 | 0.0549 | OK |
| Manila | low | 194 | 156 | 0.1743 | 0.3269 | OK |
| Mexico City | high | 840 | 110 | 0.0838 | 0.2458 | OK |
| Mexico City | low | 840 | 110 | 0.0440 | 0.1403 | OK |
| Miami | high | 838 | 125 | 0.0939 | 0.2659 | OK |
| Miami | low | 311 | 110 | 0.0120 | 0.0301 | OK |
| Milan | high | 839 | 110 | 0.0472 | 0.1715 | OK |
| Milan | low | 426 | 110 | 0.1158 | 0.2738 | OK |
| Moscow | high | 837 | 110 | 0.0525 | 0.1866 | OK |
| Moscow | low | 314 | 145 | 0.1338 | 0.2805 | OK |
| Munich | high | 840 | 110 | 0.0375 | 0.1540 | OK |
| Munich | low | 415 | 110 | 0.0737 | 0.1568 | OK |
| NYC | high | 838 | 110 | 0.0324 | 0.1468 | OK |
| NYC | low | 343 | 110 | 0.0059 | 0.0195 | OK |
| Panama City | high | 835 | 110 | 0.0244 | 0.0722 | OK |
| Panama City | low | 256 | 135 | 0.2001 | 0.3581 | OK |
| San Francisco | high | 840 | 110 | 0.0185 | 0.0555 | OK |
| San Francisco | low | 116 | not found | 0.1902 | 0.3271 | N_STAR_NOT_FOUND |
| Sao Paulo | high | 840 | 110 | 0.0360 | 0.1498 | OK |
| Sao Paulo | low | 256 | 150 | 0.1091 | 0.2385 | OK |
| Seattle | high | 839 | 110 | 0.0645 | 0.2159 | OK |
| Seattle | low | 97 | not found | 0.1443 | 0.2657 | N_STAR_NOT_FOUND |
| Seoul | high | 839 | 131 | 0.0421 | 0.1467 | OK |
| Seoul | low | 328 | 110 | 0.1148 | 0.2557 | OK |
| Shanghai | high | 840 | 110 | 0.0346 | 0.0960 | OK |
| Shanghai | low | 218 | 131 | 0.0683 | 0.1207 | OK |
| Shenzhen | high | 839 | 110 | 0.0518 | 0.1677 | OK |
| Shenzhen | low | 302 | 161 | 0.1131 | 0.2512 | OK |
| Singapore | high | 840 | 124 | 0.0403 | 0.1332 | OK |
| Singapore | low | 123 | not found | 0.0752 | 0.1720 | N_STAR_NOT_FOUND |
| Taipei | high | 840 | 110 | 0.0185 | 0.0818 | OK |
| Taipei | low | 138 | 110 | 0.0465 | 0.0806 | OK |
| Tel Aviv | high | 840 | 110 | 0.0606 | 0.1833 | OK |
| Tel Aviv | low | 287 | 145 | 0.0745 | 0.1398 | OK |
| Tokyo | high | 840 | 110 | 0.0454 | 0.1487 | OK |
| Tokyo | low | 180 | 130 | 0.0781 | 0.1817 | OK |
| Toronto | high | 840 | 136 | 0.0300 | 0.1141 | OK |
| Toronto | low | 283 | 117 | 0.0504 | 0.1116 | OK |
| Warsaw | high | 839 | 110 | 0.0473 | 0.1842 | OK |
| Warsaw | low | 399 | 110 | 0.1031 | 0.2394 | OK |
| Wellington | high | 840 | 110 | 0.0504 | 0.1315 | OK |
| Wellington | low | 545 | 151 | 0.0938 | 0.2048 | OK |
| Wuhan | high | 839 | 112 | 0.0374 | 0.1488 | OK |
| Wuhan | low | 380 | 154 | 0.0882 | 0.2194 | OK |
