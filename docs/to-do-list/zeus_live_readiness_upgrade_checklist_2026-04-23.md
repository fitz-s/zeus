# Zeus Live-Readiness Upgrade Checklist

Created: 2026-04-23
Branch: `data-improve` (commit `e69352fd955b`)
Status: operational evidence / workbook (NOT authority)
Authority basis: joint converged verdict from structured pro/con Opus debate
2026-04-23, read-only investigation, three rounds + Round 3 convergence,
zero residual disagreement between participants.

**This is a task queue, not durable law. Do not promote items here to
authority by reference. When an item closes, record its closure in the
owning task packet and, if durable, extract into machine manifest / test /
contract / lore card.**

## Executive verdict

**Zeus is NOT LIVE-READY today.** Zeus becomes **CONDITIONAL-LIVE** once
ten coded antibody gates (B1–B5 + G5–G9) are deployed green and the
data-fill blockers (R0-A, R3-A/B, R4-C) close. Live capital committed
today would burn on structurally unverified truth. Live capital after the
ten antibodies land is narrowly defensible on a typed, code-enforced gate
— not on human-memory promises or disposable `settings.json` flags.

## Shared framing — Fitz K<<N

Eighteen silent failures enumerated in
`docs/operations/task_2026-04-23_data_readiness_remediation/plan.md`
resolve to **five structural decisions**, not eighteen independent bugs.

### D1 — ingest co-resident with trading discovery

- Ingest lanes share a daemon process with trading discovery.
- Evidence: `src/main.py:122,140,157,175,238`; auto-pause wrapper
  `src/engine/cycle_runner.py:381-386`.
- Blast radius narrower than "everything co-dies" — four of eight ingest
  lanes kept running through the 5-day auto-pause per
  `state/scheduler_jobs_health.json` — but the boundary around
  `_execute_discovery_phase` is wide enough that a single `ValueError`
  paused entries since `2026-04-18T13:19:12` per `control_overrides`.

### D2 — no schema-writer ↔ live-table alignment antibody

- `src/state/db.py:209` declares `forecasts.rebuild_run_id`.
- `PRAGMA table_info(forecasts)` returns 13 columns; none is
  `rebuild_run_id`.
- Result: `SELECT COUNT(*) FROM forecasts = 0` — zero forecast rows ever
  written. The entire supervised-learning chain has no training
  substrate.

### D3 — no provenance / physical-bounds type gate on inherited data

- Sub-class (a) physically-impossible values: Warsaw 88 °C, Houston
  160 °F, Lagos 89 °C (3 rows / 1.81M).
- Sub-class (b) source-semantic mismatches:
  - HK WMO-half-up vs Polymarket floor containment
    (`docs/operations/known_gaps.md:141-148`, 3/3 HKO mismatches fix
    under floor, 0 regressions across 16 markets).
  - SZ / Seoul / SP / KL / Chengdu WU-API-vs-website (19 mismatches).
  - Taipei 3-source drift (CWA → NOAA → WU/RCSS, 16 mismatches).
- `known_gaps.md:140-160`.
- Fitz Constraint #4: correct code against wrong-source truth.

### D4 — DST authority is prose, not code

- `SELECT COUNT(*) FROM observation_instants_v2 WHERE
  is_missing_local_hour = 1` returns 0 across 1,813,662 rows.
- `delta_rate_per_h` is NULL across the same 1.8M rows.
- `known_gaps.md:9-19` spring-forward gap open.

### D5 — dual-track v2 scaffold is claim without instantiation

- `state/status_summary.json:240-250`:
  `ensemble_snapshots_v2 = 0`, `calibration_pairs_v2 = 0`,
  `platt_models_v2 = 0`, `historical_forecasts_v2 = 0`,
  `settlements_v2 = 0`.
- Same file: `dual_track_scaffold_claimed: true` and
  `discrepancy_flags: ["v2_empty_despite_closure_claim"]`.
- Legacy `settlements`: 1,562/1,562 NULL `winning_bin`; 629/1,562 NULL
  `settlement_value` — Polymarket bin-resolution step has never
  executed.

## Today's posture (evidence anchors)

