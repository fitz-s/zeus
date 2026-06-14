# Cold-Bias Metadata Root — Is the −0.54°C Cold Offset a Fixable Defect, and Does Fixing It Beat the Market?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `file:state/<db>?mode=ro`, `.timeout 25000`, ISO-T. Raw scripts in `/tmp/coldbias/*.py`. Every number below carries a query or file:line.
**Charge:** `overconfidence_root.md` asserted a systematic −0.54°C cold bias (raw OpenMeteo anchor −0.29°C, "doubled" by T2 fusion to −0.54°C, `bayes_precision_fusion.py:67`), worst in Taipei/Singapore/HK/Seoul/Chengdu/Wuhan. Find the metadata root, and decide whether correcting it makes the bin-belief correct and produces edge vs market.

---

## VERDICT

**ROOT = STATION_ELEVATION_MISMATCH (specifically GRID-CELL REPRESENTATIVENESS), city-specific — NOT a uniform metadata defect, NOT FUSION_DEBIAS, NOT a local-day-window or time-semantics bug.**

The "−0.54°C systematic cold offset" is a **statistical artifact of averaging a wide, city-specific distribution of station-representativeness residuals that happens to net mildly cold** — it is *not* a single correctable constant. Measured clean (Celsius-only, n=638 settled HIGH cells, raw anchor vs verified settlement):

- **HIGH anchor bias = −0.37°C, LOW = −0.62°C** (`/tmp/coldbias` query) — the −0.54 in the prior doc is the C+F blend; the dominant traded metric (HIGH) is only −0.37 at the anchor.
- **Per-city HIGH bias spans −2.18°C (Tokyo) to +2.48°C (Karachi)** — a **4.7°C spread, both signs**. A uniform window/DST/fusion-constant defect produces a *uniform* offset; this is a city-specific dispersion. (Tokyo −2.18, Kuala Lumpur −2.15, Seoul −1.93, Milan −1.54, London −1.46 … Chengdu +1.59, Toronto +1.76, Qingdao +2.0, Karachi +2.48.)
- **The cold offset already lives in the RAW anchor, before fusion** — so the dominant component is upstream data/station, not fusion math. The "fusion doubles it" claim is reframed below.

The two ruled-OUT roots (verified clean):

- **LOCAL_DAY_WINDOW = CLEAN.** `forecast_target_contract.py:90-103` builds the window midnight-to-midnight LOCAL via `ZoneInfo` (DST-correct by construction); the anchor extractor (`openmeteo_ecmwf_ifs9_anchor.py:411-416`) requests OpenMeteo with `timezone=<city tz>` and filters `local_time.date()==target_local_date`, taking `max()` over all 24 local hours (`:425`). No UTC-day / clipped-afternoon defect.
- **TIME_SEMANTICS = CLEAN at the anchor.** The anchor is run-pinned (single-runs `run=` param, `:175`) with a meta-declared-run fallback that REFUSES a different cycle (`:297-301`). No now−lag run guess on this source.

**The decisive market question CANNOT be answered against these cities with current data — and that itself is the headline finding for edge:** market_price_history for the six worst Asian cities is **catastrophically sparse — ~8–12 priced bins out of 308 (~3-4% coverage)** and the table is **stale (ends 2026-05-28)**. There is no usable market center to compare against; the worst-bias cities are exactly the **thin/illiquid** ones in our capture. See §5.

**RANK:** `STATION_ELEVATION_MISMATCH / grid representativeness` (the root, city-specific) ≫ `FUSION_DEBIAS under-correction on thin history` (amplifier, not origin) ≫ `instantaneous-hourly-max under-sampling` (small uniform ~0.1–0.3°C cold, not city-specific) ≫ `LOCAL_DAY_WINDOW` / `TIME_SEMANTICS` (clean) ≫ `GENUINE_SKILL` (the floor beneath all).

**Confidence:** HIGH that the offset is city-specific representativeness (n=638, 4.7°C two-sign spread, raw-anchor-resident). HIGH that window/time-semantics are clean (code-verified). HIGH that the market comparison is ungroundable on the worst cities (coverage query). MEDIUM on the exact per-city physical cause (grid-cell displacement is necessary but the bias does not correlate 1:1 with displacement or elevation — local terrain/coast/UHI specifics drive sign).

---

## 1. LOCAL-DAY WINDOW — CLEAN (rules out the under-stated-max hypothesis)

`src/contracts/forecast_target_contract.py:90-103` `compute_target_local_day_window_utc`:
```
start_local = datetime.combine(target_local_date, time.min, tzinfo=ZoneInfo(city_timezone))
end_local   = datetime.combine(target_local_date + 1d, time.min, tzinfo=ZoneInfo(city_timezone))
```
Midnight-to-midnight LOCAL, converted to UTC bounds. DST-correct because `ZoneInfo` resolves the offset on each date. The anchor extractor independently re-derives local membership: `openmeteo_ecmwf_ifs9_anchor.py:411` `local_time = _parse_openmeteo_time(raw_time, city_timezone=...)`, `:412` `if local_time.date() != target_local_date: continue`, `:425` `high_c = max(contributing_temperatures_c)`. The OpenMeteo request itself passes `timezone=self.timezone_name` (`:175`), so the provider returns locally-stamped hours. **The hottest afternoon hours are inside the window; no clipping, no UTC-day shift.** This root is dead.

