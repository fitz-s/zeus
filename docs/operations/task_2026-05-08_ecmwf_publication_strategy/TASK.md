# TASK — ECMWF publication-schedule strategy for D+10 horizon

Created: 2026-05-08
Authority: operator decision 2026-05-08 — freshness MUST be preserved; simple lag-increase rejected
Phase: scientist evaluation (opus)

## CONTEXT

PR #94 extended `STEP_HOURS` to 282h (D+10 covering UTC+12 cities for lead_day 7-8). After merge + daemon restart, ALL ECMWF Open Data download attempts since 2026-05-04 have failed with HTTP 404 on step `147h+` files.

Phase B Round-2 audit (`docs/operations/task_2026-05-08_phase_b_download_root_cause/DOSSIER.md`) confirmed ECMWF Open Data **publishes long steps (147h+) in a LATER batch** than short steps. The current `default_lag_minutes: 485` gate (8.08h) is calibrated for ≤120h step lists from the pre-#94 era; it fires before the long-step batch is published.

Result: 100 BLOCKED `SOURCE_RUN_HORIZON_OUT_OF_RANGE` rows for 2026-05-13/14, plus zero new source_runs since 2026-05-04 (4-day data gap on production).

## OPERATOR CONSTRAINT

**Freshness is critical for Zeus's trading edge.** Simple options like raising `default_lag_minutes` to 720 (12h) sacrifice D1-D5 freshness uniformly to recover D6-D10 — REJECTED.

Need a strategy that:
- Preserves D1-D5 freshness at current ~8h lag
- Recovers D6-D10 coverage by waiting only as long as ECMWF needs to publish those long steps
- Minimizes orchestration complexity where possible
- Plays well with existing `source_runs` / `producer_run_coverage` schema and calibration pipeline

## INPUT MATERIAL (read FIRST)

- Phase B dossier: `docs/operations/task_2026-05-08_phase_b_download_root_cause/DOSSIER.md`
- Prior step-grid scientist eval: `docs/operations/task_2026-05-08_ecmwf_step_grid_scientist_eval/REPORT.md` (your-own prior work; build on it)
- Architecture: `architecture/zeus_grid_resolution_authority_2026_05_07.yaml`
- Current calendar: `config/source_release_calendar.yaml`
- Daemon entry: `src/data/ecmwf_open_data.py`
- Cycle selection: `src/data/forecast_target_contract.py`'s `select_source_run_for_target_horizon`

## REQUIRED INVESTIGATION

### 1. Empirical publication schedule

WebFetch ECMWF Open Data documentation to confirm **per-step publication timing** for ENS:
- https://www.ecmwf.int/en/forecasts/datasets/set-iii
- https://confluence.ecmwf.int/display/DAC/Dissemination+schedule
- ecmwf-opendata client docs

Extract verbatim quotes about:
- When are short steps (0-90h hourly, 93-144h 3h) typically available after cycle time?
- When are long steps (150-360h 6h) typically available?
- Are the publication batches discrete (0-144h block then 150+ block) or staggered?
- Is there an official "approximate publication time" per step?

Cross-reference with empirical evidence from Zeus's own logs:
- When did pre-#94 daemon successfully fetch (≤120h list)? Timing relative to cycle?
- The 5/8/00Z cycle: when did step=144h become available? (Probe by attempting fetch NOW, ~16h post cycle)

### 2. Strategy options to evaluate

For each, produce: freshness impact, complexity cost, schema impact, failure mode.

**Option A: Single increased lag**
Single `default_lag_minutes: ~720` for full step list. Operator rejected; included for comparison.

**Option B: Two-phase download (cycle-cumulative)**
First attempt at 485min for short steps (0-144h) → write `source_run` with `step_horizon_hours=144`. Second attempt at +N min for long steps (150-282h) → augment same `source_run` with `step_horizon_hours=282`.

**Option C: Two source_runs per cycle**
First attempt at 485min: write `source_run_short` (max=144h). Second attempt at +N min: write `source_run_long` (covers 150-282h additively or replaces). Readiness logic picks the right one per target_date.

**Option D: Adaptive progressive retry**
Single attempt that requests current best-available step list, with retry every 30/60/120/240 min until full step list achieved or max-retries exhausted.

**Option E: Per-step decoupled state**
Track per-step availability in DB. Each step has its own retry-able unit. When all required steps for a target_date available → mark readiness=LIVE_ELIGIBLE.

**Option F: Target-date-aware horizon downgrade (your hypothesis space — propose if better)**
Per-target-date routing: lead_day < 6 uses 0-144h fetch (485min lag), lead_day 6-10 uses 150-282h fetch (720min lag). Decouple triggers from cycle.

Open: any other strategy that fits Zeus's pattern?

### 3. Match to existing schema

Read `state/zeus-world.db` schema for source-related tables:
- `producer_run_coverage`, `source_runs`, `ensemble_snapshots_v2` (or whatever exists)
- Identify what each option requires changing
- Prefer options that fit existing schema with minimal additions

### 4. Calibration impact

Per A1+3h authority: training uses TIGGE 6h, live uses Opendata mixed-grid. Does any of these strategies change the calibration training/live alignment? Specifically:
- If a cycle has D1-D5 at 485min but D6-D10 doesn't arrive until 720min, can we cycle-stratify Platt by short-vs-long horizons?
- Does the 3h envelope-learning happen per-step or per-cycle?

### 5. Quantitative recommendation

Final output:
- **Recommended option** (one)
- **Why** (compared to all others, freshness/complexity/failure-mode table)
- **File:line patches** if implementable in <50 lines
- **Schema changes** if needed
- **Test/validation plan**

## DELIVERABLE

Write to: `docs/operations/task_2026-05-08_ecmwf_publication_strategy/REPORT.md` (you are scientist subagent_type → no Write tool. Return inline; orchestrator will write. Use the `<RECOMMENDATION_TOKEN>\n---REPORT_BEGIN---\n<full markdown>\n---REPORT_END---` format established yesterday.)

## VERDICT_TOKENS

- `RECOMMEND_<X>` where X is option letter A-F or your proposed
- `INSUFFICIENT_INFO_<reason>` if you can't decide

## EVIDENCE_FLOOR

- ≥3 ECMWF official-source citations on publication timing (verbatim quotes)
- ≥1 empirical observation from Zeus's own logs/DB
- Schema impact concretely described (table names + columns)
- ≥1 quantitative comparison: freshness-cost in hours per strategy
- Cross-reference to your prior step-grid REPORT.md (don't re-derive — extend)

## INVESTIGATION_BUDGET_FLOOR

Use the 1M context. ≥30 tool calls. Web research + code reading + DB schema introspection.

## CONSTRAINTS

- Read-only investigation; no code/DB writes
- Operator authority: simple lag-increase REJECTED. Solutions that uniformly reduce freshness ineligible
- Match Zeus's existing pipeline architecture; don't propose ground-up rewrites
- If proposing new option (F or beyond), justify why it beats A-E
