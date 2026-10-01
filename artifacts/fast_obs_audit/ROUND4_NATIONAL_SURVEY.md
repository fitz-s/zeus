# Round 4 — every configured city, free public sources only

Every city has a reviewed access disposition and measured reference/alternative evidence. This is not a claim of exact native pairs or finite publication bounds where access/clock grid or absence of a transition prevented them.

Pairs are exact matches / distinct exact-time overlaps. Full mismatch values and interval bounds are in the JSON. National origin and redistribution are different roles.

|City / station|Free native candidate / access|Native exact / paired|Other public channel exact / paired|Decision|
|---|---|---|---|---|
|Amsterdam / EHAM|KNMI 06240 NetCDF and public EHAM METAR — FREE_ANONYMOUS_PAYLOAD|knmi_observations 0/0; knmi_public_metar 3/3|mgm_metar 50/50|NO_ALTERNATIVE_SPEED_PROMOTION|
|Ankara / LTAC|MGM Hezarfen native METAR — FREE_ANONYMOUS_PAYLOAD|mgm_metar 50/50|none acquired|PROMOTED_NATIVE_FAST|
|Atlanta / KATL|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Auckland / NZAA|New Zealand free airport sources — COMMERCIAL_ORIGIN_EXCLUDED|0/0 native (access disposition below)|mgm_metar 48/48|NO_ALTERNATIVE_SPEED_PROMOTION|
|Austin / KAUS|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Beijing / ZBAA|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 49/49|NO_ALTERNATIVE_SPEED_PROMOTION|
|Buenos Aires / SAEZ|Argentina SMN SAEZ — PUBLIC_ACCESS_FAILED|0/0 native (access disposition below)|mgm_metar 26/26|NO_ALTERNATIVE_SPEED_PROMOTION|
|Busan / RKPK|KMA AMO native METAR — FREE_ANONYMOUS_PAYLOAD|kma_amo_raw_metar 28/28|mgm_metar 27/27|RETAIN_ADMITTED_NATIVE|
|Cape Town / FACT|SAWS FACT aviation — PUBLIC_ACCESS_FAILED|0/0 native (access disposition below)|mgm_metar 25/25|NO_ALTERNATIVE_SPEED_PROMOTION|
|Chengdu / ZUUU|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 23/23|NO_ALTERNATIVE_SPEED_PROMOTION|
|Chicago / KORD|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 9/11|none acquired|RETAIN_NATIVE_RESOLVER|
|Chongqing / ZUCK|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 12/12|NO_ALTERNATIVE_SPEED_PROMOTION|
|Dallas / KDAL|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 8/9|none acquired|RETAIN_NATIVE_RESOLVER|
|Denver / KBKF|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 8/8|none acquired|RETAIN_NATIVE_RESOLVER|
|Guangzhou / ZGGG|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 48/48|NO_ALTERNATIVE_SPEED_PROMOTION|
|Helsinki / EFHK|FMI WFS 100968 — FREE_ANONYMOUS_PAYLOAD|fmi_wfs 40/48|mgm_metar 50/50|PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH|
|Hong Kong / HKO|HKO headquarters native temperature CSV — FREE_ANONYMOUS_PAYLOAD|hko_native_csv 0/0; hko_rhr_json 0/1|none acquired|RETAIN_NATIVE_RESOLVER|
|Houston / KHOU|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Istanbul / LTFM|MGM Hezarfen native METAR — FREE_ANONYMOUS_PAYLOAD|mgm_metar 49/49|none acquired|PROMOTED_NATIVE_FAST|
|Jinan / ZSJN|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Jakarta / WIHH|BMKG WIHH aviation — PUBLIC_ACCESS_FAILED|0/0 native (access disposition below)|mgm_metar 44/44|NO_ALTERNATIVE_SPEED_PROMOTION|
|Jeddah / OEJN|NCM OEJN aviation — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 25/25|NO_ALTERNATIVE_SPEED_PROMOTION|
|Karachi / OPKC|PMD OPKC aviation — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 50/50|NO_ALTERNATIVE_SPEED_PROMOTION|
|Kuala Lumpur / WMKK|MET Malaysia WMKK — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 39/39|NO_ALTERNATIVE_SPEED_PROMOTION|
|Lagos / DNMM|NiMet DNMM aviation — PUBLIC_ACCESS_FAILED|0/0 native (access disposition below)|mgm_metar 22/22|NO_ALTERNATIVE_SPEED_PROMOTION|
|London / EGLC|Met Office DataHub EGLC — REQUIRES_REGISTRATION|0/0 native (access disposition below)|mgm_metar 50/50|NO_ALTERNATIVE_SPEED_PROMOTION|
|Los Angeles / KLAX|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Lucknow / VILK|IMD OLBS VILK — FREE_ANONYMOUS_PAYLOAD|imd_olbs_metar 2/2|mgm_metar 47/47|PROMOTED_NATIVE_FAST|
|Madrid / LEMD|AEMET 3129 public station XML — FREE_ANONYMOUS_PAYLOAD|aemet_station_xml 22/25|mgm_metar 49/49|PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH|
|Manila / RPLL|PAGASA RPLL METAR — FREE_ANONYMOUS_PAYLOAD|pagasa_metar 38/38|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Mexico City / MMMX|SMN/SENEAM MMMX — PUBLIC_ACCESS_FAILED|0/0 native (access disposition below)|meteoam_metar 23/23|NO_ALTERNATIVE_SPEED_PROMOTION|
|Miami / KMIA|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Milan / LIMC|Aeronautica Militare LIMC METAR — FREE_ANONYMOUS_PAYLOAD|meteoam_metar 48/48|mgm_metar 50/50|NO_ALTERNATIVE_SPEED_PROMOTION|
|Moscow / UUWW|Aviamettelecom UUWW raw METAR — FREE_ANONYMOUS_PAYLOAD|metaviatelecom_display 4/4|mgm_metar 49/49|PROMOTED_NATIVE_FAST|
|Munich / EDDM|DWD CDC 01262 — FREE_ANONYMOUS_PAYLOAD|dwd_cdc 34/45|mgm_metar 49/49|PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH|
|NYC / KLGA|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Panama City / MPMG|Panama AAC MPMG METAR — FREE_ANONYMOUS_PAYLOAD|aac_metar 2/2|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Paris / LFPB|Meteo-France LFPB observation API — REQUIRES_REGISTRATION|0/0 native (access disposition below)|mgm_metar 39/39|NO_ALTERNATIVE_SPEED_PROMOTION|
|Qingdao / ZSQD|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|San Francisco / KSFO|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 7/7|none acquired|RETAIN_NATIVE_RESOLVER|
|Sao Paulo / SBGR|REDEMET SBGR — REQUIRES_REGISTRATION|0/0 native (access disposition below)|meteoam_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Seattle / KSEA|NOAA resolver-native Fahrenheit product — RETAIN_EXISTING_NATIVE_PRODUCT|awc 8/8|none acquired|RETAIN_NATIVE_RESOLVER|
|Seoul / RKSI|KMA AMO native METAR — FREE_ANONYMOUS_PAYLOAD|kma_amo_raw_metar 51/51|mgm_metar 49/49|RETAIN_ADMITTED_NATIVE|
|Shanghai / ZSPD|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 46/46|NO_ALTERNATIVE_SPEED_PROMOTION|
|Shenzhen / ZGSZ|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Singapore / WSSS|NEA/MSS Changi S24 — PREVIOUS_MEASURED_MISMATCH_RETAINED|0/0 native (access disposition below)|mgm_metar 38/38|PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH|
|Taipei / RCSS|ANWS RCSS — CONNECTOR_TRANSPORT_UNAVAILABLE|0/0 native (access disposition below)|mgm_metar 41/41|NO_ALTERNATIVE_SPEED_PROMOTION|
|Tel Aviv / LLBG|IMS LLBG candidate — NO_IDENTIFIED_LLBG_ROW|0/0 native (access disposition below)|mgm_metar 50/50|NO_ALTERNATIVE_SPEED_PROMOTION|
|Tokyo / RJTT|JMA Haneda 44166 — FREE_ANONYMOUS_PAYLOAD|jma_amedas 6/6|mgm_metar 24/24|RETAIN_ADMITTED_NATIVE|
|Toronto / CYYZ|ECCC CYYZ-MAN SWOB — FREE_ANONYMOUS_PAYLOAD|eccc_swob 4/4|mgm_metar 28/28|RETAIN_ADMITTED_NATIVE|
|Warsaw / EPWA|IMGW 12375 SYNOP — FREE_ANONYMOUS_PAYLOAD|imgw_synop 2/2|mgm_metar 50/50|PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH|
|Wellington / NZWN|New Zealand free airport sources — COMMERCIAL_ORIGIN_EXCLUDED|0/0 native (access disposition below)|mgm_metar 45/45|NO_ALTERNATIVE_SPEED_PROMOTION|
|Wuhan / ZHHH|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|
|Zhengzhou / ZHCC|CMA/civil-aviation exact airport METAR — NO_EXACT_ANONYMOUS_STATION_PAYLOAD|0/0 native (access disposition below)|mgm_metar 24/24|NO_ALTERNATIVE_SPEED_PROMOTION|

