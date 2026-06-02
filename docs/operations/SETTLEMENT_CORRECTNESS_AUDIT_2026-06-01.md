# Settlement-Correctness Audit — q-vs-resolution divergence class

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: operator read-only audit directive (settlement-correctness class, sibling to the Paris day0 wrong-side bug); boot profile zeus-task-boot-settlement-semantics; Fitz Constraint #1 (make the category impossible) + #4 (data provenance).
- Scope: every axis where Zeus's probability `q = P(outcome ∈ bin B | our assumed settlement rule)` can be computed under a DIFFERENT rule than the market actually resolves on, producing a wrong-bin / wrong-unit / wrong-side trade on a KNOWN/edge market.
- Explicitly OUT of scope (another agent owns it): day0 time-validity / observed-so-far masking correctness.

---

## 12-line executive summary

The five resolution axes (unit / bin-semantics / rounding / station / metric) are all CORRECT at the
data layer today: SF/Seattle members are stored in °F (`members_unit='degF'`, `settlement_unit='F'`,
values 55–65 for a June high), °C cities in °C, and across the entire `ensemble_snapshots` table there
are **zero rows where a populated `members_unit` disagrees with a populated `settlement_unit`** and
**zero rows with both unit fields null**. The bin vocabulary parser correctly maps all three market
phrasings (range "X-Y°F", shoulder "X or higher/below", point "be X°C"). The `Bin` type carries unit
explicitly, enforces °F-width-2 / °C-width-1, and fail-closes on label/unit mismatch. Rounding is
unit-polymorphic WMO half-up (HKO truncation isolated by city). Station and metric are gated by the
same `City` object on both the forecast and settlement sides.

The defects are STRUCTURAL, not present-instance: the live q-computation seam consumes member values
and bins on the FAITH that ingest aligned their units — there is **no runtime assertion that
`snapshot_unit == bin.unit == city.settlement_unit`** at the point q is computed. `_snapshot_unit()` is
called and its return **discarded** (`event_reactor_adapter.py:3456`); `MarketAnalysis._settle` rounds
in `self._unit` then tests `bin.contains()` with no unit cross-check (`market_analysis.py:357,711,901`);
`p_raw_vector_from_maxes` documents "members already in settlement unit" as a docstring promise, not a
checked invariant (`ensemble_signal.py:199`). The only active guard is the `Bin.__post_init__` label
cross-check, which fires **only when the label string literally contains a °-symbol** — a silent ingest
unit-swap that also mislabels (or omits the °) would pass. Today the upstream ingest is correct so no
live wrong-trade is occurring on the unit axis; the risk is a future ingest regression (new city, source
swap, Kelvin leak) silently inverting q with no fail-closed tripwire. Severity is ranked below by the
blast radius of the wrong-trade each gap would cause if its upstream guarantee broke.

---

## Coverage statement

| Axis | Verdict | Live evidence checked | Structural gap |
|------|---------|----------------------|----------------|
| 1. UNIT | CORRECT (data) / WEAK (code invariant) | SF/Seattle/intl snapshots, full-table consistency scan | No `snapshot_unit==bin.unit==city.unit` assertion at q seam |
| 2. BIN SEMANTICS | CORRECT | `_parse_temp_range` against all 3 live phrasings | Unit dropped during parse, re-attached from city (mostly a strength) |
| 3. ROUNDING | CORRECT | `round_values` dispatch, HKO isolation, analytic-CDF preimage | none material |
| 4. STATION | CORRECT (code) / CONFIG-RISK (provenance) | `_station_matches_city`, `_expected_settlement_station_id` | airport≠settlement-station is a config-truth question, not a code divergence |
| 5. METRIC | CORRECT | snapshot `physical_quantity`/`observation_field` by metric, `_validate_snapshot_members_metric_identity` | metric validated; **unit NOT validated in the same gate** |

This is not an empty-findings pass: every axis was traced to live DB rows and the exact q-computation
source lines. The findings are "structurally under-guarded but presently correct", with one MEDIUM and
two LOW structural defects plus one config-provenance watch item.

---

## Axis 1 — UNIT  ·  VERDICT: data CORRECT, code invariant WEAK (DEFECT U1, severity MEDIUM)

