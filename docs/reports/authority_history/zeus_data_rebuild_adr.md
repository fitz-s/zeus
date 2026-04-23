# Zeus Data Rebuild — Architecture Decision Record (ADR)

**File:** `docs/authority/zeus_data_rebuild_adr.md`  
**Phase:** K4 (Authority Reset + Fresh Rebuild)  
**Source:** `.omc/plans/data-rebuild.md` Section 3, verbatim

---

## Decision

- **K1:** Introduce a typed `ObservationAtom` frozen dataclass + `IngestionGuard` with 5-layer write-time validation, making malformed observations unconstructable rather than caught at runtime.
- **K2:** Replace the fixed UTC 12:00 WU collection trigger with a per-city physical-clock schedule (`peak_hour + 4h` local time, DST-aware), so collection always captures the full local day.
- **K3:** Collapse cluster identity to city identity (cluster := city name for all 46 cities), replace the 30-cluster hand-guessed correlation matrix with a data-driven Pearson + haversine fallback, and remove `max_region_pct` as a risk dimension.
- **K4:** Add `authority ∈ {VERIFIED, UNVERIFIED, QUARANTINED}` to all 5 world-data tables; downgrade existing WU/derived rows to UNVERIFIED; preserve TIGGE ensemble_snapshots as VERIFIED; gate the computation chain (evaluator + market_fusion) with hard asserts; rebuild settlements, calibration pairs, and Platt models from VERIFIED sources only.

---

## Drivers

1. **One packet at a time (INV-10 + packet discipline).** The dependency graph K1 → {K2 ∥ K3} → K4 is a hard sequence. Each packet is an atomic authority-bearing unit. No cross-packet leakage.

2. **Structural decisions over patches (Fitz methodology).** 5 P0 contaminations + cluster degradation = 4 structural decisions (K << N). Patching each symptom independently produces new bugs at patch boundaries.

3. **Data provenance as machine-readable contract (INV-03 + INV-04).** Every row's trustworthiness must be readable by code, not just known by humans. `authority` column is the machine-checkable provenance signal.

---

## Alternatives Considered

### Why not patch the WU unit bug in place (`wu_daily_collector.py` heuristic)?

The heuristic approach — "if city is Celsius and value > threshold, flip" — is ambiguous for transitional seasons (Wellington October: 40°C = 104°F, which is plausible for a warm spring day in NZ southern hemisphere). It fixes one of four contamination categories (P0-1) while leaving P0-2 (impossible values), P0-3 (collection timing), and P0-4 (zero coverage) unaddressed. Per Fitz's structural-decisions methodology, 4 symptoms are 1 structural decision incompletely executed. The patch creates a new failure mode (unit detection false positives) at the boundary where it's applied. More fundamentally: a heuristic patch is a security guard (detect and react to known cases). `ObservationAtom.__post_init__` is an immune system (encounter the category → permanent structural immunity against all future instances). Principle 2 (antibody not alert) defeats the patch alternative independent of the problem-count axis. **Invalidated:** does not make the category impossible, only handles the most obvious instance.

### Why not keep regional clusters and fix the 21 singleton cases by merging nearby cities?

The core failure is not singleton count — it is that regional cluster averages contaminate per-city Platt calibration. Paris and London in the same cluster means a Paris calibration pair is treated as evidence for a London model. They have different diurnal profiles, different settlement station characteristics, and for this dataset, Paris had unit contamination (P0-1) that would corrupt any cluster it participated in. Level 1 maturity (≥150 pairs) is currently structurally unreachable for 19 singleton clusters regardless of merging strategy, because the TIGGE training data covers those cities individually. The `max_region_pct=35%` vs `max_city_pct=20%` conflict is irresolvable within regional cluster semantics — a city IS a region when it is the sole cluster member. **Invalidated:** merging cities constructs false correlation and does not resolve the maturity or limit-conflict problems.

### Why not soft-flag contaminated rows instead of authority downgrade to UNVERIFIED?

Soft flags (contamination_risk, confidence_weight) leave the decision of "how much to trust this row" to runtime math. But the problem is epistemic, not quantitative: we do not know what temperature Paris actually had on dates where the WU API returned °F values misattributed as °C. A soft flag on an unknown value does not make the unknown value usable — it makes an unknown value seem partially usable, which is worse than excluding it. INV-04 (point-in-time learning only) requires that training data be observations that were validly knowable at the time. A row that was never correctly measured fails this criterion regardless of its confidence weight. **Invalidated:** soft flags violate the data provenance principle and INV-04.

---

## Why Chosen (map to Fitz methodology)

