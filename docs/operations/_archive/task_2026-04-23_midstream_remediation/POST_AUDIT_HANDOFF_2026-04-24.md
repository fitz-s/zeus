# Post-Audit Handoff — Training-Readiness Blocker Register

Author: team-lead (2026-04-24 session, post-compaction-ready form)
Target: next session's team-lead OR any agent resuming the
training-readiness workstream.
Status: 17 session commits shipped + 4-subagent adversarial audit
complete + cross-checked against operator-owned `tigge_data_training_
handoff_2026-04-23.md` + existing `zeus_world_data_forensic_audit_
package_2026-04-23/`.
Verdict: **TRAINING-BLOCKED** via forensic P0→P4 progression.

---

## 1. Why this file exists

Operator directive (2026-04-24): "complete all remaining tasks, then
run multi-subagent multi-angle review of upstream integrity, focused
on logical issues + unexpected problems. TIGGE data is downloaded;
ensure system is ready for training."

17 session commits closed every task in the immediate packet queue +
applied migrations to live `state/zeus-world.db`. Four parallel
adversarial subagents (data-flow / provenance / training-readiness /
gotcha-finder) audited the result. Cross-check against the forensic
audit package found **17 of 19 my-subagent claims VALID, 1 refined,
1 needs-reverify, PLUS 7 additional forensic findings my subagents
missed**.

This file:
1. Captures the authoritative state at `origin/data-improve` HEAD `6a8e974`.
2. Enumerates every finding (39 total across both audits) with
   severity, evidence, and one-sentence fix direction.
3. Maps remaining work against the forensic P0→P4 sequencing at
   `zeus_world_data_forensic_audit_package_2026-04-23/17_apply_order.md`.
4. Provides per-finding fix recipes with exact file:line anchors.
5. Surfaces open questions requiring operator decision.
6. Gives the next session a zero-context resumption protocol.

**Read this file first** after `docs/operations/current_state.md`.

---

## 2. Authoritative session state (post HEAD `6a8e974`)

### Commits shipped this session (chronological)

| # | Commit | Scope |
|---|---|---|
| 1 | `a29fd5b` | S2.3 — SHA-256 rollback snapshot sidecars (4 new sidecars) |
| 2 | `619b278` | **REOPEN-1** — forecasts schema ALTER (rebuild_run_id + data_source_version) |
| 3 | `69520ba` | S2.1 — `settlements_authority_monotonic` trigger v2 (json_type+length gate) |
| 4 | `cdfd558` | S2.4 — strict canonical bin label parser (NH-E1) |
| 5 | `3517282` | Schema migration runbook (embedded in work_log) |
| 6 | `c7784ec` | S2.5 — AST antibody against harvester format-range regression (NH-E2) |
| 7 | `b580521` | S6.1 — DR-33-B packet stub (embedded in work_log) |
| 8 | `f8f403e` | S2.2 — `settlements_verified_{insert,update}_integrity` triggers (AP-2) |
| 9 | `092d263` | S3.1 — `zeus_current_architecture.md` §4.2 INV-14 + triggers refresh |
| 10 | `36c5f1d` | S1.3 — T2.g real v2 fixture + DT7 positive+negative antibodies |
| 11 | `6c5a29a` | REOPEN-2 packet stub (embedded in work_log) |
| 12 | `3eaa772` | Critic-opus 3 LOW findings fix (doc corrections) |
| 13 | `56b0749` | A1+A2 — 4 live-DB migrations applied + REOPEN-2 UNIQUE live rebuild |
| 14 | `2a62623` | A3 / DR-33-B — `append_many_and_project` SAVEPOINT refactor |
| 15 | `685e421` | A4 / C7 — `observation_instants_v2` INV-14 spine + training fields |
| 16 | `a7fc74c` | A5 — `current_state.md` CONDITIONAL-ACHIEVED posture |
| 17 | `6a8e974` | Post-audit — NULL-metric trigger + dual-write INSIDE sp_candidate_* |

All pushed to `origin/data-improve`. HEAD: `6a8e974`.

### Live DB state post-migration (2026-04-24, verified by probe)

```
state/zeus-world.db
├── forecasts: 15 cols (rebuild_run_id + data_source_version added), 0 rows
│   → k2_forecasts_daily cron succeeds on next 07:30 UTC tick (pending verification)
├── settlements: 14 cols, 1,561 rows (1,469 VERIFIED + 92 QUARANTINED)
│   ├── UNIQUE(city, target_date, temperature_metric) ← REOPEN-2 rebuild
│   ├── settlements_authority_monotonic (v2 with json_type check) ← S2.1
│   ├── settlements_verified_insert_integrity ← S2.2
│   ├── settlements_verified_update_integrity ← S2.2
│   └── settlements_non_null_metric ← post-audit fix (rejects NULL metric INSERT)
├── observation_instants_v2: 32 cols, 1,813,662 rows
│   ├── INV-14 spine columns present (ALL NULL on existing rows)
│   ├── training_allowed DEFAULT 1 (⚠ silent training-eligible on 1.8M rows)
│   ├── causality_status DEFAULT 'OK'
│   └── source_role (NULL on all rows; writer doesn't populate)
├── observations: 42,749 rows (42,743 VERIFIED); 39,431 with empty provenance_metadata
├── ensemble_snapshots_v2: 0 rows (awaiting TIGGE ingest)
├── calibration_pairs_v2: 0 rows (awaiting Stage 2)
├── calibration_pairs (legacy): 0 rows
├── platt_models_v2: 0 rows (awaiting Stage 3)
├── settlements_v2: 0 rows
├── market_events + market_events_v2 + market_price_history: 0 rows
└── historical_forecasts + historical_forecasts_v2: 0 rows
```