### What I proved correct
- `config.py:351,360` — `City.settlement_unit` comes from required `cities.json` `"unit"` field. SF=F,
  Seattle=F, Miami=F, NYC=F; Tokyo/Shanghai/London/Paris/TelAviv=C; HongKong=C (hko).
- DB ground truth (`state/zeus-forecasts.db`, `ensemble_snapshots`): SF/Seattle live snapshots carry
  `members_unit='degF'`, `settlement_unit='F'`, member values 55.7–65.2 — physically °F for a June SF/SEA
  high. A °C interpretation (55–65 °C) is impossible, so the stored members are genuinely °F.
- Full-table scan: **0** rows where both `members_unit` and `settlement_unit` are populated and disagree;
  **0** rows where both are null. Recent (≥2026-05-28) live snapshots all carry both unit fields, aligned.
- The bias-correction unit handling IS correct: `event_reactor_adapter.py:3420-3426` converts the stored
  `effective_bias_c` (°C) to native unit via `×1.8` for F-cities before subtracting from °F members. This
  is the only place a °C→°F conversion is needed in the live q path, and it is present and correct.

### The structural defect (U1)
The member values and the bins are combined **without any runtime assertion that their units agree**.
The unit alignment is an upstream-ingest GUARANTEE, surfaced only as a docstring contract:

- `src/signal/ensemble_signal.py:199` — docstring: `member_maxes: ... already in city.settlement_unit`.
  `p_raw_vector_from_maxes` (line 256-258) calls `settlement_semantics.round_values(noised)` then
  `bin_counts_from_array(measured, bins)` with **no check** that `members` unit == `bins[*].unit`.
- `src/engine/event_reactor_adapter.py:3456` — `_snapshot_p_raw` calls `_snapshot_unit(snapshot, payload)`
  but **discards the return value** (no assignment). It validates that *a* unit exists; it never asserts it
  equals `city.settlement_unit` or the bin unit.
- `src/engine/event_reactor_adapter.py:3240` — `MarketAnalysis(... unit=_snapshot_unit(snapshot, payload) ...)`
  passes the snapshot unit to `MarketAnalysis`, but `family.bins` carry an **independently-derived** unit
  (`_settlement_unit_for_payload_city`, line 3899-3921, from the city contract). Two independent unit
  derivations that are never compared.
- `src/strategy/market_analysis.py:357,711,901` — `_settle(noised)` rounds in `self._unit`; `_bin_probability`
  tests `bin.contains()`. `self._unit` and `bin.unit` are never asserted equal.

The **only** active guard is `src/types/market.py:64-72` (`Bin.__post_init__`): raises if the label string
contains "°F" but `unit!='F'` (or vice-versa). This is real protection — and `event_reactor_adapter.py:3905`
explicitly documents it caught a prior "default-to-F mislabelled every Celsius bin" regression — but it is
**label-string-dependent**: a bin whose label omits the °-symbol, or an ingest swap that mislabels members
without touching the bin label, slips through silently and inverts q.

### The exact wrong-trade U1 causes (if upstream guarantee breaks)
A new city, a source-family swap, or a Kelvin/°C leak that writes `members_json` in the wrong unit while
`settlement_unit` / bin labels stay correct → `round_values` rounds the wrong-unit members, `bin.contains`
tests them against correct-unit boundaries → q collapses to the wrong bins. E.g. °C members (max ~18) tested
against °F bins (60-65) → all mass lands in the left shoulder → Zeus sees q≈1.0 on "65°F or below", buys YES
on a bin the market resolves NO. This is exactly the Paris-class wrong-side trade, on a KNOWN market, at
size. Today this is dormant because ingest is correct; it has **no fail-closed tripwire**.

### Structural fix design (make the category impossible)
A single typed assertion at the q seam, fail-closed, comparing all three unit sources:
1. In `_market_analysis_from_event_snapshot` (before constructing `MarketAnalysis`,
   `event_reactor_adapter.py:~3230`): assert
   `_snapshot_unit(snapshot, payload) == SettlementSemantics.for_city(city).measurement_unit ==
   {b.unit for b in bins}` (single-element set). Raise `FORECAST_SETTLEMENT_UNIT_DIVERGENCE` on any
   mismatch. This converts the discarded `_snapshot_unit()` call into a load-bearing gate.
