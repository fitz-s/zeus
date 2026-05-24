# Gamma Market Evidence: Jinan + Zhengzhou
<!-- Created: 2026-05-24 -->
<!-- Authority basis: Polymarket gamma-api live fetch 2026-05-24 (tag_id=84 + tag_id=103040) -->
<!-- Source: /tmp/gamma_84.json + /tmp/gamma_103040.json (fetched 2026-05-24 03:15 UTC) -->

This file captures the authoritative Polymarket market `description` text for Jinan and
Zhengzhou markets fetched live on 2026-05-24. Per the Zeus source-contract doctrine
(`docs/reference/zeus_vendor_change_response_registry.md §1`), the Polymarket market
description is the **only authority** for settlement provider, station code, and URL.

---

## Jinan

### Markets observed
- `highest-temperature-in-jinan-on-may-20-2026` (active=True, closed=False)
- `highest-temperature-in-jinan-on-may-21-2026` (active=True, closed=False)

### Description text (verbatim, from gamma-api)

> This market will resolve to the temperature range that contains the highest temperature
> recorded at the Jinan Yaoqiang International Airport Station in degrees Celsius on 20 May '26.
>
> The resolution source for this market will be information from Wunderground, specifically
> the highest temperature recorded for all times on this day by the Forecast for the Jinan
> Yaoqiang International Airport Station once information is finalized, available here:
> https://www.wunderground.com/history/daily/cn/jinan/ZSJN.

All 4 Jinan events share the same settlement provider/station/URL (only the date differs).

### Extracted settlement fields

| Field | Value |
|---|---|
| Provider | Wunderground (wu_icao) |
| Station name | Jinan Yaoqiang International Airport Station |
| ICAO code | ZSJN |
| URL (from description) | `https://www.wunderground.com/history/daily/cn/jinan/ZSJN` |
| Unit | Celsius (C) |

### Byte-for-byte match against cities.json proposal

`cities.json` `settlement_source` value: `https://www.wunderground.com/history/daily/cn/jinan/ZSJN`

Gamma description URL: `https://www.wunderground.com/history/daily/cn/jinan/ZSJN`

**MATCH — identical, no trailing slash in either.**

---

## Zhengzhou

### Markets observed
- `highest-temperature-in-zhengzhou-on-may-20-2026` (active=True, closed=False)
- `highest-temperature-in-zhengzhou-on-may-21-2026` (active=True, closed=False)

### Description text (verbatim, from gamma-api)

> This market will resolve to the temperature range that contains the highest temperature
> recorded at the Zhengzhou Xinzheng International Airport Station in degrees Celsius on 20 May '26.
>
> The resolution source for this market will be information from Wunderground, specifically
> the highest temperature recorded for all times on this day by the Forecast for the Zhengzhou
> Xinzheng International Airport Station once information is finalized, available here:
> https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC.

All 4 Zhengzhou events share the same settlement provider/station/URL (only the date differs).

### Extracted settlement fields

| Field | Value |
|---|---|
| Provider | Wunderground (wu_icao) |
| Station name | Zhengzhou Xinzheng International Airport Station |
| ICAO code | ZHCC |
| URL (from description) | `https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC` |
| Unit | Celsius (C) |

### Byte-for-byte match against cities.json proposal

`cities.json` `settlement_source` value: `https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC`

Gamma description URL: `https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC`

**MATCH — identical, no trailing slash in either.**

---

## How this evidence was produced

```bash
# Fetched 2026-05-24 ~03:15 UTC
curl -s "https://gamma-api.polymarket.com/events?tag_id=84&active=true&closed=false&limit=200" \
    -o /tmp/gamma_84.json
curl -s "https://gamma-api.polymarket.com/events?tag_id=103040&active=true&closed=false&limit=200" \
    -o /tmp/gamma_103040.json

# Parsed with python3 — slug filter 'jinan'/'zhengzhou', extracted .description field
```

If markets are re-fetched in a future session, re-verify the URL against this document.
If the gamma description URL differs from what is recorded here, the source contract has
changed — treat as a vendor change event per `docs/reference/zeus_vendor_change_response_registry.md`.
