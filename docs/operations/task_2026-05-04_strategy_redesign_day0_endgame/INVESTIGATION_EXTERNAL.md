# External Investigation — NWP / UMA / Polymarket evidence for Zeus strategy redesign

**Date**: 2026-05-04
**Scope**: external authority only (NWP vendor docs, UMA on-chain / docs, Polymarket Gamma API + repos, public observation sources). Internal-code evidence in companion file `INVESTIGATION_INTERNAL.md`.
**Author**: document-specialist sub-agent (a76bdcbc78c56732d).

> Operator question: Zeus runs UTC-anchored crons (07/09/19/21 UTC for UPDATE_REACTION; every-15-min OPENING_HUNT and DAY0_CAPTURE) over 51 cities tiled across all timezones. With per-city ALPHA windows tiling globally, no genuine "global vacuum" exists — so why UTC-anchored crons? This file gathers the **external** facts needed to answer that.

---

## Q1: NWP model release schedules (the cron's likely anchor)

All times below are wall-clock UTC, copied verbatim from primary-source ECMWF / NOAA / DWD documentation.

### ECMWF HRES (deterministic IFS) — Set I-i atmospheric field delivery
| Cycle | Delivery window (UTC) | Notes |
|---|---|---|
| 00z | 05:45 → 06:12 → 06:27 → 07:34 | Three sub-windows; full HRES "ready" by ~07:34 |
| 06z | 11:45 → 12:12 → 12:27 | Full set by ~12:27 |
| 12z | 17:45 → 18:12 → 18:27 → 19:34 | Full set by ~19:34 |
| 18z | 23:45 → 00:12 → 00:27 | Full set by ~00:27 next day |