- **Paused.** `state/status_summary.json:11`
  `entries_pause_reason="auto_pause:ValueError"`;
  `state/auto_pause_failclosed.tombstone` carries `auto_pause:RuntimeError`;
  5-day outage.
- **Empty calibration substrate.** All five v2 tables at zero rows
  despite scaffold-claimed flag; `state/daemon-heartbeat.json` shows
  `mode: live, alive: true` with no gate refusing to boot the live
  daemon against empty v2.
- **Settlement chain incomplete.** 100 % of 1,562 settlements carry
  `winning_bin = NULL`.

## Architecture credit (what already works)

- **Typed atoms deployed.** `ExecutionPrice` (INV-21),
  `SettlementSemantics` per-city rounding for °F/°C, `TemperatureDelta`
  all prevent whole classes of Kelly/rounding/unit bugs from being
  writable. `docs/authority/zeus_current_architecture.md §2.2`.
- **INV-19/20 coded and tested.**
  `tests/test_dual_track_law_stubs.py::test_red_triggers_active_position_sweep`,
  `::test_load_portfolio_degrades_gracefully_on_authority_loss`,
  `::test_chain_reconciliation_three_state_machine`,
  `::test_fdr_family_key_is_canonical`. RED sweep implemented at
  `src/engine/cycle_runner.py:306-320`.
- **Fail-closed is working.** Today's 5-day auto-pause is the antibody
  firing correctly. The trigger surface (D1) is too wide, but egress
  under authority-loss is preserved per INV-20.

## Axis-by-axis convergence

| Axis | Joint finding | Status |
|---|---|---|
| A1 Data plane | ECMWF 16-day gap; `WU_API_KEY` missing (25 failures); `forecasts=0`; HK `observations` frozen 2026-03-31 | **NOT-READY** — fill via B1 + B2 + B3 |
| A2 Source/settlement truth | 100 % NULL `winning_bin`; calibration chain empty | **NOT-READY** — fill via B2 + B4 + B3 |
| A3 Risk actuation | INV-19 RED sweep coded + tested; current `DATA_DEGRADED` blocks entries | **READY for egress** — current blocking is fail-closed credential, not readiness credential |
| A4 Calibration/probability chain | `platt_models_v2=0` → no posterior construction possible | **NOT-READY** — fill via B3 |
| A5 Execution & reconciliation | INV-18/20 coded + tested; residual `known_gaps.md:261-266` `stale_legacy_fallback` path OPEN | **CONDITIONAL** — acceptable after G5 |
| A6 Governance / authority kernel | 22 INV invariants + machine manifests + plan.md enumerating remediation | **ARCHITECTURE-READY** — kernel enforced; data-fill blockers separate |
| A7 Fail-safety invariants | Auto-pause fired correctly; proves fail-closed but reveals D1 scope | **READY** after G9 diagnosability fix |

## Upgrade checklist (consensus)

**B1–B5** address the five structural decisions. **G5–G9** convert the
prose-conditional gate into a typed, code-enforced gate. **G6 + G7 + G8
are the category-impossible triad** (Fitz "make the category impossible,
not just the instance"): after they land, (live trading on uncalibrated
data, live trading by an unallowlisted strategy, live order routing to an
unallowlisted city) all become unconstructable in code, not merely
guarded against in disposable config.

Verified disposable: `config/settings.json:5-7`
`smoke_test_portfolio_cap_usd: 5.0` carries operator comment
*"Not a permanent feature — remove after first full lifecycle"*;
`set_strategy_gate` is advisory-only per
`src/supervisor_api/contracts.py:141`.

G9 is the diagnosability prerequisite for any future R1-class RCA —
different evidence class from invariant antibodies, but blocking on the
same critical path.

### BLOCKERs — required before live cutover