- **K1 → Immune System > Security Guard.** `ObservationAtom.__post_init__` is a full antibody — it makes the bad-value category unconstructable. A runtime logger that fires when Wellington reads -40°F is a security guard (discover, alert, repeat). The atom is an immune system (encounter pathogen → permanent structural immunity).
- **K2 → High-Dimensional Thinking.** P0-3 (timing wrong) and P0-4 (zero coverage) are one design failure: the scheduler assumes UTC is a universal clock for daily-maximum collection. The fix is not two patches — it is one structural decision (physical-clock collection) that makes both failures impossible simultaneously.
- **K3 → Structural Decisions > Patches.** The cluster problem is not 21 singleton clusters, a conflicting risk limit, and a hand-guessed matrix. It is one decision (cluster := city) incompletely executed. Executing it completely removes all three symptoms.
- **K4 → Data Provenance > Code Correctness.** The evaluator and market_fusion code are correct. The Platt calibration code is correct. The data is wrong. Code review cannot see data provenance. The `authority` column is a machine-readable provenance contract that code can check. Once it is present, correct code + wrong data becomes a compile-time (init-time) error, not a silent runtime corruption.

---

## Consequences

### What breaks:
- All 120 existing `platt_models` are deleted in K3 (cleared for city-based rebuild). System operates at calibration Level 3-4 during K4 rebuild window.
- The 30-cluster hand-guessed correlation matrix in `settings.json` is replaced. Any operator tooling that reads `correlation.matrix` from settings will break.
- `max_region_pct` is removed from `RiskLimits`. Any code that passes `current_cluster_exposure` to `check_position_allowed` will break (parameter removed).
- The `WU_HISTORY_URL` path in `wu_daily_collector.py` that currently fires at UTC 12:00 will no longer fire — the scheduler owns the trigger.
- The `cluster` field on `City` objects will equal the city name after K3, not a regional label. Any downstream code that used `city.cluster` as a geographic category (not a bucket key) will produce different output.

### What becomes impossible:
- Storing a temperature observation without a provenance chain (ObservationAtom required).
- Collecting WU data before the local daily maximum has occurred (scheduler enforces local-time trigger).
- Calibrating or pricing a position using contaminated historical data (authority gate in evaluator + market_fusion).
- A new regional cluster string silently entering the codebase (semantic linter rule in CI).
- Platt models trained on mixed-unit or timing-contaminated data (rebuild pipeline requires VERIFIED sources).

### Ongoing cost:
- Every new city added to Zeus must have `peak_hour` set in `cities.json` and must generate monthly bounds in `city_monthly_bounds.json` before WU collection is enabled.
- The `authority` column must be maintained in all DB migrations going forward — any new table that stores observations or calibration data must include it.
- The Pearson correlation matrix must be rebuilt when new TIGGE data accumulates (offline script; not automated).
- During the K4 re-collection window (6-17 days), calibration operates at Level 3-4 for all cities.

---

## Follow-ups (explicitly out of scope for this rebuild)

1. **Open-Meteo verification lane.** All Open-Meteo observations currently have `authority = 'UNVERIFIED'`. A future packet would validate Open-Meteo historical data against TIGGE ensemble p01/p99 and upgrade qualifying rows to VERIFIED. Not in this rebuild — scope is too large and Open-Meteo is a secondary source.

2. **Cross-region data-driven correlation backfill.** The Pearson matrix built in K3 uses TIGGE ensemble means. A more accurate matrix would use actual settlement outcomes. After 500+ VERIFIED settlements accumulate, a second correlation revision packet can replace the ensemble-mean matrix with outcome-based correlations.

3. **DST diurnal curves historical rebuild.** `docs/operations/known_gaps.md` documents stale pre-fix diurnal aggregates for DST cities (London, NYC, Chicago, Paris). This rebuild does not fix that — it establishes the correct schema and collection. The diurnal ETL rebuild is a separate packet.

4. **NOAA CDO / meteostat verification.** `observations` contains rows from `noaa_cdo_ghcnd`, `meteostat_daily_max`, `iem_asos`. These are left at `UNVERIFIED` in K4. Verification of each alternate source is a separate, smaller packet.

5. **HKO (Hong Kong) daily extract re-integration.** 296 rows from `hko_daily_extract` are currently in observations. Source authority is unclear. Verification and potential upgrade to VERIFIED is out of scope.

6. **Automated authority maintenance.** K4 gates are hard asserts. Future work: a soft-warn mode during re-collection that gates paper/shadow trading on UNVERIFIED but allows live trading on TIGGE-VERIFIED only. Not in this rebuild — too many moving parts.
