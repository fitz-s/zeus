# Harvest Cycle 2 — Post-Peak NO Harvest (LIVE, real money)

- Created: 2026-06-14
- Authority basis: operator live directive (post-peak harvest cycle 2); paranoid
  guard = grant a free +1 notch / 2σ, keep only bins still +EV.
- Run window: 2026-06-14 ~15:42–16:00 UTC
- Method: DIRECT Polymarket Gamma API (events `slug=highest-temperature-in-<city>-on-june-14-2026`)
  + CLOB `/book?token_id=<NO_token>` live asks + aviationweather METAR (ICAO station,
  8h history for max-so-far + trend). NOT `find_weather_markets`.

## RESULT: NO CLEAN OPPS — 0 orders placed, $0 deployed.

No bin in any live June-14-2026 market cleared the guard with edge ≥10¢ on a
≥2-notch-above-favorite ("near-impossible") bin with real cheap NO inventory.
No placement attempted (no order touched the signed adapter path).

## Decisive structural finding

The market makers have already absorbed the post-peak harvest. On EVERY live
city, the "near-impossible" far bins (≥2 notches above the day's locked max)
are already priced at **NO ask = 0.998–0.999, or have zero NO ask inventory**
(market-implied NO ~0.999). Buying NO there = ≤0.2¢ edge — far below the 10¢
threshold. The only sub-0.90 NO asks anywhere sit on either:
  (a) the actual settlement bin (a directional YES bet dressed as NO), or
  (b) the **one-notch-up** bin — which the paranoid guard correctly rejects
      (a single-notch overshoot is well within realized intraday variance).

This is the SAME wall London hit: London's far bins (17–20°C, 24–27°C) have
NO best-bid 0.999 and **zero NO asks** — nothing cheap to buy. The earlier
London 22°C NO win was a *one-notch* bet that later resolved favorably; that
window is now closed (22°C NO already 0.85, mostly resolved).

## Programmatic sweep (2-notch-above-fav, NO ask edge ≥10¢)

Scanned 27 live June-14-2026 cities. Result for the guard window:

| Scope | Cities | Harvest hits (≥2-notch, NO edge ≥10¢) |
|---|---|---|
| EU/Med/MidEast | london, paris, amsterdam, madrid, milan, istanbul, tel-aviv, warsaw, munich, moscow, helsinki | NONE |
| Asia (out of scope: closed local) | shanghai, beijing, singapore, hong-kong | NONE |
| Americas | sao-paulo, mexico-city, toronto, miami, houston, denver, seattle, dallas, atlanta | NONE except Dallas (rejected, see below) |

## Per-city rejection reasons (in-scope EU/Med/MidEast post-peak set)

METAR max-so-far (8h history) + live NO CLOB asks:

- **London** EGLL — max ~21–22°C, settling. Far bins (≥23°C) NO already 0.992–0.999
  or no ask inventory. No cheap NO. EFFICIENT / already-absorbed. SKIP (also already held).
- **Paris** LFPG — max 25°C, now 24°C (just past peak, wobbling 24/25). Harvest target
  27°C+ needs +2°C: 27°C NO=0.998, 28°C NO=0.999, 29°C+ no inventory → ≤0.2¢ edge.
  Sub-0.90 only at 25°C (settle bin, 0.41) and 26°C one-notch (0.69 → guard REJECT). NO CHEAP NO.
- **Madrid** LEMD — max 34°C locked & declining (34 held 5 readings, now 33). FIRMLY post-peak.
  Harvest 36°C+ needs +2°C: 36°C NO=0.998, 37°C NO=0.999, 38°C+ no inventory → ≤0.2¢ edge.
  Sub-0.90 only at 34°C (settle, 0.26) and 35°C one-notch (0.79 → guard REJECT). NO CHEAP NO.
- **Milan** LIML — cur=max=31°C and STILL CLIMBING (26→…→31 monotone). NOT post-peak. REJECT (climbing).
- **Munich** EDDM — cur=max=21°C, flat 5 readings, peak not clearly broken. No ≥10¢/2-notch NO. REJECT (peak not locked).
- **Rome** LIRF — max 28°C, oscillating 27/28. ~at peak; no qualifying cheap NO.
- **Athens** LGAV — max 27°C, now 26°C (just past peak); no qualifying cheap NO.
- **Istanbul** LTFM — cur=max=26°C, flat/slightly rising; not clearly post-peak; no qualifying cheap NO.
- **Tel-Aviv** LLBG — fav 30°C YES=0.9995, market saturated; no cheap NO ≥2 notches.
- **Amsterdam / Warsaw / Helsinki** — fav at low bin (17/17/15°C) YES≈0.98–0.99; far bins already NO≈0.999, no inventory.

## Dallas — the only programmatic hit, REJECTED

Dallas 90-91°F NO @ 0.88 (12¢ edge, depth 13) surfaced in the sweep. REJECTED:
KDFW METAR at 16:00 UTC = **11:00 CDT (late MORNING)**, current 72°F, on the
morning warm-up; daily high is hours away. This is a PRE-PEAK US city — exactly
the "SKIP US cities (still pre/at peak)" rule and the climbing-city guard. The
90-91°F bin is a live afternoon settlement candidate, not a locked-out harvest.

## Side finding (provenance / antibody)

- The legacy `highest-temperature-in-london-on-june-14` slug (no `-2026`) is a
  **June 2025** Fahrenheit-bin artifact (endDate 2025-06-14T12:00Z, closed). The
  live 2026 markets require the explicit `-2026` suffix.
- 2026 daily-temp markets settle/end at **12:00 UTC** (endDate=2026-06-14T12:00:00Z),
  NOT local midnight — yet remain `closed=False` with live order books ~4h past
  endDate. The task premise "close at local midnight (~21–23 UTC)" is INCORRECT
  for these markets; correct settlement basis is the station's daily high, end 12:00 UTC.
  Books were still live/queryable at run time, but this does not change the verdict:
  there was no cheap NO to buy regardless of order-acceptance window.
