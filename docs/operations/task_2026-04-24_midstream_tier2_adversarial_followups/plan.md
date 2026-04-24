# T2 Midstream Adversarial Followups — Packet Plan

Date: 2026-04-24
Branch: `data-improve`
Status: implementation packet for Tier 2 adversarial-audit followups from
`docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
§3.1 (my-subagent findings). Forensic-package findings (F1-F20) are NOT in
scope — owned by the P0 containment lane.

## Task

Close five adversarial-audit findings from my 4-subagent parallel audit that
are exclusively mine (not covered by the forensic audit's P0→P4 apply order):

- **M1** — remove `setdefault("causality", {"status":"OK"})` in
  `scripts/ingest_grib_to_snapshots.py:164` so Law 5 (R-AJ) rejection
  actually fires instead of being silently bypassed at the ingest layer.
- **C5** — route HIGH-track calibration-pair write through
  `add_calibration_pair_v2` with `HIGH_LOCALDAY_MAX` metric identity so
  HIGH pairs reach the v2 trainer; HIGH currently writes to legacy
  `calibration_pairs` table that `refit_platt_v2` never reads.
- **C6** — replace hardcoded settlement `physical_quantity=
  "daily_maximum_air_temperature"` / `observation_field="high_temp"` in
  harvester with canonical `HIGH_LOCALDAY_MAX.*` so canonical
  physical_quantity JOINs stop silently dropping all 1,561 harvester rows.
- **H3** — add explicit `AND s.temperature_metric = 'high'` to four
  cross-table JOINs that today silently assume HIGH-only data and would
  silently mix metrics once LOW data lands.
- **M3** — rename `CANONICAL_DATA_VERSIONS` →
  `CANONICAL_ENSEMBLE_DATA_VERSIONS` and add parallel
  `CANONICAL_OBSERVATION_DATA_VERSIONS` and
  `CANONICAL_SETTLEMENT_DATA_VERSIONS` allowlists so the name reflects
  ensemble-only scope.

**C3 was investigated and ruled FALSE POSITIVE** — the subagent confused
`_UNIT_MAP` (maps city manifest C/F → degC/degF) with `members_unit`
(Kelvin canonical for ensemble members). Module header explicitly states
"members_unit is the city's native unit ('degC' or 'degF'), never 'K'".
No fix needed.

## Route

- Originating handoff:
  `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
  §3.1 C5/C6/H3/M1/M3