| ID | Item | Files / tests | Acceptance | Priority |
|---|---|---|---|---|
| **B1** | R0-A forecasts schema fix + schema-alignment antibody | `src/state/db.py`, migration adding `rebuild_run_id TEXT`, `data_source_version TEXT`; `tests/test_forecasts_schema_alignment.py` | `PRAGMA table_info(forecasts)` ≡ INSERT column set; `COUNT(*) FROM forecasts > 0` after one `k2_forecasts_daily` run | P0 — wave 1 |
| **B2** | R3-A/B settlement backfill + bin-resolution antibody | `scripts/backfill_settlement_values.py`, `scripts/compute_settlement_winning_bins.py`; `tests/test_settlement_bin_resolution_complete.py` | `COUNT(*) FROM settlements WHERE winning_bin IS NULL == 0`; same for `settlement_value` | P0 — wave 3 |
| **B3** | R4-C calibration refit (TIGGE extractor + ingest + Platt) | cloud-VM TIGGE extractor runbook; `scripts/ingest_grib_to_snapshots.py`; `scripts/rebuild_calibration_pairs_v2.py`; `scripts/refit_platt_v2.py` | `ensemble_snapshots_v2 > 0 AND calibration_pairs_v2 > 0 AND platt_models_v2 > 0` | P1 — wave 4 (operator-dependent) |
| **B4** | R0-B physical-bounds CHECK + per-city `SettlementSemantics` source-binding test | `src/data/observation_instants_v2_writer.py` bounds CHECK; `tests/test_obs_v2_physical_bounds.py`; new test asserting each settlement row's `source_id` matches `architecture/city_truth_contract.yaml` for that (city, target_date) | Poison class rejected at write; source-semantic-mismatch class both coded | P0 — wave 2 |
| **B5** | R0-C DST flag writer fix + backfill + regression antibody | `src/data/observation_instants_v2_writer.py`; one-shot backfill script; `tests/test_obs_v2_dst_missing_hour_flag.py` | London 2025-03-30 / Atlanta 2025-03-09 spring-forward dates carry `is_missing_local_hour=1` | P0 — wave 2 |
| **G5** | Paper/live DB isolation antibody | `tests/test_paper_live_db_isolation.py` | Paper session cannot open `state/risk_state-live.db`; cross-env open raises | P0 — wave 2 |
| **G6** | `LIVE_SAFE_STRATEGIES` typed frozenset + live-boot assertion (category-impossible) | `src/control/control_plane.py` module constant `LIVE_SAFE_STRATEGIES = frozenset({"opening_inertia"})`; boot assert in `src/main.py` under `ZEUS_MODE=live` | Live boot raises if any non-allowlisted strategy reaches discovery | P0 — wave 3 |
| **G7** | `LIVE_SAFE_CITIES` typed frozenset + executor pre-order assertion (category-impossible) | module constant + assertion in `src/execution/executor.py` before CLOB call | Order placement to non-allowlisted city raises before CLOB call | P0 — wave 3 |
| **G8** | Live-boot precondition on calibrated v2 + healthy risk (category-impossible) | `tests/test_live_boot_requires_calibrated_v2.py` plus boot precondition in `src/main.py` | Live daemon refuses to start if any of `{ensemble_snapshots_v2, calibration_pairs_v2, platt_models_v2}` empty, or `strategy_health_snapshot_status == "empty"`, or `risk.level != GREEN`, or `consistency_check.ok != True` | P1 — wave 5 (after B3) |
| **G9** | Structured auto-pause diagnosability | `src/engine/cycle_runner.py:381-386` — write full traceback + cycle_id + `decision_log` row to `state/auto_pause_diagnostics/<cycle_id>.json` *before* tombstone | Next auto-pause yields a diagnosable artifact; precondition for R1-class RCA (plan.md pre-mortem S5 flags pre-G9 RCA as undiagnosable) | P0 — wave 1 |

### CONDITIONALs — required for long-term resilience or gated re-inclusion

