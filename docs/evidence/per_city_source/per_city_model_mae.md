# Per-city per-model settlement MAE @ lead-1 (unit-corrected to °C)

DB=state/zeus-forecasts.db  min_n=8  (validator for per-city-best selection; every city's BEST row is its settlement-faithful near-airport source)

Cities with a <=1.5°C-MAE best source: 48/51


## Amsterdam — BEST: icon_d2 (MAE 0.72°C, bias -0.34, n=100)
   0.72  bias -0.34  n=100  icon_d2
   0.75  bias -0.47  n= 95  ukmo_global_deterministic_10km
   0.77  bias -0.39  n= 99  icon_eu
   0.81  bias -0.79  n= 37  icon_seamless
   0.87  bias +0.42  n= 64  meteofrance_arome_france_hd
   0.88  bias -0.03  n= 98  gfs_global
   1.01  bias +0.09  n= 78  gem_global
   1.13  bias -0.94  n= 98  ecmwf_ifs
   1.40  bias -1.26  n= 99  icon_global
   1.87  bias -1.86  n= 96  jma_seamless

## Ankara — BEST: icon_eu (MAE 1.24°C, bias -0.21, n=37)
   1.24  bias -0.21  n= 37  icon_eu
   1.24  bias -0.26  n= 39  icon_seamless
   1.31  bias -0.09  n= 36  jma_seamless
   1.39  bias -0.41  n= 39  icon_global
   1.52  bias -0.96  n= 38  gfs_global
   1.76  bias -0.94  n= 17  gem_global
   1.76  bias -1.41  n=165  ukmo_global_deterministic_10km
   1.93  bias -1.31  n= 38  ecmwf_ifs

## Atlanta — BEST: icon_seamless (MAE 1.16°C, bias -0.53, n=41)
   1.16  bias -0.53  n= 41  icon_seamless
   1.20  bias -0.50  n=180  ncep_nbm_conus
   1.31  bias -0.63  n=183  ecmwf_ifs
   1.34  bias -0.90  n=184  icon_global
   1.35  bias -0.39  n=183  gfs_global
   1.99  bias -1.34  n=161  gem_global
   2.13  bias -1.50  n=180  ukmo_global_deterministic_10km
   2.29  bias -1.91  n=180  jma_seamless

## Austin — BEST: ukmo_global_deterministic_10km (MAE 0.96°C, bias 0.34, n=105)
   0.96  bias +0.34  n=105  ukmo_global_deterministic_10km
   1.06  bias +0.12  n=108  ecmwf_ifs
   1.13  bias -0.31  n=109  icon_global
   1.17  bias -0.14  n=105  ncep_nbm_conus
   1.18  bias +0.50  n=108  gfs_global
   1.19  bias -0.28  n= 41  icon_seamless
   2.00  bias -0.30  n= 86  gem_global
   2.97  bias -2.67  n=105  jma_seamless

## Beijing — BEST: icon_seamless (MAE 1.27°C, bias -0.06, n=45)
   1.27  bias -0.06  n= 45  icon_seamless
   1.37  bias -0.48  n=114  icon_global
   1.62  bias -0.38  n=106  ukmo_global_deterministic_10km
   1.67  bias -0.50  n=113  ecmwf_ifs
   1.82  bias +0.63  n= 89  gem_global
   2.00  bias +1.45  n=110  jma_seamless
   2.16  bias +1.89  n=113  gfs_global

## Buenos Aires — BEST: ukmo_global_deterministic_10km (MAE 1.29°C, bias -0.82, n=207)
   1.29  bias -0.82  n=207  ukmo_global_deterministic_10km
   1.38  bias -0.98  n=210  ecmwf_ifs
   1.43  bias -1.07  n=211  icon_global
   1.58  bias -1.58  n= 41  icon_seamless
   1.64  bias +0.31  n=210  gfs_global
   1.86  bias -1.21  n=188  gem_global
   2.15  bias -1.99  n=207  jma_seamless

## Busan — BEST: gfs_global (MAE 0.85°C, bias -0.22, n=102)
   0.85  bias -0.22  n=102  gfs_global
   0.91  bias +0.01  n=102  ecmwf_ifs
   1.12  bias -0.25  n= 78  gem_global
   1.29  bias -0.53  n= 95  ukmo_global_deterministic_10km
   1.37  bias -0.96  n= 98  jma_seamless
   2.24  bias -2.01  n=103  icon_global
   2.80  bias -2.80  n= 45  icon_seamless

## Cape Town — BEST: icon_global (MAE 0.97°C, bias -0.44, n=89)
   0.97  bias -0.44  n= 89  icon_global
   0.99  bias -0.82  n= 37  icon_seamless
   1.07  bias -0.62  n= 85  ukmo_global_deterministic_10km
   1.16  bias +0.52  n= 88  gfs_global
   1.17  bias -0.18  n= 68  gem_global
   1.27  bias -0.46  n= 88  ecmwf_ifs
   1.74  bias -1.69  n= 86  jma_seamless

## Chengdu — BEST: ukmo_global_deterministic_10km (MAE 1.47°C, bias -0.42, n=106)
   1.47  bias -0.42  n=106  ukmo_global_deterministic_10km
   1.57  bias -1.32  n= 45  icon_seamless
   1.76  bias +0.41  n=113  ecmwf_ifs
   1.77  bias -1.07  n=113  gfs_global
   1.82  bias -1.34  n=114  icon_global
   1.82  bias -0.74  n= 89  gem_global
   2.44  bias -1.92  n=110  jma_seamless

## Chicago — BEST: icon_seamless (MAE 1.07°C, bias 0.67, n=41)
   1.07  bias +0.67  n= 41  icon_seamless
   1.21  bias -0.34  n=147  ncep_nbm_conus
   1.36  bias -0.38  n=152  icon_global
   1.53  bias -0.10  n=151  gfs_global
   1.54  bias -0.66  n=151  ecmwf_ifs
   1.64  bias +0.00  n=147  ukmo_global_deterministic_10km
   1.83  bias -0.85  n=129  gem_global
   1.98  bias +0.05  n=148  jma_seamless

## Chongqing — BEST: icon_global (MAE 1.51°C, bias -0.63, n=113)
   1.51  bias -0.63  n=113  icon_global
   1.56  bias -0.29  n=112  ecmwf_ifs
   1.63  bias +0.36  n=105  ukmo_global_deterministic_10km
   1.80  bias -0.61  n= 45  icon_seamless
   1.85  bias -0.83  n=109  jma_seamless
   2.03  bias -1.56  n=112  gfs_global
   2.66  bias -2.41  n= 88  gem_global

## Dallas — BEST: ncep_nbm_conus (MAE 1.29°C, bias -0.13, n=174)
   1.29  bias -0.13  n=174  ncep_nbm_conus
   1.31  bias +0.33  n=179  icon_global
   1.58  bias -0.17  n=178  ecmwf_ifs
   1.75  bias +1.15  n= 41  icon_seamless
   1.76  bias -0.00  n=156  gem_global
   1.80  bias +0.49  n=178  gfs_global
   1.99  bias +0.47  n=174  ukmo_global_deterministic_10km
   2.42  bias -1.83  n=175  jma_seamless

## Denver — BEST: gfs_global (MAE 1.35°C, bias -0.17, n=110)
   1.35  bias -0.17  n=110  gfs_global
   1.35  bias -0.51  n=106  ncep_nbm_conus
   1.41  bias -0.20  n=110  ecmwf_ifs
   1.42  bias +0.30  n=106  ukmo_global_deterministic_10km
   1.46  bias -0.67  n=111  icon_global
   1.60  bias -0.67  n= 88  gem_global
   1.96  bias -1.73  n= 41  icon_seamless
   2.61  bias -1.98  n=107  jma_seamless

## Guangzhou — BEST: gfs_global (MAE 1.48°C, bias -0.74, n=92)
   1.48  bias -0.74  n= 92  gfs_global
   1.52  bias -1.12  n= 45  icon_seamless
   1.66  bias -0.59  n= 92  ecmwf_ifs
   1.66  bias -1.27  n= 93  icon_global
   1.71  bias -1.22  n= 85  ukmo_global_deterministic_10km
   1.81  bias -0.71  n= 68  gem_global
   1.81  bias -1.39  n= 89  jma_seamless

## Helsinki — BEST: icon_global (MAE 0.9°C, bias -0.18, n=97)
   0.90  bias -0.18  n= 97  icon_global
   0.95  bias +0.17  n= 96  ecmwf_ifs
   0.97  bias -0.14  n= 95  icon_eu
   1.00  bias +0.34  n= 35  icon_seamless
   1.17  bias -0.36  n= 93  ukmo_global_deterministic_10km
   1.30  bias -0.50  n= 93  jma_seamless
   1.49  bias -0.25  n= 96  gfs_global
   1.53  bias -1.22  n= 77  gem_global

## Hong Kong — BEST: ukmo_global_deterministic_10km (MAE 0.96°C, bias -0.46, n=136)
   0.96  bias -0.46  n=136  ukmo_global_deterministic_10km
   1.09  bias -0.22  n=126  jma_seamless
   1.09  bias -0.18  n= 51  icon_seamless
   1.25  bias -0.73  n=135  icon_global
   1.29  bias -0.96  n=101  gem_global
   1.30  bias -1.04  n=133  gfs_global
   1.31  bias -1.16  n=133  ecmwf_ifs

## Houston — BEST: icon_seamless (MAE 0.64°C, bias 0.06, n=41)
   0.64  bias +0.06  n= 41  icon_seamless
   0.80  bias -0.05  n=105  ncep_nbm_conus
   0.85  bias +0.05  n=109  icon_global
   1.09  bias +0.74  n=108  gfs_global
   1.19  bias +0.46  n=105  ukmo_global_deterministic_10km
   1.21  bias -0.82  n=108  ecmwf_ifs
   1.58  bias +0.46  n= 86  gem_global
   2.02  bias -1.72  n=105  jma_seamless

## Istanbul — BEST: icon_seamless (MAE 0.43°C, bias -0.07, n=45)
   0.43  bias -0.07  n= 45  icon_seamless
   0.76  bias -0.22  n= 98  icon_eu
   0.78  bias -0.35  n=100  icon_global
   0.92  bias -0.29  n= 99  ecmwf_ifs
   1.09  bias -0.30  n= 73  gem_global
   1.14  bias -0.13  n= 99  gfs_global
   3.11  bias -3.08  n= 94  jma_seamless
   4.19  bias -4.04  n= 96  ukmo_global_deterministic_10km

## Jakarta — BEST: gem_global (MAE 0.97°C, bias -0.45, n=46)
   0.97  bias -0.45  n= 46  gem_global
   1.12  bias -0.73  n= 46  gfs_global
   1.22  bias -0.83  n= 46  icon_global
   1.27  bias -0.73  n= 46  ukmo_global_deterministic_10km
   2.05  bias -1.93  n= 46  jma_seamless
   2.37  bias -2.30  n= 46  ecmwf_ifs

## Jeddah — BEST: gfs_global (MAE 1.45°C, bias -0.9, n=96)
   1.45  bias -0.90  n= 96  gfs_global
   1.55  bias +0.77  n= 41  icon_seamless
   1.90  bias +0.87  n= 96  ecmwf_ifs
   2.16  bias -2.07  n= 74  gem_global
   3.22  bias +3.18  n= 93  ukmo_global_deterministic_10km
   3.54  bias -2.30  n= 97  icon_global
   6.93  bias -6.93  n= 93  jma_seamless

## Karachi — BEST: icon_seamless (MAE 0.64°C, bias 0.26, n=45)
   0.64  bias +0.26  n= 45  icon_seamless
   0.81  bias -0.36  n= 69  gem_global
   1.09  bias -0.55  n= 94  icon_global
   1.18  bias +0.66  n= 93  ecmwf_ifs
   1.37  bias -0.95  n= 93  gfs_global
   1.60  bias -1.49  n= 90  jma_seamless
   1.65  bias +1.55  n= 86  ukmo_global_deterministic_10km

## Kuala Lumpur — BEST: icon_seamless (MAE 0.81°C, bias -0.69, n=45)
   0.81  bias -0.69  n= 45  icon_seamless
   1.04  bias -0.69  n=103  icon_global
   1.15  bias -0.52  n= 95  ukmo_global_deterministic_10km
   1.68  bias -1.59  n=102  ecmwf_ifs
   2.03  bias -1.96  n= 78  gem_global
   2.25  bias -2.19  n=102  gfs_global
   4.07  bias -4.07  n= 99  jma_seamless

## Lagos — BEST: ukmo_global_deterministic_10km (MAE 1.52°C, bias 0.0, n=35)
   1.52  bias +0.00  n= 35  ukmo_global_deterministic_10km
   1.53  bias +0.08  n= 35  icon_global
   1.55  bias +1.16  n= 35  gfs_global
   1.81  bias -0.67  n= 35  gem_global
   1.82  bias -0.69  n= 35  ecmwf_ifs
   3.92  bias -3.71  n= 35  jma_seamless

## London — BEST: icon_d2 (MAE 0.75°C, bias 0.02, n=292)
   0.75  bias +0.02  n=292  icon_d2
   0.76  bias -0.10  n=212  meteofrance_arome_france_hd
   0.76  bias -0.03  n= 82  icon_seamless
   0.82  bias +0.04  n=278  ukmo_uk_deterministic_2km
   0.83  bias -0.29  n=290  icon_global
   0.83  bias -0.32  n=290  icon_eu
   0.88  bias -0.31  n=278  ukmo_global_deterministic_10km
   0.95  bias -0.49  n=288  ecmwf_ifs
   1.06  bias -0.62  n=288  gfs_global
   1.16  bias -0.44  n=280  jma_seamless
   1.17  bias -0.58  n=244  gem_global

## Los Angeles — BEST: ncep_nbm_conus (MAE 0.79°C, bias -0.29, n=107)
   0.79  bias -0.29  n=107  ncep_nbm_conus
   0.97  bias -0.35  n=107  ukmo_global_deterministic_10km
   1.12  bias -0.17  n=108  jma_seamless
   1.15  bias +0.21  n= 89  gem_global
   1.19  bias +0.60  n=111  gfs_global
   1.76  bias +0.88  n=112  icon_global
   2.09  bias +1.15  n=111  ecmwf_ifs
   2.71  bias +2.71  n= 41  icon_seamless

## Lucknow — BEST: ecmwf_ifs (MAE 1.11°C, bias 0.18, n=132)
   1.11  bias +0.18  n=132  ecmwf_ifs
   1.37  bias +0.66  n=133  icon_global
   1.60  bias +1.05  n=108  gem_global
   1.60  bias +1.01  n= 45  icon_seamless
   2.22  bias +1.88  n=125  ukmo_global_deterministic_10km
   2.69  bias +2.45  n=129  jma_seamless
   5.34  bias +5.34  n=132  gfs_global

## Madrid — BEST: icon_eu (MAE 0.75°C, bias -0.32, n=115)
   0.75  bias -0.32  n=115  icon_eu
   0.79  bias -0.76  n= 41  icon_seamless
   0.82  bias +0.03  n=116  ecmwf_ifs
   0.87  bias -0.48  n=116  gfs_global
   0.89  bias -0.68  n=117  icon_global
   1.04  bias -0.85  n=113  ukmo_global_deterministic_10km
   1.16  bias +0.91  n= 78  meteofrance_arome_france_hd
   1.26  bias -0.97  n= 94  gem_global
   1.36  bias -1.21  n=113  jma_seamless

## Manila — BEST: icon_seamless (MAE 0.82°C, bias 0.73, n=45)
   0.82  bias +0.73  n= 45  icon_seamless
   1.02  bias -0.39  n= 92  gfs_global
   1.16  bias -0.86  n= 92  ecmwf_ifs
   1.22  bias +0.79  n= 85  ukmo_global_deterministic_10km
   1.66  bias -1.46  n= 89  jma_seamless
   1.68  bias -1.55  n= 68  gem_global
   1.93  bias -1.02  n= 93  icon_global

## Mexico City — BEST: ukmo_global_deterministic_10km (MAE 0.86°C, bias -0.36, n=100)
   0.86  bias -0.36  n=100  ukmo_global_deterministic_10km
   1.20  bias +0.86  n=103  gfs_global
   1.37  bias -0.78  n=103  ecmwf_ifs
   1.40  bias -0.19  n=104  icon_global
   1.40  bias +0.73  n= 41  icon_seamless
   1.88  bias -1.32  n=100  jma_seamless
   2.00  bias -1.35  n= 81  gem_global

## Miami — BEST: icon_seamless (MAE 0.69°C, bias -0.52, n=82)
   0.69  bias -0.52  n= 82  icon_seamless
   0.77  bias -0.39  n=214  ncep_nbm_conus
   0.86  bias -0.20  n=223  icon_global
   0.96  bias -0.27  n=214  ukmo_global_deterministic_10km
   0.98  bias +0.60  n=221  gfs_global
   1.11  bias -0.84  n=221  ecmwf_ifs
   1.12  bias -0.82  n=177  gem_global
   1.31  bias -0.72  n=215  jma_seamless

## Milan — BEST: icon_seamless (MAE 0.83°C, bias 0.59, n=41)
   0.83  bias +0.59  n= 41  icon_seamless
   0.85  bias +0.06  n=115  icon_d2
   0.97  bias -0.11  n=114  icon_eu
   1.08  bias -0.32  n=110  ukmo_global_deterministic_10km
   1.15  bias -0.35  n=114  icon_global
   1.15  bias +0.74  n=114  meteofrance_arome_france_hd
   1.47  bias -0.88  n=113  ecmwf_ifs
   1.61  bias -1.44  n=110  jma_seamless
   1.65  bias -0.44  n= 91  gem_global
   1.86  bias -1.81  n=113  gfs_global

## Moscow — BEST: gem_global (MAE 1.0°C, bias 0.05, n=69)
   1.00  bias +0.05  n= 69  gem_global
   1.28  bias -0.48  n= 95  ecmwf_ifs
   1.28  bias -0.62  n= 94  icon_eu
   1.30  bias -0.72  n= 96  icon_global
   1.43  bias -1.38  n= 45  icon_seamless
   1.46  bias -0.64  n= 92  ukmo_global_deterministic_10km
   1.92  bias -1.45  n= 95  gfs_global
   2.46  bias -1.38  n= 90  jma_seamless

## Munich — BEST: meteofrance_arome_france_hd (MAE 1.08°C, bias -0.32, n=92)
   1.08  bias -0.32  n= 92  meteofrance_arome_france_hd
   1.15  bias -0.72  n=132  icon_d2
   1.16  bias -0.87  n=131  icon_eu
   1.18  bias -0.94  n=131  icon_global
   1.28  bias -0.53  n=108  gem_global
   1.40  bias -1.07  n=130  ecmwf_ifs
   1.45  bias -1.07  n=127  ukmo_global_deterministic_10km
   1.77  bias -1.30  n= 41  icon_seamless
   1.92  bias -1.82  n=130  gfs_global
   2.10  bias -1.55  n=127  jma_seamless

## NYC — BEST: icon_global (MAE 1.26°C, bias -0.49, n=259)
   1.26  bias -0.49  n=259  icon_global
   1.35  bias -0.12  n=257  gfs_global
   1.37  bias -0.09  n=247  ukmo_global_deterministic_10km
   1.39  bias -0.66  n=247  ncep_nbm_conus
   1.42  bias -0.43  n= 82  icon_seamless
   1.55  bias -0.50  n=251  jma_seamless
   1.62  bias -0.25  n=257  ecmwf_ifs
   1.99  bias -1.18  n=213  gem_global

## Panama City — BEST: icon_seamless (MAE 0.9°C, bias 0.23, n=41)
   0.90  bias +0.23  n= 41  icon_seamless
   0.93  bias +0.15  n=100  icon_global
   1.24  bias +0.24  n= 99  gfs_global
   1.28  bias -0.38  n= 96  ukmo_global_deterministic_10km
   1.32  bias -1.27  n= 99  ecmwf_ifs
   1.35  bias -1.31  n= 77  gem_global
   2.33  bias -2.33  n= 96  jma_seamless

## Paris — BEST: icon_seamless (MAE 0.87°C, bias -0.35, n=74)
   0.87  bias -0.35  n= 74  icon_seamless
   0.90  bias +0.13  n=414  icon_d2
   0.96  bias -0.12  n=412  icon_eu
   0.96  bias -0.01  n=412  meteofrance_arome_france_hd
   1.03  bias -0.14  n=412  icon_global
   1.10  bias -0.60  n=410  ecmwf_ifs
   1.24  bias -0.43  n=370  gem_global
   1.33  bias +0.06  n=396  ukmo_global_deterministic_10km
   1.34  bias +0.62  n=410  gfs_global
   1.54  bias -0.14  n=406  jma_seamless

## Qingdao — BEST: gem_global (MAE 0.99°C, bias 0.23, n=56)
   0.99  bias +0.23  n= 56  gem_global
   1.14  bias +1.01  n= 80  gfs_global
   1.16  bias -0.59  n= 81  icon_global
   1.16  bias -0.88  n= 45  icon_seamless
   1.27  bias +1.23  n= 73  ukmo_global_deterministic_10km
   1.32  bias -0.78  n= 76  jma_seamless
   1.61  bias +1.31  n= 80  ecmwf_ifs

## San Francisco — BEST: ukmo_global_deterministic_10km (MAE 1.76°C, bias 0.97, n=105)
   1.76  bias +0.97  n=105  ukmo_global_deterministic_10km
   1.81  bias +0.60  n=108  ecmwf_ifs
   1.91  bias +0.05  n=105  ncep_nbm_conus
   2.20  bias +1.52  n= 41  icon_seamless
   2.65  bias -0.92  n=109  icon_global
   2.84  bias -2.66  n= 86  gem_global
   4.16  bias +4.12  n=108  gfs_global
   4.18  bias -4.18  n=105  jma_seamless

## Sao Paulo — BEST: icon_seamless (MAE 0.79°C, bias -0.3, n=41)
   0.79  bias -0.30  n= 41  icon_seamless
   0.96  bias -0.00  n=141  gfs_global
   0.97  bias -0.22  n=142  icon_global
   1.00  bias -0.25  n=141  ecmwf_ifs
   1.47  bias -1.02  n=138  ukmo_global_deterministic_10km
   1.49  bias +0.42  n=119  gem_global
   2.19  bias -2.08  n=138  jma_seamless

## Seattle — BEST: ukmo_global_deterministic_10km (MAE 0.96°C, bias 0.3, n=165)
   0.96  bias +0.30  n=165  ukmo_global_deterministic_10km
   1.11  bias -0.42  n=165  ncep_nbm_conus
   1.22  bias +0.33  n=168  gfs_global
   1.30  bias -0.49  n=168  ecmwf_ifs
   1.38  bias -0.97  n=169  icon_global
   1.39  bias -0.84  n=146  gem_global
   1.77  bias -1.13  n=165  jma_seamless
   2.04  bias -2.04  n= 41  icon_seamless

## Seoul — BEST: icon_seamless (MAE 0.55°C, bias 0.4, n=90)
   0.55  bias +0.40  n= 90  icon_seamless
   1.18  bias -0.75  n=232  gem_global
   1.32  bias -1.01  n=280  ecmwf_ifs
   1.39  bias -0.50  n=282  icon_global
   3.01  bias -1.99  n=266  ukmo_global_deterministic_10km
   3.07  bias -2.25  n=272  jma_seamless
   3.13  bias -2.32  n=280  gfs_global

## Shanghai — BEST: ukmo_global_deterministic_10km (MAE 0.98°C, bias -0.32, n=191)
   0.98  bias -0.32  n=191  ukmo_global_deterministic_10km
   1.20  bias -0.37  n=205  ecmwf_ifs
   1.31  bias -0.81  n=205  gfs_global
   1.38  bias -0.41  n= 90  icon_seamless
   1.52  bias -0.64  n=207  icon_global
   1.63  bias -1.15  n=157  gem_global
   2.52  bias -1.57  n=197  jma_seamless

## Shenzhen — BEST: ukmo_global_deterministic_10km (MAE 0.99°C, bias -0.26, n=90)
   0.99  bias -0.26  n= 90  ukmo_global_deterministic_10km
   1.29  bias -0.12  n= 73  gem_global
   1.41  bias -0.16  n= 97  ecmwf_ifs
   1.59  bias -0.84  n= 93  jma_seamless
   1.71  bias -0.99  n= 45  icon_seamless
   1.76  bias +0.46  n= 97  gfs_global
   1.80  bias -1.32  n= 98  icon_global

## Singapore — BEST: ukmo_global_deterministic_10km (MAE 0.93°C, bias -0.04, n=117)
   0.93  bias -0.04  n=117  ukmo_global_deterministic_10km
   0.96  bias +0.23  n= 45  icon_seamless
   1.00  bias -0.22  n=125  icon_global
   1.17  bias -0.90  n=124  gfs_global
   1.40  bias -1.26  n=124  ecmwf_ifs
   1.49  bias -1.16  n=100  gem_global
   2.54  bias -2.51  n=120  jma_seamless

## Taipei — BEST: icon_seamless (MAE 1.15°C, bias 0.24, n=45)
   1.15  bias +0.24  n= 45  icon_seamless
   1.41  bias -0.93  n= 98  ukmo_global_deterministic_10km
   1.58  bias -0.69  n=105  ecmwf_ifs
   1.76  bias -1.52  n=100  jma_seamless
   1.82  bias -1.19  n=105  gfs_global
   1.94  bias -1.15  n=106  icon_global
   2.25  bias -2.10  n= 81  gem_global

## Tel Aviv — BEST: icon_seamless (MAE 0.64°C, bias -0.32, n=45)
   0.64  bias -0.32  n= 45  icon_seamless
   0.77  bias -0.50  n=118  icon_eu
   0.80  bias +0.19  n=116  ukmo_global_deterministic_10km
   0.91  bias -0.18  n=119  gfs_global
   1.11  bias -1.03  n= 93  gem_global
   1.12  bias -0.37  n=119  ecmwf_ifs
   1.13  bias -0.80  n=113  jma_seamless
   1.29  bias -1.19  n=120  icon_global

## Tokyo — BEST: icon_seamless (MAE 0.74°C, bias -0.15, n=90)
   0.74  bias -0.15  n= 90  icon_seamless
   0.89  bias -0.12  n=211  icon_global
   0.90  bias +0.20  n=195  ukmo_global_deterministic_10km
   1.02  bias -0.24  n=209  gfs_global
   1.11  bias -0.87  n=199  jma_seamless
   1.41  bias -0.94  n=209  ecmwf_ifs
   1.63  bias -0.97  n=161  gem_global

## Toronto — BEST: icon_seamless (MAE 0.83°C, bias 0.04, n=41)
   0.83  bias +0.04  n= 41  icon_seamless
   1.19  bias -0.14  n=207  ukmo_global_deterministic_10km
   1.21  bias -0.34  n=207  ncep_nbm_conus
   1.45  bias -0.81  n=211  icon_global
   1.46  bias -0.41  n=210  ecmwf_ifs
   1.56  bias -0.79  n=188  gem_global
   1.62  bias -0.89  n=210  gfs_global
   2.65  bias -2.17  n=206  jma_seamless

## Warsaw — BEST: ecmwf_ifs (MAE 0.92°C, bias -0.41, n=116)
   0.92  bias -0.41  n=116  ecmwf_ifs
   0.94  bias -0.42  n=117  icon_global
   0.94  bias -0.37  n=115  icon_eu
   1.10  bias -0.56  n= 41  icon_seamless
   1.12  bias -0.49  n=116  gfs_global
   1.20  bias -0.54  n=113  ukmo_global_deterministic_10km
   1.22  bias -0.61  n= 94  gem_global
   1.79  bias -1.51  n=112  jma_seamless

## Wellington — BEST: icon_seamless (MAE 0.61°C, bias -0.59, n=49)
   0.61  bias -0.59  n= 49  icon_seamless
   0.89  bias -0.44  n=171  ukmo_global_deterministic_10km
   0.99  bias -0.43  n=150  gem_global
   1.04  bias -0.83  n=179  icon_global
   1.13  bias -1.04  n=178  ecmwf_ifs
   1.29  bias -1.14  n=178  gfs_global
   1.54  bias -1.44  n=172  jma_seamless

## Wuhan — BEST: gfs_global (MAE 1.31°C, bias 0.13, n=113)
   1.31  bias +0.13  n=113  gfs_global
   1.42  bias +0.38  n=106  ukmo_global_deterministic_10km
   1.45  bias -0.90  n=114  icon_global
   1.54  bias -0.68  n= 89  gem_global
   1.58  bias -0.08  n=113  ecmwf_ifs
   1.88  bias -1.26  n= 45  icon_seamless
   2.12  bias +1.30  n=109  jma_seamless
