# Current Source Validity

Status: active current-fact surface
Last audited: 2026-04-21
Max staleness: 14 days for source/backfill/routing planning
Evidence packet: `docs/operations/task_2026-04-21_gate_f_data_backfill/step1b_source_validity.md`
Receipt path: `docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json`
Authority status: not authority law; audit-bound current routing fact only
If stale, do not use for: settlement source routing, provider health,
Hong Kong routing, or backfill-source planning
Refresh trigger: source/provider audit, `config/cities.json` source change,
provider stall/drift, endpoint behavior change, or age > max staleness for
planning

## Purpose

Use this file only for the compact current audited answer to provider/source
posture. Durable source-role schema lives in
`architecture/city_truth_contract.yaml`; durable runtime semantics live in
`docs/authority/zeus_current_architecture.md`.

## Current Conclusions

1. `wu_icao` class was valid and advancing at audit time.
2. `noaa` / Ogimet-proxy class was valid and advancing at audit time.
3. `hko` class was suspect or stalled relative to the audited window.
4. Current provider-class counts at audit time were: 47 WU ICAO cities, 3
   Ogimet/NOAA-proxy cities, and 1 HKO city.
5. Istanbul, Moscow, and Tel Aviv were in the Ogimet/NOAA-proxy primary class.
6. Hong Kong was the explicit current caution path; current truth claims for
   Hong Kong require fresh audit evidence, not assumption.
7. Historical rows such as `ogimet_metar_fact` and `ogimet_metar_vilk` are
   fossil lineage, not active source routing.

## Invalidation Conditions

Re-audit before relying on this file if:

- any provider stalls, advances unexpectedly, or changes endpoint behavior
- market description/source text changes
- `config/cities.json` source routing changes
- Hong Kong/HKO is in scope
- the file is older than Max staleness and the task needs current source truth

## Stale Behavior

If stale, this file may be used only as historical planning context. It must
not justify settlement source routing, endpoint/source equivalence, or
Hong Kong current truth. Record `needs fresh source audit` and stop before
implementation that depends on current source validity.