### Operational state (probe: scheduler_jobs_health.json + status_summary.json)

- `daemon-heartbeat.json`: alive=true, mode=live, last heartbeat stale
- `state/LIVE_LOCK`: `LIVE PAUSED`
- `state/status_summary.json`: `entries_paused=true`,
  `entries_pause_reason="auto_pause:ValueError"`, `risk.level="RED"`,
  `portfolio_position_count=0`, wallet ~$94.94
- `state/auto_pause_failclosed.tombstone`: since 2026-04-16 (7+ days old)
- **FAILED scheduler jobs**: `k2_forecasts_daily` (fixed in code pending
  07:30 UTC tick), `k2_daily_obs` (missing `WU_API_KEY`),
  `ecmwf_open_data`, `harvester` (pagination failure)

### Critic-opus team status

`~/.claude/teams/zeus-midstream-critic/config.json` — team lead + critic-opus
still registered. Critic-opus processed 13 commits + signed off clean on
the 5-slice batch (S2.5 / S6.1 / S2.2 / S3.1 / runbook) with 3 LOW
findings all addressed in `3eaa772`. Idle and standing by.

**Rehydration protocol** if team is missing on resume: `TeamCreate` new
team + `Agent({subagent_type: architect, model: opus, team_name, name: critic-opus})`
with bootstrap prompt pointing to this handoff + recent commit trail.
Do NOT re-spawn critic inside `zeus-live-readiness-debate` team — that
team's con-nyx was silent across the prior handoff session; new team is
the working lane.

---

## 3. Complete findings registry (39 findings)

### 3.1 My-subagent findings (19 total)

#### 🔴 CRITICAL — training-blocking, must-fix before `refit_platt_v2 --no-dry-run`

**C1 — TIGGE local state vs cloud state**
- Source: subagent 3 (training-readiness)
- Original claim: "TIGGE data on disk is wrong parameter (param_167_128 = 2t instantaneous) — extractors need mx2t6/mn2t6"
- **Refined after cross-check**: Handoff doc `tigge_data_training_handoff_2026-04-23.md §1 point 7` + §3 state that 342,312 mx2t6 + 342,312 mn2t6 JSON files were extracted on CLOUD (integrity passed). Subagent 3 was looking at local raw backup gribs; the extracted JSON is at `/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max/` (cloud-only, not accessible from local).
- **Corrected severity**: HIGH ops-blocker (not CRITICAL data-corruption)
- **Fix**: per handoff §7 step 1 — "Copy or rsync the extracted JSON asset from cloud to local" (~5.54 GiB total)

**C2 — TIGGE directory layout**
- Source: subagent 3
- **Refined**: cloud directory IS correctly suffixed (`tigge_ecmwf_ens_mx2t6_localday_max/` + `tigge_ecmwf_ens_mn2t6_localday_min/`). Local directory absent entirely.
- Fix: same as C1

**C3 — Low-track Kelvin contradiction**
- Source: subagent 1 (data-flow)
- Claim: `scripts/extract_tigge_mn2t6_localday_min.py:106,365` emits `unit="K"`; ingest `_UNIT_MAP` at `scripts/ingest_grib_to_snapshots.py:61-69` only knows `{"C":"degC","F":"degF"}` → raises `ValueError` on low-track JSON
- **Needs re-verify**: handoff §3 says "mn2t6_low: files_checked=342,312, failures=0" — JSON validation PASSED. Either (a) subagent 1 cited Python constants that aren't load-bearing at JSON emit boundary, or (b) the validation doesn't check the unit dictionary path, or (c) the subagent's inferred failure mode is future — ingest hasn't actually been run yet.
- Fix direction: run `ingest_grib_to_snapshots.py --track mn2t6_low --dry-run` on one low JSON file, verify whether ValueError actually fires. Then decide.

**C4 — Low-track settlements structurally absent**
- Source: subagent 1 + subagent 2
- Evidence: live DB probe — ALL 1,561 settlements have `temperature_metric='high'`; zero low rows. `src/execution/harvester.py:766` hardcodes `"high"`; no LOW writer exists on any code path.
- **CONFIRMED by forensic #3 CRITICAL** "legacy settlements cannot represent high/low or multiple markets"
- Fix: add low-track settlement writer OR hard-fail low-track calibration rebuild until low settlements exist

**C5 — Harvester split-brain calibration writer**
- Source: subagent 2
- Evidence: `src/execution/harvester.py:1066-1079` LOW branch calls `add_calibration_pair_v2` with full metric identity; `:1081-1091` HIGH branch calls LEGACY `add_calibration_pair` → writes to `calibration_pairs` (no INV-14). `scripts/refit_platt_v2.py:84-116` reads ONLY v2 with metric filter → **HIGH pairs NEVER reach v2 trainer**.
- Fix: route HIGH branch through `add_calibration_pair_v2(metric_identity=HIGH_LOCALDAY_MAX, ...)`; delete or gate legacy table.

**C6 — Harvester physical_quantity mismatch with canonical spine**
- Source: subagent 2
- Evidence: harvester hardcodes `physical_quantity="daily_maximum_air_temperature"` at `:762-768`, but canonical `HIGH_LOCALDAY_MAX.physical_quantity="mx2t6_local_calendar_day_max"` at `src/types/metric_identity.py:82`. Any filter by `physical_quantity` silently drops all 1,561 settlements.
- Fix: replace hardcoded strings with `metric_identity.physical_quantity`; split `_HARVESTER_LIVE_DATA_VERSION` per metric

