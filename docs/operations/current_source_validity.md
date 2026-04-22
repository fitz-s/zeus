# Current Source Validity

Status: active current-fact surface
Last audited: 2026-04-21
Authority basis: Gate F Step 1b per-city source validity audit plus
`config/cities.json` cross-check
Authority status: not authority law; this is current operational
source-validity routing only

## Purpose

Read this file when you need the current audited answer to:

- which city/provider routes are currently valid
- which sources are stalled or suspect
- which historical sources are fossils and must not be treated as active routing
- whether source-validity evidence has advanced beyond older provenance snapshots

## Aggregate Current Posture

- `wu_icao` class: valid and advancing
- `noaa` / Ogimet-proxy class: valid and advancing
- `hko` class: currently suspect/stalled relative to the last audit window

## Current Provider-Class Summary

- 47 configured cities are in the WU ICAO family
- 3 configured cities are in the Ogimet/noaa-proxy family
- 1 configured city is in the HKO family

Operational status as of the audit:

- WU ICAO family: valid, advancing
- Ogimet family: valid, advancing
- Hong Kong / HKO: suspect or stalled relative to the audited window

## Per-City Current Routing Summary

### WU ICAO primary

Amsterdam, Ankara, Atlanta, Auckland, Austin, Beijing, Buenos Aires, Busan,
Cape Town, Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki,
Houston, Jakarta, Jeddah, Karachi, Kuala Lumpur, Lagos, London, Los Angeles,
Lucknow, Madrid, Manila, Mexico City, Miami, Milan, Munich, NYC, Panama City,
Paris, San Francisco, Sao Paulo, Seattle, Seoul, Shanghai, Shenzhen, Singapore,
Taipei, Tokyo, Toronto, Warsaw, Wellington, Wuhan.

### Ogimet / NOAA-proxy primary

Istanbul, Moscow, Tel Aviv.

### HKO primary

Hong Kong.

## Fossil / Zombie Sources

Historical source rows such as:

- `ogimet_metar_fact`
- `ogimet_metar_vilk`

should be treated as fossil lineage, not active source routing.

They are evidence of historical source experiments or drift, not proof of
current city routing.

## Hong Kong Status

Hong Kong is the explicit current caution path.

Interpretation at the time of audit:

- the route exists
- the source family is still known
- the provider may be publishing late, may have changed behavior, or the fetch
  path may be drifting
- current truth claims for Hong Kong must route through fresh audit evidence,
  not assumption

## Relationship To Durable References

- Durable settlement semantics, rounding, and source-risk classes live in
  `docs/reference/zeus_market_settlement_reference.md`.
- Dated source-audit evidence lives in reports and artifacts.
- Configuration truth still comes from `config/cities.json` and executable code
  paths.

## Update Rule

Only update this file from:

- fresh packet audit evidence
- config/runtime cross-checks
- explicit operator validation

Do not update it from memory, old provenance tables, or inference only.

## Refresh Protocol

Refresh trigger:

- a source/provider audit lands
- `config/cities.json` source routing changes
- a provider stalls, advances unexpectedly, or changes endpoint behavior
- this file is older than 14 days and is being used for source/backfill
  planning

Required evidence:

- fresh per-city source audit
- config/runtime cross-check
- dated report or packet evidence for any source transition

Manual refresh rule:

- update from evidence only
- keep durable semantics in `docs/reference/zeus_market_settlement_reference.md`
- keep dated evidence in reports/artifacts
- preserve `Last audited`
- do not update from memory or old provenance snapshots

Maximum staleness:

- 14 days for source/backfill planning
- otherwise re-audit before relying on the file