## Measured per-city first-availability comparisons

Intervals are seconds after the stated UTC observation instant. Unknown is not zero. Negative lower bounds are uncertainty bounds, not negative physical latency.

**Amsterdam (EHAM)** — knmi_public_metar: 2026-09-30T23:55:00+00:00: candidate [526.307,592.233]s; awc [168.977,230.178]s (SLOWER); resolver [230.255,289.084]s (SLOWER); mgm_metar: 2026-09-30T22:55:00+00:00: candidate [226.158,292.730]s; awc [162.218,224.357]s (SLOWER); resolver [222.227,287.909]s (OVERLAP)
**Ankara (LTAC)** — mgm_metar: 2026-09-30T23:50:00+00:00: candidate [226.232,288.448]s; awc [351.870,410.770]s (FASTER); resolver [352.025,411.478]s (FASTER)
**Atlanta (KATL)** — awc: 2026-09-30T23:52:00+00:00: candidate [111.528,172.413]s; resolver [232.715,292.070]s (FASTER)
**Auckland (NZAA)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [508.910,568.296]s; awc [226.209,291.210]s (SLOWER)
**Austin (KAUS)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Beijing (ZBAA)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [508.910,568.296]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Buenos Aires (SAEZ)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [809.804,870.073]s; awc [226.209,291.210]s (SLOWER); resolver [270.900,330.658]s (SLOWER)
**Busan (RKPK)** — kma_amo_raw_metar: 2026-09-30T23:00:00+00:00: candidate [43.899,104.341]s; awc [226.209,291.210]s (FASTER); resolver [270.900,330.658]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [508.910,568.296]s; awc [226.209,291.210]s (SLOWER); resolver [270.900,330.658]s (SLOWER); kma_amo_raw_metar [43.899,104.341]s (SLOWER)
**Cape Town (FACT)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [508.910,568.296]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Chengdu (ZUUU)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [508.910,568.296]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Chicago (KORD)** — awc: 2026-09-30T23:51:00+00:00: candidate [113.014,171.955]s; resolver [292.715,352.070]s (FASTER)
**Chongqing (ZUCK)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [509.869,569.241]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Dallas (KDAL)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Denver (KBKF)** — awc: 2026-09-30T22:58:00+00:00: candidate [1002.906,1062.750]s; resolver [1122.597,1184.174]s (FASTER)
**Guangzhou (ZGGG)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [509.869,569.241]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Helsinki (EFHK)** — mgm_metar: 2026-09-30T23:20:00+00:00: candidate [210.551,272.197]s; awc [58.438,123.370]s (SLOWER); resolver [181.397,241.652]s (OVERLAP)
**Hong Kong (HKO)** — No bracketed paired alternative transition; publication lag unknown
**Houston (KHOU)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Istanbul (LTFM)** — mgm_metar: 2026-09-30T23:50:00+00:00: candidate [226.232,288.448]s; awc [351.870,410.770]s (FASTER); resolver [352.025,411.478]s (FASTER)
**Jinan (ZSJN)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [207.916,267.601]s; resolver [3469.909,3768.772]s (FASTER)
**Jakarta (WIHH)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [509.869,569.241]s; awc [329.664,389.330]s (SLOWER)
**Jeddah (OEJN)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [208.790,268.843]s; awc [-13.830,52.437]s (SLOWER); resolver [102.977,164.084]s (SLOWER)
**Karachi (OPKC)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [208.790,268.843]s; awc [52.025,112.230]s (SLOWER); resolver [102.977,164.084]s (SLOWER)
**Kuala Lumpur (WMKK)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [805.462,871.451]s; awc [466.251,530.746]s (SLOWER); resolver [643.773,703.694]s (SLOWER)
**Lagos (DNMM)** — No bracketed paired alternative transition; publication lag unknown
**London (EGLC)** — mgm_metar: 2026-09-30T23:50:00+00:00: candidate [470.065,532.999]s; awc [231.528,292.413]s (SLOWER); resolver [352.025,411.478]s (SLOWER)
**Los Angeles (KLAX)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Lucknow (VILK)** — imd_olbs_metar: 2026-09-30T23:30:00+00:00: candidate [-29.442,32.557]s; awc [87.758,154.390]s (FASTER); resolver [272.548,330.560]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [510.639,569.819]s; awc [102.826,163.284]s (SLOWER); resolver [270.900,330.658]s (SLOWER)
**Madrid (LEMD)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [510.639,569.819]s; awc [226.209,291.210]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Manila (RPLL)** — pagasa_metar: 2026-09-30T23:00:00+00:00: candidate [343.109,405.049]s; awc [102.826,163.284]s (SLOWER); resolver [270.900,330.658]s (SLOWER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [510.639,569.819]s; awc [102.826,163.284]s (SLOWER); resolver [270.900,330.658]s (SLOWER)
**Mexico City (MMMX)** — No bracketed paired alternative transition; publication lag unknown
**Miami (KMIA)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Milan (LIMC)** — mgm_metar: 2026-09-30T23:50:00+00:00: candidate [471.294,533.719]s; awc [351.870,410.770]s (SLOWER); resolver [352.025,411.478]s (SLOWER)
**Moscow (UUWW)** — metaviatelecom_display: 2026-09-30T23:00:00+00:00: candidate [47.070,107.153]s; awc [226.209,291.210]s (FASTER); resolver [270.900,330.658]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [511.360,570.507]s; awc [226.209,291.210]s (SLOWER); resolver [270.900,330.658]s (SLOWER); metaviatelecom_display [47.070,107.153]s (SLOWER)
**Munich (EDDM)** — mgm_metar: 2026-09-30T23:20:00+00:00: candidate [213.808,275.118]s; awc [181.208,240.859]s (OVERLAP); resolver [327.717,395.003]s (FASTER)
**NYC (KLGA)** — awc: 2026-09-30T23:51:00+00:00: candidate [113.014,171.955]s; resolver [292.715,352.070]s (FASTER)
**Panama City (MPMG)** — aac_metar: 2026-09-30T23:00:00+00:00: candidate [-76.980,52.416]s; awc [-13.830,52.437]s (OVERLAP); resolver [-14.592,44.012]s (OVERLAP); mgm_metar: 2026-10-01T00:00:00+00:00: candidate [-8.391,52.851]s; awc [347.076,406.653]s (FASTER); resolver [-11.780,49.027]s (OVERLAP)
**Paris (LFPB)** — mgm_metar: 2026-10-01T00:00:00+00:00: candidate [229.974,291.024]s; awc [107.507,168.130]s (SLOWER); resolver [347.081,407.822]s (FASTER)
**Qingdao (ZSQD)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [511.360,570.507]s; awc [329.664,389.330]s (SLOWER); resolver [522.298,582.920]s (OVERLAP)
**San Francisco (KSFO)** — awc: 2026-09-30T23:56:00+00:00: candidate [108.977,170.178]s; resolver [170.255,229.875]s (FASTER)
**Sao Paulo (SBGR)** — meteoam_metar: 2026-09-30T23:00:00+00:00: candidate [962.097,1019.509]s; awc [-13.830,52.437]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Seattle (KSEA)** — awc: 2026-09-30T22:53:00+00:00: candidate [162.211,222.687]s; resolver [162.321,223.555]s (OVERLAP)
**Seoul (RKSI)** — kma_amo_raw_metar: 2026-09-30T23:00:00+00:00: candidate [-15.284,43.896]s; awc [226.209,291.210]s (FASTER); resolver [270.900,330.658]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [512.050,571.207]s; awc [226.209,291.210]s (SLOWER); resolver [270.900,330.658]s (SLOWER); kma_amo_raw_metar [-15.284,43.896]s (SLOWER)
**Shanghai (ZSPD)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [512.050,571.207]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Shenzhen (ZGSZ)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [512.050,571.207]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER)
**Singapore (WSSS)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [210.160,270.203]s; awc [-13.830,52.437]s (SLOWER); resolver [102.977,164.084]s (SLOWER)
**Taipei (RCSS)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [1110.193,1170.570]s; awc [530.402,589.696]s (SLOWER); resolver [3469.955,3768.798]s (FASTER)
**Tel Aviv (LLBG)** — mgm_metar: 2026-09-30T23:20:00+00:00: candidate [205.521,276.615]s; awc [58.438,123.370]s (SLOWER); resolver [181.397,241.652]s (OVERLAP)
**Tokyo (RJTT)** — jma_amedas: 2026-09-30T23:00:00+00:00: candidate [343.755,404.481]s; awc [329.664,389.330]s (OVERLAP); resolver [448.584,508.834]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [512.050,571.207]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (SLOWER); jma_amedas [343.755,404.481]s (SLOWER)
**Toronto (CYYZ)** — eccc_swob: 2026-09-30T23:00:00+00:00: candidate [102.395,162.451]s; awc [402.289,462.761]s (FASTER); resolver [448.584,508.834]s (FASTER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [210.160,270.203]s; awc [402.289,462.761]s (FASTER); resolver [448.584,508.834]s (FASTER); eccc_swob [102.395,162.451]s (SLOWER)
**Warsaw (EPWA)** — imgw_synop: 2026-09-30T23:00:00+00:00: candidate [882.352,944.163]s; awc [102.826,163.284]s (SLOWER); resolver [270.900,330.658]s (SLOWER); mgm_metar: 2026-09-30T23:00:00+00:00: candidate [210.160,270.203]s; awc [102.826,163.284]s (SLOWER); resolver [270.900,330.658]s (FASTER)
**Wellington (NZWN)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [505.429,571.643]s; awc [226.209,291.210]s (SLOWER); resolver [270.900,330.658]s (SLOWER)
**Wuhan (ZHHH)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [505.429,571.643]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (OVERLAP)
**Zhengzhou (ZHCC)** — mgm_metar: 2026-09-30T23:00:00+00:00: candidate [505.429,571.643]s; awc [329.664,389.330]s (SLOWER); resolver [448.584,508.834]s (OVERLAP)

## Access and source references

**Amsterdam (EHAM)** — Published anonymous API key obtained without registration; one NetCDF decoded, then 429 and stopped. Ten-minute clock has no exact overlap. Separately, the free public METAR page yields an exact EHAM report without any key. Source: https://www.knmi.nl/nederland-nu/luchtvaart/vliegveldwaarnemingen
**Ankara (LTAC)** — Revising the unresolved frontend: public Next data contains exact station/time reports. Actual response maximum is ten stations, enforced in the tested universal adapter. Source: https://rasat.mgm.gov.tr/result
**Atlanta (KATL)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Auckland (NZAA)** — MetService Classic commercial API is excluded, not deferred for a paid key. No independently verified free native endpoint was obtained; public MGM NZAA/NZWN redistribution was measured. Source: https://rasat.mgm.gov.tr/result
**Austin (KAUS)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Beijing (ZBAA)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Buenos Aires (SAEZ)** — The public route returned a browser challenge; an older service URL failed transport. No bypass. MGM SAEZ exact reports were compared. Source: https://www.smn.gob.ar/metar
**Busan (RKPK)** — Existing shared KMA runtime cursor retained; no duplicate source polling implementation. Source: https://global.amo.go.kr/observation/PkObsMetarList.do
**Cape Town (FACT)** — Public root returned access denial and the older mobile endpoint failed connection. No credentials or paid access used. Source: https://aviation.weathersa.co.za/
**Chengdu (ZUUU)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Chicago (KORD)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Chongqing (ZUCK)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Dallas (KDAL)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Denver (KBKF)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Guangzhou (ZGGG)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Helsinki (EFHK)** — Repeated same-clock mismatches: physical-current only. No instrument certificate is demanded. Source: https://opendata.fmi.fi/wfs
**Hong Kong (HKO)** — The native station CSV is retained. Public RHR JSON and native CSV disagree under the contract truncation law. This compares spot products, not a final daily-max/min settlement value. Source: https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv
**Houston (KHOU)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Istanbul (LTFM)** — Revising the unresolved frontend: public Next data contains exact station/time reports. Actual response maximum is ten stations, enforced in the tested universal adapter. Source: https://rasat.mgm.gov.tr/result
**Jinan (ZSJN)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Jakarta (WIHH)** — Public page returned a browser challenge; no bypass. No substitution of WIII for WIHH. Source: https://web-aviation.bmkg.go.id/web/metar_speci.php
**Jeddah (OEJN)** — Public service/API documentation did not provide an anonymous exact OEJN response. Contract/commercial routes were excluded. Source: https://api-doc.ncm.gov.sa/
**Karachi (OPKC)** — The public aviation service description is not a station observation response. Exact reports were available via MGM, without pretending it is the Pakistani origin. Source: https://rmcsindh.pmd.gov.pk/Services_Aviation.html
**Kuala Lumpur (WMKK)** — The reachable public product documentation describes forecasts/warnings; those are not airport observations. Aviation endpoint errors are recorded, not called a national outage. Source: https://www.met.gov.my/en/info/data-terbuka/
**Lagos (DNMM)** — Public page returned redirect/JavaScript challenge. No login/challenge bypass. Exact DNMM reports were available through MGM. Source: https://nimet.gov.ng/
**London (EGLC)** — A genuinely free tier is documented, but an account/key is required; no account was created. MGM is a separate tested public redistribution channel. Source: https://datahub.metoffice.gov.uk/pricing/observations
**Los Angeles (KLAX)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Lucknow (VILK)** — Revising the empty-form finding: public POST fields icaos and type return the report. No registration or credential is needed. Source: https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php
**Madrid (LEMD)** — Revising the old 404/key-only finding: the public website XML works without registration. Temperature mismatches prevent admission. Source: https://www.aemet.es/es/api-eltiempo/udat/tablas-graficas/horario/9/3129
**Manila (RPLL)** — The free origin matched exact values; the measured speed evidence does not justify replacing the current path. Source: https://www.pagasa.dost.gov.ph/aviation/metar
**Mexico City (MMMX)** — Government pages returned challenge content; candidate service hosts failed connection. No challenge/login bypass. The Italian public global METAR API was independently queried for MMMX. Source: https://www.gob.mx/seneam/acciones-y-programas/mas-servicios-de-control
**Miami (KMIA)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Milan (LIMC)** — Revising the old missing path: ICAO/start-UTC/end-UTC returns public JSON without a key. Complete dated path is retained in the transport evidence. Source: https://api.meteoam.it/deda-ows/metar-taf-icao/
**Moscow (UUWW)** — Revising the unextracted frontend: its public modal contains the exact raw METAR. Native lead measured. HTTPS failed; active route uses fixed-host plaintext HTTP and that security limitation is explicit. Source: http://display.meteocenter.ru/219
**Munich (EDDM)** — Repeated same-clock mismatches: physical-current only. Source: https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/now/
**NYC (KLGA)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Panama City (MPMG)** — Exact airport reports were extracted. Overlapping/absent speed intervals are not called a proven lead. Source: https://www.aeronautica.gob.pa/met/met.php?c=metar
**Paris (LFPB)** — The producer describes open access with account. No authenticated call; old anonymous SYNOP request did not yield current station data. Source: https://www.data.gouv.fr/dataservices/api-package-observations
**Qingdao (ZSQD)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**San Francisco (KSFO)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Sao Paulo (SBGR)** — Native request was refused without registration/key. The Italian national-service public global METAR API independently supplies exact SBGR reports, but is redistribution, not Brazilian origin. Source: https://api-redemet.decea.mil.br/mensagens/metar/SBGR
**Seattle (KSEA)** — The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round. Source: https://www.weather.gov/wrh/timeseries
**Seoul (RKSI)** — Existing shared KMA runtime cursor retained; no duplicate source polling implementation. Source: https://global.amo.go.kr/observation/PkObsMetarList.do
**Shanghai (ZSPD)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Shenzhen (ZGSZ)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Singapore (WSSS)** — Round-3 same-clock 27/49 proof with 22 mismatches is retained; no re-promotion from matching MGM redistributions. Source: https://api.data.gov.sg/v1/environment/air-temperature
**Taipei (RCSS)** — The public web renderer can display the form; connector HTTP attempts failed. No login attempted, no RCTP substitution. MGM RCSS comparison is independent redistribution. Source: https://aoaws.anws.gov.tw/Report
**Tel Aviv (LLBG)** — The free XML candidate was checked, but no exact LLBG reading was established. An unmatched surface station was not renamed to LLBG. MGM supplies exact airport raw reports. Source: https://ims.gov.il/en/CurrentDataXML
**Tokyo (RJTT)** — Existing native route retained only while the accumulated exact-time proof remains uncontradicted. Source: https://www.jma.go.jp/bosai/amedas/data/point/44166/
**Toronto (CYYZ)** — Existing native route retained. A new alternative must beat this route, not merely a slower AWC response. Source: https://dd.weather.gc.ca/today/observations/swob-ml/latest/CYYZ-MAN-swob.xml
**Warsaw (EPWA)** — Round-4 matches do not erase the Round-3 1/2 contradiction. Remains physical-only. Source: https://danepubliczne.imgw.pl/api/data/synop/id/12375
**Wellington (NZWN)** — MetService Classic commercial API is excluded, not deferred for a paid key. No independently verified free native endpoint was obtained; public MGM NZAA/NZWN redistribution was measured. Source: https://rasat.mgm.gov.tr/result
**Wuhan (ZHHH)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
**Zhengzhou (ZHCC)** — Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM. Source: https://aviation.nmc.cn/