## 2. TIME-SEMANTICS — CLEAN at the anchor

`openmeteo_ecmwf_ifs9_anchor.py`: run is pinned via the single-runs API `run=<cycle>` param (`OpenMeteoEcmwfIfs9AnchorRequest.params`, `:166-176`); the meta-stamped standard-API fallback (`:269-328`) reads provider meta BEFORE and AFTER and **raises** if the declared run ≠ requested run (`:297-301`) or if the dataset was modified mid-fetch (`:315-319`). This is the antidote to the now−lag/previous-run guess class. Valid-time→local mapping is exact (`_parse_openmeteo_time`, `:83-90`). No lead/cycle mis-map at the anchor.

## 3. STATION / GRID REPRESENTATIVENESS — THE ROOT (city-specific, two-sign)

**Config coordinates ARE the WU settlement station** — `config/cities.json._coord_note`: *"All lat/lon correspond to the airport weather station used by Weather Underground for Polymarket settlement. These MUST match the WU station exactly."* Verified: every worst city's `config/cities.json` lat/lon equals the `request_latitude/longitude` in `state/anchor_city_elevation.json` (`/tmp/coldbias/grid_disp.py`, `cfg=req:True` for all 7). So the request point is correct.

**But the served 9km IFS grid cell is displaced 3.2–4.7 km from that station point, and its bias is city-specific** (`/tmp/coldbias/grid_disp.py`; bias = all-leads Celsius HIGH anchor−settlement):

| city | req lat,lon | served grid lat,lon | displ_km | grid_elev_m | anchor_bias_C |
|---|---|---|---:|---:|---:|
| Seoul | 37.469,126.451 | 37.504,126.431 | 4.30 | 5 | **−1.93** |
| Singapore | 1.368,103.982 | 1.371,103.945 | 4.16 | 15 | −0.54 |
| Ankara | 40.128,32.995 | 40.105,33.025 | 3.59 | 948 | −0.41 |
| Hong Kong | 22.302,114.174 | 22.320,114.199 | 3.20 | 38 | −0.03 |
| Wuhan | 30.783,114.205 | 30.756,114.227 | 3.67 | 32 | +0.16 |
| Taipei | 25.069,121.552 | 25.062,121.519 | 3.39 | 4 | +0.62 |
| Chengdu | 30.578,103.947 | 30.545,103.977 | 4.68 | 494 | **+1.59** |

**Signature:** displacement is uniform (~3-4 km, the 9km cell-snap), but bias ranges −1.93 to +1.59 with **both signs**. Bias does NOT track elevation linearly (Seoul 5 m is coldest; Chengdu 494 m is warmest — opposite of a lapse-rate story) NOR displacement. It tracks **local representativeness**: coastal/maritime/urban grid cells that the 9 km IFS smooths differently from the airport microclimate. Seoul's cell sits 4.3 km NW toward cooler terrain/water; Chengdu's cell in the Sichuan basin reads the warm valley floor.

**Full Celsius-HIGH distribution (n≥5, ranked, `/tmp/coldbias`):** −2.18 Tokyo, −2.15 Kuala Lumpur, −1.93 Seoul, −1.54 Milan, −1.51 Amsterdam, −1.46 London, −1.18 Paris, −1.17 Panama City, −1.15 Beijing … +0.62 Taipei, +1.01 Cape Town, +1.59 Chengdu, +1.76 Toronto, +2.00 Qingdao, +2.48 Karachi. Mean nets mildly cold because cold cities outnumber warm — that average is the "−0.54". **There is no single global metadata constant to fix; the defect is a per-city grid-cell-vs-station offset.**

Elevation metadata note: `state/anchor_city_elevation.json` records the **grid-cell DEM elevation** (`authority: openmeteo_90m_dem_api_reported`), e.g. Taipei 4 m, Chengdu 494 m — i.e. the elevation OF the served cell, not of the settlement station. No lapse-rate correction is applied anywhere in the anchor path; none would help, because the residual is not elevation-driven.

## 4. FUSION "DOUBLING" — AMPLIFIER ON THIN HISTORY, NOT THE ORIGIN

`bayes_precision_fusion.py:67` `eb_bias(resids, parent_bias)` is the **per-source walk-forward DE-bias** (`b_hat = λ·rbar + (1−λ)·parent`, `λ = n/(n+KAPPA)`, `KAPPA=8`, `:51,77`) — it SUBTRACTS each source's residual mean (vs the settlement label Y) before fusion. So de-bias IS applied per source. The prior doc's "fusion doubles the bias" is **not a fusion-math defect**; it is two real but secondary effects:

1. **Under-correction by EB shrink.** With `KAPPA=8` and `MIN_TRAIN=25`, a city with thin or persistent station history has `λ<1` and shrinks its correction toward a structural parent — so a genuine, persistent station-representativeness offset is only *partially* removed. The residual cold survives into `z`, and the T2 posterior (`mu* = V*(mu0/τ0² + 1'Σ⁻¹z)`, `:156`) inherits it.
2. **Low-σ instruments dominate the precision weight.** A cold instrument with tight residual variance gets large `Σ⁻¹` weight, pulling `mu*` colder than the anchor prior `mu0`. The prior doc measured this amplification at −0.29→−0.54 PRE-σ-floor and only −0.38→−0.10 POST-floor (`overconfidence_root.md §3`) — the σ-floor already de-amplified the fusion pull.

The σ-scale/floor surface is **family-level (C/F) only**, not per-city (`replacement_forecast_materializer.py:645-700`, keyed on settlement unit). So the per-city representativeness residual has **no per-city correction** in the live path except the EB de-bias, which under-corrects. **Fix is correctable but must be per-city/per-station, applied as a walk-forward de-bias with enough history (or a per-city representativeness offset), NOT a global warm constant** — a global +0.4°C shift would *worsen* the 13 warm cities (Karachi, Qingdao, Chengdu, Toronto…).

## 5. MARKET COMPARISON — UNGROUNDABLE on the worst cities (the real edge headline)

The edge test requires the market-implied center on the worst cities. It cannot be run, because market data for exactly these cities is missing:

- **Coverage (`/tmp/coldbias`, market_events ⋈ market_price_history, target_date≥2026-05-20):** Taipei 8, Seoul 9, Wuhan 10, HK 11, Singapore 11, Chengdu 12 **priced tokens out of 308 bins each (~3-4%)**. The whole `market_price_history` table is **10,621 rows, ending 2026-05-28** (stale).
- Attempts to derive a market center collapse to noise: probability-weighted midpoint over the few priced bins gives HK "center" 25°C when settlement was 33°C (`/tmp/coldbias/market_center.py`); the modal *priced* bin carries price 0.0–0.16 (`/tmp/coldbias/market_modal.py`) — i.e. only near-zero-probability tail bins were ever snapshotted. **The high-mass bins were never captured for these cities.**

**Interpretation for edge:** the worst-bias cities are the **thin, illiquid Asian markets** where our market capture is near-empty. This cuts two ways and is the decisive operational finding:
- If these markets are genuinely illiquid on Polymarket (likely, given the capture gaps), **there is little tradable size there regardless of belief correctness** — fixing the cold bias on Tokyo/Seoul/Singapore may not monetize.
- The edge case that *would* pay — market is naive about station representativeness while our corrected per-city read is sharp — **cannot be confirmed or refuted with this DB**; the market center for these cities is not captured. This must be re-measured against a fresh, dense orderbook capture before any "fixing the bias = edge" claim is sound.

## DECISIVE ANSWER

1. **Is it a fixable metadata defect?** Partly. The ROOT is **per-city 9km-grid-cell-vs-settlement-station representativeness** (−2.2 to +2.5°C, two signs). It is correctable only by a **per-city/per-station de-bias** (walk-forward residual offset with adequate history, or an explicit representativeness adjustment) — the EB de-bias already attempts this but under-corrects via KAPPA shrink on thin history. A **global warm constant is the WRONG fix** (worsens the 13 warm cities). Window and time-semantics are clean and need no fix.
2. **Would fixing it move bin-selection toward the winning bin?** YES for the large-magnitude cities (Tokyo/Seoul/Kuala Lumpur cold, Karachi/Qingdao/Chengdu warm): a correct per-city offset moves μ* by 1.5–2.5°C, which crosses ≥1 bin boundary in a 1°C-bin topology — directly changing the argmax bin.
3. **Does the market share/beat our corrected read (→ no edge) or is it naive (→ edge)?** **UNDECIDABLE on the worst cities with current data** — their market is ~3-4% captured and stale (2026-05-28). The worst-bias cities are the illiquid ones. Re-run the market-center-vs-settlement comparison only after a dense, current orderbook capture for these cities; until then, "fix bias → beat market" is unproven, and the more likely operational reality is that these specific markets are too thin to monetize a corrected read.

---

## RAW (deciding numbers)
- Clean Celsius anchor bias: HIGH **−0.37** (n=638), LOW −0.62; per-city HIGH spans **−2.18 (Tokyo) … +2.48 (Karachi)**, both signs.
- Grid-cell displacement 3.2–4.7 km (uniform); bias uncorrelated with displacement or elevation → city-specific representativeness.
- Window clean: `forecast_target_contract.py:90-103`, `openmeteo_ecmwf_ifs9_anchor.py:411-425`. Time clean: `:175,297-301,315-319`.
- Fusion de-bias `bayes_precision_fusion.py:67` (KAPPA=8 under-correction); σ-surface family-level only `replacement_forecast_materializer.py:645-700`.
- Market coverage for 6 worst cities: **8–12 priced tokens / 308 bins (~3-4%)**, `market_price_history` ends **2026-05-28** → market center ungroundable.

*End. Read-only. Scripts: /tmp/coldbias/{grid_disp,market_center,market_modal}.py + inline sqlite queries.*
