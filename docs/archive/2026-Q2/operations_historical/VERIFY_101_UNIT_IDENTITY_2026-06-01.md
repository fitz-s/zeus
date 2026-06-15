# VERIFY #101 — settlement-unit-identity gate (commit 7deb2d2608)

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: read-only verification of fix #101 / SETTLEMENT_CORRECTNESS_AUDIT_2026-06-01.md Axis-1 U1 (RT-U1). Verifier role; NO code edits, NO git ops.
- Subject: `_assert_settlement_unit_identity` (3-way gate) + its load-bearing call in `_market_analysis_from_event_snapshot`, at `src/engine/event_reactor_adapter.py`.

---

## VERDICT: CORRECT

The #101 gate is correctly placed, correctly fail-closed, sits on the single live q-seam through which BOTH the no-submit receipt path and the canonical-FDR path flow, and produces NO false positive on any currently-aligned live city — including the HKO/Hong Kong case the directive flagged. The audit's hypothetical concern about `city.settlement_unit` vs `SettlementSemantics.for_city().measurement_unit` is moot: for Hong Kong both equal `"C"` (the HKO special case changes only `rounding_rule`, never the unit). RT-U2 and RT-U3 are real but out-of-scope residuals that DO NOT undermine #101 — the live q-seam snapshot is sourced by `SELECT *` and carries the unit fields, so RT-U2's lossy `_snapshot_from_join` projection never reaches the gate; and missing unit fails-closed via `FORECAST_UNIT_AUTHORITY_MISSING` rather than silently passing.

---

## Per-item findings

### Item 1 — CORRECTNESS of the 3-way + the HKO question · OK

- The gate (`event_reactor_adapter.py:3256-3281`) computes `snapshot_unit = _snapshot_unit(...)`, `city_unit = city.settlement_unit`, `bin_units = {b.unit for b in bins}`; raises `FORECAST_SETTLEMENT_UNIT_DIVERGENCE` unless `len(bin_units)==1` and `snapshot_unit == city_unit == bin_unit`. Returns the single agreed unit, fed verbatim into `MarketAnalysis(unit=unit, ...)` at `:3366`. This kills the pre-fix defect where the `_snapshot_unit()` return was discarded and `MarketAnalysis.unit` / `bins` came from two un-compared derivations.
- **HKO / Hong Kong — NO false divergence (the flagged risk does NOT materialize):** Confirmed at runtime: `runtime_cities_by_name()["Hong Kong"].settlement_unit == "C"`, `settlement_source_type == "hko"`, and `SettlementSemantics.for_city(HK).measurement_unit == "C"` with `rounding_rule == "oracle_truncate"`. `settlement_unit == measurement_unit == "C"` (identical). The HKO branch in `settlement_semantics.py:221-233` diverges ONLY on `rounding_rule`; `measurement_unit` is hard-set `"C"`, matching `City.settlement_unit`. So the gate's use of raw `city.settlement_unit` (rather than `measurement_unit`) is equivalent for HK — it does NOT fail-close a legit HK trade. Using `measurement_unit` would be a no-op refactor, not a fix.
- Live HK rows (≥2026-05-28): 114 snapshots, all `members_unit=degC, settlement_unit=C`; HK °C bins are width-1 point bins → `bin.unit="C"`. Three-way "C"==. PASS.

### Item 2 — NO false positive on live cities · OK