**C7 — observation_instants_v2 writer has zero INV-14 awareness**
- Source: subagent 2
- Evidence: `ObsV2Row` dataclass at `src/data/observation_instants_v2_writer.py:94-133` has no fields for 6 new INV-14 columns. `_INSERT_SQL:265-278` doesn't reference them. All new rows get NULL metric + DEFAULT 1 training_allowed.
- **CONFIRMED by forensic #7 HIGH** "v2 hourly observations lack metric/training/causality fields"
- Fix: extend `ObsV2Row` with 6 required fields; add construction-time `MetricIdentity` requirement mirroring `src/calibration/store.py:116 _resolve_training_allowed`

**C8 — C7 `training_allowed DEFAULT 1` defeats its own purpose**
- Source: subagent 4 + subagent 2
- Evidence: 1.8M existing rows have `training_allowed=1` via ALTER DEFAULT; reader filtering on `WHERE temperature_metric='high'` gets 0 rows; reader NOT filtering silently mixes metrics. No backfill script scheduled.
- Fix: change `DEFAULT 1` → `DEFAULT NULL` so unset rows announce themselves; update writer to populate explicitly; add reader assertion `COUNT WHERE temperature_metric IS NULL = 0`

**C9 — NULL-NULL UNIQUE hole on settlements** — ✅ **FIXED in `6a8e974`**
- Source: subagent 4 (live DEMO)
- Evidence: `temperature_metric TEXT CHECK (IS NULL OR IN ('high','low'))` tolerated NULL; SQLite UNIQUE treats NULL as distinct → unlimited NULL-metric duplicates. `scripts/onboard_cities.py:383` writer emits NULL-metric scaffold rows.
- Fix applied: `settlements_non_null_metric` BEFORE INSERT trigger rejects any NULL-metric row (regardless of authority). Live DB applied.
- **Remaining**: add antibody test; fix `onboard_cities.py:383` to supply metric explicitly.

#### 🟠 HIGH — silent-success risks / relationship drift

**H1 — DR-33-B torn-state window** — ✅ **FIXED in `6a8e974`**
- Source: subagent 4
- Evidence: `src/engine/cycle_runtime.py:1246-1252` placed `_dual_write_canonical_entry_if_available` OUTSIDE sp_candidate_*; any dual-write failure left trade_decisions + execution_report committed without matching position_events.
- Fix applied: moved `_dual_write` INSIDE sp_candidate_* try-block; stale comment replaced; ROLLBACK TO sp_candidate_* now unwinds all three on any failure.

**H2 — `append_event_and_project` sibling still uses `with conn:`**
- Source: subagent 4
- Evidence: `src/state/ledger.py:173` still has `with conn:` pattern; grep confirms zero production callers in `src/` or `scripts/` but the function is EXPORTED from `src/state/db.py:19`. Passive landmine: next caller inside a SAVEPOINT silently breaks rollback.
- Fix: delete `append_event_and_project` function + remove import from `src/state/db.py:19`

**H3 — Cross-table joins silently ignore temperature_metric**
- Source: subagent 2 (4 concrete sites cited)
- Evidence:
  - `src/engine/monitor_refresh.py:471-475` — persistence delta SELECT
  - `scripts/etl_historical_forecasts.py:141-148` — forecast × settlement JOIN
  - `scripts/validate_dynamic_alpha.py:168-177` — same pattern
  - `scripts/etl_forecast_skill_from_forecasts.py:109` — same pattern
- Fix: add `AND s.temperature_metric = f.temperature_metric` to every JOIN; add `scripts/semantic_linter.py` rule analogous to K2 struct rule

**H4 — Empty-provenance observations trusted via VERIFIED authority**
- Source: subagent 2
- Evidence: 39,431 / 42,749 VERIFIED observations have empty `provenance_metadata`. P-E settlements reconstruction referenced these rows via `obs_id`. No read-side check rejects settlements backed by empty-provenance obs.
- **Forensic #5 CRITICAL** upgrade "WU daily obs empty provenance; rows VERIFIED but cannot be reproduced"
- Fix: add read-side gate OR backfill `observations.provenance_metadata` for 39,431 rows (requires logs/source payloads — forensic §14 open question)

**H5 — k2_forecasts_daily success UNVERIFIED**
- Source: subagent 4
- Evidence: scheduler_jobs_health still shows `status=FAILED` with no `last_success_at`. Next cron tick scheduled 07:30 UTC; commit landed 01:31 UTC. Forward-looking claim, not evidence-backed.
- Fix: one-off `python -m src.main` tick of `_k2_forecasts_daily_tick` + verify `SELECT COUNT(*) FROM forecasts > 0`

**H6 — Forecasts table 0 rows; not populated by TIGGE**
- Source: subagent 1
- Evidence: TIGGE feeds `ensemble_snapshots_v2`, NOT `forecasts`. Any LEFT JOIN forecasts × ensemble gets NULL-filled features → numpy coerces to 0.0 → biased model trained on absent feature.
- **Forensic #2 CRITICAL** upgrade "coverage ledger confirms forecast missingness; forecast silence can be mistaken for no data needed"
- Fix: explicit check in `rebuild_calibration_pairs_v2.py` preflight that rejects runs when consumers need forecasts but forecasts=0

**H7 — Missing WU_API_KEY + ecmwf_open_data FAILED**
- Source: subagent 3 + subagent 4
- Evidence: `state/scheduler_jobs_health.json::k2_daily_obs.last_failure_reason="WU_API_KEY environment variable is required but not set"`
- Fix: operator env repair; if WU_API_KEY rotated, update `~/.zeus/.env` or equivalent

**H8 — Settlement authority mono-source**
- Source: subagent 3
- Evidence: 1,400/1,469 VERIFIED settlements are `wu_icao_history_v1`. 14 HKO, 55 ogimet. WU SZ/Seoul/SP/KL/Chengdu has documented 19-case API-vs-website mismatch per `docs/to-do-list/zeus_live_readiness_upgrade_checklist_2026-04-23.md:60`.
- Fix: operator-level decision — accept WU-dominant training or re-authorize against HKO/ogimet canonical sources

