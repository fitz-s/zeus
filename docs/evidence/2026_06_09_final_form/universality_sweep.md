# Universality Sweep Report — 2026-06-09

Patterns P1–P6. Read-only analysis. No edits made.

---

## P1 — COVERAGE never implies CYCLE-CURRENCY

Sweep: every consumer of `.covered`, `posterior_count`, `readiness_count`, `ALREADY_COVERED`, `skip_covered`, and `if ... covered ... continue/return`.

| file:line | snippet (≤80 chars) | verdict | reason |
|---|---|---|---|
| `src/data/replacement_forecast_current_target_plan.py:36` | `def covered(self) -> bool: return self.posterior_count > 0 and self.readiness_count > 0` | CLEAN | `readiness_count` is computed via SQL with `expires_at > now` guard (line 349); expired rows → count=0 → covered=False. `posterior_count` has no expiry but a posterior only exists AFTER materialization for that baseline_source_run_id — callers use this to skip the download only when readiness is also fresh. |
| `src/data/replacement_forecast_current_target_plan.py:598` | `covered_count = sum(1 for row in out if row.covered)` | CLEAN | Aggregation metric only; not used to gate a download/fetch. |
| `src/data/replacement_forecast_current_target_plan.py:631` | `missing = [row for row in plan.rows if not row.covered]` | CLEAN | Used to enumerate targets that still need materialization, not to skip a download. Complement of covered is what is acted on. |
| `src/data/replacement_forecast_shadow_materialization_queue.py:345` | `if _seed_already_covered(forecast_db=..., seed=seed): continue` | CLEAN | `_seed_already_covered` checks posterior EXISTS **AND** readiness not-expired via `expires_at > now` clause (lines 281–285). Both conditions must hold. Fail-open on read error (returns False → reprocesses). |
| `scripts/download_replacement_forecast_current_targets.py:177` | `_rows = list(plan.rows) if include_covered else [row for row in plan.rows if not row.covered]` | CLEAN | Fixed today (K-root #3): `include_covered=True` is passed when the available cycle is newer than the downloaded cycle. The freshness test is upstream of this filter. |
| `scripts/check_live_release_gate.py:308` | `if row.covered or not row.day0_observed_extreme_required: continue` | CLEAN | This is a release-gate check, not a download gate. Skips the day0-obs check for cities that already have a live posterior+readiness — correct behavior: those cities don't need day0 obs to unblock trading. |
| `src/data/replacement_forecast_seed_discovery.py:202–253` | `skip_covered_sql = ... expires_at > strftime(...)` | CLEAN | SQL-level skip already contains `expires_at > now` guard for readiness row; the `NOT EXISTS (posteriors ...)` check uses the exact `baseline_source_run_id` key so a prior cycle's posterior doesn't block a new cycle's seed. |
| `src/calibration/ens_bias_repo.py:731,740` | `covered = _parse_coverage_months(row["coverage_months"])` | CLEAN | `covered` here is a set of calendar months, not a freshness/cycle-currency concept. Unrelated to P1 pattern. |
| `src/observability/calibration_serving_status.py:173,218` | `"readiness_count": 0` / `producer["readiness_count"] += 1` | CLEAN | Observability counter; not used to gate work. |
| `src/engine/replay_selection_coverage.py:856` | `summary.cities_covered = sorted(per_city.keys())` | CLEAN | Replay reporting; not a live-download gate. |
| `src/main.py:1067` | `covered = sorted(license_map.keys())` | CLEAN | EMOS license audit list — `covered` is a local variable listing season-covered cities for logging, not a download gate. |

**P1 verdict: NO unresolved suspects found.** All coverage-based gates either have fresh TTL guards or are non-freshness contexts (release gates, reporting, calendar-month filtering).

---

## P2 — SILENT SINK: subprocesses with capture_output whose stderr/stdout reach no daemon log

Sweep: all `subprocess.run`/`Popen` with `capture_output=True` or `stdout/stderr=PIPE` in `src/` and production daemon paths in `scripts/`.

| file:line | snippet (≤80 chars) | verdict | reason |
|---|---|---|---|
| `src/data/replacement_forecast_shadow_materialization_queue.py:72–86` | `subprocess.run(... capture_output=True)` → `_surface_subprocess_warnings` | CLEAN | **Fixed today.** Per-line WARNING/ERROR re-emission implemented. |
| `src/data/ecmwf_open_data.py:1246` | `result = subprocess.run(args, capture_output=True, ...)` | CLEAN | `_run_subprocess` logs stderr tail via `logger.warning` on rc!=0 (line 1257–1258). Sufficient for a binary-fail subprocess. |
| `src/data/tigge_pipeline.py:240–274` | `result = subprocess.run(..., capture_output=True)` | CLEAN | Logs stderr tail on rc!=0 (line 265–268). Same pattern as ecmwf_open_data. |
| `src/ingest_main.py:824–832` | `subprocess.run([automation_analysis.py], capture_output=True)` | CLEAN | stdout logged via `logger.info` on every run (line 830); stderr logged on rc!=0 (line 832). |
| `src/ingest_main.py:1293–1304` | `subprocess.run([bridge_oracle_to_calibration.py], capture_output=True)` | CLEAN | Full stderr logged on rc!=0 (line 1301). |
| `src/ingest_main.py:1427–1436` | `subprocess.run([promote_calibration.py inspect], capture_output=True)` | CLEAN | Non-zero exit logged including stdout (line 1433–1436). |
| `src/ingest_main.py:1443–1454` | `subprocess_run_with_write_class([promote_calibration.py promote --commit], capture_output=True)` | CLEAN | stderr logged on rc!=0 (line 1450–1452). |
| `src/ingest_main.py:762–774` | `subprocess_run_with_write_class([etl_diurnal_curves.py], capture_output=True)` | **SUSPECT** | Only `r.stderr[-200:]` is logged on rc!=0 (line 774: `f"FAIL: {r.stderr[-200:]}"`). No stdout logging at all. Any `WARNING` the ETL scripts emit to stdout (e.g., calibration anomalies, missing-data notices) is silently discarded. This is a production ingest-daemon scheduler job (`@_scheduler_job("ingest_etl_recalibrate")`), same class as the materializer-queue gap fixed today. Same applies to `etl_temp_persistence.py` in the same loop. |
| `src/ingest_main.py:1181–1195` | `subprocess_run_with_write_class([etl_forecast_skill.py], capture_output=True)` | MARGINAL | stdout IS logged (line 1188) but truncated to last 2000 chars. If the script emits many lines before a WARNING, the WARNING is lost. Not silently discarded but lossy. |
| `src/main.py:5546–5568` | `subprocess.run([measure_arm_gate_settlement.py], capture_output=True)` | MARGINAL | stderr logged on rc!=0 (line 5562–5567). stdout is silently discarded on rc==0 (line 5570 logs only "re-emitted artifact" — no stdout passthrough). WARNINGs from the producer script on successful runs are lost. However this is a non-critical observability script (ARM gate artifact emission). |
| `src/riskguard/discord_alerts.py:80–89` | `subprocess.run([keychain_resolver], capture_output=True)` | CLEAN | Read stdout on success (webhook URL retrieval). Errors caught via exception (line 88–89). Not a production warning path. |
| `src/runtime/posture.py:104–116` | `subprocess.run(["git", "rev-parse", ...], capture_output=True)` | CLEAN | Git branch name retrieval; stdout consumed (line 111). No domain warnings to surface. |

**P2 suspects: `src/ingest_main.py:762–774` (etl_diurnal + etl_temp_persistence daily jobs)** — stdout WARNINGs silently discarded; only rc!=0 stderr is surfaced. Same structural gap that was fixed in the materializer queue today.

---

## P3 — ROW-FACTORY DEPENDENCE

Sweep: `row["col"]` patterns near sqlite execute/fetch where the connection comes from a caller parameter.

**Key finding: `_connect()` at `src/state/db.py:203–204` ALWAYS sets `conn.row_factory = sqlite3.Row`.** Every `get_world_connection()`, `get_forecasts_connection()`, and `_connect()` call guarantees Row semantics. The `get_forecasts_connection()` chain covers all forecast DB paths; `get_world_connection()` covers all world DB paths.

| file:line | snippet (≤80 chars) | verdict | reason |
|---|---|---|---|
| `src/data/executable_forecast_reader.py:850–866` | `row["snapshot_id"]`, `row["city"]`, etc. | CLEAN | Line 810: `dict(row)` conversion applied to all rows before indexing. Dict access is always safe. |
| `src/data/replacement_forecast_materializer.py:441,630–632,654–655,1124` | `row[0] if not isinstance(row, sqlite3.Row) else row["anchor_id"]` | CLEAN | Dual-path guard: explicit `isinstance` check falls back to positional index when row_factory not Row. |
| `src/calibration/ens_bias_repo.py:731,740` | `row["coverage_months"]` — conn passed by caller | CLEAN | All callers traced: `get_world_connection()` → `_connect()` sets Row factory. Return type annotation is `sqlite3.Row | None`. |
| `src/execution/exchange_reconcile.py:1592,1599` | `row["token_id"]`, `row["command_id"]` — caller-conn | CLEAN | Traced to callers at lines 1155/1447; conn originates from `get_world_connection()` chain. |
| `src/execution/settlement_commands.py:739` | `row["condition_id"]` — conn passed by caller | CLEAN | All settlement command entry points use `get_world_connection()`. |
| `src/data/tigge_db_fetcher.py:173–205` | `row["members_json"]`, `row["snapshot_id"]`, etc. | CLEAN | Connection created via `get_forecasts_connection()` at line 105. Row factory guaranteed. |
| `src/state/schema/edli_no_submit_receipts_schema.py:123–167` | `row["receipt_id"]` etc. — dual-path guards | CLEAN | All instances use `isinstance(row, sqlite3.Row)` fallback. |
| `src/events/edli_position_bridge.py:192–222` | `row["payload_json"]` etc. — dual-path guards | CLEAN | All instances use `isinstance(row, sqlite3.Row)` fallback. |
| `src/data/replacement_forecast_current_target_plan.py:116` | `row["name"]` (PRAGMA table_info) | CLEAN | PRAGMA returns sqlite3.Row objects when row_factory=Row; the function is always called with a `_connect()`-sourced connection. |

**P3 verdict: NO unresolved suspects found.** `_connect()` universally sets Row factory; caller-conn cases all trace to `_connect()`-derived connections. The `isinstance` dual-path guards in materializer, exchange_reconcile, edli_bridge, etc. are belt-and-suspenders.

---

## P4 — COORDINATE AUTHORITY

Sweep: hardcoded lat/lon literals outside `tests/` and `.omc/`, particularly in code that fetches weather data.

| file:line | snippet (≤80 chars) | verdict | reason |
|---|---|---|---|
| `scripts/onboard_cities.py:130–205` | `lon=174.7645`, `lat=2.7456`, etc. | CLEAN | These ARE the cities.json entries being onboarded. Onboarding script defines the authority; it is the write path, not a consumer. |
| `scripts/oos_validation_harness.py:321` | `lat=40.7772, lon=-73.8726` (NYC fixture) | MARGINAL | Hardcoded NYC fixture for OOS validation harness. Not a live-data fetch but diverges from cities.json (cities.json entry for NYC uses `wu_station: KLGA`). Risk: if cities.json NYC coord is ever updated, this test fixture silently uses stale coordinates. Not money-path (offline validation only). |
| `scripts/produce_activation_evidence.py:72–73` | `lat=51.4775, lon=-0.4614` (London) | MARGINAL | Hardcoded London `City` construction for an evidence-generation script. Does not fetch live weather data; constructs a City object for protocol testing. Same stale-coord risk as above. Not money-path. |
| `scripts/build_replacement_forecast_simple_switch_evidence.py:170–171` | `--openmeteo-latitude default=31.2304, --openmeteo-longitude default=121.4737` (Shanghai) | MARGINAL | CLI defaults for an operator-run evidence script (`--probe-openmeteo`). Default coordinates match Shanghai; if cities.json Shanghai is re-pinned, the default diverges silently. Operator can override; not automated. |
| `scripts/verify_reality_contracts_2026-05-17.py:55` | `?latitude=40.71&longitude=-74.01` | CLEAN | One-off verification script (filename-dated), hardcoded NYC API probe for connectivity test. Not production. |
| `scripts/antibody_scan.py:196` | `season_from_date("2025-07-15", lat=40.0)` | CLEAN | Rounded latitude for seasonal hemisphere test (summer vs winter). Not a weather data fetch. |
| `scripts/ingest_grib_to_snapshots.py:224` | `grid_lon = 180.0` | CLEAN | Dateline boundary constant for GRIB grid-coordinate arithmetic, not a city coordinate. |
| `src/engine/event_reactor_adapter.py:2643` | `_epsilon = -1e-9` | CLEAN | Floating-point rounding epsilon, not a coordinate. |
| `config/model_domain_polygons.yaml:34–38` | `[-3.94, 43.18]` etc. | CLEAN | Polygon vertices for model domain gates. These are domain hull coordinates, not city fetch coordinates. Governed by `config/model_domain_polygons.yaml` (the designated polygon authority). |
| `scripts/backfill_u0r_promoted_model_history.py:64–65` | `"latitude": city["lat"], "longitude": city["lon"]` | CLEAN | Reads from cities.json (line 57). Coordinate authority respected. |

**P4 verdict: three MARGINAL findings** (oos_validation_harness, produce_activation_evidence, build_replacement_forecast_simple_switch_evidence). None are on live money paths. The risk is stale-fixture-vs-cities.json drift if coordinates are ever re-pinned. **No production src/ file hardcodes weather-fetch coordinates.**

---

## P5 — ENDPOINT/MODEL-ID ASSUMPTIONS NEVER VERIFIED

Full enumeration of requested (model, endpoint) combos vs. persisted rows in `raw_model_forecasts` (read-only query on `state/zeus-forecasts.db`).

**DB row counts as of sweep:**

| model | endpoint | rows | verdict |
|---|---|---|---|
| `ecmwf_ifs` | `previous_runs` | 95,045 | CLEAN |
| `ecmwf_ifs` | `single_runs` | 545 | CLEAN |
| `gem_global` | `previous_runs` | 95,045 | CLEAN — previous_runs is gem's ONLY live leg (current value served from here per K2 fix) |
| `gem_global` | `single_runs` | 0 | CLEAN — `SINGLE_RUNS_UNSERVABLE_MODELS` gate prevents requests; K2 curl-verified |
| `gfs_global` | `previous_runs` | 95,045 | CLEAN |
| `gfs_global` | `single_runs` | 545 | CLEAN |
| `icon_d2` | `previous_runs` | 1,932 | CLEAN — regional, fewer cities |
| `icon_d2` | `single_runs` | 42 | CLEAN — domain-gated, fewer cities |
| `icon_eu` | `previous_runs` | 12,574 | CLEAN |
| `icon_eu` | `single_runs` | 100 | CLEAN |
| `icon_global` | `previous_runs` | 76,145 | CLEAN |
| `icon_global` | `single_runs` | 545 | CLEAN |
| `icon_seamless` | `previous_runs` | 545 | CLEAN — alias-dedup probe only |
| `icon_seamless` | `single_runs` | 545 | CLEAN — alias-dedup probe only |
| `jma_seamless` | `previous_runs` | 95,045 | CLEAN |
| `jma_seamless` | `single_runs` | 545 | CLEAN |
| `meteofrance_arome_france_hd` | `previous_runs` | 2,301 | CLEAN — regional |
| `meteofrance_arome_france_hd` | `single_runs` | 36 | CLEAN — domain-gated |
| `ncep_nbm_conus` | `previous_runs` | 13,392 | CLEAN — backfill ran successfully today |
| `ncep_nbm_conus` | `single_runs` | 0 | EXPECTED-NEW — promoted 2026-06-09; daily download has not run post-promotion. Curl-verified as available. |
| `ukmo_global_deterministic_10km` | `previous_runs` | 60,264 | CLEAN |
| `ukmo_global_deterministic_10km` | `single_runs` | 0 | EXPECTED-NEW — same as above |
| `ukmo_uk_deterministic_2km` | `previous_runs` | 372 | CLEAN — London-only regional, fewer rows expected |
| `ukmo_uk_deterministic_2km` | `single_runs` | 0 | EXPECTED-NEW — same as above |

**Additional observations:**

- `OPENMETEO_MODEL_IDS` (`src/data/u0r_multimodel_capture.py:54`) does NOT include `ncep_nbm_conus`, `ukmo_global_deterministic_10km`, or `ukmo_uk_deterministic_2km`. These fall back to identity mapping (model name = OM API model name). The backfill at `scripts/backfill_u0r_promoted_model_history.py` confirmed `ncep_nbm_conus` as a valid OM model ID (13,392 rows successfully fetched). However, **there is no explicit entry in `OPENMETEO_MODEL_IDS` for these three new models** — the identity fallback is implicit. If OM ever names a model differently from our internal identifier, this would silently request the wrong model.

- `OPENMETEO_PREVIOUS_RUNS_MODEL_IDS` (`src/data/u0r_multimodel_download.py:281`) contains ONLY `ecmwf_ifs → ecmwf_ifs025`. For all other models (including the three new ones), it falls through to `OPENMETEO_MODEL_IDS.get(model, model)` — the same identity fallback.

- **Replacement forecast sources** (`raw_forecast_artifacts`): `openmeteo_ecmwf_ifs_9km` (1,090 rows) and `ecmwf_aifs_ens` (30 rows). Both have persisted rows; both confirmed operational. AIFS has 0 rows in `raw_model_forecasts` (correct: AIFS is not a U0R Bayes member).

**P5 suspect: implicit identity fallback for ncep_nbm_conus, ukmo_global_deterministic_10km, ukmo_uk_deterministic_2km.** Not a current failure (backfill rows prove the model IDs work), but the absence of explicit entries in `OPENMETEO_MODEL_IDS` means a future OM API rename would silently request the wrong model with no code-level signal. Low immediate risk (models just verified to work), noted for future hardening.

---

## P6 — PROVIDER-FAMILY DOUBLE-COUNT

Sweep: all models in selection + download sets; check for same-provider pairs not covered by `PROVIDER_FAMILIES`.

**Full model set as of 2026-06-09:**

- Anchor (prior): `ecmwf_ifs`
- DECORR_GLOBALS: `gfs_global`, `icon_global`, `gem_global`, `jma_seamless`, `ukmo_global_deterministic_10km`
- GLOBAL_LIKELIHOOD_MODELS: DECORR_GLOBALS + `icon_eu` + `ncep_nbm_conus`
- REGIONAL_MODELS: `icon_d2`, `meteofrance_arome_france_hd`, `ukmo_uk_deterministic_2km`
- Alias-dedup probe (not a fusion member): `icon_seamless`

**PROVIDER_FAMILIES coverage:**

| family | members | pairs covered | verdict |
|---|---|---|---|
| `ICON_FAMILY` | `(icon_d2, icon_eu, icon_global)` | d2↔eu, d2↔global, eu↔global | COVERED |
| `NCEP_FAMILY` | `(ncep_nbm_conus, gfs_global)` | nbm↔gfs | COVERED |
| `UKMO_FAMILY` | `(ukmo_uk_deterministic_2km, ukmo_global_deterministic_10km)` | ukmo_uk↔ukmo_global | COVERED |
| `icon_seamless` alias-dedup | vs `icon_d2` (correlation gate) | seamless↔d2 | COVERED (alias gate) |

**Uncovered pairs evaluated:**

| pair | same-provider? | verdict |
|---|---|---|
| `ecmwf_ifs` (anchor/prior) + `ecmwf_aifs_ens` (AIFS likelihood) | Both ECMWF | NOT-APPLICABLE — AIFS is NOT a U0R Bayes member (0 rows in `raw_model_forecasts`). It enters only the replacement forecast as a separate probabilistic input to a different strategy. No double-count risk in U0R fusion. |
| `ecmwf_ifs` + `ecmwf_ifs025` (previous-runs history bridge) | Both ECMWF | NOT-APPLICABLE — `ecmwf_ifs025` is the same physical product as `ecmwf_ifs`, bridged via `u0r_anchor_bridge.py`. Stored under single `model='ecmwf_ifs'` key. Never a separate fusion member. |
| `jma_seamless` (only JMA product in system) | JMA only | NOT-APPLICABLE — no second JMA product in selection. |
| `meteofrance_arome_france_hd` (only MF product in U0R) | MF only | NOT-APPLICABLE — `openmeteo_arome_fr_hd` exists in registry but `tier="disabled"`, `enabled_by_default=False`. No active second MF product. |
| `gem_global` (only CMC product) | CMC only | NOT-APPLICABLE — no second CMC product. |

**P6 verdict: NO unresolved suspects.** PROVIDER_FAMILIES covers all active same-provider pairs. ECMWF (ifs+AIFS) is not a U0R family concern because AIFS never enters U0R Bayes. MF, JMA, and CMC each have only one active product.

---

## TOP-N SUSPECTS (ranked by money-path impact)

| rank | pattern | file:line | impact | severity |
|---|---|---|---|---|
| 1 | **P2** | `src/ingest_main.py:762–774` — `etl_diurnal_curves.py` and `etl_temp_persistence.py` subprocess stdout WARNINGs silently discarded | **Production ingest daemon scheduler job.** Calibration anomalies, data-gap notices, or model-fit warnings from the daily ETL scripts are invisible to the operator unless the script exits non-zero. Same structural gap as the materializer-queue fix applied today. No rc!=0 needed to produce a WARNING — the script can succeed with degraded output. | HIGH |
| 2 | **P2** | `src/main.py:5546–5568` — `measure_arm_gate_settlement.py` subprocess stdout silently discarded on rc==0 | ARM-gate artifact emission in the live-trading daemon. On successful runs, any WARNING from the producer script (e.g., incomplete settlement data, coverage gap) is lost. The artifact is written but its provenance quality is opaque. | MEDIUM |
| 3 | **P5** | `src/data/u0r_multimodel_capture.py:54` — `OPENMETEO_MODEL_IDS` missing explicit entries for `ncep_nbm_conus`, `ukmo_global_deterministic_10km`, `ukmo_uk_deterministic_2km` | Identity fallback works today (backfill proved it), but any OM API rename would silently request the wrong model for three newly-promoted selection members. The gap is latent: no current failure. | LOW |
| 4 | **P4** | `scripts/oos_validation_harness.py:321` / `scripts/produce_activation_evidence.py:72–73` — hardcoded City fixtures not reading from cities.json | Diverges from cities.json authority for offline validation and evidence scripts. If NYC or London coordinates are re-pinned to match updated WU/METAR stations, these fixtures silently use stale coordinates. Not money-path but could invalidate evidence artifacts. | LOW |
| 5 | **P2** | `src/ingest_main.py:1181–1195` — `etl_forecast_skill.py` stdout truncated to last 2000 chars | WARNINGs emitted early in a long-running ETL run are truncated. Partial information, not total loss. | LOW |

---

*Sweep completed 2026-06-09. All findings are read-only observations. No files were modified.*
