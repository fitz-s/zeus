# P2 ledger seam — hard findings + exact wiring plan

- Created: 2026-05-29
- Author: session (Opus), context burned on the hard forecast↔settlement seam per operator
- Scope: `scripts/build_ens_residual_evidence.py::build_evidence` (the residual-formation seam)
- Status: 4 defects found (2 masked-live), 2 antibodies built+committed, wiring plan below

---

## Defects found at the seam (live code, verified against the live DB)

**D-U1 — masked degC/degF settlement-conversion corruption (Cons-SEV-1.C, CONFIRMED).**
`build_ens_residual_evidence.py:204` and `:224` compute the settlement in °C via
`_to_celsius(e["settlement_value_c"], e["members_unit"])` — converting the SETTLEMENT with
the ENSEMBLE's `members_unit`. Correct ONLY when both sides share a unit. Verified: for
Chicago mx2t3 HIGH, members_unit=`degF` (member values ~72-79) and settlement=`77.0` (°F) —
they coincide, so it works today. But units ARE mixed across sources (OpenData members
`degF`; the provenance contract docstring claims `degC`; WU settlements °F). Any city with
`degC` members + °F settlement yields a residual ~50°C wrong, silently.
→ FIX: `residual_value.residual_celsius` (committed `10fe7f5a31`) converts each side by its
own unit. Replace the `_to_celsius(settle, members_unit)` calls.

**D-J1 — loose JOIN collapses settlement target (lineage bug).**
`build_evidence` JOINs `settlements_v2 ON s.city=e.city AND s.target_date=e.target_date AND
s.temperature_metric=e.temperature_metric` (`:129-131`) — NO station, NO source/authority,
NO unit. A forecast is paired to a settlement of a different station/authority whenever a
city has >1. Masked today (one WU station + one authority per city) but structurally the
exact lineage collapse the redesign kills.
→ FIX: route pairing through `residual_key.pair_residual` (target-gated on station/unit/
authority); requires exposing both sides' identity (below).

**D-S1 — settlement schema lacks unit + station columns (provenance gap).**
Canonical `settlement_outcomes` (v2_schema.py:33) columns: city, target_date, metric,
settlement_value, settlement_source, settled_at, authority, provenance_json. **No
`settlement_unit`, no `settlement_station`.** Station is buried in the `settlement_source`
URL (`.../chicago/KORD`); unit is convention (WU ICAO US → °F); authority is in
`provenance_json.data_version` (`wu_icao_history_v1`). So the settlement's true unit/station
cannot be cross-checked against the forecast's claim from columns alone.
→ RECOMMENDATION: add `settlement_unit` + `settlement_station` columns to settlement_outcomes
(harvester writes them), so SettlementObject.from_settlement_row can VERIFY rather than
trust the snapshot's claim. Until then, the contract converts using the snapshot's
`settlement_unit` claim and the gap is documented (not silently assumed).

**D-M1 — members_unit is mixed across sources (latent landmine).**
OpenData mx2t3 rows carry `members_unit=degF`; the provenance contract docstring asserts the
pipeline "stores and compares in degC". Per-row unit handling is therefore mandatory; any
code path that assumes a single global members unit corrupts silently.

---

## Antibodies built this session (committed)

- `src/contracts/forecast_target.py` — ForecastTarget + assert_same_target (target identity).
- `src/contracts/forecast_object.py` — ForecastObject.from_snapshot_row (fail-closed RV parse).
- `src/contracts/residual_key.py` — pair_residual (target-gated) + source_kind_for_data_version
  (derived lineage, fixes the hardcoded 'prior').
- `src/contracts/residual_value.py` — residual_celsius (own-unit conversion, fixes D-U1).

30 tests, all RED-verified before impl.

---

## Exact P2 wiring plan for build_ens_residual_evidence (the application)

1. **SELECT**: add `e.settlement_unit`, `e.settlement_station_id`, `e.settlement_source_type`
   (the forecast's settlement CLAIM — already columns on ensemble_snapshots).
2. **source_kind** (`:227`): `"prior"` → `source_kind_for_data_version(e["data_version"])`.
3. **unit** (`:204/:224`): `_to_celsius(settle, members_unit)` →
   `residual_celsius(members, members_unit, settlement_value, e["settlement_unit"])`.
4. **target gate**: build `ForecastObject.from_snapshot_row(e)` + a `SettlementObject`
   (target from the settlement side: station parsed from `settlement_source` URL, unit from
   the snapshot claim, authority from `provenance_json.data_version`), then `pair_residual`
   — raises on a loose-join mismatch (D-J1). Emit the ResidualKey dims (product/cycle/lead).
5. **window provenance** (C1b): add `forecast_window_start_utc/end_utc` (already on snapshot)
   + `source_run_id`, `available_at` (available_at already selected) to the row.
6. **naming** (#16): script uses stale `ensemble_snapshots_v2` / `settlements_v2`; canonical
   is `ensemble_snapshots` / `settlement_outcomes`. Sweep when building on pr3-schema-stable.

Relationship test to add (CLAUDE.md cross-module invariant): a forecast row + its TRUE
settlement row → residual forms in a consistent unit with matched target; a forecast row +
a wrong-station/wrong-date settlement → pair_residual raises. On real column shapes.

---

## Note
D-S1 (settlement schema gap) is the deepest item — it means the antibody can currently only
enforce CONVERSION consistency (the snapshot's claimed unit), not VERIFY the settlement's
true unit, because settlement_outcomes doesn't store it. Adding the columns closes the gap.
This is a settlement-side schema migration for the redesign, separate from the ledger wiring.