**H9 — auto_pause 7-day tombstone**
- Source: subagent 4
- Evidence: `state/auto_pause_failclosed.tombstone` since 2026-04-16; system has not been cleanly operational for a week.
- Fix: operator triage — clear tombstone + un-pause OR explicitly accept paused posture as training-safe

**H10 — TIGGE cloud path contains literal space**
- Source: subagent 4
- Evidence: path literally `/data/tigge/workspace-venus/51 source data/raw/...`. scripts embed space in subprocess call → `ecmwf_open_data` fails on shell split.
- Fix: either rename cloud directory (remove space) OR audit subprocess wrappers to quote paths

#### 🟡 MEDIUM

**M1 — `setdefault("causality", {"status":"OK"})`** at `scripts/ingest_grib_to_snapshots.py:164` defeats contract Law 5 rejection rule (subagent 1)

**M2 — Harvester stale UNIQUE comment** at `src/execution/harvester.py:750` still references pre-REOPEN-2 `UNIQUE(city, target_date)` (subagent 1)

**M3 — Observation `data_version` namespace disjoint from canonical** (subagent 2). Observations use `v1.wu-native`; ensemble uses `tigge_mx2t6_local_calendar_day_max_v1`. Should rename `CANONICAL_DATA_VERSIONS` → `CANONICAL_ENSEMBLE_DATA_VERSIONS` + add parallel allowlists.

**M4 — `source_role` column NULL on all rows** (subagent 2). C7 ALTER added the column but writer doesn't populate. Fallback-vs-canonical indistinguishable at SQL-level.

### 3.2 Forensic audit findings (20 total) — additions my subagents missed

Cross-ref: `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/12_major_findings.md`

**F1 — Empty causal forecast/training/replay spine** — CRITICAL. All v2 tables 0 rows. OVERLAP with my C1/H6.

**F2 — Coverage ledger confirms forecast missingness** — CRITICAL. `data_coverage` table tracks forecasts as MISSING at large scale. MINE: my H6 noted forecasts=0 but didn't check coverage ledger.

**F3 — Legacy settlements cannot represent high/low** — CRITICAL. OVERLAP with my C4 + REOPEN-2 (pre-session schema was UNIQUE(city, target_date); REOPEN-2 fixed that, but still no LOW writer).

**F4 — Settlement rows lack market identity** — CRITICAL. `market_slug NULL` for all 1,561 rows. OVERLAP with my A4-deferred C5.

**F5 — WU daily observations have empty provenance** — CRITICAL (forensic rank). OVERLAP with my H4 (I ranked HIGH; forensic correctly ranks CRITICAL).

**F6 — Legacy hourly table not time-safe** — HIGH. `hourly_observations` lacks UTC/timezone/provenance. My subagents didn't audit this table.

**F7 — V2 hourly observations lack metric/training/causality** — HIGH. OVERLAP with my C7.

**F8 — Current v2 observation view mixes fallback sources** — HIGH. `observation_instants_current` mixes rows by data_version='v1.wu-native' regardless of source role. OVERLAP with my C7/M4.

**F9 — Settlement source cannot join to observation source** — HIGH. URL source vs short-tag source; exact SQL join returns zero matches. My subagents didn't test this join.

**F10 — HKO observation and settlement semantics differ** — HIGH. Decimal HKO obs vs integer settlement. Subagent 1 noted HK as caution path; forensic made the oracle-transform explicit.

**F11 — Forecast `available_at` may be reconstructed** — HIGH. `forecasts_append.py` / `etl_historical_forecasts.py` may heuristically derive issue/available time → hindsight leakage risk. **My subagents completely missed this.** Critical training-readiness finding.

**F12 — TIGGE/v2 forecast architecture exists but no rows** — MEDIUM. OVERLAP with my C1.

**F13 — Market event tables are empty** — CRITICAL. `market_events`, `market_events_v2`, `market_price_history` all 0 rows → Polymarket REPLAY IMPOSSIBLE. My subagent 3 noted empty but framed as "upstream broken" not "replay-blocked".

**F14 — Data coverage omits canonical v2 forecast/settlement families** — MEDIUM. Coverage ledger tracks limited table set; v2 gaps invisible.

**F15 — Backfills can silently partially succeed** — HIGH. `backfill_obs_v2.py`, Ogimet, Meteostat scripts can continue after failed chunks. **My subagents didn't test this.**

**F16 — INSERT OR REPLACE erases audit history** — MEDIUM. `observation_instants_v2_writer.py` + backfill scripts use REPLACE without hash-equality check. **My subagents missed.**

**F17 — Derived features inherit unsafe labels** — MEDIUM. `diurnal_curves`, `diurnal_peak_prob`, `temp_persistence`, `solar_daily` computed on upstream rows whose lineage isn't explicit. **My subagents missed the derived-features downstream.**

**F18 — Open-Meteo used as fallback but not tagged** — HIGH. `observation_client.py`, `hourly_instants_append.py`, `forecasts_append.py` treat Open-Meteo as model/forecast/fallback but without source_role tagging. OVERLAP with my M4 but more specific.

**F19 — Meteostat bulk lag/aggregation not encoded in eligibility** — MEDIUM. `meteostat_bulk_client.py` appears VERIFIED even though it's bulk+lagged. **My subagents didn't test.**

**F20 — Polymarket finalization/revision policy not in settlement key** — HIGH. `settlements` / `market_events_v2` lack market rules about final values. Late revisions silently contaminate labels. **My subagents missed.**

### 3.3 Forensic audit ruling (categorical)

Per `11_data_readiness_ruling.md`:

**SAFE NOW**:
- Read-only forensic auditing
- Evidence exploration of station observations with caveats
- Gap analysis using `data_coverage`
- Development of v2 migrations/guards using existing empty v2 scaffolding

**UNSAFE NOW**:
- **Live trading decisions** that treat current DB rows as canonical settlement truth
- **Calibration/training from uploaded DB**
- Forecast probability replay
- Exact Polymarket market replay
- Settlement reconstruction where market identity/source finalization must be proven
- Any canonical use of `hourly_observations`

**Direct implication**: my session's structural hardening (migrations, triggers, antibody tests) does NOT by itself move the system from UNSAFE to SAFE for calibration training. The forensic ruling requires the P0→P4 apply-order progression below.

### 3.4 Forensic P0→P4 apply order vs my session progress

Per `17_apply_order.md`:

| Packet | Forensic scope | My session progress |
|---|---|---|
| **P0 Data audit containment** | Read-only data-readiness command; fail-closed guards for empty v2 tables; evidence-only views for legacy; regression tests proving unsafe rows rejected | **~30% done**: REOPEN-2 UNIQUE, S2.1+S2.2 triggers, S2.3 SHA-256, C7 ALTER. Missing: evidence-only views; explicit empty-table preflight guards; readiness query |
| **P1 Provenance hardening** | Retrofit provenance requirements for daily obs + settlements; payload hash/source URL/parser/station registry fields; quarantine WU empty-provenance rows; canonical source-role registry + eligibility views | **~0% done**: C6 (observations provenance retrofit 39,431 rows) deferred; no source-role registry; no eligibility views |
| **P2 Backfill guardrails** | Dry-run default on all repair scripts; completeness manifests + expected counts + fail thresholds; `INSERT OR REPLACE` → hash-checked idempotence or revision history | **~0% done**: F15 + F16 unaddressed |
| **P3 Usage path hardening** | Safe-view-only readers for calibration/replay/live; ban `hourly_observations` from canonical paths; require `training_allowed=1 AND causality_status='OK' AND eligible source role AND market identity` for training | **~10% done**: settlements triggers (S2.1/S2.2) enforce row-integrity; broader consumer readers not gated |
| **P4 Populate canonical v2 truth** | Backfill `settlements_v2` from verified payloads; populate forecast/ensemble v2 with true issue/available/fetch times; rebuild calibration only after P0-P3 pass | **0%, correctly deferred** per forensic ordering |

**Net verdict**: my session is approximately 10% of the forensic apply-order through the system. The 17 commits represent meaningful P0-partial + P3-partial work but zero P1/P2. Training-ready requires P0-P3 complete + P4 executed with verified inputs.

---

## 4. Fix packages — prioritized remediation recipes

Each fix is a self-contained slice with file:line anchors, expected
behavior change, and acceptance criteria. Sized small to medium so the
next session can tackle them incrementally.

### 4.1 Immediate — complete in-flight post-audit work

#### Fix 4.1.A: Add NULL-metric antibody test + fix onboard_cities scaffold
- Context: C9 trigger applied in `6a8e974` but (a) no test yet pins the rejection, (b) `scripts/onboard_cities.py:383` still writes NULL-metric rows which will now fail at insert
- Anchor: `scripts/onboard_cities.py:382-385`
- Change: supply `temperature_metric='high'` explicitly; OR delete the scaffold path entirely if unused
- Test: `tests/test_settlements_unique_migration.py` — add `test_null_metric_insert_rejected` that verifies trigger fires on INSERT with NULL metric
- Acceptance: pytest green; `scripts/onboard_cities.py` either passes new UNIQUE+trigger OR doesn't call INSERT

#### Fix 4.1.B: Delete `append_event_and_project` landmine
- Context: H2 — zero callers, but still exports the `with conn:` pattern
- Anchor: `src/state/ledger.py:157-183` (approximate); `src/state/db.py:19` re-export
- Change: delete function from ledger.py; delete import from db.py
- Test: grep confirms zero remaining references; regression pytest suite unaffected
- Acceptance: `grep -rn "append_event_and_project" src/ scripts/ tests/` returns only test/memory artifacts

#### Fix 4.1.C: Hardcode-string cleanup in harvester
- Context: M2 stale UNIQUE comment at `src/execution/harvester.py:750`
- Change: comment update (trivial) + grep-scan for other `UNIQUE(city, target_date)` string literals
- Acceptance: comment matches REOPEN-2 reality

### 4.2 Forensic P0 closure — data-audit containment

#### Fix 4.2.A: Readiness query + fail-closed guards for empty v2 tables
- Context: forensic P0 item 1+2; C1/C2/H5/F1/F2/F13
- Scope: new `scripts/zeus_readiness_check.py` (enforcement class) that verifies minimum row counts per table before any "safe to train" claim
- Thresholds per table:
  - `forecasts > 0` (recent per `retrieved_at`)
  - `ensemble_snapshots_v2 > 0` per (city, target_date, metric)
  - `calibration_pairs_v2 > MIN_DECISION_GROUPS` per metric
  - `settlements > 0` AND `0 < market_slug_null_count` (if C5 fix has landed, else allow NULL)
  - `market_events_v2 > 0` (if P4 done)
- Output: JSON with `{table: {count, status, threshold, met}}`; exit 0 if ALL met, exit 1 otherwise
- Antibody test: pytest that seeds a scratch DB with exactly the threshold and asserts exit 0; then removes one row and asserts exit 1

#### Fix 4.2.B: Evidence-only views for legacy tables
- Context: forensic P0 item 3; F6 "hourly_observations not time-safe"
- Scope: CREATE VIEW `v_evidence_hourly_observations AS SELECT ... WHERE ... /* evidence only */ FROM hourly_observations;` + consumer tests that use the view instead of base table
- Fix: add view to `src/state/db.py::init_schema`; add grep lint rule that rejects `FROM hourly_observations` in `src/calibration/` / `src/engine/` / `scripts/refit*/` etc.
- Acceptance: linter catches bare `FROM hourly_observations` in canonical paths