- Full live-table scan (≥2026-05-28, `state/zeus-forecasts.db`): every city groups to a single aligned `(members_unit, settlement_unit)` pair — `degF/F` for SF, Seattle, Miami, NYC, Chicago, LA, Atlanta, etc.; `degC/C` for Tokyo, Shanghai, London, Paris, Hong Kong, and all intl. **Zero** divergent rows (`members_unit=degC ∧ settlement_unit≠C`, or the °F mirror). **Zero** recent rows with both unit fields NULL/empty.
- `_snapshot_unit` (`:3729-3738`) precedence: returns `snapshot.settlement_unit` (or `.unit`) first when in `{F,C}`; only falls back to `members_unit` (`degC→C`, `degF→F`) when settlement_unit is absent. On live rows `settlement_unit` is populated, so the gate compares `settlement_unit` directly against `city.settlement_unit` and `bin.unit` — same token, all aligned. The 6-case test suite passes (`test_edli_settlement_unit_divergence.py` 6/6), and the 74-test no-bypass suite passes with the aligned seam unaffected. No legit live trade is newly blocked.
- (Historical-only note, not a #101 concern: ~769k OLD rows carry empty `settlement_unit` with a present `members_unit`; the `_snapshot_unit` fallback maps those correctly, and none are in the recent live window.)

### Item 3 — COVERAGE of the live q path · OK (single chokepoint, no bypass)

- The gate sits in `_market_analysis_from_event_snapshot` at `:3312`, BEFORE `_snapshot_p_raw`/`_snapshot_p_cal` (`:3318/:3322`) and BEFORE `MarketAnalysis` construction. It is the ONE assembly point.
- Both production q entry points converge here:
  - **No-submit receipt path:** `build_event_bound_no_submit_receipt` → `_generate_candidate_proofs` (`:650`) → `_live_yes_probabilities` (`:2947`) → `_canonical_probability_and_fdr_proof` (`:2967`) → `_market_analysis_from_event_snapshot` (`:3076`) → gate.
  - **Canonical/Day0 path:** `_canonical_probability_and_fdr_proof` for both `FORECAST_SNAPSHOT_READY` and `DAY0_EXTREME_UPDATED` (`:2967`, `:2976`) → same `:3076` call → gate.
- **No bypass:** `_snapshot_p_raw`/`_snapshot_p_cal` are module-private and, in `src/`, are called ONLY from inside `_market_analysis_from_event_snapshot` (the `:3318/:3322` lines). All other repo hits for the `p_raw` token are differently-named symbols — `_store_snapshot_p_raw`/`get_snapshot_p_raw` (evaluator/harvester persistence) and the `backfill_tigge_snapshot_p_raw_v2.py` script — NOT the live q kernel. Direct `_snapshot_p_raw` calls exist only in tests. So there is no production q-compute path that bypasses the gate. The U1 hole is FULLY closed on the live decision path.
  - Minor (non-blocking): `_snapshot_p_raw` still calls `_snapshot_unit(...)` and discards the return at `:3582` — harmless dead remnant now that the gate owns the assertion upstream; not a defect.

### Item 4 — RT-U2 / RT-U3 residuals · OK (real but do not reach the #101 seam; fail-closed if they did)

- **RT-U2 (`_snapshot_from_join` drops `members_unit`/`settlement_unit`):** Confirmed — `forecast_snapshot_ready.py:549-563` projects a dict WITHOUT the two unit fields. BUT this dict is the FSR-**emit** path (`forecast_snapshot_ready.py:348`, builds `OpportunityEvent`s); it is NOT the q-seam snapshot. The live q-seam snapshot is fetched by `_forecast_snapshot_row_for_event` (`event_reactor_adapter.py:3173`) via `SELECT *` (`:3211`), so it carries `settlement_unit`/`members_unit` straight from `ensemble_snapshots`. The gate therefore always has real unit data on the live path. RT-U2 is a latent provenance-hygiene residual, correctly scoped OUT of #101.
- **Fail-closed-if-missing check:** Were a unit-less snapshot ever to reach the gate, `_snapshot_unit` (`:3729-3738`) raises `FORECAST_UNIT_AUTHORITY_MISSING` when neither `settlement_unit`/`unit` nor a recognizable `members_unit` is present. That exception propagates out of `_assert_settlement_unit_identity` → `_market_analysis_from_event_snapshot` → caught as `LIVE_INFERENCE_INPUTS_MISSING` (no-submit) / decision rejection. It does NOT silently pass. So even the RT-U2 hazard degrades to fail-closed, not wrong-trade.
- **RT-U3 (label-only `Bin` cross-check):** Confirmed the `Bin.__post_init__` guard (`market.py:69-72`) only fires when the label literally contains `°F`/`°C`. This is a real residual for the `Bin` type in isolation. BUT #101 makes it non-load-bearing on the q seam: the gate compares `bin.unit` (the explicit field, not the label string) against snapshot+city, so a °-less mislabel cannot slip a wrong-unit bin past the q seam — `bin.unit` itself must equal the agreed unit. RT-U3 remains a worthwhile defense-in-depth hardening for non-seam `Bin` construction, correctly scoped OUT of #101.

### Item 5 — Mixed-bin / empty-bin handling · OK (fail-closed)

- Mixed bin units: `len(bin_units) != 1` → raise (`:3268-3273`). Covered by `test_mixed_bin_units_raise` (passing).
- Empty bins: `{b.unit for b in []} == set()` → `len == 0 != 1` → raise (same branch). Fail-closed.
- Single-unit bins that disagree with snapshot/city → caught by the `snapshot_unit == city_unit == bin_unit` equality (`:3275`). Covered by `test_snapshot_unit_diverges...` and `test_bins_unit_diverges...` (passing).

---

## 5-line verdict

1. CORRECT. The 3-way `snapshot==city=={bins.unit}` gate is right, fail-closed, and on the single live q-seam (no production bypass of `_snapshot_p_raw`/`_snapshot_p_cal`).
2. HKO is NOT a false-positive risk: Hong Kong `city.settlement_unit == measurement_unit == "C"`; HKO changes only `rounding_rule`. Raw `city.settlement_unit` is equivalent — switching to `measurement_unit` is cosmetic, not required.
3. No false positive on live cities: full ≥2026-05-28 scan shows every city unit-aligned (degF/F, degC/C), zero divergent and zero double-null recent rows; 6/6 + 74-test no-bypass suites green.
4. RT-U2/RT-U3 are real but out-of-scope residuals that do NOT reach or weaken the #101 seam (q snapshot is `SELECT *` so units survive; missing units fail-closed via `FORECAST_UNIT_AUTHORITY_MISSING`); they are defense-in-depth, not a #101 gap.
5. Ship as-is. Optional follow-ups (separate from #101): land RT-U2 unit carry-through in `_snapshot_from_join`, RT-U3 label-independent `Bin` guard, and delete the now-dead discarded `_snapshot_unit()` call at `:3582`.
