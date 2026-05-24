# Data Ingest + Collection Temporal Kernel — Program Plan

Status: active
Authority: false (advisory file management, not architecture law)
Scope: data ingestion temporal control plane (src/data/**, src/ingest_main.py, scheduler)
Blocks live completion: false (additive; live gating unchanged until later PR)
Created: 2026-05-24
Authority basis: external "Zeus Data Ingest + Collection Efficiency Refactor" spec (operator-supplied),
  ground-truth-audited against repo on 2026-05-24; root AGENTS.md (money path, data zone law);
  config/source_release_calendar.yaml; architecture/data_sources_registry_2026_05_08.yaml.

## Objective

Give Zeus a first-class **temporal control plane** over data collection: every source's
time semantics (issue / release / safe-fetch / event-time vs write-time / freshness /
expiry) become typed, queryable, and CI-lintable — so "data present but temporally wrong"
(early, late, stale, shadow, backfilled, wrong-cycle, wrong-local-day) becomes a
detectable, fail-closed category instead of a silent live-money failure.

## Phase 0 — Ground-truth audit verdict (COMPLETE 2026-05-24)

The spec author never ran the repo (GitHub-connector only, no tests). Audited load-bearing
claims against actual code/config. Verdicts:

- VERIFIED: safe_fetch 485min + 00/12 full / 06/18 short (calendar L9-82); Open-Meteo
  SHADOW_ONLY + TIGGE backfill_only (calendar L97-120); OpenData ownership env switch
  `_ingest_main_owns_opendata() = _forecast_live_owner() != "forecast_live"` (ingest_main:59-60);
  fast executor mw=4, UMA listener on `fast` writes DB via record_resolution (ingest_main:1463-1656);
  mx2t6 param deprecated → live fetch uses mx2t3 (`open_data_param:"mx2t3" # was mx2t6 … API ValueError`,
  ecmwf_open_data.py:145); source_time_frontier table absent.
- REFUTED / OVERCLAIMED: UMA "scans from genesis / unbounded" — cursor + `_UMA_MAX_BLOCKS_PER_TICK=100_000`
  ALREADY exist (ingest_main:897,1017-1022). UMA work shrinks to era_end_block guard + fast-executor
  DB-write split only.
- INVENTED (not in repo): freshness ladder 18h/24h/30h. Calendar already carries
  `max_source_lag_seconds` (ECMWF 108000=30h, OpenMeteo 172800=48h, TIGGE 604800=7d).
  Freshness states must DERIVE from that field, not new constants.
- STRUCTURAL CORRECTION (load-bearing): spec's proposed `src/data/source_contracts.py` would be a
  FOURTH parallel registry duplicating existing BINDING infra:
    * architecture/data_sources_registry_2026_05_08.yaml (BINDING source catalog + provenance)
    * src/data/forecast_source_registry.py (runtime tiers/roles/degradation/operator-gates/live_authorization)
    * config/source_release_calendar.yaml (temporal facts)
    * src/contracts/source_family.py, forecast_ingest_protocol.py (family/authority-tier)
  Methodology law forbids parallel wrapper layers. DECISION: do NOT author a new contract registry.
  The genuinely-missing piece is a TemporalPolicy that COMPUTES safe_fetch/freshness/expiry from
  the EXISTING calendar, plus (PR2) the source_time_frontier table + report.
- CALENDAR DRIFT (real, pre-existing, OUT OF PR1 SCOPE): calendar still says `parameter: mx2t6` /
  `track: mx2t6_high` while code fetches mx2t3. Lint REPORTS it; a later scoped PR fixes the calendar.
  PR1 does not edit the calendar (source-routing change → planning-lock + behavior risk).

## PR1 (this branch) — temporal kernel + coherence lint, advisory, ZERO behavior change

New files only. No scheduler / schema / ingestion-function / calendar edits.

- `src/data/source_time.py` — TimePlane, LateArrivalPolicy enums; frozen `TemporalPolicy` dataclass;
  `load_temporal_policy(calendar_id) -> TemporalPolicy` reads ONE calendar entry. ZERO hardcoded
  temporal constants — every field sourced from the yaml entry. `safe_fetch_not_before(issue)` and
  freshness-state ladder derived from `max_source_lag_seconds` via documented ratios
  (degraded ≥ 0.8×, expired ≥ 1.0×).
- `scripts/source_contract_lint.py` — INTER-REGISTRY COHERENCE (not a new registry). Assertions:
    1. calendar.source_id ⊆ data_sources_registry.sources[].id
    2. forecast_source_registry entry_primary ROLE ⇒ calendar live_authorization=true
       (diagnostic-only / experimental-backfill ⇒ NOT live; keyed on allowed_roles + backfill_only, not tier)
    3. partial_policy=SHADOW_ONLY ⇒ live_authorization=false
    4. backfill_only=true ⇒ live_authorization=false
    5. code data_version param (snapshot_ingest_contract mx2t3) matches SDK param (ecmwf_open_data.py:145);
       MISMATCH vs calendar mx2t6 = drift report (advisory)
    6. HKO source never carries WU/VHHH station mapping
  Advisory by default (calendar drift trips it day-one; promote to blocking after calendar-fix PR).
- Relationship tests FIRST (RED before code):
    * test_temporal_policy_safe_fetch_matches_calendar
    * test_calendar_entries_have_safe_fetch_for_live
    * test_calendar_in_data_sources_registry
    * test_shadow_partial_policy_implies_not_live_authorization
    * test_backfill_only_implies_not_live_authorization
    * test_code_data_version_param_matches_calendar_or_lint_flags_drift  (XFAIL+ticket if RED on main)
- Manifest companions (K2 loop-break): register new files in script_manifest.yaml, test_topology.yaml,
  source_rationale.yaml.

## PR2-PR8 (this branch) — as-built + operator-gated deferrals

- PR2 (RESHAPED, as-built): collection_frontier.py + data_collection_frontier_report.py — IN-MEMORY
  frontier from existing surfaces (source_run/readiness_state/job_run/coverage + health/heartbeat JSON),
  read-only, NO new table. Freshness on SOURCE/EVENT time (backfill write-time cannot fake freshness);
  missing data fails closed to UNKNOWN_BLOCKED. Reason: a persisted source_time_frontier is forecast-class
  → SCHEMA_FORECASTS_VERSION bump → live daemon schema-gate (SystemExit) → operator-gated, NOT zero-change.
- PR2b (OPERATOR-GATED, deferred): persist source_time_frontier + source_time_variance_sample (forecast-class
  for INV-37 same-DB write locality; deviates from spec world-class rec — documented for critic/operator) +
  SCHEMA_FORECASTS_VERSION bump + db_table_ownership + table_registry + live forecasts-DB migration (dry-run+rollback).
- PR3: source_job_registry inventory of existing scheduled jobs (advisory lint, no scheduler replacement).
- PR4: OpenData singleton ownership enforced by registry + runtime assertion.
- PR5: source_watermarks + bounded backfill; UMA era_end_block guard (cursor/cap already exist).
- PR6: scheduler adapter builds APScheduler from JobSpec; executor classes (live/backfill/derived/io/heartbeat).
- PR7: row-level temporal provenance columns + live-reader gating (behind flag).
- PR8: derived/diagnostic worker separation; move UMA DB-write off fast executor.

## Safety rails (whole program)

No live order placement. No production DB mutation without operator-go. No deletion of
ensemble_snapshots_v2/readiness_state/source_run/market_events_v2/settlements. No auto cities.json
station remap. No HKO→WU/VHHH fallback. No TIGGE/Open-Meteo as live authority. Each PR has a
named rollback (env flag or file removal). PR1 rollback = remove new files (zero runtime touch).