#### Fix 4.2.C: Market-events empty-table preflight for replay consumers
- Context: F13 CRITICAL; replay engine exists but market_events is 0 rows
- Scope: add startup assertion to `src/replay/*` (if any exists) that raises if `market_events_v2` is empty; same for `src/execution/executor.py` live path

### 4.3 Forensic P1 closure — provenance hardening (LARGEST work)

#### Fix 4.3.A: WU observations empty-provenance triage
- Context: F5 CRITICAL + H4 (39,431 rows)
- Open question from forensic §14: "What source payloads or logs, if any, exist for the 39,431 empty-provenance WU daily observation rows?"
- Three options per forensic recommendation:
  - **A1**: reconstruct provenance from `source` + `imported_at` + `station_id` → low-confidence, risks implying forensic-grade certainty (see my A4 assessment)
  - **A2**: quarantine the 39,431 rows (set `authority='QUARANTINED'` + `quarantine_reason='empty_provenance_wu_daily'`) until reconciled
  - **A3**: build a log-replay tool that reads historical fetcher logs + reconstructs provenance_metadata deterministically
- Recommendation: A2 (quarantine) as default P1 closure; A3 as follow-up if logs exist

#### Fix 4.3.B: Payload hash + source URL + parser version on observation writers
- Context: forensic P1 item 2
- Scope: extend `ObsV2Row` + daily observations writer contracts to require:
  - `payload_hash` (sha256 of raw API response body)
  - `source_url` (the exact endpoint hit)
  - `parser_version` (e.g., `wu_icao_parser_v3`)
- Touch: `src/data/observation_instants_v2_writer.py`, `src/data/observation_client.py`, every `scripts/backfill_*.py` that writes obs
- Antibody test: writer rejects payloads missing any of the 3 fields

#### Fix 4.3.C: Canonical source-role registry + eligibility views
- Context: F18 + M4 + F8 (source_role column exists but NULL)
- Scope: 
  - Create `architecture/source_role_registry.yaml` enumerating every `source` string with its canonical role (`settlement_truth` / `day0_monitor` / `historical_hourly` / `forecast_skill` / `fallback` / `model_only`)
  - Writer: populate `source_role` column at every obs/settlement/ensemble write boundary
  - Backfill: run once to populate existing rows via source → role mapping
  - Eligibility view: `v_training_eligible_observations AS SELECT ... WHERE source_role='settlement_truth' OR source_role='historical_hourly' ... AND training_allowed=1 AND causality_status='OK'`
- Antibody: writer rejects `source_role` not in registry; calibration reader uses view only

### 4.4 Forensic P2 closure — backfill guardrails

#### Fix 4.4.A: INSERT OR REPLACE → hash-checked upsert
- Context: F16 MEDIUM
- Scope: replace all `INSERT OR REPLACE` in backfill scripts with `INSERT ... ON CONFLICT DO UPDATE` that checks `payload_hash` equality; if hash differs, write a revision row to `observation_revisions` instead of overwriting
- Touch: `src/data/observation_instants_v2_writer.py`, `scripts/backfill_*.py` (grep-inventory)

#### Fix 4.4.B: Backfill completeness manifests + fail thresholds
- Context: F15 HIGH
- Scope: every backfill script requires `--expected-count` + `--fail-threshold-percent` flags; writes `tmp/backfill_manifest_{script}_{date}.json` summarizing success/fail counts; exits non-zero on threshold breach
- Touch: `scripts/backfill_obs_v2.py`, ogimet/meteostat scripts, `scripts/backfill_hourly_openmeteo.py`, `scripts/backfill_wu_daily_all.py`, `scripts/backfill_hko_daily.py`

### 4.5 Forensic P3 closure — usage path hardening

#### Fix 4.5.A: Cross-table join metric filter (H3)
- Context: 4 concrete sites from subagent 2
- Changes:
  - `src/engine/monitor_refresh.py:471-475` — add `AND temperature_metric = ?` bind param
  - `scripts/etl_historical_forecasts.py:141-148` — add `AND f.temperature_metric = s.temperature_metric`
  - `scripts/validate_dynamic_alpha.py:168-177` — same
  - `scripts/etl_forecast_skill_from_forecasts.py:109` — same
- Antibody: `scripts/semantic_linter.py` rule that greps for `JOIN settlements` / `FROM settlements` in `scripts/` + `src/` and asserts `temperature_metric` is in the predicate

#### Fix 4.5.B: observation_instants_v2 reader gate
- Context: C7 + C8 + F7
- Design question (forensic §14, meta-question from subagent 2): should hourly observations be metric-tagged at ingest (single-valued) or tagged at daily-aggregate layer (multi-valued per row)?
  - Subagent 2 argues metric identity belongs at daily-aggregate layer; C7 ALTER may have been premature
  - If daily-aggregate: revert C7 columns on `observation_instants_v2`; add separate `daily_observations_v2` with HIGH and LOW as distinct rows
  - If instant-level: extend `ObsV2Row` to populate INV-14 + define the semantics for hourly readings that contribute to both daily-max and daily-min
- Operator decision required before Fix 4.5.B can proceed

#### Fix 4.5.C: Ban `hourly_observations` from canonical paths
- Context: forensic P3 item 2
- Scope: `scripts/semantic_linter.py` rule + remove `hourly_observations` references from `src/calibration/*`, `src/engine/*`, canonical `scripts/rebuild_*_v2.py`
- Acceptance: grep returns only evidence-view / legacy-read paths

### 4.6 Forensic P4 closure — populate canonical v2 truth (POST-P0-P3)

