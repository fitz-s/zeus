# E8 Audit — platt_models_v2 fit-time provenance

Created: 2026-05-03
Authority: read-only forensic audit (haiku-B)

## Headline

- Total active calibrators: 399
- Distinct (city, metric) covered: 102
- Calibrators fit AFTER 2026-04-30 (post-leak window): 12 (3.01% of total)
- Mass-refit dates (>50 fits in one calendar day): 2026-04-29 (387 fits)

## Fit-time histogram (top 15 calendar days)

| date(fitted_at) | n_calibrators | dominant cities |
|:---|:---|:---|
| 2026-04-29 | 387 | Amsterdam, Ankara, Atlanta, Auckland, Austin (49 total) |
| 2026-05-01 | 12 | Hong Kong |

## Per-(city, metric) active calibrator inventory

| city | metric | fitted_at | recorded_at | n_samples | brier_insample | data_version | post-leak-window? |
|:---|:---|:---|:---|:---|:---|:---|:---|
| Amsterdam | high | 2026-04-29T21:36:59.591802+00:00 | 2026-04-29 21:36:59 | 1892 | 0.008156 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Amsterdam | high | 2026-04-29T21:37:06.996485+00:00 | 2026-04-29 21:37:06 | 1472 | 0.009165 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Amsterdam | high | 2026-04-29T21:37:16.748209+00:00 | 2026-04-29 21:37:16 | 1871 | 0.009192 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Amsterdam | high | 2026-04-29T21:37:25.386108+00:00 | 2026-04-29 21:37:25 | 1456 | 0.008386 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Amsterdam | low | 2026-04-29T22:06:09.719919+00:00 | 2026-04-29 22:06:09 | 123 | 0.007722 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Amsterdam | low | 2026-04-29T22:06:10.813520+00:00 | 2026-04-29 22:06:10 | 211 | 0.00819 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Amsterdam | low | 2026-04-29T22:06:12.174098+00:00 | 2026-04-29 22:06:12 | 290 | 0.007098 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Amsterdam | low | 2026-04-29T22:06:13.054181+00:00 | 2026-04-29 22:06:13 | 128 | 0.008048 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Ankara | high | 2026-04-29T21:37:36.005958+00:00 | 2026-04-29 21:37:36 | 1892 | 0.009336 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Ankara | high | 2026-04-29T21:37:42.831641+00:00 | 2026-04-29 21:37:42 | 1472 | 0.008795 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Ankara | high | 2026-04-29T21:37:52.247897+00:00 | 2026-04-29 21:37:52 | 1871 | 0.009415 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Ankara | high | 2026-04-29T21:37:59.888807+00:00 | 2026-04-29 21:37:59 | 1448 | 0.009432 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Ankara | low | 2026-04-29T22:06:13.941561+00:00 | 2026-04-29 22:06:13 | 147 | 0.009159 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Ankara | low | 2026-04-29T22:06:17.177689+00:00 | 2026-04-29 22:06:17 | 626 | 0.008862 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Ankara | low | 2026-04-29T22:06:18.655754+00:00 | 2026-04-29 22:06:18 | 289 | 0.009278 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Ankara | low | 2026-04-29T22:06:20.237491+00:00 | 2026-04-29 22:06:20 | 286 | 0.009289 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Atlanta | high | 2026-04-29T21:38:09.027459+00:00 | 2026-04-29 21:38:09 | 1892 | 0.010236 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Atlanta | high | 2026-04-29T21:38:16.882902+00:00 | 2026-04-29 21:38:16 | 1472 | 0.009269 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Atlanta | high | 2026-04-29T21:38:25.728370+00:00 | 2026-04-29 21:38:25 | 1863 | 0.009945 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Atlanta | high | 2026-04-29T21:38:33.027205+00:00 | 2026-04-29 21:38:33 | 1448 | 0.010196 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Atlanta | low | 2026-04-29T22:06:22.422860+00:00 | 2026-04-29 22:06:22 | 393 | 0.009673 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Atlanta | low | 2026-04-29T22:06:23.982600+00:00 | 2026-04-29 22:06:23 | 302 | 0.010479 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Atlanta | low | 2026-04-29T22:06:27.036068+00:00 | 2026-04-29 22:06:27 | 582 | 0.010449 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Atlanta | low | 2026-04-29T22:06:29.299276+00:00 | 2026-04-29 22:06:29 | 389 | 0.010267 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Auckland | high | 2026-04-29T21:38:40.540835+00:00 | 2026-04-29 21:38:40 | 1472 | 0.00954 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Auckland | high | 2026-04-29T21:38:49.992315+00:00 | 2026-04-29 21:38:49 | 1892 | 0.009372 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Auckland | high | 2026-04-29T21:38:57.188865+00:00 | 2026-04-29 21:38:57 | 1448 | 0.009644 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Auckland | high | 2026-04-29T21:39:06.446986+00:00 | 2026-04-29 21:39:06 | 1871 | 0.009504 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Auckland | low | 2026-04-29T22:06:37.129818+00:00 | 2026-04-29 22:06:37 | 1472 | 0.008695 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Auckland | low | 2026-04-29T22:06:38.985013+00:00 | 2026-04-29 22:06:38 | 341 | 0.009511 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Auckland | low | 2026-04-29T22:06:41.803292+00:00 | 2026-04-29 22:06:41 | 516 | 0.009431 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Auckland | low | 2026-04-29T22:06:48.057608+00:00 | 2026-04-29 22:06:48 | 1177 | 0.008993 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Austin | high | 2026-04-29T21:39:15.835265+00:00 | 2026-04-29 21:39:15 | 1892 | 0.010117 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Austin | high | 2026-04-29T21:39:23.899579+00:00 | 2026-04-29 21:39:23 | 1472 | 0.009483 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Austin | high | 2026-04-29T21:39:32.825072+00:00 | 2026-04-29 21:39:32 | 1871 | 0.009785 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Austin | high | 2026-04-29T21:39:40.738875+00:00 | 2026-04-29 21:39:40 | 1456 | 0.008784 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Austin | low | 2026-04-29T22:06:56.896683+00:00 | 2026-04-29 22:06:56 | 1892 | 0.010418 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Austin | low | 2026-04-29T22:06:59.957614+00:00 | 2026-04-29 22:06:59 | 626 | 0.009784 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Austin | low | 2026-04-29T22:07:03.192241+00:00 | 2026-04-29 22:07:03 | 652 | 0.010319 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Austin | low | 2026-04-29T22:07:07.643385+00:00 | 2026-04-29 22:07:07 | 929 | 0.010641 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Beijing | high | 2026-04-29T21:39:50.317957+00:00 | 2026-04-29 21:39:50 | 1892 | 0.009372 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Beijing | high | 2026-04-29T21:39:58.125703+00:00 | 2026-04-29 21:39:58 | 1472 | 0.009183 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Beijing | high | 2026-04-29T21:40:06.416139+00:00 | 2026-04-29 21:40:06 | 1871 | 0.008837 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Beijing | high | 2026-04-29T21:40:13.489796+00:00 | 2026-04-29 21:40:13 | 1456 | 0.00938 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Beijing | low | 2026-04-29T22:07:10.067963+00:00 | 2026-04-29 22:07:10 | 510 | 0.009635 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Beijing | low | 2026-04-29T22:07:10.933626+00:00 | 2026-04-29 22:07:10 | 160 | 0.008791 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Beijing | low | 2026-04-29T22:07:13.920886+00:00 | 2026-04-29 22:07:13 | 521 | 0.00931 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Beijing | low | 2026-04-29T22:07:15.475247+00:00 | 2026-04-29 22:07:15 | 271 | 0.008589 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Buenos Aires | high | 2026-04-29T21:40:21.005804+00:00 | 2026-04-29 21:40:21 | 1472 | 0.009262 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Buenos Aires | high | 2026-04-29T21:40:31.476155+00:00 | 2026-04-29 21:40:31 | 1892 | 0.009197 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Buenos Aires | high | 2026-04-29T21:40:38.532363+00:00 | 2026-04-29 21:40:38 | 1440 | 0.009513 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Buenos Aires | high | 2026-04-29T21:40:48.337422+00:00 | 2026-04-29 21:40:48 | 1871 | 0.009088 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Buenos Aires | low | 2026-04-29T22:07:16.453019+00:00 | 2026-04-29 22:07:16 | 191 | 0.009258 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Buenos Aires | low | 2026-04-29T22:07:17.839580+00:00 | 2026-04-29 22:07:17 | 255 | 0.009386 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Buenos Aires | low | 2026-04-29T22:07:18.921682+00:00 | 2026-04-29 22:07:18 | 182 | 0.009522 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Buenos Aires | low | 2026-04-29T22:07:19.870815+00:00 | 2026-04-29 22:07:19 | 171 | 0.009151 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Busan | high | 2026-04-29T21:40:58.980726+00:00 | 2026-04-29 21:40:58 | 1892 | 0.008686 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Busan | high | 2026-04-29T21:41:06.704058+00:00 | 2026-04-29 21:41:06 | 1472 | 0.009697 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Busan | high | 2026-04-29T21:41:16.612749+00:00 | 2026-04-29 21:41:16 | 1871 | 0.009603 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Busan | high | 2026-04-29T21:41:23.804940+00:00 | 2026-04-29 21:41:23 | 1456 | 0.00899 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Busan | low | 2026-04-29T22:07:21.612788+00:00 | 2026-04-29 22:07:21 | 298 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Busan | low | 2026-04-29T22:07:22.305432+00:00 | 2026-04-29 22:07:22 | 115 | 0.009608 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Busan | low | 2026-04-29T22:07:24.205641+00:00 | 2026-04-29 22:07:24 | 336 | 0.009706 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Busan | low | 2026-04-29T22:07:24.989842+00:00 | 2026-04-29 22:07:24 | 120 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Cape Town | high | 2026-04-29T21:41:31.091984+00:00 | 2026-04-29 21:41:31 | 1448 | 0.008962 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Cape Town | high | 2026-04-29T21:41:39.416553+00:00 | 2026-04-29 21:41:39 | 1708 | 0.008393 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Cape Town | high | 2026-04-29T21:41:47.532504+00:00 | 2026-04-29 21:41:47 | 1448 | 0.00903 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Cape Town | high | 2026-04-29T21:41:57.920125+00:00 | 2026-04-29 21:41:57 | 1871 | 0.009118 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Cape Town | low | 2026-04-29T22:07:26.015068+00:00 | 2026-04-29 22:07:26 | 188 | 0.008872 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Cape Town | low | 2026-04-29T22:07:28.404485+00:00 | 2026-04-29 22:07:28 | 483 | 0.008846 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Cape Town | low | 2026-04-29T22:07:30.024560+00:00 | 2026-04-29 22:07:30 | 312 | 0.00913 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Cape Town | low | 2026-04-29T22:07:31.700321+00:00 | 2026-04-29 22:07:31 | 345 | 0.009032 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chengdu | high | 2026-04-29T21:42:08.227942+00:00 | 2026-04-29 21:42:08 | 1892 | 0.009003 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chengdu | high | 2026-04-29T21:42:15.546445+00:00 | 2026-04-29 21:42:15 | 1472 | 0.009362 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chengdu | high | 2026-04-29T21:42:25.855134+00:00 | 2026-04-29 21:42:25 | 1871 | 0.009071 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chengdu | high | 2026-04-29T21:42:33.785422+00:00 | 2026-04-29 21:42:33 | 1456 | 0.008981 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chengdu | low | 2026-04-29T22:07:34.443819+00:00 | 2026-04-29 22:07:34 | 488 | 0.009265 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chengdu | low | 2026-04-29T22:07:35.986113+00:00 | 2026-04-29 22:07:35 | 207 | 0.009059 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chengdu | low | 2026-04-29T22:07:38.593355+00:00 | 2026-04-29 22:07:38 | 448 | 0.009155 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chengdu | low | 2026-04-29T22:07:39.905378+00:00 | 2026-04-29 22:07:39 | 188 | 0.009068 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chicago | high | 2026-04-29T21:42:43.385798+00:00 | 2026-04-29 21:42:43 | 1892 | 0.010059 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chicago | high | 2026-04-29T21:42:51.276679+00:00 | 2026-04-29 21:42:51 | 1472 | 0.009352 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chicago | high | 2026-04-29T21:43:00.853274+00:00 | 2026-04-29 21:43:00 | 1863 | 0.01021 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chicago | high | 2026-04-29T21:43:10.293127+00:00 | 2026-04-29 21:43:10 | 1456 | 0.00967 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chicago | low | 2026-04-29T22:07:48.891668+00:00 | 2026-04-29 22:07:48 | 1892 | 0.010291 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chicago | low | 2026-04-29T22:07:50.640305+00:00 | 2026-04-29 22:07:50 | 295 | 0.009965 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chicago | low | 2026-04-29T22:07:52.705332+00:00 | 2026-04-29 22:07:52 | 378 | 0.010255 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chicago | low | 2026-04-29T22:07:56.621985+00:00 | 2026-04-29 22:07:56 | 742 | 0.009532 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chongqing | high | 2026-04-29T21:43:23.712667+00:00 | 2026-04-29 21:43:23 | 1892 | 0.00906 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chongqing | high | 2026-04-29T21:43:32.261408+00:00 | 2026-04-29 21:43:32 | 1472 | 0.009209 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chongqing | high | 2026-04-29T21:43:42.561861+00:00 | 2026-04-29 21:43:42 | 1871 | 0.009267 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chongqing | high | 2026-04-29T21:43:51.046917+00:00 | 2026-04-29 21:43:51 | 1456 | 0.008975 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Chongqing | low | 2026-04-29T22:07:58.231400+00:00 | 2026-04-29 22:07:58 | 287 | 0.008877 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chongqing | low | 2026-04-29T22:08:00.031380+00:00 | 2026-04-29 22:08:00 | 297 | 0.009276 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chongqing | low | 2026-04-29T22:08:01.960705+00:00 | 2026-04-29 22:08:01 | 341 | 0.008894 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Chongqing | low | 2026-04-29T22:08:03.193512+00:00 | 2026-04-29 22:08:03 | 208 | 0.008858 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Dallas | high | 2026-04-29T21:44:00.721322+00:00 | 2026-04-29 21:44:00 | 1892 | 0.010299 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Dallas | high | 2026-04-29T21:44:08.262029+00:00 | 2026-04-29 21:44:08 | 1472 | 0.009896 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Dallas | high | 2026-04-29T21:44:17.894287+00:00 | 2026-04-29 21:44:17 | 1863 | 0.010082 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Dallas | high | 2026-04-29T21:44:25.474038+00:00 | 2026-04-29 21:44:25 | 1456 | 0.009579 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Dallas | low | 2026-04-29T22:08:11.845199+00:00 | 2026-04-29 22:08:11 | 1892 | 0.009719 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Dallas | low | 2026-04-29T22:08:15.053138+00:00 | 2026-04-29 22:08:15 | 592 | 0.008757 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Dallas | low | 2026-04-29T22:08:18.819924+00:00 | 2026-04-29 22:08:18 | 728 | 0.009742 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Dallas | low | 2026-04-29T22:08:23.865523+00:00 | 2026-04-29 22:08:23 | 911 | 0.008517 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Denver | high | 2026-04-29T21:44:34.462861+00:00 | 2026-04-29 21:44:34 | 1892 | 0.010653 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Denver | high | 2026-04-29T21:44:42.156729+00:00 | 2026-04-29 21:44:42 | 1456 | 0.01027 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Denver | high | 2026-04-29T21:44:51.501590+00:00 | 2026-04-29 21:44:51 | 1863 | 0.010474 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Denver | high | 2026-04-29T21:44:58.992771+00:00 | 2026-04-29 21:44:58 | 1448 | 0.010371 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Denver | low | 2026-04-29T22:08:30.885772+00:00 | 2026-04-29 22:08:30 | 1456 | 0.010364 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Denver | low | 2026-04-29T22:08:36.904191+00:00 | 2026-04-29 22:08:36 | 1656 | 0.010555 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Denver | low | 2026-04-29T22:08:41.839144+00:00 | 2026-04-29 22:08:41 | 999 | 0.010438 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Guangzhou | high | 2026-04-29T21:45:09.073527+00:00 | 2026-04-29 21:45:09 | 1892 | 0.009477 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Guangzhou | high | 2026-04-29T21:45:16.712052+00:00 | 2026-04-29 21:45:16 | 1472 | 0.009621 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Guangzhou | high | 2026-04-29T21:45:26.115898+00:00 | 2026-04-29 21:45:26 | 1871 | 0.009419 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Guangzhou | high | 2026-04-29T21:45:33.846443+00:00 | 2026-04-29 21:45:33 | 1456 | 0.009568 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Guangzhou | low | 2026-04-29T22:08:44.113103+00:00 | 2026-04-29 22:08:44 | 436 | 0.009664 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Guangzhou | low | 2026-04-29T22:08:44.590177+00:00 | 2026-04-29 22:08:44 | 61 | 0.009594 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Guangzhou | low | 2026-04-29T22:08:45.804407+00:00 | 2026-04-29 22:08:45 | 190 | 0.009641 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Guangzhou | low | 2026-04-29T22:08:46.867023+00:00 | 2026-04-29 22:08:46 | 170 | 0.009696 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Helsinki | high | 2026-04-29T21:45:44.847058+00:00 | 2026-04-29 21:45:44 | 1892 | 0.008659 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Helsinki | high | 2026-04-29T21:45:51.564719+00:00 | 2026-04-29 21:45:51 | 1472 | 0.008636 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Helsinki | high | 2026-04-29T21:46:01.381634+00:00 | 2026-04-29 21:46:01 | 1871 | 0.008968 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Helsinki | high | 2026-04-29T21:46:09.475262+00:00 | 2026-04-29 21:46:09 | 1456 | 0.00835 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Helsinki | low | 2026-04-29T22:08:47.570290+00:00 | 2026-04-29 22:08:47 | 98 | 0.008753 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Helsinki | low | 2026-04-29T22:08:48.438130+00:00 | 2026-04-29 22:08:48 | 151 | 0.009316 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Helsinki | low | 2026-04-29T22:08:49.254269+00:00 | 2026-04-29 22:08:49 | 143 | 0.008957 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Helsinki | low | 2026-04-29T22:08:49.819625+00:00 | 2026-04-29 22:08:49 | 87 | 0.008918 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Hong Kong | high | 2026-05-01T18:12:47.771345+00:00 | 2026-05-01 18:12:47 | 1892 | 0.008855 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Hong Kong | high | 2026-05-01T18:12:54.301061+00:00 | 2026-05-01 18:12:54 | 1472 | 0.009334 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Hong Kong | high | 2026-05-01T18:13:05.453926+00:00 | 2026-05-01 18:13:05 | 1941 | 0.008595 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Hong Kong | high | 2026-05-01T18:13:12.961614+00:00 | 2026-05-01 18:13:12 | 1456 | 0.009025 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Hong Kong | low | 2026-05-01T18:13:15.764708+00:00 | 2026-05-01 18:13:15 | 476 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| Hong Kong | low | 2026-05-01T18:13:16.018049+00:00 | 2026-05-01 18:13:16 | 39 | 0.009647 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| Hong Kong | low | 2026-05-01T18:13:17.155137+00:00 | 2026-05-01 18:13:17 | 199 | 0.009686 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| Hong Kong | low | 2026-05-01T18:13:18.247461+00:00 | 2026-05-01 18:13:18 | 163 | 0.009708 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| Houston | high | 2026-04-29T21:46:18.206570+00:00 | 2026-04-29 21:46:18 | 1892 | 0.010617 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Houston | high | 2026-04-29T21:46:25.454654+00:00 | 2026-04-29 21:46:25 | 1472 | 0.010696 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Houston | high | 2026-04-29T21:46:34.540860+00:00 | 2026-04-29 21:46:34 | 1863 | 0.010704 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Houston | high | 2026-04-29T21:46:41.830173+00:00 | 2026-04-29 21:46:41 | 1456 | 0.010722 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Houston | low | 2026-04-29T22:08:58.914196+00:00 | 2026-04-29 22:08:58 | 1892 | 0.010189 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Houston | low | 2026-04-29T22:09:01.579817+00:00 | 2026-04-29 22:09:01 | 467 | 0.00807 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Houston | low | 2026-04-29T22:09:04.209647+00:00 | 2026-04-29 22:09:04 | 476 | 0.009532 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Houston | low | 2026-04-29T22:09:07.913818+00:00 | 2026-04-29 22:09:07 | 754 | 0.009698 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Istanbul | high | 2026-04-29T21:46:51.770557+00:00 | 2026-04-29 21:46:51 | 1892 | 0.009158 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Istanbul | high | 2026-04-29T21:46:58.124267+00:00 | 2026-04-29 21:46:58 | 1240 | 0.009685 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Istanbul | high | 2026-04-29T21:47:06.949091+00:00 | 2026-04-29 21:47:06 | 1808 | 0.009485 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Istanbul | high | 2026-04-29T21:47:12.947451+00:00 | 2026-04-29 21:47:12 | 1080 | 0.008936 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Istanbul | low | 2026-04-29T22:09:08.718180+00:00 | 2026-04-29 22:09:08 | 136 | 0.009559 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Istanbul | low | 2026-04-29T22:09:09.415487+00:00 | 2026-04-29 22:09:09 | 132 | 0.009174 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Istanbul | low | 2026-04-29T22:09:10.151121+00:00 | 2026-04-29 22:09:10 | 135 | 0.009327 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Istanbul | low | 2026-04-29T22:09:10.629870+00:00 | 2026-04-29 22:09:10 | 64 | 0.0097 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jakarta | high | 2026-04-29T21:47:20.486187+00:00 | 2026-04-29 21:47:20 | 1472 | 0.009603 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jakarta | high | 2026-04-29T21:47:31.227992+00:00 | 2026-04-29 21:47:31 | 1892 | 0.009679 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jakarta | high | 2026-04-29T21:47:38.727306+00:00 | 2026-04-29 21:47:38 | 1456 | 0.009595 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jakarta | high | 2026-04-29T21:47:48.997922+00:00 | 2026-04-29 21:47:48 | 1871 | 0.009699 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jakarta | low | 2026-04-29T22:09:11.288169+00:00 | 2026-04-29 22:09:11 | 93 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jakarta | low | 2026-04-29T22:09:11.854008+00:00 | 2026-04-29 22:09:11 | 53 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jakarta | low | 2026-04-29T22:09:12.318545+00:00 | 2026-04-29 22:09:12 | 45 | 0.009702 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jakarta | low | 2026-04-29T22:09:12.613942+00:00 | 2026-04-29 22:09:12 | 37 | 0.009707 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jeddah | high | 2026-04-29T21:47:59.273671+00:00 | 2026-04-29 21:47:59 | 1892 | 0.009706 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jeddah | high | 2026-04-29T21:48:08.840858+00:00 | 2026-04-29 21:48:08 | 1472 | 0.009705 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jeddah | high | 2026-04-29T21:48:19.723074+00:00 | 2026-04-29 21:48:19 | 1871 | 0.009707 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jeddah | high | 2026-04-29T21:48:28.662820+00:00 | 2026-04-29 21:48:28 | 1456 | 0.009706 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Jeddah | low | 2026-04-29T22:09:15.829717+00:00 | 2026-04-29 22:09:15 | 648 | 0.009573 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jeddah | low | 2026-04-29T22:09:17.696213+00:00 | 2026-04-29 22:09:17 | 318 | 0.007972 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jeddah | low | 2026-04-29T22:09:19.702380+00:00 | 2026-04-29 22:09:19 | 388 | 0.009278 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Jeddah | low | 2026-04-29T22:09:21.160551+00:00 | 2026-04-29 22:09:21 | 249 | 0.009362 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Karachi | high | 2026-04-29T21:48:40.242487+00:00 | 2026-04-29 21:48:40 | 1892 | 0.008683 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Karachi | high | 2026-04-29T21:48:47.172625+00:00 | 2026-04-29 21:48:47 | 1472 | 0.008361 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Karachi | high | 2026-04-29T21:48:57.204588+00:00 | 2026-04-29 21:48:57 | 1871 | 0.008522 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Karachi | high | 2026-04-29T21:49:05.306625+00:00 | 2026-04-29 21:49:05 | 1448 | 0.008832 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Karachi | low | 2026-04-29T22:09:22.482706+00:00 | 2026-04-29 22:09:22 | 230 | 0.009268 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Karachi | low | 2026-04-29T22:09:23.261834+00:00 | 2026-04-29 22:09:23 | 132 | 0.009214 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Karachi | low | 2026-04-29T22:09:23.714252+00:00 | 2026-04-29 22:09:23 | 48 | 0.009106 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Kuala Lumpur | high | 2026-04-29T21:49:15.427981+00:00 | 2026-04-29 21:49:15 | 1892 | 0.009707 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Kuala Lumpur | high | 2026-04-29T21:49:22.805157+00:00 | 2026-04-29 21:49:22 | 1472 | 0.009611 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Kuala Lumpur | high | 2026-04-29T21:49:32.142244+00:00 | 2026-04-29 21:49:32 | 1871 | 0.009704 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Kuala Lumpur | high | 2026-04-29T21:49:39.589831+00:00 | 2026-04-29 21:49:39 | 1456 | 0.009664 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Kuala Lumpur | low | 2026-04-29T22:09:24.157730+00:00 | 2026-04-29 22:09:24 | 45 | 0.009601 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Kuala Lumpur | low | 2026-04-29T22:09:24.492423+00:00 | 2026-04-29 22:09:24 | 43 | 0.009592 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Kuala Lumpur | low | 2026-04-29T22:09:24.669531+00:00 | 2026-04-29 22:09:24 | 20 | 0.008472 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lagos | high | 2026-04-29T21:49:49.386002+00:00 | 2026-04-29 21:49:49 | 1860 | 0.009693 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lagos | high | 2026-04-29T21:49:57.030217+00:00 | 2026-04-29 21:49:57 | 1464 | 0.009623 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lagos | high | 2026-04-29T21:50:06.524801+00:00 | 2026-04-29 21:50:06 | 1823 | 0.009675 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lagos | high | 2026-04-29T21:50:14.138250+00:00 | 2026-04-29 21:50:14 | 1448 | 0.009669 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lagos | low | 2026-04-29T22:09:30.094249+00:00 | 2026-04-29 22:09:30 | 1112 | 0.008201 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lagos | low | 2026-04-29T22:09:31.433557+00:00 | 2026-04-29 22:09:31 | 226 | 0.006647 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lagos | low | 2026-04-29T22:09:32.016023+00:00 | 2026-04-29 22:09:32 | 103 | 0.008683 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lagos | low | 2026-04-29T22:09:34.852111+00:00 | 2026-04-29 22:09:34 | 515 | 0.00774 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| London | high | 2026-04-29T21:50:25.274240+00:00 | 2026-04-29 21:50:25 | 1892 | 0.008608 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| London | high | 2026-04-29T21:50:31.769424+00:00 | 2026-04-29 21:50:31 | 1472 | 0.008807 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| London | high | 2026-04-29T21:50:41.561034+00:00 | 2026-04-29 21:50:41 | 1871 | 0.008762 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| London | high | 2026-04-29T21:50:49.611864+00:00 | 2026-04-29 21:50:49 | 1456 | 0.008555 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| London | low | 2026-04-29T22:09:45.262405+00:00 | 2026-04-29 22:09:45 | 1892 | 0.00921 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| London | low | 2026-04-29T22:09:47.608975+00:00 | 2026-04-29 22:09:47 | 443 | 0.009476 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| London | low | 2026-04-29T22:09:52.813944+00:00 | 2026-04-29 22:09:52 | 957 | 0.009172 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| London | low | 2026-04-29T22:09:56.737746+00:00 | 2026-04-29 22:09:56 | 674 | 0.009181 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Los Angeles | high | 2026-04-29T21:50:58.650729+00:00 | 2026-04-29 21:50:58 | 1892 | 0.010268 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Los Angeles | high | 2026-04-29T21:51:06.218551+00:00 | 2026-04-29 21:51:06 | 1472 | 0.009122 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Los Angeles | high | 2026-04-29T21:51:14.817172+00:00 | 2026-04-29 21:51:14 | 1871 | 0.009759 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Los Angeles | high | 2026-04-29T21:51:21.962601+00:00 | 2026-04-29 21:51:21 | 1456 | 0.009955 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Los Angeles | low | 2026-04-29T22:09:57.425331+00:00 | 2026-04-29 22:09:57 | 114 | 0.010158 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Los Angeles | low | 2026-04-29T22:09:59.189188+00:00 | 2026-04-29 22:09:59 | 293 | 0.01018 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Los Angeles | low | 2026-04-29T22:10:00.199422+00:00 | 2026-04-29 22:10:00 | 193 | 0.010304 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Los Angeles | low | 2026-04-29T22:10:00.853748+00:00 | 2026-04-29 22:10:00 | 109 | 0.009902 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lucknow | high | 2026-04-29T21:51:30.664138+00:00 | 2026-04-29 21:51:30 | 1748 | 0.009351 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lucknow | high | 2026-04-29T21:51:38.452243+00:00 | 2026-04-29 21:51:38 | 1472 | 0.00886 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lucknow | high | 2026-04-29T21:51:48.621774+00:00 | 2026-04-29 21:51:48 | 1871 | 0.008336 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lucknow | high | 2026-04-29T21:51:56.970253+00:00 | 2026-04-29 21:51:56 | 1456 | 0.008585 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Lucknow | low | 2026-04-29T22:10:01.592429+00:00 | 2026-04-29 22:10:01 | 143 | 0.008987 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lucknow | low | 2026-04-29T22:10:02.018698+00:00 | 2026-04-29 22:10:02 | 70 | 0.008015 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Lucknow | low | 2026-04-29T22:10:02.191671+00:00 | 2026-04-29 22:10:02 | 15 | 0.007917 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Madrid | high | 2026-04-29T21:52:07.247444+00:00 | 2026-04-29 21:52:07 | 1892 | 0.008859 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Madrid | high | 2026-04-29T21:52:14.516317+00:00 | 2026-04-29 21:52:14 | 1472 | 0.008376 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Madrid | high | 2026-04-29T21:52:22.722856+00:00 | 2026-04-29 21:52:22 | 1871 | 0.008702 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Madrid | high | 2026-04-29T21:52:30.359905+00:00 | 2026-04-29 21:52:30 | 1456 | 0.008815 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Madrid | low | 2026-04-29T22:10:04.513203+00:00 | 2026-04-29 22:10:04 | 479 | 0.008311 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Madrid | low | 2026-04-29T22:10:09.774156+00:00 | 2026-04-29 22:10:09 | 1048 | 0.008409 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Madrid | low | 2026-04-29T22:10:13.194182+00:00 | 2026-04-29 22:10:13 | 789 | 0.008019 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Madrid | low | 2026-04-29T22:10:15.799920+00:00 | 2026-04-29 22:10:15 | 547 | 0.008872 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Manila | high | 2026-04-29T21:52:39.861044+00:00 | 2026-04-29 21:52:39 | 1892 | 0.009626 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Manila | high | 2026-04-29T21:52:47.327748+00:00 | 2026-04-29 21:52:47 | 1472 | 0.009635 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Manila | high | 2026-04-29T21:52:57.107650+00:00 | 2026-04-29 21:52:57 | 1871 | 0.009692 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Manila | high | 2026-04-29T21:53:04.431274+00:00 | 2026-04-29 21:53:04 | 1448 | 0.009604 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Manila | low | 2026-04-29T22:10:16.929602+00:00 | 2026-04-29 22:10:16 | 197 | 0.007397 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Manila | low | 2026-04-29T22:10:18.612531+00:00 | 2026-04-29 22:10:18 | 266 | 0.008442 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Manila | low | 2026-04-29T22:10:18.815851+00:00 | 2026-04-29 22:10:18 | 23 | 0.005144 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Mexico City | high | 2026-04-29T21:53:13.605618+00:00 | 2026-04-29 21:53:13 | 1892 | 0.007205 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Mexico City | high | 2026-04-29T21:53:20.127499+00:00 | 2026-04-29 21:53:20 | 1472 | 0.008855 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Mexico City | high | 2026-04-29T21:53:30.575235+00:00 | 2026-04-29 21:53:30 | 1871 | 0.007673 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Mexico City | high | 2026-04-29T21:53:38.373839+00:00 | 2026-04-29 21:53:38 | 1456 | 0.008479 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Mexico City | low | 2026-04-29T22:10:28.732512+00:00 | 2026-04-29 22:10:28 | 1892 | 0.008988 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Mexico City | low | 2026-04-29T22:10:35.799367+00:00 | 2026-04-29 22:10:35 | 1472 | 0.009576 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Mexico City | low | 2026-04-29T22:10:45.158586+00:00 | 2026-04-29 22:10:45 | 1871 | 0.009118 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Mexico City | low | 2026-04-29T22:10:52.056668+00:00 | 2026-04-29 22:10:52 | 1456 | 0.009374 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Miami | high | 2026-04-29T21:53:47.993780+00:00 | 2026-04-29 21:53:47 | 1892 | 0.008853 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Miami | high | 2026-04-29T21:53:55.854327+00:00 | 2026-04-29 21:53:55 | 1472 | 0.008649 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Miami | high | 2026-04-29T21:54:06.025697+00:00 | 2026-04-29 21:54:06 | 1863 | 0.008915 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Miami | high | 2026-04-29T21:54:14.023263+00:00 | 2026-04-29 21:54:14 | 1448 | 0.008393 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Miami | low | 2026-04-29T22:10:53.912748+00:00 | 2026-04-29 22:10:53 | 379 | 0.010706 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Miami | low | 2026-04-29T22:10:54.415435+00:00 | 2026-04-29 22:10:54 | 78 | 0.010744 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Miami | low | 2026-04-29T22:10:56.486555+00:00 | 2026-04-29 22:10:56 | 419 | 0.010739 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Miami | low | 2026-04-29T22:10:57.232719+00:00 | 2026-04-29 22:10:57 | 128 | 0.010737 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Milan | high | 2026-04-29T21:54:24.348963+00:00 | 2026-04-29 21:54:24 | 1892 | 0.009116 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Milan | high | 2026-04-29T21:54:32.076554+00:00 | 2026-04-29 21:54:32 | 1472 | 0.009341 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Milan | high | 2026-04-29T21:54:42.316010+00:00 | 2026-04-29 21:54:42 | 1871 | 0.0086 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Milan | high | 2026-04-29T21:54:49.975108+00:00 | 2026-04-29 21:54:49 | 1448 | 0.008931 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Milan | low | 2026-04-29T22:10:58.465399+00:00 | 2026-04-29 22:10:58 | 228 | 0.008104 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Milan | low | 2026-04-29T22:11:00.619329+00:00 | 2026-04-29 22:11:00 | 417 | 0.008123 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Milan | low | 2026-04-29T22:11:02.904075+00:00 | 2026-04-29 22:11:02 | 455 | 0.008268 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Milan | low | 2026-04-29T22:11:04.031493+00:00 | 2026-04-29 22:11:04 | 209 | 0.008209 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Moscow | high | 2026-04-29T21:54:59.636228+00:00 | 2026-04-29 21:54:59 | 1892 | 0.008368 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Moscow | high | 2026-04-29T21:55:07.270227+00:00 | 2026-04-29 21:55:07 | 1472 | 0.008404 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Moscow | high | 2026-04-29T21:55:16.718454+00:00 | 2026-04-29 21:55:16 | 1848 | 0.009171 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Moscow | high | 2026-04-29T21:55:25.666482+00:00 | 2026-04-29 21:55:25 | 1456 | 0.008534 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Moscow | low | 2026-04-29T22:11:04.610731+00:00 | 2026-04-29 22:11:04 | 89 | 0.008454 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Moscow | low | 2026-04-29T22:11:05.375734+00:00 | 2026-04-29 22:11:05 | 152 | 0.007632 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Moscow | low | 2026-04-29T22:11:06.174790+00:00 | 2026-04-29 22:11:06 | 143 | 0.008838 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Moscow | low | 2026-04-29T22:11:06.953940+00:00 | 2026-04-29 22:11:06 | 132 | 0.007778 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Munich | high | 2026-04-29T21:55:36.308460+00:00 | 2026-04-29 21:55:36 | 1892 | 0.009061 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Munich | high | 2026-04-29T21:55:44.384055+00:00 | 2026-04-29 21:55:44 | 1472 | 0.00895 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Munich | high | 2026-04-29T21:55:54.351923+00:00 | 2026-04-29 21:55:54 | 1871 | 0.009206 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Munich | high | 2026-04-29T21:56:02.419691+00:00 | 2026-04-29 21:56:02 | 1456 | 0.008985 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Munich | low | 2026-04-29T22:11:07.857056+00:00 | 2026-04-29 22:11:07 | 160 | 0.008927 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Munich | low | 2026-04-29T22:11:10.072912+00:00 | 2026-04-29 22:11:10 | 426 | 0.009526 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Munich | low | 2026-04-29T22:11:12.491323+00:00 | 2026-04-29 22:11:12 | 489 | 0.009575 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Munich | low | 2026-04-29T22:11:13.498961+00:00 | 2026-04-29 22:11:13 | 183 | 0.009384 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| NYC | high | 2026-04-29T21:56:11.805440+00:00 | 2026-04-29 21:56:11 | 1892 | 0.010358 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| NYC | high | 2026-04-29T21:56:19.052142+00:00 | 2026-04-29 21:56:19 | 1472 | 0.009728 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| NYC | high | 2026-04-29T21:56:28.571638+00:00 | 2026-04-29 21:56:28 | 1863 | 0.010092 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| NYC | high | 2026-04-29T21:56:35.635247+00:00 | 2026-04-29 21:56:35 | 1448 | 0.009793 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| NYC | low | 2026-04-29T22:11:14.630341+00:00 | 2026-04-29 22:11:14 | 209 | 0.010751 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| NYC | low | 2026-04-29T22:11:16.253701+00:00 | 2026-04-29 22:11:16 | 313 | 0.010743 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| NYC | low | 2026-04-29T22:11:17.531978+00:00 | 2026-04-29 22:11:17 | 236 | 0.010751 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| NYC | low | 2026-04-29T22:11:19.184823+00:00 | 2026-04-29 22:11:19 | 305 | 0.010751 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Panama City | high | 2026-04-29T21:56:45.107795+00:00 | 2026-04-29 21:56:45 | 1852 | 0.009623 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Panama City | high | 2026-04-29T21:56:52.507806+00:00 | 2026-04-29 21:56:52 | 1472 | 0.00953 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Panama City | high | 2026-04-29T21:57:01.953774+00:00 | 2026-04-29 21:57:01 | 1871 | 0.009656 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Panama City | high | 2026-04-29T21:57:09.250485+00:00 | 2026-04-29 21:57:09 | 1456 | 0.00957 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Panama City | low | 2026-04-29T22:11:20.786950+00:00 | 2026-04-29 22:11:20 | 279 | 0.007253 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Panama City | low | 2026-04-29T22:11:21.062311+00:00 | 2026-04-29 22:11:21 | 40 | 0.007643 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Panama City | low | 2026-04-29T22:11:22.865354+00:00 | 2026-04-29 22:11:22 | 305 | 0.007991 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Panama City | low | 2026-04-29T22:11:23.103591+00:00 | 2026-04-29 22:11:23 | 33 | 0.008148 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Paris | high | 2026-05-01T18:22:31.469745+00:00 | 2026-05-01 18:22:31 | 224 | 0.009303 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Paris | high | 2026-05-01T18:22:33.844294+00:00 | 2026-05-01 18:22:33 | 482 | 0.00947 | tigge_mx2t6_local_calendar_day_max_v1 | YES |
| Paris | low | 2026-05-01T18:22:34.424183+00:00 | 2026-05-01 18:22:34 | 25 | 0.008633 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| Paris | low | 2026-05-01T18:22:35.195979+00:00 | 2026-05-01 18:22:35 | 167 | 0.007695 | tigge_mn2t6_local_calendar_day_min_v1 | YES |
| San Francisco | high | 2026-04-29T21:57:51.223468+00:00 | 2026-04-29 21:57:51 | 1892 | 0.010402 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| San Francisco | high | 2026-04-29T21:57:58.782130+00:00 | 2026-04-29 21:57:58 | 1472 | 0.01074 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| San Francisco | high | 2026-04-29T21:58:08.225285+00:00 | 2026-04-29 21:58:08 | 1871 | 0.010744 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| San Francisco | high | 2026-04-29T21:58:15.374500+00:00 | 2026-04-29 21:58:15 | 1456 | 0.010702 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| San Francisco | low | 2026-04-29T22:11:30.853544+00:00 | 2026-04-29 22:11:30 | 62 | 0.008252 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| San Francisco | low | 2026-04-29T22:11:31.093643+00:00 | 2026-04-29 22:11:31 | 37 | 0.009895 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| San Francisco | low | 2026-04-29T22:11:31.614222+00:00 | 2026-04-29 22:11:31 | 68 | 0.009588 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| San Francisco | low | 2026-04-29T22:11:31.806385+00:00 | 2026-04-29 22:11:31 | 23 | 0.006067 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Sao Paulo | high | 2026-04-29T21:58:23.409341+00:00 | 2026-04-29 21:58:23 | 1472 | 0.008866 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Sao Paulo | high | 2026-04-29T21:58:33.204183+00:00 | 2026-04-29 21:58:33 | 1892 | 0.009386 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Sao Paulo | high | 2026-04-29T21:58:39.783489+00:00 | 2026-04-29 21:58:39 | 1456 | 0.009088 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Sao Paulo | high | 2026-04-29T21:58:49.631126+00:00 | 2026-04-29 21:58:49 | 1871 | 0.00873 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Sao Paulo | low | 2026-04-29T22:11:32.998028+00:00 | 2026-04-29 22:11:32 | 206 | 0.008596 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Sao Paulo | low | 2026-04-29T22:11:33.583536+00:00 | 2026-04-29 22:11:33 | 112 | 0.008874 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Sao Paulo | low | 2026-04-29T22:11:34.342372+00:00 | 2026-04-29 22:11:34 | 129 | 0.007936 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Sao Paulo | low | 2026-04-29T22:11:35.465045+00:00 | 2026-04-29 22:11:35 | 215 | 0.008692 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seattle | high | 2026-04-29T21:58:59.779230+00:00 | 2026-04-29 21:58:59 | 1892 | 0.008863 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seattle | high | 2026-04-29T21:59:07.381988+00:00 | 2026-04-29 21:59:07 | 1472 | 0.010009 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seattle | high | 2026-04-29T21:59:16.680533+00:00 | 2026-04-29 21:59:16 | 1863 | 0.009468 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seattle | high | 2026-04-29T21:59:24.559568+00:00 | 2026-04-29 21:59:24 | 1456 | 0.008846 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seattle | low | 2026-04-29T22:11:35.694354+00:00 | 2026-04-29 22:11:35 | 29 | 0.009913 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seattle | low | 2026-04-29T22:11:36.140803+00:00 | 2026-04-29 22:11:36 | 59 | 0.010323 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seattle | low | 2026-04-29T22:11:36.611907+00:00 | 2026-04-29 22:11:36 | 61 | 0.009741 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seattle | low | 2026-04-29T22:11:36.817578+00:00 | 2026-04-29 22:11:36 | 25 | 0.007474 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seoul | high | 2026-04-29T21:59:35.190695+00:00 | 2026-04-29 21:59:35 | 1892 | 0.008846 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seoul | high | 2026-04-29T21:59:42.716865+00:00 | 2026-04-29 21:59:42 | 1472 | 0.009603 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seoul | high | 2026-04-29T21:59:51.983127+00:00 | 2026-04-29 21:59:51 | 1863 | 0.009536 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seoul | high | 2026-04-29T21:59:59.854930+00:00 | 2026-04-29 21:59:59 | 1456 | 0.009029 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Seoul | low | 2026-04-29T22:11:38.562498+00:00 | 2026-04-29 22:11:38 | 349 | 0.008638 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seoul | low | 2026-04-29T22:11:39.137957+00:00 | 2026-04-29 22:11:39 | 78 | 0.006307 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seoul | low | 2026-04-29T22:11:40.473149+00:00 | 2026-04-29 22:11:40 | 242 | 0.008022 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Seoul | low | 2026-04-29T22:11:41.749221+00:00 | 2026-04-29 22:11:41 | 229 | 0.008986 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shanghai | high | 2026-04-29T22:00:10.009747+00:00 | 2026-04-29 22:00:10 | 1892 | 0.009377 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shanghai | high | 2026-04-29T22:00:17.715998+00:00 | 2026-04-29 22:00:17 | 1472 | 0.009675 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shanghai | high | 2026-04-29T22:00:27.190550+00:00 | 2026-04-29 22:00:27 | 1871 | 0.009638 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shanghai | high | 2026-04-29T22:00:34.701709+00:00 | 2026-04-29 22:00:34 | 1456 | 0.009305 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shanghai | low | 2026-04-29T22:11:42.967260+00:00 | 2026-04-29 22:11:42 | 217 | 0.009636 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shanghai | low | 2026-04-29T22:11:43.196872+00:00 | 2026-04-29 22:11:43 | 33 | 0.009077 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shanghai | low | 2026-04-29T22:11:43.903331+00:00 | 2026-04-29 22:11:43 | 123 | 0.009235 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shanghai | low | 2026-04-29T22:11:44.316947+00:00 | 2026-04-29 22:11:44 | 68 | 0.009664 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shenzhen | high | 2026-04-29T22:00:45.220105+00:00 | 2026-04-29 22:00:45 | 1892 | 0.008922 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shenzhen | high | 2026-04-29T22:00:52.881419+00:00 | 2026-04-29 22:00:52 | 1464 | 0.009239 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shenzhen | high | 2026-04-29T22:01:02.659441+00:00 | 2026-04-29 22:01:02 | 1871 | 0.008949 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shenzhen | high | 2026-04-29T22:01:10.292639+00:00 | 2026-04-29 22:01:10 | 1456 | 0.009108 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Shenzhen | low | 2026-04-29T22:11:46.859973+00:00 | 2026-04-29 22:11:46 | 476 | 0.008798 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shenzhen | low | 2026-04-29T22:11:47.118527+00:00 | 2026-04-29 22:11:47 | 39 | 0.008946 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shenzhen | low | 2026-04-29T22:11:48.308671+00:00 | 2026-04-29 22:11:48 | 196 | 0.007226 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Shenzhen | low | 2026-04-29T22:11:49.327509+00:00 | 2026-04-29 22:11:49 | 163 | 0.008727 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Singapore | high | 2026-04-29T22:01:20.049282+00:00 | 2026-04-29 22:01:20 | 1892 | 0.009474 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Singapore | high | 2026-04-29T22:01:27.581043+00:00 | 2026-04-29 22:01:27 | 1472 | 0.009223 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Singapore | high | 2026-04-29T22:01:36.652796+00:00 | 2026-04-29 22:01:36 | 1871 | 0.00943 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Singapore | high | 2026-04-29T22:01:44.247819+00:00 | 2026-04-29 22:01:44 | 1456 | 0.009298 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Singapore | low | 2026-04-29T22:11:49.886122+00:00 | 2026-04-29 22:11:49 | 76 | 0.009526 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Singapore | low | 2026-04-29T22:11:50.101971+00:00 | 2026-04-29 22:11:50 | 30 | 0.008011 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Singapore | low | 2026-04-29T22:11:50.677377+00:00 | 2026-04-29 22:11:50 | 75 | 0.009719 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Singapore | low | 2026-04-29T22:11:50.868662+00:00 | 2026-04-29 22:11:50 | 18 | 0.008928 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Taipei | high | 2026-04-29T22:01:53.446286+00:00 | 2026-04-29 22:01:53 | 1892 | 0.009495 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Taipei | high | 2026-04-29T22:02:01.066435+00:00 | 2026-04-29 22:02:01 | 1472 | 0.009689 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Taipei | high | 2026-04-29T22:02:10.196399+00:00 | 2026-04-29 22:02:10 | 1871 | 0.009589 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Taipei | high | 2026-04-29T22:02:17.475642+00:00 | 2026-04-29 22:02:17 | 1456 | 0.009549 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Taipei | low | 2026-04-29T22:11:51.387179+00:00 | 2026-04-29 22:11:51 | 76 | 0.009459 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Taipei | low | 2026-04-29T22:11:51.860089+00:00 | 2026-04-29 22:11:51 | 55 | 0.009686 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Taipei | low | 2026-04-29T22:11:52.552893+00:00 | 2026-04-29 22:11:52 | 99 | 0.009687 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Taipei | low | 2026-04-29T22:11:52.786722+00:00 | 2026-04-29 22:11:52 | 26 | 0.009703 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tel Aviv | high | 2026-04-29T22:02:27.405962+00:00 | 2026-04-29 22:02:27 | 1892 | 0.009639 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tel Aviv | high | 2026-04-29T22:02:35.710502+00:00 | 2026-04-29 22:02:35 | 1472 | 0.008126 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tel Aviv | high | 2026-04-29T22:02:45.529526+00:00 | 2026-04-29 22:02:45 | 1871 | 0.00907 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tel Aviv | high | 2026-04-29T22:02:53.871813+00:00 | 2026-04-29 22:02:53 | 1456 | 0.008013 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tel Aviv | low | 2026-04-29T22:11:53.362914+00:00 | 2026-04-29 22:11:53 | 85 | 0.008097 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tel Aviv | low | 2026-04-29T22:11:54.827382+00:00 | 2026-04-29 22:11:54 | 263 | 0.009692 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tel Aviv | low | 2026-04-29T22:11:55.749574+00:00 | 2026-04-29 22:11:55 | 163 | 0.009408 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tel Aviv | low | 2026-04-29T22:11:56.488999+00:00 | 2026-04-29 22:11:56 | 123 | 0.009523 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tokyo | high | 2026-04-29T22:03:04.317268+00:00 | 2026-04-29 22:03:04 | 1892 | 0.008719 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tokyo | high | 2026-04-29T22:03:11.767821+00:00 | 2026-04-29 22:03:11 | 1472 | 0.009644 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tokyo | high | 2026-04-29T22:03:20.632535+00:00 | 2026-04-29 22:03:20 | 1871 | 0.009369 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tokyo | high | 2026-04-29T22:03:28.321191+00:00 | 2026-04-29 22:03:28 | 1456 | 0.008922 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Tokyo | low | 2026-04-29T22:11:57.322506+00:00 | 2026-04-29 22:11:57 | 139 | 0.008974 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tokyo | low | 2026-04-29T22:11:57.535700+00:00 | 2026-04-29 22:11:57 | 25 | 0.009481 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tokyo | low | 2026-04-29T22:11:58.267676+00:00 | 2026-04-29 22:11:58 | 116 | 0.009546 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Tokyo | low | 2026-04-29T22:11:58.807340+00:00 | 2026-04-29 22:11:58 | 86 | 0.008368 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Toronto | high | 2026-04-29T22:03:38.361557+00:00 | 2026-04-29 22:03:38 | 1892 | 0.009064 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Toronto | high | 2026-04-29T22:03:45.623529+00:00 | 2026-04-29 22:03:45 | 1472 | 0.009557 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Toronto | high | 2026-04-29T22:03:54.607774+00:00 | 2026-04-29 22:03:54 | 1871 | 0.009503 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Toronto | high | 2026-04-29T22:04:01.612031+00:00 | 2026-04-29 22:04:01 | 1456 | 0.009304 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Toronto | low | 2026-04-29T22:11:59.406768+00:00 | 2026-04-29 22:11:59 | 105 | 0.009589 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Toronto | low | 2026-04-29T22:12:01.138498+00:00 | 2026-04-29 22:12:01 | 339 | 0.009586 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Toronto | low | 2026-04-29T22:12:02.066218+00:00 | 2026-04-29 22:12:02 | 152 | 0.008969 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Toronto | low | 2026-04-29T22:12:03.262371+00:00 | 2026-04-29 22:12:03 | 206 | 0.009698 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Warsaw | high | 2026-04-29T22:04:11.557103+00:00 | 2026-04-29 22:04:11 | 1884 | 0.008641 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Warsaw | high | 2026-04-29T22:04:20.123622+00:00 | 2026-04-29 22:04:20 | 1472 | 0.008623 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Warsaw | high | 2026-04-29T22:04:29.922076+00:00 | 2026-04-29 22:04:29 | 1871 | 0.009034 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Warsaw | high | 2026-04-29T22:04:37.903596+00:00 | 2026-04-29 22:04:37 | 1456 | 0.00861 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Warsaw | low | 2026-04-29T22:12:03.946677+00:00 | 2026-04-29 22:12:03 | 124 | 0.008362 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Warsaw | low | 2026-04-29T22:12:05.527388+00:00 | 2026-04-29 22:12:05 | 309 | 0.008962 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Warsaw | low | 2026-04-29T22:12:07.346128+00:00 | 2026-04-29 22:12:07 | 361 | 0.008755 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Warsaw | low | 2026-04-29T22:12:08.333291+00:00 | 2026-04-29 22:12:08 | 185 | 0.008572 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wellington | high | 2026-04-29T22:04:45.836503+00:00 | 2026-04-29 22:04:45 | 1472 | 0.008783 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wellington | high | 2026-04-29T22:04:55.455958+00:00 | 2026-04-29 22:04:55 | 1892 | 0.009623 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wellington | high | 2026-04-29T22:05:02.448131+00:00 | 2026-04-29 22:05:02 | 1456 | 0.009554 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wellington | high | 2026-04-29T22:05:11.642797+00:00 | 2026-04-29 22:05:11 | 1871 | 0.009466 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wellington | low | 2026-04-29T22:12:15.287013+00:00 | 2026-04-29 22:12:15 | 1472 | 0.009128 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wellington | low | 2026-04-29T22:12:16.580690+00:00 | 2026-04-29 22:12:16 | 244 | 0.008519 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wellington | low | 2026-04-29T22:12:19.622367+00:00 | 2026-04-29 22:12:19 | 543 | 0.009055 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wellington | low | 2026-04-29T22:12:25.073302+00:00 | 2026-04-29 22:12:25 | 1084 | 0.009094 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wuhan | high | 2026-04-29T22:05:21.226732+00:00 | 2026-04-29 22:05:21 | 1884 | 0.008889 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wuhan | high | 2026-04-29T22:05:29.033879+00:00 | 2026-04-29 22:05:29 | 1472 | 0.009213 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wuhan | high | 2026-04-29T22:05:38.620352+00:00 | 2026-04-29 22:05:38 | 1871 | 0.009239 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wuhan | high | 2026-04-29T22:05:45.740412+00:00 | 2026-04-29 22:05:45 | 1456 | 0.009351 | tigge_mx2t6_local_calendar_day_max_v1 | no |
| Wuhan | low | 2026-04-29T22:12:27.221473+00:00 | 2026-04-29 22:12:27 | 416 | 0.008926 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wuhan | low | 2026-04-29T22:12:28.229159+00:00 | 2026-04-29 22:12:28 | 151 | 0.008929 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wuhan | low | 2026-04-29T22:12:30.062094+00:00 | 2026-04-29 22:12:30 | 306 | 0.00902 | tigge_mn2t6_local_calendar_day_min_v1 | no |
| Wuhan | low | 2026-04-29T22:12:31.575480+00:00 | 2026-04-29 22:12:31 | 266 | 0.008434 | tigge_mn2t6_local_calendar_day_min_v1 | no |