2. Stronger (Fitz #1): make `member_maxes` a typed `FahrenheitArray | CelsiusArray` (extend
   `src/types/temperature.py`) and have `p_raw_vector_from_maxes` accept only the type matching
   `bins[0].unit`, so mixing is unconstructable at the call site rather than caught at runtime. The
   NewType scaffolding already exists (`CelsiusDecimal`); extend it to the member-array path.
3. Carry `members_unit` through `_snapshot_from_join` (`forecast_snapshot_ready.py:549-563` currently
   DROPS `members_unit`/`settlement_unit` from the projected dict) so the FSR-emit path cannot lose the
   provenance field the assertion depends on.

---

## Axis 2 — BIN SEMANTICS  ·  VERDICT: CORRECT

### Proof
`src/data/market_scanner.py:3983 _parse_temp_range` correctly handles all three live phrasings (confirmed
against real `event_slug`s "highest/lowest-temperature-in-CITY-on-DATE" and the live pool's three forms):
- Range "X-Y°F" / en-dash → `(X, Y)` (line 3991-3993).
- Shoulder "X°F or below/lower" → `(None, X)`; "X°F or higher/above/more" → `(X, None)` (3996-4003).
- Point "X°C" anchored / "be X°C on …" → `(X, X)` (4007-4027).
The "or higher/below" branches run BEFORE the point branch, so "26°C or higher" is never mis-parsed as a
point bin. The strict round-trip companion `_parse_canonical_bin_label` (4062) uses `fullmatch` to reject
trailing-garbage labels (the NH-E1 "17°Cfoo" antibody).

Side/inversion check (labels_swapped): across 20,000 live `executable_market_snapshots`, `labels_swapped`
is `False` in 100% of rows, and `outcome_label` matches `selected_outcome_token_id`'s YES/NO side in every
sampled row. The token-side → direction mapping is consistent; no side inversion observed.

The unit is intentionally NOT captured by `_parse_temp_range` (it matches `°[FfCc]` but discards the
letter) and is re-attached from `city.settlement_unit` at bin construction. This is mostly a STRENGTH: it
means a market-text/city-config unit disagreement is caught by the `Bin` label cross-check
(`market.py:69-72`) rather than silently trusted. The residual weakness is the same as U1 (label must
contain the °-symbol for the cross-check to fire).

---

## Axis 3 — ROUNDING  ·  VERDICT: CORRECT

### Proof
- `src/contracts/settlement_semantics.py:115-144 round_values` dispatches: `wmo_half_up`→`floor(x+0.5)`,
  `oracle_truncate`/`floor`→`floor(x)`, `ceil`→`ceil(x)`. WMO half-up is asymmetric-toward-+∞ (−3.5→−3),
  matching the legacy `round_wmo_half_up_values` byte-for-byte (the SIDECAR-3/batch_C fix that replaced a
  silently-divergent `Decimal ROUND_HALF_UP`).
- HKO truncation is isolated to Hong Kong via `for_city()` dispatch (line 221-233) and, in the type-encoded
  path, by a `TypeError` if `HKO_Truncation` is used for a non-HK city (`settle_market`, line 379-388). The
  empirical basis (floor 14/14 vs half-up 5/14 on HKO settlement days) is documented inline.
- The same `round_values` is used by BOTH the q side (`ensemble_signal.py:256`, MC) /
  `analytic_p_raw_vector_from_maxes` (preimage `[t−0.5, t+0.5)` for half-up — line 286) AND the
  settlement-write side (`harvester.py:1458 SettlementSemantics.for_city(city).assert_settlement_value`).
  q and settlement share one rounding authority — no boundary off-by-one between model and resolution.

No material defect. (Minor: the analytic preimage edges assume precision=1.0, which the docstring
acknowledges is universal across current markets; a future 0.1°-precision market would need the preimage
scaling verified, but none exists today.)

---

## Axis 4 — STATION  ·  VERDICT: code CORRECT, provenance is a config-truth watch item

### Proof
- `src/execution/harvester.py:455 _expected_settlement_station_id` → `city.wu_station` (HKO→"HKO"), and
  `_station_matches_city` (461) rejects any observation row whose `station_id` ≠ the city's expected
  station before it can become settlement truth. `_lookup_settlement_obs` (471) additionally routes by
  `settlement_source_type` (wu_icao→`wu_icao_history`, noaa→`ogimet_metar_%`, hko→`hko_daily_api`,
  cwa_station→quarantine) and requires `authority='VERIFIED'`.
- The forecast side targets the same `City` (lat/lon → forecast grid) and calibration pairs forecast vs the
  SAME settlement observation family, so q and settlement reference the same physical station-by-construction.

### Watch item (not a code divergence)
Whether `city.wu_station` (an ICAO airport) is genuinely the station Polymarket's resolver reads is a
DATA-PROVENANCE question (`city_truth_contract.yaml` caution `airport_station_not_settlement_station`;
fatal-misread `airport_station_not_city_settlement_station`). It cannot be settled from code — it requires
a dated per-city resolution-source audit against the market resolver text. This is correctly flagged as a
known caution surface; it is not introduced or worsened by the q-computation path.

---

## Axis 5 — METRIC  ·  VERDICT: CORRECT

### Proof
- Snapshot `physical_quantity` carries the extremum identity: `high`→`mx2t{3,6}_local_calendar_day_max`
  + `observation_field='high_temp'`; `low`→`mn2t{3,6}_local_calendar_day_min` + `low_temp`. The window is
  the LOCAL calendar day (`forecast_window_start_local`/`_end_local`, `local_day_start_utc` columns
  present and populated).
- `src/engine/event_reactor_adapter.py:3623 _validate_snapshot_members_metric_identity` fail-closes the
  live decision if snapshot metric ≠ family metric (`FORECAST_MEMBERS_METRIC_IDENTITY_MISMATCH`), and
  `_members_extrema_transform` (3632) maps high→daily_max / low→daily_min explicitly.
- The market metric is carried in the slug ("highest-…"/"lowest-…") → `temperature_metric`, matched against
  the snapshot at bind time.

### Adjacent gap (folds into U1)
`_validate_snapshot_members_metric_identity` validates the METRIC identity but NOT the UNIT identity in the
same gate. The natural fix is to extend this existing fail-closed gate to also assert unit alignment
(see U1 fix #1) — it is already the right chokepoint, already runs on every live decision, and already has
the snapshot + family + city in scope.

---

## Defect ranking by wrong-trade severity

| ID | Axis | Severity | Wrong-trade if upstream guarantee breaks | Present-instance status |
|----|------|----------|------------------------------------------|-------------------------|
| **U1** | Unit invariant | **MEDIUM** | wrong-unit members → q lands in wrong bins → wrong-SIDE buy on KNOWN market at size (Paris-class) | DORMANT — ingest currently correct, but no fail-closed tripwire |
| **U2** | FSR snapshot projection drops `members_unit`/`settlement_unit` (`forecast_snapshot_ready.py:549-563`) | **LOW** | weakens the provenance field U1's assertion would rely on; on the FSR-emit path the unit must be re-derived from city, re-coupling two derivations | latent; not wrong-trading today |
| **U3** | Label-only Bin cross-check (`market.py:69-72`) | **LOW** | a unit swap that also drops/omits the °-symbol in the label bypasses the only active guard | latent; live labels currently carry the °-symbol |
| **W1** | Station provenance (airport vs resolver station) | **WATCH** | forecasting/settling a different physical station than the market resolves on → systematic bias, not a clean side-flip | config-truth audit item, pre-existing known caution |

MEDIUM (not HIGH) for U1 because the present data is verified correct and the failure requires an upstream
regression; but it is the highest-blast-radius gap because the failure mode is a silent wrong-SIDE trade on
a market Zeus believes it KNOWS, with no fail-closed stop.

---

## RED relationship-tests (one per defect — write BEFORE any fix; each must FAIL today)

These are CROSS-MODULE invariant tests (Fitz: "test relationships, not functions"). They assert a property
that holds when Module A's output (snapshot members / bins) flows into Module B (q computation).

### RT-U1 — q seam must reject unit divergence (currently NO such assertion → test RED)
```python
# tests/engine/test_edli_settlement_unit_divergence.py
# Relationship: snapshot members_unit  ⟷  family.bins[*].unit  ⟷  city.settlement_unit
def test_market_analysis_rejects_unit_divergence():
    # SF city (settlement_unit='F'); build a snapshot whose members are °C-scaled (max~18)
    # and a family of °F bins (60-65°F). The current code computes q silently; assert it RAISES.
    city = runtime_cities_by_name()["San Francisco"]
    snapshot = make_snapshot(city="San Francisco", members=[16,17,18,17,16],  # °C values
                             members_unit="degC", settlement_unit="C")          # wrong-for-SF unit
    family = make_family(city="San Francisco", bins=fahrenheit_bins(60,65))     # °F bins
    with pytest.raises(ValueError, match="FORECAST_SETTLEMENT_UNIT_DIVERGENCE"):
        _market_analysis_from_event_snapshot(calibration_conn=cal, snapshot=snapshot,
                                             family=family, native_costs={}, payload=pl,
                                             decision_time=now)
    # RED today: no assertion exists; _snapshot_unit() return is discarded → q is computed on mismatch.
```

### RT-U1b — q must invert when members are unit-swapped (proves the wrong-trade is real)
```python
def test_unit_swap_inverts_q_side():
    # Same °F bins; correct °F members (max 64) → q mass on the 64-65 bin (buy_yes side).
    q_correct = p_raw_for(members=[63,64,64,65], unit="F", bins=fahrenheit_bins(60,65))
    # °C members (max 18) tested against °F bins → all mass in left shoulder (≤60), q inverts.
    q_swapped = p_raw_for(members=[16,17,18,17], unit="F", bins=fahrenheit_bins(60,65))
    assert argmax(q_correct) != argmax(q_swapped)        # side flips
    assert q_swapped[left_shoulder_idx] > 0.9            # collapses to wrong bin
    # This test PASSES today (documents the live hazard); RT-U1 is the one that must start RED.
```

### RT-U2 — FSR snapshot projection must preserve unit provenance (currently dropped → RED)
```python
# tests/events/test_fsr_snapshot_preserves_unit.py
def test_snapshot_from_join_carries_units():
    row = fsr_join_row(members_unit="degF", settlement_unit="F", members_json="[64.0,65.0]")
    snap = _snapshot_from_join(row)
    assert snap["members_unit"] == "degF"      # RED: _snapshot_from_join omits members_unit
    assert snap["settlement_unit"] == "F"      # RED: and settlement_unit
```

### RT-U3 — Bin must reject unit mismatch even with a °-less label (currently only label-string guard → RED)
```python
# tests/types/test_bin_unit_guard_label_independent.py
def test_bin_rejects_unit_mismatch_without_degree_symbol():
    # A bin whose label has NO °-symbol must still not silently accept members of the wrong unit.
    # Today Bin only cross-checks when the label literally contains '°F'/'°C'.
    b = Bin(low=64, high=65, unit="F", label="64-65")    # no ° symbol — constructs fine today
    # Relationship guard we want: a degC member array routed to an F bin is rejected at p_raw build.
    with pytest.raises(ValueError):
        p_raw_vector_from_maxes(np.array([16.0,17.0,18.0]), sf_city, F_semantics, [b, *grid])
    # RED today: p_raw_vector_from_maxes has no member-unit/bin-unit relationship check.
```

### RT-METRIC+UNIT — extend the existing metric-identity gate to unit (currently metric-only → RED on unit)
```python
# tests/engine/test_snapshot_metric_and_unit_identity.py
def test_metric_identity_gate_also_validates_unit():
    snap = make_snapshot(temperature_metric="high", settlement_unit="C")
    family = make_family(metric="high", city="San Francisco")   # SF is °F
    with pytest.raises(ValueError, match="UNIT_IDENTITY_MISMATCH"):
        _validate_snapshot_members_metric_identity(snapshot=snap, family=family, payload=pl)
    # RED today: the gate checks metric only; unit divergence passes.
```

---

## Bottom line

No live wrong-side trade is occurring on the unit/bin/rounding/metric axes today — the data is verified
aligned and the `Bin` label cross-check has already caught one historical regression. The exposure is the
ABSENCE of a fail-closed unit-identity assertion at the q-computation seam: q is computed on the FAITH that
ingest aligned member-unit, bin-unit, and city-unit, with the °-symbol-dependent `Bin` label check as the
only tripwire. The fix is to convert the already-present-but-discarded `_snapshot_unit()` call into a
load-bearing three-way assertion (and, durably, to type the member array by unit so the mismatch is
unconstructable). Station provenance (airport vs resolver) remains a separate, pre-existing config-audit
watch item, not a code divergence.