#### Fix 4.6.A: settlements_v2 backfill from verified market rules
- Context: F4 CRITICAL + F20 HIGH (market_slug null, finalization policy missing)
- Open question (forensic §14): "Are there market-rule source files for the null-market_slug settlement rows?"
- Pre-req: market_events_v2 populated (F13 resolution) so settlements_v2 can FK to real market identity
- Scope: script `scripts/populate_settlements_v2_from_market_events.py` that joins settlements (legacy, high-only) × market_events_v2 on (city, target_date) × polymarket-lookup → populates `settlements_v2` with `market_slug`, `condition_id`, `rule_version`, `finalization_policy`
- Hard blocker until market_events_v2 is populated

#### Fix 4.6.B: Populate forecast/ensemble v2 with verified times
- Context: F11 HIGH (reconstructed available_at = hindsight leakage) + C1/H6
- Scope: run `scripts/ingest_grib_to_snapshots.py --track mx2t6_high` + `mn2t6_low` after operator rsyncs cloud JSON to local
- Pre-req: Fix 4.7 (TIGGE local rsync)
- Acceptance: `ensemble_snapshots_v2 > 0` per metric + per city; `issue_time` and `available_at` are source-verified (not reconstructed) per F11

#### Fix 4.6.C: Rebuild calibration pairs after P0-P3 verified
- Context: forensic P4 item 3
- Pre-req: Fix 4.6.A + Fix 4.6.B + all P0-P3 items green
- Scope: `scripts/rebuild_calibration_pairs_v2.py --dry-run` then `--no-dry-run --force`
- Acceptance: `calibration_pairs_v2 >= MIN_DECISION_GROUPS` per metric

### 4.7 TIGGE cloud → local rsync (operator-owned)

- Context: C1/C2/H10 refined
- Scope: per handoff §7 + §8, operator-side:
  1. `rsync -avz --progress "<cloud-path>/tigge_ecmwf_ens_mx2t6_localday_max/" state/tigge_localday_mx2t6_max/`
  2. Same for mn2t6
  3. Verify md5/sha256 match between cloud and local via manifest
  4. Run `scripts/ingest_grib_to_snapshots.py` with correct `--track` arg
- Pre-req: fix space-in-path handling (H10)

### 4.8 Scheduler / daemon / env fixes (operator-owned)

#### Fix 4.8.A: WU_API_KEY env repair (H7)
- Scope: operator adds to `~/.zeus/.env` or launchd plist; restart scheduler
- Acceptance: `k2_daily_obs` next tick flips to OK

#### Fix 4.8.B: k2_forecasts_daily one-off verification (H5)
- Scope: `.venv/bin/python -c "from src.main import _k2_forecasts_daily_tick; _k2_forecasts_daily_tick()"` + probe `forecasts` row count
- Acceptance: `SELECT COUNT(*) FROM forecasts WHERE rebuild_run_id IS NOT NULL > 0`

#### Fix 4.8.C: auto_pause tombstone triage (H9)
- Scope: operator decision — clear tombstone (`rm state/auto_pause_failclosed.tombstone`) + un-pause OR explicitly accept paused posture + document training-safe rationale
- Acceptance: `state/status_summary.json::entries_paused` matches declared intent

---

## 5. Open questions requiring operator decision

### 5.1 From forensic audit §14 (unanswered)

1. What exact code path wrote the populated settlements? — **ANSWERED by my session**: P-E `_write_settlement_truth` (data-readiness workstream, 2026-04-23)
2. What source payloads/logs exist for 39,431 empty-provenance WU daily obs? — **UNANSWERED**; required for Fix 4.3.A A3 option
3. Are there market-rule source files for null-market_slug settlements? — **UNANSWERED**; required for Fix 4.6.A
4. What Polymarket high/low market universe is in scope for Zeus? — **UNANSWERED**; required for F13 + F20 resolution
5. Intended cutoff for Day0 low/high observations per market in local time? — **UNANSWERED**
6. Station mappings frozen by market date or current authority? — **UNANSWERED**

### 5.2 From adversarial audit

7. Should `observation_instants_v2` INV-14 columns stay (instant-level metric tagging) or revert (move metric to daily-aggregate layer)? — **blocks Fix 4.5.B**
8. Is WU-dominant training (1,400/1,469 settlements) acceptable given WU SZ/Seoul/SP/KL/Chengdu 19-mismatch drift? — **blocks training-kickoff decision**
9. Should the cloud VM be scaled up to run training (per handoff §6 "cloud training blocked until full repo+DB deployed")? — **blocks cloud-vs-local training decision**
10. Should the daemon stay paused during training, or be stopped entirely? — **blocks concurrency decision**

### 5.3 Design decisions pinned but not executed

11. `CANONICAL_DATA_VERSIONS` naming — rename to `_ENSEMBLE_` + add parallel `_OBSERVATION_` + `_SETTLEMENT_` allowlists? — blocks M3
12. Delete legacy `calibration_pairs` table or keep with `authority='QUARANTINED'`? — blocks C5 Fix
13. Revert C7 ALTER or extend writer? — blocks Fix 4.5.B

---

## 6. Next-session resumption protocol

### 6.1 First 15 minutes (zero-context cold-start)