## Live serving path

File: /Users/leofitz/.openclaw/workspace-venus/zeus/src/calibration/store.py
Lines: 627-661
Query the live engine uses to load active calibrators:
```python
    if data_version is not None:
        row = conn.execute(
            f"""
            SELECT param_A, param_B, param_C, bootstrap_params_json,
                   n_samples, brier_insample, fitted_at, input_space
            FROM {table}
            WHERE temperature_metric = ?
              AND cluster = ?
              AND season = ?
              AND data_version = ?
              AND input_space = ?
              AND is_active = 1
              AND authority = 'VERIFIED'
            ORDER BY fitted_at DESC
            LIMIT 1
            """,
            (temperature_metric, cluster, season, data_version, input_space),
        ).fetchone()
```

## Conclusion

The live serving path in `src/calibration/store.py` (`load_platt_model_v2`) pulls calibrators where `is_active=1` and `authority='VERIFIED'`. The audit reveals that 387 of 399 active calibrators (97%) were mass-refit on 2026-04-29. While this date is technically before the 2026-04-30 cutoff, it is extremely close to the end of the calibration test window (2026-01-01 → 2026-04-30), and likely uses training pairs that overlap with the test period. Only 12 calibrators (3% for Hong Kong) were fit strictly after the cutoff. However, the 2026-04-29 event constitutes a "mass-refit" that aligns with the timing of suspected leakage.