| ID | Item | Files | Acceptance | Notes |
|---|---|---|---|---|
| **G10** | R2 ingest decoupling (scripts/ingest/* per plan.md §R2) + import-isolation antibody | `scripts/ingest/__init__.py`, `_shared.py`, `wu_icao_tick.py`, `ogimet_tick.py`, `openmeteo_hourly_tick.py`, `openmeteo_solar_tick.py`, `forecasts_tick.py`, `ecmwf_open_ens_tick.py`, `hko_tick.py`, `hole_scan_tick.py`; `tests/test_ingest_isolation.py` AST-walk; 8 launchd plists; remove `_k2_*_tick` + `_ecmwf_open_data_cycle` from `src/main.py` scheduler | No `src.engine\|execution\|strategy\|signal\|supervisor_api\|control\|observability\|main` imports under `scripts/ingest/`; each tick script runs standalone; ingest log rows per lane in `state/ingest_log.jsonl` | Permanently closes D1; not strictly required for live correctness after G6/G7/G8 land, but required for long-term operational resilience |
| **R1** | Auto-pause RCA *or* explicit decision-to-defer-resume with receipt | `control_overrides` table; logs; `src/control/*`; packet receipt | Receipt doc in `docs/operations/task_2026-04-23_data_readiness_remediation/` (unblocked by G9 after next incident) | G9 is the hard precondition |
| **U1** | HK settlement rounding floor + constitutional review | `src/contracts/settlement_semantics.py` → HK path uses `rounding_rule="floor"`; `tests/test_hk_settlement_floor_rounding.py`; constitutional review packet addressing AGENTS.md line 49 "WMO half-up is universal" | HK excluded from `LIVE_SAFE_CITIES` until both ship | Per `known_gaps.md:141-148`: floor fixes 3/3 HKO mismatches with 0 regressions across 16 markets. Architecture-level change requires constitutional review. |

### NICE-TO-HAVEs — operational hygiene

| ID | Item | Acceptance |
|---|---|---|
| **N1.1** | R5-A Meteostat 46-source dropout recovery | Bulk endpoint probe; if alive, re-run `scripts/fill_obs_v2_meteostat.py` for 2025-07-27 → today; if dead, accept 24 obs/day baseline and document |
| **N1.2** | R5-B Lagos March 2026 gap | Ogimet probe for DNMM; if coverage present, supplemental fill; else allowlist shortfall |
| **N1.3** | R6-A delete 0-byte `state/observations.db` orphan | `ls -la` pre/post; no runtime reader references it |
| **N1.4** | R6-B `delta_rate_per_h` decision | Populate or mark reserved + test absence |
| **N1.5** | R6-C solar_daily 5-city fill | Amsterdam/Guangzhou/Helsinki/Karachi/Manila refreshed |
| **N1.6** | R6-D manual spot-check of 3 suspicious 0.0 °C settlements | Receipt |
| **N1.7** | R6-E fossil `ogimet_metar_fact` Cape Town decision | Promoted to exception or deprecated |
| **N1.8** | R6-F `availability_fact` Warsaw validator coverage gap | Validator covers all cities, not subset (Warsaw 88 °C escaped) |
| **N1.9** | R6-G legacy `observation_instants` DROP (post-dwell) | `grep -rn "observation_instants[^_]" src/ scripts/` returns 0 and table is dropped after +30 day dwell per Gate F |

## Sequencing

Ten antibody gates + five data-fill blockers group into five waves. Each
wave is independently deployable and CI-testable; waves only cross once
their predecessors are green.

**Wave 1 — longest-dark bug + diagnosability (~3 engineering days)**
- `B1` forecasts schema — zero forecast rows ever is the longest
  structurally-masked bug; fixes the supervised-learning substrate.
- `G9` auto-pause diagnosability — precondition for any future R1 RCA;
  next failure becomes debuggable.

**Wave 2 — typed data-integrity + paper/live isolation (~4 engineering days)**
- `B4` physical-bounds CHECK + per-city source-binding test.
- `B5` DST flag writer + backfill + regression test.
- `G5` paper/live DB isolation antibody.

**Wave 3 — settlement completion + positive-credential allowlists (~3 engineering days, parallel with Wave 2 tail)**
- `B2` settlement `settlement_value` + `winning_bin` backfill + completeness antibody.
- `G6` `LIVE_SAFE_STRATEGIES` typed frozenset + boot assertion.
- `G7` `LIVE_SAFE_CITIES` typed frozenset + executor pre-order assertion.

G6 + G7 replace the disposable `settings.json` smoke-test cap and
advisory-only `set_strategy_gate` with code-enforced, CI-testable
invariants.

**Wave 4 — calibration refit (operator-dependent)**
- `B3` TIGGE extractor on cloud VM, ingest, Platt refit. Unknown
  completion date per plan.md pre-mortem S3 — execution is out of local
  control.

**Wave 5 — live-boot category-impossible gate (~1 engineering day after B3)**
- `G8` live daemon refuses to start if v2 is empty, health is empty, or
  risk is not GREEN. Cannot be meaningfully tested until Wave 4 lands.

**Parallel — long-term D1 closure**
- `G10` ingest decoupling. Runs alongside any of the waves; closes D1
  permanently.

**Constitutional packet — HK floor rounding**
- `U1` HK stays out of `LIVE_SAFE_CITIES` until both floor-rounding +
  constitutional review ship.

Live cutover proceeds only on CI-green across all BLOCKERs, B3 verified
producing non-empty v2, G8 green, and operator approval. `G6 + G7 + G8`
are the three category-impossible antibodies that convert Fitz's "make
the wrong code unwritable" from advice to enforcement.

## Residual disagreement

None. The joint verdict recorded zero residual disagreement between
pro-vega (PRO, CONDITIONAL-LIVE thesis) and con-nyx (CON, NOT-READY
thesis) at Round 3 close.

## Closure path

When each item ships and tests go green, record the closure in:

- The owning task packet (most items belong to
  `docs/operations/task_2026-04-23_data_readiness_remediation/`; G9
  belongs alongside R1; G6/G7/G8 warrant a new execution packet labeled
  "Zeus Live Cutover Gates").
- If the item produces durable law (a new INV, a new typed atom, a new
  manifest clause), extract into the appropriate machine manifest /
  test / contract / lore card. Do **not** leave durable law inside this
  workbook.
- Mark the row "CLOSED YYYY-MM-DD — packet/receipt link".

When this workbook is fully closed, record closure in
`docs/operations/current_state.md` and demote this file to evidence per
`docs/authority/zeus_current_delivery.md §10` (demotion, not deletion).

## Cross-references

- Five structural decisions ↔ 18 symptoms inventory:
  `docs/operations/task_2026-04-23_data_readiness_remediation/plan.md`
- Runtime semantic law:
  `docs/authority/zeus_current_architecture.md`
- Delivery/change-control:
  `docs/authority/zeus_current_delivery.md`
- Current-fact surfaces:
  `docs/operations/current_state.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`
- Machine invariants:
  `architecture/invariants.yaml`
- Fatal misreads antibodies:
  `architecture/fatal_misreads.yaml`
- HK floor-rounding evidence (U1):
  `docs/operations/known_gaps.md:141-148`
- Paper-position `token_id=""` stale-fallback evidence (A5 residual):
  `docs/operations/known_gaps.md:261-266`

## Provenance

- Debate team: `zeus-live-readiness-debate` (native Claude Code team;
  team config at `~/.claude/teams/zeus-live-readiness-debate/config.json`).
- Participants: `pro-vega` (Opus architect, CONDITIONAL-LIVE thesis),
  `con-nyx` (Opus architect, NOT-READY thesis).
- Judge: team-lead (this session).
- Mode: read-only architectural inquiry. No source, state, or
  `.code-review-graph/graph.db` mutation occurred during the debate.
- Rounds: Round 0 openings → Round 1 rebuttals → Round 2 closes →
  Round 3 convergence, all conducted A2A peer-to-peer with zero residual
  disagreement at close.
- Judge spot-verified load-bearing citations before accepting the
  verdict:
  - `forecasts` table: 0 rows, no `rebuild_run_id` column.
  - `observation_instants_v2`: 1,813,662 rows, `SUM(is_missing_local_hour)=0`, 3 physically-impossible rows.
  - `settlements`: 1,562/1,562 NULL `winning_bin`, 629/1,562 NULL `settlement_value`.
  - `src/main.py`: scheduler-job co-residence of four ingest lanes
    inside the trading daemon.
  - `src/state/db.py:209`: `rebuild_run_id` declared in schema but
    physical table lacks it.