1. Read this file (`POST_AUDIT_HANDOFF_2026-04-24.md`) entirely.
2. Read `docs/operations/current_state.md` (≤50 lines).
3. Read `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/11_data_readiness_ruling.md` (≤70 lines) — the categorical ruling.
4. Read `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/17_apply_order.md` (≤70 lines) — the P0→P4 sequencing.
5. `git log --oneline origin/data-improve | head -20` + `git status` to verify HEAD state.
6. Probe live DB state:
   ```
   .venv/bin/python -c "
   import sqlite3
   conn = sqlite3.connect('file:state/zeus-world.db?mode=ro', uri=True)
   for t in ['settlements', 'forecasts', 'observation_instants_v2',
             'ensemble_snapshots_v2', 'calibration_pairs_v2', 'platt_models_v2',
             'market_events_v2']:
       n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
       print(f'{t}: {n:,}')
   conn.close()
   "
   ```
   Expected: settlements=1,561 / forecasts=0 (or >0 if cron ran) /
   observation_instants_v2=1,813,662 / others=0

### 6.2 Critic rehydration

Critic-opus team at `~/.claude/teams/zeus-midstream-critic/config.json`.
Check if still alive: `ls ~/.claude/teams/zeus-midstream-critic/config.json`.

- If present: use existing critic-opus via `SendMessage` with bootstrap
  pointing to this handoff.
- If missing: `TeamCreate` + `Agent({subagent_type: architect, model: opus,
  team_name: zeus-midstream-critic, name: critic-opus})` with bootstrap
  prompt that references THIS handoff as the context anchor (not last
  session's memory).

### 6.3 Pre-work before any slice

Per memory rule L20 (grep-gate), verify every file:line cited in this
handoff is fresh within 10 minutes of editing. File line numbers may
have drifted since 2026-04-24 if intervening commits landed.

Per memory rule L22 (executor-before-critic), never autocommit before
critic dispatch; wait for verdict before pushing.

Per memory rule L30 (SAVEPOINT / with conn:), if next slice touches
ledger.py or any other append path, grep every caller first and
classify transaction boundary.

### 6.4 Pick next slice

Recommended order:

**Immediate (4.1.A-C)** — 1-2 hours total, closes in-flight post-audit work:
1. Fix 4.1.A: NULL-metric antibody test + onboard_cities fix
2. Fix 4.1.B: delete append_event_and_project
3. Fix 4.1.C: harvester stale comment cleanup

**Then forensic P0 (4.2.A-C)** — 4-8 hours, closes data-audit containment:
4. Fix 4.2.A: readiness query + empty-table guards
5. Fix 4.2.B: evidence-only views
6. Fix 4.2.C: market_events preflight

**Parallel to P0 — operator-owned (4.7 + 4.8)**:
- TIGGE rsync cloud → local
- WU_API_KEY env repair
- auto_pause tombstone decision

**Then forensic P1 (4.3.A-C)** — 1-3 days, closes provenance hardening:
- 39,431 WU obs triage (quarantine recommended)
- Payload hash / source URL retrofit
- Source-role registry

**P2 / P3 (4.4.A-B, 4.5.A-C)** — subsequent week

**P4 (4.6.A-C)** — post P0-P3 verification; actual training kickoff

### 6.5 What NOT to do

- **Do not kick off `refit_platt_v2.py --no-dry-run`** until forensic
  ruling's UNSAFE-NOW list is clear (see §3.3). The 17 session commits
  did not move the system off that list.
- **Do not discard or overwrite** `state/zeus-world.db.pre-migration-batch_2026-04-24`
  or `state/zeus-world.db.pre-reopen2_2026-04-24` snapshots — those are
  the ONLY rollback path if any migration-introduced bug is discovered.
- **Do not merge the forensic audit package into the main packet** —
  it's evidence, not authority. Keep as-is at
  `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/`.
- **Do not trust the `tigge_data_training_handoff_2026-04-23.md`
  "data ready for training" framing** without running the forensic
  readiness check (Fix 4.2.A). Handoff doc is scoped to "cloud
  extraction complete"; training-ready requires much more.

### 6.6 Memory notes to read

- `/Users/leofitz/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/MEMORY.md`
  — index; load all referenced notes
- `project_midstream_tail_session_2026-04-24.md` — this session's
  17-commit tally
- `feedback_critic_via_team_not_agent.md` — critic rehydration protocol
- `feedback_zeus_plan_citations_rot_fast.md` — grep-gate L20 reminder
- `feedback_no_git_add_all_with_cotenant.md` — scope-bleed discipline
- `feedback_with_conn_nested_savepoint_audit.md` — memory rule L30

---

## 7. File inventory for this handoff

Files created/modified this session (beyond code commits):

- `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` ← THIS FILE
- `/Users/leofitz/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/project_midstream_tail_session_2026-04-24.md` — memory note

Reference files to preserve:

- `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/` — DO NOT MODIFY (it's evidence at a specific timestamp)
- `docs/artifacts/tigge_data_training_handoff_2026-04-23.md` — operator's own asset-handoff doc
- `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` — 36-slice authoritative plan (largely complete per this session's work)
- `state/zeus-world.db.pre-migration-batch_2026-04-24` + `.sha256` — rollback for A1 migration
- `state/zeus-world.db.pre-reopen2_2026-04-24` + `.sha256` — rollback for A2 migration

---

## 8. Closing note

This session successfully closed the midstream remediation packet's
CONDITIONAL gate at the code + schema level. The adversarial audit
surfaced that **training-readiness requires substantial additional
work beyond what the midstream packet scoped** — specifically the
forensic P1-P2-P3 items that touch provenance, backfill guardrails,
and consumer hardening.

The operator's "TIGGE data downloaded" premise is true on the cloud;
the "system ready for training" premise is true at the schema/trigger
level but false at the data-readiness level per the forensic ruling.

Next session should prioritize Fix 4.1.A-C (1-2 hours, closes
in-flight audit debt), then operator runs 4.7 + 4.8 in parallel with
the agent continuing on forensic P0 → P1 progression.

Training itself (Fix 4.6.C) is realistically 3-7 days away from this
point, assuming linear P0-P3 progression. The cloud-vs-local training
decision (operator question #9) may change that arithmetic
significantly.