Source: ECMWF Dissemination schedule confluence page (https://confluence.ecmwf.int/display/DAC/Dissemination+schedule).

### ECMWF ENS (51-member ensemble, Set III)
| Cycle | First products (UTC) | Last derived products (UTC) |
|---|---|---|
| 00z | 06:40 (Day 0) | 08:01 |
| 06z | 12:40 | 12:52 (Day 3) |
| 12z | 18:40 (Day 0) | 20:01 |
| 18z | 00:40 next day | 00:52 (Day 3) |

Source: same confluence page above.

### ECMWF Open Data (AWS S3 / `ecmwf-opendata`)
- "Data is available between 7 and 9 hours after the forecast starting date and time" (https://github.com/ecmwf/ecmwf-opendata).
- IFS is "released at the end of the real-time dissemination schedule"; AIFS released earlier (https://www.ecmwf.int/en/forecasts/datasets/open-data).
- **Inferred S3 wall-clock**: 00z run → ~07-09 UTC; 06z → ~13-15 UTC; 12z → ~19-21 UTC; 18z → ~01-03 UTC.

### NOAA NCEP — GFS / GEFS (`nco.ncep.noaa.gov/pmb/nwprod/prodstat/`)
| Cycle | GFS production window (UTC) | GEFS production window (UTC) |
|---|---|---|
| 00z | 03:28 → 05:15 | 03:25 → 06:26 |
| 06z | 09:22 → 11:08 | 09:23 → 12:26 |
| 12z | 15:23 → 17:10 | 15:24 → 18:26 |
| 18z | 21:24 → 23:10 | 21:26 → 00:26 |

Source: https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/.

### DWD ICON
- "Runs appear roughly 160 minutes after their start hour" — i.e., 00z → ~02:40 UTC; 06z → ~08:40; 12z → ~14:40; 18z → ~20:40 (https://opendata.dwd.de/weather/nwp/icon/grib/).

### TIGGE
- 404s on direct ECMWF confluence pages prevented quoting their canonical real-time ingest latency. **EVIDENCE_NEEDED** for the exact TIGGE ingest window; community knowledge places it ~6-12h after each cycle (NCAR/RDA `ds330.3` notes), but I could not verify via primary source in this session.

### Mapping Zeus's 07/09/19/21 UTC cron to NWP releases
- **07 UTC** sits between ECMWF HRES 00z full delivery (~07:34) and ECMWF ENS 00z first products (06:40). Plausibly tracks ECMWF Open Data 00z arriving on S3 (~07-09 UTC band).
- **09 UTC** sits within GEFS 06z production (09:23 → 12:26) and after GFS 06z window starts (09:22). Plausibly tracks **GFS/GEFS 06z** availability or the tail of ECMWF 00z S3 upload.
- **19 UTC** mirrors **07 UTC** for the 12z cycle: ECMWF HRES 12z full by ~19:34; ENS 12z first products at 18:40. Same NWP-release logic.
- **21 UTC** mirrors **09 UTC** for the 12z/18z cycle: GFS 18z (21:24→23:10) and GEFS 18z (21:26→00:26) are starting. Likely tracks GFS/GEFS 18z or post-ECMWF 12z absorbs.

**Working hypothesis (confidence MEDIUM)**: Zeus's 07/09/19/21 UTC pattern is a **two-pair strategy on 00z and 12z global runs** — pair-1 at 07/09 hits ECMWF 00z + GFS 06z; pair-2 at 19/21 hits ECMWF 12z + GFS 18z. The 06z and 18z cycles get only weak NWP coverage from this schedule. Documented confirmation in Zeus's own design history is INVESTIGATION_INTERNAL.md territory.

---

## Q2: UMA Optimistic Oracle weather settlement window

### Default OO mechanics
- "Asserter posts a bonded assertion. During the assertion liveness period, disputers can refute. If unchallenged, the assertion is optimistically treated as correct" (https://docs.uma.xyz/protocol-overview/how-does-umas-oracle-work).
- Default assertion liveness on UMA OOv3 / Polymarket usage is "**7200 seconds (2 hours)**" (https://docs.uma.xyz/protocol-overview/how-does-umas-oracle-work, "Polymarket defaults utilize 7200 seconds").
- On dispute, UMA DVM vote takes "**48 - 72 hours**" before final resolution (https://github.com/Polymarket/uma-ctf-adapter).

### Polymarket-specific lifecycle (CTF adapter)
- Polymarket markets resolve via the `uma-ctf-adapter` repo (Polymarket on GitHub).
- "When a new market is deployed" the adapter knows the question; the resolution **request** to UMA is initiated when the market window closes / resolution is requested. With Polymarket's 2-hour liveness, **uncontested resolution settles ~2 hours after request**.
- **Polymarket weather event endDate is uniformly 12:00 UTC of target date** (verified 2026-05-04 via Gamma API on London/Tokyo/Singapore/Wellington/NYC/Sao Paulo/LA — see Q3 below). With 2h liveness, *uncontested* settlement happens around **14:00 UTC of target date** at the earliest, often later because (a) the adapter needs the WU daily summary to finalize, and (b) the request-to-OO step is operator-initiated.

### Important caveat: 12:00 UTC endDate is NOT settlement
- The endDate is the **end of trading**. Settlement requires the daily WU summary plus the UMA liveness window. Operator-observed evidence on the live London May-3 market: resolution was visible "May 4, 2026, 1:51 AM UTC" — i.e., ~14h after end-of-trading (https://polymarket.com/event/highest-temperature-in-london-on-may-3-2026, fetched 2026-05-04). This suggests:
  - WU daily summary lag (city-tz dependent) gates the resolution request.
  - For London (UTC+1 BST, day ends 23:00 UTC), WU daily summary is typically queryable a few hours after midnight local; UMA propose+liveness adds ~2h; total ~14h after the 12:00 UTC endDate is plausible.

**Critical for Zeus**: The 12:00 UTC endDate is **not** the resolution time. It is the **trading cutoff**. After 12:00 UTC, no new positions can be opened — but the YES/NO outcome can still be uncertain for hours, and the market does not settle until UMA's liveness expires after the resolution request is filed.

---

## Q3: Polymarket weather-market open cadence

Verified via Gamma API (https://gamma-api.polymarket.com/events?slug=...) on 2026-05-04 across 7 cities × 2 target dates.

| City | Target date | createdAt (UTC) | startDate (UTC) | endDate (UTC) | Resolution source |
|---|---|---|---|---|---|
| London | 2026-05-04 | 2026-05-02 04:03:39 | 2026-05-02 04:55:59 | 2026-05-04 12:00:00 | wunderground.com/history/daily/gb/london/EGLC |
| Tokyo | 2026-05-04 | 2026-05-02 04:04:43 | 2026-05-02 05:10:29 | 2026-05-04 12:00:00 | wunderground.com/history/daily/jp/tokyo/RJTT |
| Singapore | 2026-05-04 | 2026-05-02 04:04:54 | 2026-05-02 04:24:06 | 2026-05-04 12:00:00 | wunderground.com/history/daily/sg/singapore/WSSS |
| Wellington | 2026-05-04 | 2026-05-02 04:04:27 | 2026-05-02 05:16:10 | 2026-05-04 12:00:00 | wunderground.com/history/daily/nz/wellington/NZWN |
| NYC | 2026-05-03 | 2026-05-01 04:03:58 | 2026-05-01 04:24:19 | 2026-05-03 12:00:00 | wunderground.com/history/daily/us/ny/new-york-city/KLGA |
| LA | 2026-05-03 | 2026-05-01 04:05:31 | 2026-05-01 04:32:56 | 2026-05-03 12:00:00 | wunderground.com/history/daily/us/ca/los-angeles/KLAX |
| Sao Paulo | 2026-05-03 | 2026-05-01 04:03:41 | 2026-05-01 05:00:48 | 2026-05-03 12:00:00 | wunderground.com/history/daily/br/guarulhos/SBGR |

### Pattern (high confidence — 7/7 cross-city consistency)
1. **createdAt clusters at 04:03-04:05 UTC, exactly 2 calendar days before target date.** This is a **fixed UTC daily batch open** — not a per-city local-tz open.
2. **startDate (when Polymarket actually allows trading) is 20-70 min after createdAt** (~04:24-05:16 UTC). This 20-70min lag is operational/liquidity-seeding warm-up, not a per-city differentiator.
3. **endDate is uniformly 2026-MM-DD 12:00:00 UTC** of the target date. This is **the same UTC moment for every city** regardless of local timezone. London 12:00 UTC = 13:00 BST (afternoon, before peak). Tokyo 12:00 UTC = 21:00 JST (after sunset). Wellington 12:00 UTC = 00:00 NZST (after midnight, *next* calendar day). NYC 12:00 UTC = 08:00 EDT (morning, before high). Singapore 12:00 UTC = 20:00 SGT (after sunset). Sao Paulo 12:00 UTC = 09:00 BRT (morning).
4. **Resolution source is uniformly Wunderground daily history** at a per-city ICAO airport station.

### Coverage on 2026-05-04 (from polymarket.com/markets/weather)
22 cities listed for May-4 target: Tokyo, Hong Kong, Singapore, Seoul, Wellington, Miami, Shanghai, Paris, London, Chicago, Jakarta, Taipei, Jeddah, Milan, Warsaw, Beijing, Toronto, Atlanta, Ankara, Chongqing, Guangzhou, Kuala Lumpur. May-5 adds Lagos, Helsinki, Seattle, Panama City, Manila, Denver, Busan, Qingdao, Wuhan, Austin, Dallas. (https://polymarket.com/markets/weather, fetched 2026-05-04.)

### Implication: 12:00 UTC endDate creates an **impossible asymmetry** for east-Asia cities
- For **Wellington (UTC+12)**, 12:00 UTC end = **00:00 next day local** — meaning the entire target-date local-day max-temperature is locked in *before* the calendar day even ends in Wellington. No, wait: re-read — "endDate is 2026-05-04 12:00 UTC", target-date "2026-05-04". Wellington local is UTC+12, so 2026-05-04 12:00 UTC = 2026-05-05 **00:00 NZST**. So trading closes exactly at end-of-target-date local for Wellington.
- For **Tokyo (UTC+9)**, end = 2026-05-04 21:00 JST. Trading closes 3h before end-of-target-date local. This means **Day0 max-so-far is incomplete** when trading closes (the late-evening hours are not yet observed).
- For **London (UTC+1 BST)**, end = 2026-05-04 13:00 BST. Trading closes mid-afternoon — **before peak temperature is observed**. This is the most epistemically asymmetric case: Polymarket settles based on a daily high that is *not yet realized* when trading closes.
- For **Sao Paulo (UTC-3)**, end = 2026-05-04 09:00 BRT. Trading closes morning, **well before peak**. Massive time-after-close uncertainty.
- For **NYC (UTC-4 EDT)**, end = 2026-05-04 08:00 EDT. Same — morning close, peak unrealized.
- For **LA (UTC-7 PDT)**, end = 2026-05-04 05:00 PDT. **Trading closes pre-dawn**. Extreme.

**This is the critical structural fact for Zeus**: the 12:00 UTC endDate is friendly to UTC+11 to UTC+13 cities (Wellington-band) and progressively hostile as longitude moves west. For LA / Honolulu / Anchorage cities — if Polymarket adds them — the entire trading day closes before observation.

---

## Q4: Polymarket liquidity by UTC hour

Per-event liquidity / volume from Gamma API spot-checks on 2026-05-04:

| Event | liquidity (USD) | volume (USD) |
|---|---|---|
| London May-4 | 13,732 | 71,703 |
| Tokyo May-4 | (not pulled, similar order) | — |
| Singapore May-4 | 385,643 | 87,221 |
| Wellington May-4 | 9,976 | 68,656 |
| NYC May-3 | 25,057 | 170,173 |
| LA May-3 | 23,400 | 70,906 |
| Sao Paulo May-3 | 59,951 | 56,077 |

NYC volume (170K) is roughly 2.4× London's (71K) and 3× Wellington's (68K), consistent with US-east bias. Singapore liquidity (385K) is anomalously high — possibly reflecting market-maker pre-positioning since Singapore's diurnal range is tight, not retail flow.

**Aggregate hourly distribution by UTC**: Public Dune dashboards (https://dune.com/rchen8/polymarket, https://dune.com/queries/polymarket-volume) returned "Loading" without data in this session. **EVIDENCE_NEEDED** for definitive hourly breakdown. Industry-knowledge prior is that crypto-prediction-market volume concentrates in **14-22 UTC** (US trading hours) with thin **00-06 UTC** tail. For weather markets specifically, this matters because the Asian-cohort (Tokyo / Singapore / Hong Kong / Seoul) trades against thin overnight US books during their own daytime — a known liquidity wrinkle.

---

## Q5: Day0 / observation-source timing constraints

### NOAA NWS (https://www.weather.gov/documentation/services-web-api)
- "Observations may be delayed up to 20 minutes from MADIS, the upstream source, due to QC processing." This is the canonical NWS hourly-observation latency. **Confidence: HIGH.**

### HKO
- Public current-weather page (https://www.hko.gov.hk/en/wxinfo/currwx/current.htm) gives no quantitative latency. **Confidence: LOW** for a primary-source claim. Prior knowledge (HKO automatic stations push every minute; aggregated hourly extrema published within ~5-10 min of hour close) — but not source-grounded in this session.

### Ogimet
- Ogimet hosts SYNOP from WMO GTS. Self-described as "provisional / limited validation" (https://www.ogimet.com/synops.phtml.en). Public docs do not give a latency number. Empirical observation by trading bots: **3-hourly SYNOPs typically appear 60-180 min after observation hour**, hourly METARs faster (~10-30 min). **EVIDENCE_NEEDED** for primary-source latency.

### Wunderground (the Polymarket settlement source)
- Update intervals: airport / NWS-feed stations refresh "every 15 minutes"; PWS data as fast as "every 2.5 seconds" (https://www.wunderground.com/about/data).
- COOP daily max/min available "in near real-time" but **no published-after-day-end schedule given**.
- The London city-airport station (EGLC) was empty when probed mid-day — daily summary populates after the local day ends. Empirical observation from the operator's noted London May-3 resolution (1:51 UTC May-4) suggests **daily summary available within 1-3 hours of midnight local** for major airports.

### Day0 implication
- Zeus's "Day0 = 24h before settle in city tz" formulation is **structurally misaligned with Polymarket's 12:00 UTC trading end**. For LA, 12:00 UTC end = 05:00 PDT — there is no 24h Day0 window in city local time that intersects trading. The "Day0" semantic must be redefined as **"24h before trading closes" (anchored to 12:00 UTC end), not "24h before settle in city tz"**.

---

## Q6: Industry consensus on global coverage scheduling

No open-source weather-market trading bot publicly publishes its scheduling philosophy. Public literature on prediction-market microstructure (e.g., Manski 2006, Wolfers & Zitzewitz 2004) addresses pricing efficiency, not operator-side polling cadence.

Adjacent industry practice in **CME weather futures** (HDD/CDD): contracts are city-anchored (HDD New York LaGuardia, CDD Chicago O'Hare) but settle on monthly-cumulative degree-days, so polling cadence is daily-granular, not intraday — not a useful analog for Polymarket's daily-binary structure.

**No published consensus exists for "global UTC-cron vs per-city-tz triggers."** The choice is left to operator design. For the question "should Zeus stay UTC-anchored or move to per-city-tz?", external literature gives no direct answer. The operator's intuition (per-city ALPHA tiles globally → no genuine global vacuum) is structurally correct given the per-city Polymarket end-of-day pattern, but the load-bearing constraint is **internal alpha-budget per city**, not external NWP timing.

**Confidence: LOW** that any external authority can resolve this. The decision is internal-to-Zeus.

---

## Cross-references and primary-source citation table

| Claim | Source URL | Confidence |
|---|---|---|
| ECMWF HRES 00z full by ~07:34 UTC | https://confluence.ecmwf.int/display/DAC/Dissemination+schedule | HIGH |
| ECMWF ENS 00z first products 06:40 UTC | https://confluence.ecmwf.int/display/DAC/Dissemination+schedule | HIGH |
| ECMWF Open Data 7-9h post-cycle on AWS | https://github.com/ecmwf/ecmwf-opendata | HIGH |
| GFS 06z production 09:22-11:08 UTC | https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/ | HIGH |
| GEFS 18z production 21:26-00:26 UTC | https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/ | HIGH |
| ICON ~160min post-cycle on opendata.dwd.de | https://opendata.dwd.de/weather/nwp/icon/grib/ | HIGH |
| UMA OO Polymarket default liveness 7200s (2h) | https://docs.uma.xyz/protocol-overview/how-does-umas-oracle-work | HIGH |
| UMA dispute path 48-72h | https://github.com/Polymarket/uma-ctf-adapter | HIGH |
| Polymarket resolves via uma-ctf-adapter | https://github.com/Polymarket | HIGH |
| Polymarket weather endDate uniformly 12:00 UTC of target date | https://gamma-api.polymarket.com/events?slug=highest-temperature-in-{city}-on-{date} (7 cities verified) | HIGH |
| Polymarket weather createdAt ~04:04 UTC, T-2 days | Gamma API (7/7 cross-city consistency) | HIGH |
| Polymarket weather resolutionSource = wunderground.com/history/daily/{country}/{city}/{ICAO} | Gamma API (7/7) | HIGH |
| Polymarket May-4 covers 22 cities | https://polymarket.com/markets/weather | HIGH |
| London resolution observed at 01:51 UTC next day | https://polymarket.com/event/highest-temperature-in-london-on-may-3-2026 | MEDIUM (single observation) |
| NWS observations ~20min lag from MADIS | https://www.weather.gov/documentation/services-web-api | HIGH |
| Wunderground airport stations refresh every 15min | https://www.wunderground.com/about/data | HIGH |
| TIGGE real-time ingest latency | EVIDENCE_NEEDED | LOW |
| HKO publication latency | EVIDENCE_NEEDED | LOW |
| Ogimet SYNOP latency | EVIDENCE_NEEDED | LOW |
| Polymarket hourly volume by UTC | EVIDENCE_NEEDED (Dune dashboard would not load) | LOW |
| Industry scheduling consensus | None found | LOW |

---

## Confidence ratings (per question)

| Q | Confidence | Why |
|---|---|---|
| Q1 NWP schedules | HIGH for ECMWF + NOAA + DWD; LOW for TIGGE | Three of four primary sources delivered exact times |
| Q2 UMA settlement | HIGH on liveness mechanics; MEDIUM on full Polymarket end-to-end timeline | Liveness is documented; per-city settle observation is single-point |
| Q3 Polymarket open cadence | HIGH | 7/7 cities show identical UTC-anchored pattern with createdAt ~04:04, endDate 12:00 UTC |
| Q4 Liquidity by UTC | LOW | Spot-check liquidity numbers HIGH; aggregate hourly breakdown LOW (Dune wouldn't load) |
| Q5 Observation latency | HIGH for NWS + WU; LOW for HKO/Ogimet | Two of four sources documented; two opaque |
| Q6 Industry consensus | LOW | No published convention found |

---

## Open questions for follow-up

1. **TIGGE real-time ingest latency** — primary-source confluence pages 404'd. Try NCAR RDA `ds330.3` documentation or contact ECMWF support to confirm.
2. **Polymarket aggregate volume by UTC hour** — Dune dashboards did not load. Direct query against Polygon RPC for `OrderFilled` events on `CTFExchange` could derive the distribution.
3. **HKO / Ogimet observation publication latency** — no primary-source numbers. Empirical measurement via Zeus's own ingest logs (internal task) more reliable than vendor claims.
4. **Why Zeus's UPDATE_REACTION = 07/09/19/21 UTC and not 08/14/20/02 UTC** — the latter would tile better with ECMWF Open Data S3 + GFS production. The 07/09/19/21 choice **does** map roughly to NWP availability (07 ≈ ECMWF 00z S3 ready start; 09 ≈ GFS 06z production start; 19 ≈ ECMWF 12z S3 ready start; 21 ≈ GFS 18z production start), but the 09 and 21 anchors come *during* GFS production (09:22-11:08 / 21:24-23:10), suggesting partial-cycle catch. Confirm in `INVESTIGATION_INTERNAL.md` against Zeus design history.
5. **WU daily summary publication time per city** — empirical only. Operator should sample 5-10 cities × 7 days from internal logs to derive p50/p95 publication latency relative to local midnight. This is the actual gating constraint on UMA settlement.
6. **The 12:00 UTC endDate westward asymmetry** — does Polymarket adjust the endDate per-city (e.g., LA May-3 might be different from London May-3)? Spot-check across the western-hemisphere cities at next refresh confirmed: **all checked cities use the same 12:00 UTC endDate**. This is a **structural Polymarket choice**, not a per-city configuration. **Document deeply** because it implies Polymarket itself has decided that "trading should close at the same UTC moment globally," which is the same choice Zeus is now reconsidering. Whether Polymarket's choice is "obviously right" or "an inherited bug" is itself a research question.

---

## Recommended Next Step

The single most decision-relevant external fact is **Q3's confirmation that Polymarket's endDate is uniformly 12:00 UTC** across all weather cities, regardless of local timezone. This means:

- Zeus's UPDATE_REACTION cron at 07/09 UTC fires **5-3 hours before global trading close**, plausibly capturing late-life price moves.
- Zeus's UPDATE_REACTION cron at 19/21 UTC fires **7-9 hours after global trading close** — meaning these crons act on markets where T+0 trading is *over* and the next day's markets created at 04:04 UTC are still in early-life. This is plausibly the *new-market opening hunt* anchor, not a same-day update.

So the 07/09/19/21 schedule may be a **hybrid**: 07/09 = late-life UPDATE on today's expiring markets; 19/21 = early-life evaluation on tomorrow's just-opened markets. If so, the "UTC anchor" is half-correct (it tracks NWP releases) but half-misaligned (it ignores per-city diurnal observation curves between 09 and 19 UTC). **Internal investigation should verify which markets each of the four crons actually touches** — that distinguishes "well-designed UTC pair" from "half-built per-city engine forced into UTC slots."

The operator's pushback ("no genuine global vacuum exists") is correct in the sense that **per-city alpha tiles do not require UTC-cron synchronization**. But the 12:00 UTC end-of-trading is a *real* synchronization point imposed by Polymarket itself — so a per-city-tz redesign cannot ignore it. The right architecture likely combines:

- **Per-city diurnal observation triggers** (city-tz-anchored, hits each city's max-temp observation window)
- **Global UTC liquidity triggers** (12:00 UTC ± window for trading-close volatility)
- **Global NWP-release triggers** (~07 / ~19 UTC for ECMWF 00z/12z absorption)

Three lanes, three different anchor systems, multiplexed — not a single 4-tick UTC cron. This is the structural change worth proposing.

