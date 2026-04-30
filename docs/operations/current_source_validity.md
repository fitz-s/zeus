# Current Source Validity

Status: active current-fact surface
Last audited: 2026-04-30T00:23:50Z for Paris source-contract monitor refresh; 2026-04-21 for broad provider audit
Max staleness: 14 days for source/backfill/routing planning
Evidence packet: `docs/operations/task_2026-04-21_gate_f_data_backfill/step1b_source_validity.md`
Runtime evidence: `scripts/watch_source_contract.py --city Paris --json --report-only --fail-on DATA_UNAVAILABLE` run on 2026-04-30T00:23:50Z; conversion-plan evidence from a temp quarantine path at 2026-04-29T23:55:51Z
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
8. As of the 2026-04-30T00:23:50Z source-contract monitor refresh, Paris is a current
   source-contract caution/quarantine path: six active Polymarket Gamma
   markets for 2026-04-29, 2026-04-30, and 2026-05-01 high/low temperatures
   resolve via Weather Underground station `LFPB`, while `config/cities.json`
   still configures Paris as `LFPG`.
9. The Paris observation is classified as `same_provider_station_change`, not
   a verified config promotion. New Paris entries must remain blocked by
   source-contract quarantine until conversion release evidence is complete;
   existing positions may continue monitor/exit handling under their existing
   lifecycle rules.

## 2026-04-30 Paris Source-Contract Caution

The live source-contract monitor returned `authority=VERIFIED`,
`status=ALERT`, and six Paris `MISMATCH` events:

- `422416` / `422449` for 2026-04-29 low/high
- `426177` / `426227` for 2026-04-30 low/high
- `429671` / `429698` for 2026-05-01 low/high

All six events carried WU daily-history URLs under station `LFPB`; the
configured Paris settlement station remained `LFPG`. Treat this as a
city-level new-entry block until all release evidence refs exist for
`config_updated`, `source_validity_updated`, `backfill_completed`,
`settlements_rebuilt`, `calibration_rebuilt`, and `verification_passed`.
This current-fact entry does not authorize config changes, production DB
mutation, settlement rebuilds, calibration rebuilds, or live order placement.

## Source-Contract Transition History Protocol

The runtime release ledger for a completed city source conversion is
`state/source_contract_quarantine.json` (runtime-local, not git-tracked). A
successful `scripts/watch_source_contract.py --release-city <CITY>
--release-evidence <PATH>` writes the released city entry plus a
`transition_history[]` record. The record must answer:

- when the mismatch was detected and released (`detected_at`, `released_at`)
- which market dates were affected (`affected_target_dates`)
- which event IDs provided Polymarket evidence (`event_ids`)
- what the city changed from and to (`from_source_contract`,
  `to_source_contract`)
- which conversion actions were completed, with per-field evidence references
  (`completed_release_evidence`)

For the Paris test case, the pending expected ledger shape is a
`same_provider_station_change` from `wu_icao` / `LFPG` to `wu_icao` / `LFPB`
covering the active Paris high/low markets first observed in the
2026-04-30T00:23:50Z monitor run. The city must not leave quarantine until the
history record contains refs for `config_updated`, `source_validity_updated`,
`backfill_completed`, `settlements_rebuilt`, `calibration_rebuilt`, and
`verification_passed`.

## Invalidation Conditions

Re-audit before relying on this file if:

- any provider stalls, advances unexpectedly, or changes endpoint behavior
- market description/source text changes
- `config/cities.json` source routing changes
- Hong Kong/HKO is in scope
- Paris source-contract monitor returns `MATCH` after full conversion evidence,
  or Polymarket changes Paris station/source again
- the file is older than Max staleness and the task needs current source truth

## Stale Behavior

If stale, this file may be used only as historical planning context. It must
not justify settlement source routing, endpoint/source equivalence, or
Hong Kong current truth. Record `needs fresh source audit` and stop before
implementation that depends on current source validity.
