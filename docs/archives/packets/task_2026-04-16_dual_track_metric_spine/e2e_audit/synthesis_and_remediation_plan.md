# Dual-Track E2E Audit — Synthesis + Compact Remediation Plan

**Post-P9C independent e2e verification** (commit `0a760bb` on `origin/data-improve`, audited 2026-04-19).

Two independent agents, different angles, consistent verdict:
- **architect** (Opus, structural audit) — `architect_end_state_audit.md`
- **tracer** (Opus, runtime trace) — `runtime_trace.md`
- coverage agent (H1-H7 antibody matrix) crashed mid-run (API ConnectionRefused @ 31 tool uses); findings above cover H1-H6; **H7 144-pre-existing-failure triage is outstanding** — cleanup followup.

---

## §1. Overall verdict (two-agent consensus)

**Dual-track scaffold complete, data migration pending.** Commit-message claim "DUAL-TRACK MAIN LINE CLOSED" is load-bearing only for **structural readiness**, not deployment.

- architect: **PARTIAL** — "ship seaworthy, cargo never loaded"
- tracer: "P9C closed three critical seams… Golden Window has never lifted, so no LOW row has ever passed end-to-end"

**Fitz 4-Constraints scoring** (architect):
- Structural > patches: **7/10** (real TypeError/ValueError antibodies in place)
- Translation loss: **6/10** (signal-layer typed; state/runtime-layer bare strings)
- Immune system: **6/10** (**no sensor for "v2 tables empty" — system doesn't know it runs on skeleton**)
- Data provenance: **8/10** (authority/causality_status/provenance_json enforced by schema)

**Meta-immune-system gap**: P9C close fired while v2 tables are zero-row; no automated check caught the discrepancy between documentation claim and live state.

---

## §2. Findings by blast radius

### 2.1 🔴 CRITICAL-1 (pre-existing bug, HIGH+LOW both affected, production impact)

**`src/engine/monitor_refresh.py:355 + 405` — undefined variable `remaining_member_maxes`**

Confirmed via direct grep (team-lead verification):
- L28: `from src.signal.day0_window import remaining_member_extrema_for_day0`
- L300: `extrema, hours_remaining = remaining_member_extrema_for_day0(...)` — returns dataclass with `.maxes` / `.mins`
- L318-319: `extrema.maxes` + `extrema.mins` — correct post-rename usage
- **L355**: `ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)` — **undefined, NameError**
- **L405**: `"member_maxes": remaining_member_maxes` — **undefined, NameError**
- L614: `except Exception as e: logger.debug(...)` — **silently swallows NameError**

Production behavior: every Day0-active position's monitor refresh silently fails. `pos.last_monitor_prob_is_fresh` stays False → stale probability enters exit decision. Affects HIGH and LOW equally.

Pre-existing: predates P9C; was introduced during an earlier rename (P6 `remaining_member_maxes_for_day0` → `remaining_member_extrema_for_day0`, P7B legacy alias removal). P9B/P9C test contracts passed because the broken path was exercise-gated by a broad except.

Remediation: 2-line fix (`remaining_member_maxes` → `extrema.maxes` at L355 + L405) + paired antibody test (simulate Day0 refresh, assert `last_monitor_prob_is_fresh=True` + no exception swallowed).

### 2.2 🔴 CRITICAL-2 (gate not actually closed)

**Gate C — HIGH v2 parity never achieved**

- `platt_models_v2` / `calibration_pairs_v2` / `settlements_v2` / `ensemble_snapshots_v2` / `historical_forecasts_v2` all **0 rows** (verified via `sqlite3 state/zeus-world.db` counts).
- `get_calibrator` at `manager.py:165-168` has explicit `if temperature_metric == "high": legacy fallback` — the live HIGH production path reads from legacy `platt_models`.
- Phase narrative (plan.md:152-153) declared Gate C in-scope; actual state: backfill scripts exist (`refit_platt_v2.py`, `rebuild_calibration_pairs_v2.py`) but **never ran against live data**.
- Consequence if Golden Window lifts without Gate-C backfill: HIGH keeps reading legacy; `is_active` UNIQUE constraint may collide if v2 is populated without careful de-duplication; HIGH served from two sources.

Remediation options:
- **(A) Document-only**: declare Gate C explicitly OPEN with trigger conditions. Current `get_calibrator` dual-read is the correct implementation until Gate-C execution.
- **(B) Execute backfill**: run `rebuild_calibration_pairs_v2.py` + `refit_platt_v2.py` against live HIGH data before any LOW activation.

### 2.3 🟡 MAJOR (runtime correctness risks)

**2.3a — Replay legacy fallback HIGH-only SQL filter**

`src/engine/replay.py:309` — legacy SQL: `WHERE city = ? AND target_date = ? AND forecast_high IS NOT NULL`. When v2 is empty (Golden-Window state), `_forecast_rows_for` falls through to this query. LOW replay either returns 0 rows OR hits TypeError on `float(None)` if a row has `forecast_low` populated but `forecast_high IS NULL` passes the WHERE.

Remediation: add metric-aware WHERE branch — when caller asks LOW, filter `AND forecast_low IS NOT NULL`; when HIGH, filter `AND forecast_high IS NOT NULL`.

**2.3b — Oracle penalty per-city, no metric key**

`src/strategy/oracle_penalty.py:46-81` — `_cache: dict[str, OracleInfo]` keyed by city only. LOW positions inherit HIGH-track accumulated error rates. Kelly sizing biased downward (structural quantitative bias, not directional error).

Remediation: key change from `city` → `(city, temperature_metric)`. Two-seam fix: producer + consumer + cache invalidation paths all need update.

**2.3c — MetricIdentity is signal-layer spine, not runtime spine**

Architect D1 65% score. Bare `temperature_metric: str` circulates through:
- `Position.temperature_metric: str = "high"` (`portfolio.py:152`)
- `get_calibrator(temperature_metric: str = "high")` (`manager.py:128`)
- `run_replay(temperature_metric: str = "high")` (`replay.py:1995`)
- 13+ total call sites

Risk: mis-typed metric ("high_temp" / "low " whitespace / wrong enum coercion) falls through to `load_platt_model_v2` None → legacy fallback → legacy has no LOW → returns `(None, 4)` uncalibrated. **Silent degrade instead of TypeError**.

Remediation: change signatures to `temperature_metric: MetricIdentity` across runtime seams; force all callers to construct via `MetricIdentity.from_raw()` at the JSON/SQL boundary only.

### 2.4 🟡 MAJOR (latent crosstalk)

**FDR family_id lacks metric** (`src/strategy/selection_family.py:28-51`). Architect D4 DT#3 40% score ("deprecated ≠ canonicalized"). Tracer qualification: "latent but not live — BH is per-candidate-invocation in current hot path, not across candidates".

Becomes live when Gate F activates and LOW candidates produce hypotheses in the same cycle as HIGH. Without metric in family_id, BH discovery budget merges HIGH + LOW across metric families.

Remediation: add `temperature_metric` to family_id tuple; update `test_fdr_family_key_is_canonical` stub to activate; verify all call sites use new signature.

### 2.5 🟡 MAJOR (DT#7 incomplete)

**DT#7 clauses 1 + 2 deferred** (`src/contracts/boundary_policy.py:17-25`). Only clause 3 (refusal) is wired (P9C A4). Clauses 1 (leverage reduction) + 2 (oracle-penalty-per-city isolation) are docstring TODOs.

Overlaps with 2.3b (oracle_penalty per-city-only). Fixing 2.3b (key change) delivers 2/3 of DT#7 mechanically.

### 2.6 🟡 MAJOR (opt-in DT#5)

**Kelly executable-price polymorphic, not structural** (`src/strategy/kelly.py:33-79`).

Bare-float path preserved for backward-compat. The gate is opt-in; failure category is not yet impossible. ~10+ bare-float call sites still exist (`replay.py:1300`, 6 test files per earlier scout).

Remediation: migrate all bare-float callers → strict ExecutionPrice requirement → delete the bare-float branch. Test surface: update `test_kelly.py`, `test_k3_slice_q.py`, `test_kelly_live_safety_cap.py`, `test_kelly_cascade_bounds.py`, `test_execution_price.py` to pass ExecutionPrice.

### 2.7 🔵 MINOR

**2.7a — `_TRUTH_AUTHORITY_MAP["degraded"] = "VERIFIED"`** (flagged critic-carol P9A; still unrevisited; documented as "periodic review" in DT#6 §B).

**2.7b — DT#1 cycle-orchestration compliance only**, no semgrep ruleset content verified for NC-13 `zeus-no-json-before-db-commit` (architect D4 DT#1 note).

**2.7c — H7 144 pre-existing failures never triaged**. Coverage agent would have done this (H7); agent crashed. Unknown how many are dual-track-law failures vs unrelated pre-existing vs stale-antibody.

---

## §3. Discriminating probe (MANDATORY pre-Golden-Window)

**Tracer's "single most load-bearing unverified fact"**:

> Does `scripts/extract_tigge_mn2t6_localday_min.py` correctly stamp `temperature_metric='low'` on every `ensemble_snapshots_v2` INSERT?

Inspect INSERT parameter tuples — literal `'low'`, typed `MetricIdentity.temperature_metric`, or inherited HIGH default?

**If wrong metric stamped at ingest**: every P9C L3 + DT#7 + boundary_policy downstream wiring is decorative. LOW data arrives in v2 labeled as HIGH, HIGH queries pull it, calibration contamination guaranteed.

**Cost**: 5-line grep + parameter inspection. Execute first.

---

## §4. Compact Remediation Plan

### §4.1 Independent Immediate (no Golden Window dependency)

**R1 — Fix monitor_refresh NameError (CRITICAL-1, 2.1)**
- Edit `src/engine/monitor_refresh.py:355` → `remaining_member_maxes` → `extrema.maxes`
- Edit `src/engine/monitor_refresh.py:405` → same
- Add antibody: `test_day0_monitor_refresh_uses_extrema_api` — simulate Day0 refresh, assert `last_monitor_prob_is_fresh=True` + no silent exception
- Consider tightening the broad `except Exception` at L614 — downgrade to `except (RuntimeError, ValueError) as e` so NameError propagates (structural lesson for future renames)
- Commit scope: **independent hygiene commit, not dual-track scope**

**R2 — Discriminating probe of ingest metric stamp (§3)**
- Grep `extract_tigge_mn2t6_localday_min.py` for `INSERT INTO ensemble_snapshots_v2` parameter tuples
- Verify `temperature_metric='low'` literal or typed field in every tuple
- Add antibody: `test_extract_mn2t6_ingest_stamps_low_metric` — mock conn, call extractor's write path, assert all v2 rows carry `temperature_metric='low'`

### §4.2 Runtime correctness (pre-Golden-Window-lift mandatory)

**R3 — Replay legacy fallback metric-aware WHERE (2.3a)**
- `src/engine/replay.py:309` — add conditional branch: when `temperature_metric='low'`, filter `AND forecast_low IS NOT NULL`; else `AND forecast_high IS NOT NULL`
- Update v2→legacy translator (`replay.py:286-295`) to correctly populate `forecast_low` for LOW rows
- Antibody: extend R-CB to cover legacy LOW fallback

**R4 — Oracle penalty (city, metric) keying (2.3b + DT#7 clause 2)**
- `src/strategy/oracle_penalty.py:46-81` — change `_cache: dict[str, OracleInfo]` to `dict[tuple[str, str], OracleInfo]`
- Update all producers (grep "oracle_penalty.record" / "penalty_multiplier" writers) + consumers (evaluator, FDR, Kelly)
- Migration: existing HIGH-only cache entries get promoted to (city, "high") keys; LOW cache starts fresh
- Antibody: `test_oracle_penalty_does_not_cross_contaminate_metrics` — seed HIGH penalty for city X, assert LOW lookup for same city returns separate OracleInfo
- Delivers 2/3 of DT#7

**R5 — MetricIdentity runtime-seam migration (2.3c)**
- Scope: 3 call sites highest priority — `Position.temperature_metric`, `get_calibrator`, `run_replay`
- Change field types to `MetricIdentity`; force `MetricIdentity.from_raw(str)` at JSON/SQL boundary
- Add AST antibody: `test_no_bare_string_temperature_metric_at_runtime_seams` — grep src/ for `temperature_metric: str` in function/method signatures; flag unlisted sites
- Consider a lightweight `Literal["high", "low"]` if full MetricIdentity too invasive

### §4.3 Architectural decisions (user ruling needed)

**R6 — Gate C resolution (CRITICAL-2, 2.2)**
- Option A: document-only — Gate C explicitly marked open with trigger conditions; keep dual-read `get_calibrator` as correct implementation
- Option B: execute backfill — run `rebuild_calibration_pairs_v2.py` + `refit_platt_v2.py` against live HIGH now
- **User decides based on operational risk tolerance**

**R7 — Golden Window lift timing**
- User-only decision (data ingress policy)
- Prerequisites for lift: R2 probe PASS + R3 + R4 done + decision on R6

**R8 — "DUAL-TRACK MAIN LINE CLOSED" title amendment**
- Architect recommends retitling to "Dual-Track Scaffold Complete, Data Migration Pending"
- Handoff text update; commit message amendment (or follow-up commit documenting reality)

### §4.4 Hygiene / cleanup (non-blocker, improves immune system)

**R9 — DT#3 FDR family_id metric-aware (2.4)**
- `src/strategy/selection_family.py:28-51` — add `temperature_metric` to family_id tuple
- Activate `test_fdr_family_key_is_canonical` stub in `test_dual_track_law_stubs.py`
- Verify all callers (grep `make_hypothesis_family_id` + `make_family_id`) migrated

**R10 — DT#5 Kelly strict migration (2.6)**
- Remove `float | ExecutionPrice` polymorphism; require ExecutionPrice
- Upgrade ~10 bare-float call sites + 6 test files
- Activate strict antibody that bare-float raises TypeError

**R11 — v2 row-count observability sensor**
- Add status surface (grep `state_get_status` / `observability/status_summary.py`) to emit v2 table row counts
- Alert when `platt_models_v2` / `calibration_pairs_v2` remain zero despite claims of closure
- Immune-system antibody per architect's Fitz C#3 scoring

**R12 — H7 triage (144 pre-existing failures)**
- Re-spawn coverage agent (or general-purpose) with focused H7 brief: git-stash all changes, run pytest, bucket the 144 into
  (a) dual-track law failures (should be fixed but aren't) — CRITICAL findings
  (b) unrelated pre-existing (tooling, infra)
  (c) stale antibodies asserting wrong behavior (need update)
- If >10 in bucket (a), that's a finding about phase-completion integrity

**R13 — `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` re-decision (2.7a)**
- Per carol P9A MINOR-2 open question; quiet but semantically questionable
- Two paths: keep + extend documentation; change to "DEGRADED" and audit all readers

---

## §5. Prioritization for next phase/packet

**Recommended packet shape** (post-compact):

**P10A — Immediate correctness hygiene** (R1 + R2 probe)
- Fixes CRITICAL-1 monitor NameError (HIGH+LOW Day0 monitoring broken today)
- Resolves ingest-stamp unknown (validates P9C wiring is load-bearing)
- Commit size: small, independent, high value
- Test: new antibody pair, full regression stays ≤144/1873

**P10B — Runtime correctness pre-activation** (R3 + R4 + R5)
- Closes LOW replay fallback correctness
- Closes 2/3 of DT#7 via oracle penalty metric-keying
- Migrates 3 highest-impact runtime seams to MetricIdentity
- Commit size: medium, 3 subpackets or single commit per user ruling

**P10C — Gate C + Golden Window** (R6 + R7)
- User ruling required; cannot proceed without policy decision
- If Option B: data migration + backfill execution
- If Option A: documentation amendment only

**Cleanup** (R8 + R9 + R10 + R11 + R12 + R13) — non-blocker, can run anytime, group into cleanup packet post-activation or in parallel

---

## §6. Compact-ready status

**Files to reference post-compact**:
- `docs/operations/task_2026-04-16_dual_track_metric_spine/e2e_audit/architect_end_state_audit.md` — full structural audit
- `docs/operations/task_2026-04-16_dual_track_metric_spine/e2e_audit/runtime_trace.md` — full runtime trace
- **This file** (`synthesis_and_remediation_plan.md`) — actionable summary
- `docs/operations/task_2026-04-16_dual_track_metric_spine/team_lead_handoff.md` — to be updated with pointer

**Commit chain (unchanged post-audit)**:
```
0a760bb docs(phase9c-close) ← HEAD / origin/data-improve
d516e6b fix(phase9c): ITERATE resolution
114a0f5 feat(phase9c): dual-track main-line closure
```

**Next session actions** (in order):
1. Read this file (§1 verdict + §4 remediation plan)
2. Execute R1 + R2 as independent first commit (no user ruling needed)
3. Present findings from R2 ingest probe — load-bearing fact before anything else
4. Present R6 (Gate C) + R7 (Golden Window) + R8 (title amendment) to user for ruling
5. Execute R3/R4/R5 per user priority

**Outstanding user rulings** (cannot proceed without):
- R6 Gate C: document-only vs execute backfill
- R7 Golden Window: lift timing
- R8 title amendment: commit message / handoff wording
- R12 H7 triage: worth coverage-agent re-spawn or defer

---

*Authored: team-lead synthesis, 2026-04-19 post e2e audit*
*Pre-compact artifact — designed to be the single read that resurrects full dual-track end-state in next session*