- Forensic ruling scope (out of scope for this packet):
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/`
  P0→P4

## Scope

Allowed files (organized by slice):

### S1 — M1 + C6 (ingest Law 5 + harvester canonical identity)

- `scripts/ingest_grib_to_snapshots.py`
- `src/execution/harvester.py`
- `tests/test_ingest_grib_law5_antibody.py` (NEW)
- `tests/test_harvester_metric_identity.py` (NEW)

### S2 — C5 (HIGH calibration-pair route to v2)

- `src/execution/harvester.py`
- `tests/test_harvester_high_calibration_v2_route.py` (NEW)

### S3 — H3 (cross-table JOIN metric filter + antibody lint rule)

- `src/engine/monitor_refresh.py`
- `scripts/etl_historical_forecasts.py`
- `scripts/validate_dynamic_alpha.py`
- `scripts/etl_forecast_skill_from_forecasts.py`
- `scripts/semantic_linter.py`
- `tests/test_semantic_linter.py`

### S4 — M3 (CANONICAL_DATA_VERSIONS rename + parallel allowlists)

- `src/contracts/ensemble_snapshot_provenance.py`
- `tests/test_fdr.py`
- `tests/test_calibration_bins_canonical.py`
- `tests/test_phase7a_metric_cutover.py`

### Shared packet surfaces

- `docs/operations/task_2026-04-24_midstream_tier2_adversarial_followups/plan.md`
- `docs/operations/task_2026-04-24_midstream_tier2_adversarial_followups/work_log.md`
- `docs/operations/task_2026-04-24_midstream_tier2_adversarial_followups/receipt.json`
- `docs/operations/AGENTS.md` (register packet)
- `docs/operations/current_state.md` (add packet pointer)

Forbidden files:

- `architecture/**`
- `docs/authority/**`
- `src/state/**` (truth ownership — not in scope)
- `src/control/**`, `src/supervisor_api/**`
- `.github/workflows/**`
- runtime DBs, state JSON, live graph DB
- forensic package, co-tenant P0 packet files

## Implementation Plan

### S1 — M1 + C6 hardening

**M1**: Remove line 164 `contract_payload.setdefault("causality",
{"status": "OK"})` from `scripts/ingest_grib_to_snapshots.py`. Keep the
other three setdefault calls (they fill from authoritative sources —
`metric.temperature_metric`, `metric.physical_quantity`,
`members_unit`). Update the preceding docstring comment to explain why
causality cannot be defaulted (Law 5 / R-AJ is first-class and any
pre-Phase-5B payload without causality must be quarantined, not
silently accepted).

Antibody: new `tests/test_ingest_grib_law5_antibody.py` feeds
`ingest_json_file` a payload missing the `causality` key and asserts
it returns `"contract_rejected: MISSING_CAUSALITY_FIELD"` (not the
bypassed silent-accept that the setdefault allowed).

**C6**: Replace the hardcoded triple at `src/execution/harvester.py`
settlements INSERT (~line 768):

```python
# before:
"high", "daily_maximum_air_temperature", "high_temp",
# after:
HIGH_LOCALDAY_MAX.temperature_metric,
HIGH_LOCALDAY_MAX.physical_quantity,
HIGH_LOCALDAY_MAX.observation_field,
```

Matching change to `data_version` which currently flows via
`_HARVESTER_LIVE_DATA_VERSION`; confirm it equals
`HIGH_LOCALDAY_MAX.data_version` or route through canonical.

Antibody: new `tests/test_harvester_metric_identity.py` harness-tests
the settlement INSERT path and asserts the written row's
`physical_quantity == HIGH_LOCALDAY_MAX.physical_quantity`.

### S2 — C5 HIGH branch to v2

Change `src/execution/harvester.py:1081-1091` (HIGH branch of the
calibration-pair writer) from `add_calibration_pair` (legacy) to
`add_calibration_pair_v2` with `metric_identity=HIGH_LOCALDAY_MAX`,
`data_version=HIGH_LOCALDAY_MAX.data_version`, `training_allowed=True`.
Preserve the `round_wmo_half_up_value` settlement rounding (the LOW
branch doesn't round — audit whether HIGH should too; if yes, also
bring rounding into LOW; if the rounding is HIGH-specific, document why).

Antibody: `tests/test_harvester_high_calibration_v2_route.py` drives
the harvester HIGH path and asserts a row lands in `calibration_pairs_v2`
with `temperature_metric='high'` + `data_version=
HIGH_LOCALDAY_MAX.data_version` + `training_allowed=1`.

### S3 — H3 cross-table JOINs

Four JOIN sites add explicit `AND s.temperature_metric = 'high'`:

- `src/engine/monitor_refresh.py:471-475`
- `scripts/etl_historical_forecasts.py:141-148`
- `scripts/validate_dynamic_alpha.py:168-177`
- `scripts/etl_forecast_skill_from_forecasts.py:109`

Rationale: `forecasts` / `forecast_skill` / `historical_forecasts`
tables have NO `temperature_metric` column (they currently store HIGH
only via `forecast_high` / `forecast_temp` columns). Once LOW writer
lands (forensic P4), the tables will need schema change; for now, pin
HIGH explicitly so the JOIN doesn't silently mix once settlements gains
LOW rows.

Antibody: new `semantic_linter` rule that greps for
`JOIN settlements` / `FROM settlements` in `src/` + `scripts/` without
a corresponding `temperature_metric` predicate; `tests/test_semantic_linter.py`
adds a failing fixture and an all-green harness.

### S4 — M3 CANONICAL_DATA_VERSIONS rename

Rename in `src/contracts/ensemble_snapshot_provenance.py`:
`CANONICAL_DATA_VERSIONS` → `CANONICAL_ENSEMBLE_DATA_VERSIONS`. Add
parallel frozensets:
- `CANONICAL_OBSERVATION_DATA_VERSIONS = frozenset({"v1.wu-native",
  "v1.hko-native", "v1.ogimet-native", "v1.meteostat-native",
  "v1.openmeteo-native"})` (enumerate from existing observation writers)
- `CANONICAL_SETTLEMENT_DATA_VERSIONS = frozenset({
  HIGH_LOCALDAY_MAX.data_version, LOW_LOCALDAY_MIN.data_version})`
  (ensemble data_version currently reused by settlements; once P4
  creates settlement-native data_version, this set can diverge)

Update three call sites in tests to reference the new name:
- `tests/test_fdr.py:66`
- `tests/test_calibration_bins_canonical.py:767, 772, 776, 779`
- `tests/test_phase7a_metric_cutover.py:458` (comment-only)

Leave a deprecation alias `CANONICAL_DATA_VERSIONS =
CANONICAL_ENSEMBLE_DATA_VERSIONS` for one cycle to unbreak any
internal import I missed; log a deprecation warning in tests so it
doesn't leak into production.

## Execution Order

Each slice: edit → targeted tests → topology freshness + map-maintenance
gates → dispatch critic-opus → on approval, commit (scoped files only) →
push. No autocommit before critic verdict (per memory rule L22). Grep-
verify file:line anchors within 10 min of editing (per L20).

Slices run sequentially (S1 → S2 → S3 → S4) to keep each commit atomic
and critic review scoped.

## Acceptance

- **S1**: `pytest tests/test_ingest_grib_law5_antibody.py
  tests/test_harvester_metric_identity.py` green; removing the
  causality setdefault causes any pre-Phase-5B payload without
  causality to fail ingest with `MISSING_CAUSALITY_FIELD`; harvester's
  new settlement rows carry `physical_quantity=
  "mx2t6_local_calendar_day_max"` not
  `"daily_maximum_air_temperature"`.
- **S2**: `pytest tests/test_harvester_high_calibration_v2_route.py`
  green; HIGH calibration pairs reach `calibration_pairs_v2` with
  HIGH_LOCALDAY_MAX identity; legacy `calibration_pairs` table receives
  no new HIGH rows from harvester.
- **S3**: `pytest tests/test_semantic_linter.py` green; new linter rule
  rejects future `JOIN settlements` without `temperature_metric`
  predicate in canonical paths; 4 JOIN sites contain the explicit
  `temperature_metric = 'high'` filter.
- **S4**: `pytest tests/test_fdr.py tests/test_calibration_bins_canonical.py
  tests/test_phase7a_metric_cutover.py` green; `CANONICAL_DATA_VERSIONS`
  still importable (deprecation alias) but all tests reference
  `CANONICAL_ENSEMBLE_DATA_VERSIONS`; parallel observation + settlement
  allowlists defined and importable.
- **Packet-wide**: topology gates pass (`--planning-lock`, `--work-record`,
  `--change-receipts`, `--freshness-metadata`, `--map-maintenance
  --map-maintenance-mode precommit`); `git diff --check` clean on
  changed files; co-tenant P0 containment scope unaffected.

## Out of scope

- TIGGE local rsync (operator-owned 4.7).
- WU_API_KEY env + scheduler repair (operator 4.8).
- Any src/state/** truth-ownership or schema changes (planning-lock
  forbids unless the packet explicitly takes that scope).
- Forensic P0-P4 items (F1-F20) — owned by other agent.
- C4 LOW-track settlement writer — requires schema work in state/**,
  defer to dedicated packet.
- C7/C8 observation_instants_v2 INV-14 writer retrofit — forensic F7
  overlap + needs operator decision on instant-vs-daily-aggregate axis
  (handoff §5.3 question 13).

## Open risks

- S4 deprecation alias may outlive one cycle if I miss a callsite; the
  fallback is a follow-up cleanup packet.
- S2 HIGH rounding vs LOW rounding semantic difference must be audited
  before the route change — if the legacy `add_calibration_pair` path
  for HIGH relied on the rounding to pass downstream constraints, the
  v2 path might fail without matching rounding.
- S3 linter rule may flag legitimate legacy paths; allowlist those with
  inline annotation.
