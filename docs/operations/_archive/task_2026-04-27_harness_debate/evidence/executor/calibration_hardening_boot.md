# CALIBRATION_HARDENING packet — executor boot

Created: 2026-04-29
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27 (LONG-LAST)
Judge: team-lead
Source dispatch: DISPATCH_CALIBRATION_HARDENING_PACKET (R3 §3 weeks 13-20 fourth edge leg)
Plan-evidence basis: docs/operations/task_2026-04-27_harness_debate/round3_verdict.md §1 + §3
                     + docs/operations/task_2026-04-26_ultimate_plan/ULTIMATE_PLAN.md §4 #2
Reuse note: same K1 read-only patterns + same canonical surface as
EDGE_OBSERVATION + ATTRIBUTION_DRIFT + WS_OR_POLL_TIGHTENING (just shipped).
Same 3-batch + critic-gate cadence — but HIGHER risk per dispatch §3.

## §0 Read summary

| Source | What I learned |
|---|---|
| AGENTS.md root (skim) | Standard zeus root authority order; no calibration-specific overrides |
| round3_verdict.md §1 #5 LOCKED | INV-15 + INV-09 upgrades MUST be shipped before CALIBRATION_HARDENING entry. **STATUS: SHIPPED** per dispatch (49cf5cc + 19e6e04 INV-15 BATCH D pattern; 0a9ec93 + 6a3d906 INV-09 AuthorityTier DEGRADED; merged via 42c9bd9). Critic-gated. Precondition CLEARED. |
| round3_verdict.md §3 weeks 13-20 | "CALIBRATION_HARDENING is HIGH-risk; deserves harness substrate"; 35-45% harness allocation in this band |
| ULTIMATE_PLAN.md §4 #2 (L302-304) | Packet definition: "Extended Platt (A·logit + B·lead_days + C) parameter monitoring; Monte Carlo noise calibration vs realized; α-fusion weight tuning; double-bootstrap CI tightness on small-sample bins." Listed as DEFERRED Dominance Roadmap item. |
| src/calibration/AGENTS.md L14-22 (Key files table) | `platt.py` HIGH (core engine); `manager.py` HIGH (controls when calibration applies); `store.py` MEDIUM (persistence); `retrain_trigger.py` HIGH (live promotion seam); `effective_sample_size.py` MEDIUM; `blocked_oos.py` MEDIUM (shadow-only); `drift.py` MEDIUM (HL χ² test). |
| src/calibration/AGENTS.md L24-32 Domain rules | Maturity gates SAFETY-CRITICAL: n<15 P_raw direct (no fit); 15-50 strong reg C=0.1; 50+ standard. 200 bootstrap parameter sets (A_i, B_i, C_i) feed σ_parameter in DBS-CI — without them, edge CI too narrow → overtrading. Logit clamping P → [0.01, 0.99]. Shoulder bins stay raw (not width-normalized). |
| src/calibration/AGENTS.md L41-46 Active vs Shadow | Active routing: platt.py + manager.py + store.py — live execution path; changes need governance packet. Shadow: blocked_oos.py + effective_sample_size.py — additive metrics in status_summary; never live blockers. **Implication**: BATCH 1+2 measurement-only adds another shadow surface; classified as SHADOW per the AGENTS.md taxonomy. |
| src/calibration/store.py L385-413 save_platt_model | LEGACY save: bucket_key TEXT UNIQUE, params A/B/C, bootstrap_params_json, n_samples, brier_insample, fitted_at, is_active, input_space, authority. **bucket_key is the only key.** |
| src/calibration/store.py L416-456 save_platt_model_v2 | V2 save: model_key = f"{temp_metric}:{cluster}:{season}:{data_version}:{input_space}". Carries temperature_metric (HIGH/LOW), cluster, season, data_version, input_space, A/B/C, bootstrap, n_samples, brier_insample, fitted_at. |
| src/calibration/store.py L488-512 load_platt_model | LEGACY read: SELECT by bucket_key + is_active=1 + authority='VERIFIED'. Returns single dict OR None. **No "list-all" function.** |
| src/calibration/store.py L515-575 load_platt_model_v2 | V2 read: SELECT by (temperature_metric, cluster, season, input_space) + is_active=1 + authority='VERIFIED' + ORDER BY fitted_at DESC LIMIT 1. **Single-bucket lookup only; no list-all.** |
| src/calibration/store.py grep `^def` | All public surfaces: infer_bin_width_from_label, add_calibration_pair, _resolve_training_allowed, add_calibration_pair_v2, _has_authority_column, get_pairs_for_bucket, get_pairs_count, get_decision_group_count, canonical_pairs_ready_for_refit, save_platt_model, save_platt_model_v2, deactivate_model_v2, load_platt_model, load_platt_model_v2, deactivate_model. **NO list_all_models / NO list_active_models / NO platt_model_history function.** |
| src/state/db.py L527-543 calibration_pairs table | columns: id, city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value, decision_group_id, bias_corrected, authority CHECK ∈ ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'), bin_source. **NO strategy_key.** |
| src/state/db.py L567-580 platt_models table (legacy) | bucket_key TEXT NOT NULL UNIQUE, param_A/B/C REAL, bootstrap_params_json TEXT, n_samples INTEGER, brier_insample REAL, fitted_at TEXT, is_active INTEGER, input_space TEXT, authority TEXT CHECK ∈ ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'). **NO strategy_key. NO city. NO target_date. Bucket-keyed only.** |
| src/state/schema/v2_schema.py L227-249 platt_models_v2 | model_key TEXT PRIMARY KEY (formed from temp_metric:cluster:season:data_version:input_space), temperature_metric CHECK ∈ ('high', 'low'), cluster, season, data_version, input_space (default 'raw_probability'), param_A/B/C, bootstrap_params_json, n_samples, brier_insample, fitted_at, is_active CHECK ∈ (0,1), authority CHECK ∈ ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'), bucket_key TEXT (legacy bridge), recorded_at DEFAULT CURRENT_TIMESTAMP, UNIQUE(temp_metric, cluster, season, data_version, input_space, is_active). **NO strategy_key. NO city. NO target_date.** |
| src/calibration/drift.py (whole file, 106L) | Existing detector: Hosmer-Lemeshow χ² test on last 50 pairs per bucket (THRESHOLD=7.81 = chi2 df=3 p<0.05). 8/20 directional failure emergency flag. Seasonal recalibration trigger dates (3-20, 6-21, 9-22, 12-21). **The HL χ² test is on FORECAST-VS-OUTCOME accuracy, not on parameter (A,B,C) drift over time.** No detector for parameter trajectory drift exists. |
| src/calibration/manager.py L1-30 imports | Imports load_platt_model + load_platt_model_v2 + save_platt_model + get_pairs_for_bucket + get_decision_group_count from store.py. Active routing surface. |
| src/calibration/retrain_trigger.py L1-100 | F2 wires control seam (operator-gate + corpus filter + frozen-replay). Has CalibrationParams dataclass + RetrainStatus enum (DISABLED/ARMED/RUNNING/COMPLETE_REPLAYED/COMPLETE_DRIFT_DETECTED). Reads venue_command_repo.load_calibration_trade_facts. **Promotion is OPERATOR-GATED ENV_FLAG_NAME=ZEUS_CALIBRATION_RETRAIN_ENABLED; does NOT auto-fire.** |
| invariants.yaml INV-09 (L83-101) | "Missing data is first-class truth." 9 cited tests; AuthorityTier='DEGRADED' CHECK constraint at collateral_ledger.py:46. SHIPPED per dispatch. |
| invariants.yaml INV-15 (L143-160) | "Forecast rows lacking canonical cycle identity may serve runtime degrade paths but must not enter canonical training." 8 cited tests; src/calibration/store.py:117 _resolve_training_allowed enforces source whitelist {tigge, ecmwf_ens}. SHIPPED per dispatch. |
| sibling boot ws_poll_reaction_boot.md (re-read) | PATH A precision-favored framing applied here; PATH B (heuristic) rejected; PATH C (writer extension) deferred. SAME framework I'll apply to CALIBRATION_HARDENING. |

## §1 KEY OPEN QUESTIONS (the load-bearing findings)

### KEY OPEN QUESTION #1 — Persistence shape mismatch with dispatch spec

**Dispatch said:** "src/state/calibration_observation.py NEW — read-only projection of Platt (A, B, C) coefficients per (city, target_date, strategy_key) tuple over time window".

**Reality at HEAD:** Platt parameters do NOT persist per (city, target_date, strategy_key). They persist per BUCKET:
- Legacy table `platt_models`: keyed by `bucket_key TEXT UNIQUE` (the bucket_key is `f"{cluster}_{season}"` per manager.py:73).
- V2 table `platt_models_v2`: keyed by `(temperature_metric, cluster, season, data_version, input_space)` UNIQUE on (..., is_active=1).
- Neither table carries `strategy_key`. Neither carries `city` independently (cluster≈city per K3 / "one-cluster-per-city" per load_platt_model_v2 docstring L538).
- Neither table carries `target_date`. Models are per-bucket fits; they apply to ALL target_dates within the cluster×season×lead_days envelope (Platt's lead_days is an INPUT FEATURE, not a key).

**Implication:** The dispatch's "(city, target_date, strategy_key)" framing is the EVALUATION-TIME identity (which decision was made when), not the PERSISTENCE-TIME identity. To project Platt parameter trajectory, the natural axis is `(temperature_metric, cluster, season, data_version, input_space, fitted_at)`. There is exactly ONE active model per (temp_metric, cluster, season, data_version, input_space) at any time, with prior fits soft-deactivated (UNIQUE on is_active=1 means only one row may be active per key).

**Honest options:**

  PATH A — Bucket-time projection (RECOMMENDED, mirrors EO/AD/WP precision-favored)
    BATCH 1 returns per-bucket-key snapshot of CURRENT active models: A, B, C, n_samples, brier_insample, fitted_at, sample_quality. Window-aware (rejects models fitted_at outside the window).
    Drops the "(city, target_date, strategy_key)" axis from the contract because the data substrate doesn't support it without a writer-side change (PATH C territory).

  PATH B — Synthetic strategy_key attribution via decision-log JOIN (HIGH-RISK)
    JOIN platt_models_v2 to trade_decisions or decision_records to attribute "which strategy USED which Platt model on which target_date". This is doable but expensive: trade_decisions has `calibration_model_version TEXT` (db.py:592) but no foreign key to platt_models_v2. Reconstructing the attribution requires assuming a snapshot+timestamp join — fragile, recall-limited.

  PATH C — Operator-decision: extend the writer
    Add columns to platt_models_v2 to tag the strategy families that consumed it. Out of scope per dispatch NOT-IN-SCOPE: "ANY mutation of platt_models / platt_models_v2 ... tables (writer-side change)".

**Default plan: PATH A** for BATCH 1 (honest precision-favored measurement; mirrors WS_POLL PATH A precedent). PATH B documented in AGENTS.md as a future enhancement; PATH C explicitly deferred.

### KEY OPEN QUESTION #2 — No list-all read function exists at HEAD

**Reality:** store.py has NO `list_all_platt_models()` or `list_active_models_v2()` function. `load_platt_model[_v2]` are SINGLE-BUCKET lookups. To enumerate models, BATCH 1 must either:
  (a) Add a NEW read function to store.py (`list_active_platt_models_v2(conn) -> list[dict]`), OR
  (b) Issue raw SQL inside the new src/state/calibration_observation.py module.

**Trade-off:** Option (a) is the right architectural answer (extends the canonical surface in the right module) but mutates store.py — store.py is HIGH-MEDIUM risk per AGENTS.md (it's the persistence module). Option (b) keeps store.py untouched but creates a parallel raw-SQL surface that future maintainers might not realize duplicates store.py's responsibility.

**Default plan:** Option (a) ADDS a single read-only function to store.py with extensive docstring + tests; this is consistent with what EO/AD/WP did (they added `query_authoritative_settlement_rows`-style canonical reads). The function is pure-SELECT, no INSERT/UPDATE/DELETE; classified as expansion of the canonical READ surface. Will await operator confirmation.

### KEY OPEN QUESTION #3 — drift.py exists; relationship to BATCH 2?

**Reality:** `src/calibration/drift.py` already implements the Hosmer-Lemeshow χ² test on (forecast, outcome) pairs + 8/20 directional failure emergency. **This is FORECAST-CALIBRATION drift (the model's outputs vs realized outcomes)** — measuring is the model still well-calibrated.

**BATCH 2 dispatch ask:** "MC-noise calibration drift detector — `detect_calibration_drift(parameter_history)` ratio-test or KL-divergence detector".

**These are different things:**
- drift.py existing: "is the forecast still accurate?" (output drift)
- BATCH 2 ask: "are the parameters (A, B, C, σ_bootstrap) drifting over consecutive refits?" (parameter trajectory drift)

Both are valuable; neither subsumes the other. Parameter-trajectory drift is a NEW shadow signal. Will not modify drift.py.

**Default plan:** Define BATCH 2 detector in NEW file `src/state/calibration_observation.py` (same file as BATCH 1's projection), function name `detect_parameter_drift(parameter_history)` to avoid namespace collision with drift.py's HL test. Reuse EO BATCH 2 ratio-test pattern OR KL-divergence on bootstrap distributions if dispatch prefers.

### KEY OPEN QUESTION #4 — DEGRADED-row handling per INV-09 upgrade

**INV-09 upgrade** (per dispatch + invariants.yaml L84) added `AuthorityTier='DEGRADED'` CHECK constraint. The `authority` column on platt_models_v2 is CHECK ∈ ('VERIFIED', 'UNVERIFIED', 'QUARANTINED') — **no 'DEGRADED' on platt tables**. AuthorityTier='DEGRADED' is on `collateral_ledger.py:46`, a DIFFERENT table.

**Implication:** BATCH 1 read filter should respect the existing platt-table authority enum (filter to VERIFIED for measurement; mirror load_platt_model_v2's L556 filter). DEGRADED is not a Platt-table state; INV-09 upgrade is orthogonal to this packet's substrate.

### KEY OPEN QUESTION #5 — α-fusion + DBS-CI in dispatch §scope vs NOT-IN-SCOPE

**Dispatch:**
- Mentions α-fusion weight tuning + double-bootstrap CI tightness in BATCH definition
- THEN explicitly NOT-IN-SCOPE: "α-fusion weight tuning (deferred to future packet)" + "double-bootstrap CI tightness (deferred — implies new statistical test framework, separate scope)"

**Resolution:** measurement-only mode means BATCH 1 can OBSERVE α-fusion weights and DBS-CI σ_bootstrap if persisted (it's in `bootstrap_params_json` per platt_models_v2:238 — list of (A_i, B_i, C_i) tuples) WITHOUT tuning them. Will surface bootstrap parameter spread as evidence in BATCH 1 report; will not implement DBS tightness adjustment.

## §2 Per-batch design sketch (PATH A + measurement-only)

### BATCH 1 — `compute_platt_parameter_snapshot_per_bucket` + tests (~6-10h)

**Files**:
- NEW: `src/state/calibration_observation.py` (~250-300 LOC)
- MAYBE: small read-only addition to `src/calibration/store.py` (~30 LOC + docstring) iff KEY OPEN QUESTION #2 option (a) approved
- NEW: `tests/test_calibration_observation.py` (~200-300 LOC, ~9 tests)

**Function signature**:
```python
def compute_platt_parameter_snapshot_per_bucket(
    conn, window_days=7, end_date=None,
) -> dict[str, dict]:
    """K1-compliant read-only. Returns per-(temp_metric, cluster, season,
    data_version, input_space) bucket key → snapshot dict:
    {
        param_A, param_B, param_C: float,
        n_samples: int,
        brier_insample: float | None,
        fitted_at: str (ISO),
        bootstrap_count: int,
        bootstrap_A_std, bootstrap_B_std, bootstrap_C_std: float | None,
            # Computed from bootstrap_params_json — surfaces DBS-CI tightness
            # without tuning anything (KEY OPEN QUESTION #5 resolution)
        bootstrap_A_p5, bootstrap_A_p95: float | None,  # 5/95 percentile bands
        sample_quality: 'insufficient' | 'low' | 'adequate' | 'high',
            # Reuse 10/30/100 thresholds via _classify_sample_quality from
            # edge_observation (sibling-coherent)
        window_start, window_end: ISO date,
        in_window: bool,  # fitted_at within window
    }
    """
```

Reads platt_models_v2 (preferred — current canonical) AND platt_models (legacy fallback for buckets not yet migrated). Filters to is_active=1 + authority='VERIFIED' (mirror load_platt_model_v2 L555-557 read filter). Window-aware: in_window flag set if fitted_at falls in [end - window_days, end].

**Tests** (`tests/test_calibration_observation.py`, ~200-300 LOC, ~9 tests):
1. snapshot returns all active VERIFIED v2 models
2. legacy fallback: bucket present in legacy but not v2 → snapshot includes legacy with explicit `source: 'legacy'` field
3. UNVERIFIED + QUARANTINED rows excluded
4. is_active=0 rows excluded
5. bootstrap stats correctness: synthetic 200-bootstrap params → known σ_A, σ_B, σ_C math
6. sample_quality boundaries (10/30/100 reusing edge_observation classifier)
7. empty_db safety
8. window filter: in_window=True iff fitted_at in window
9. unknown / pre-migration table missing → graceful empty dict

**Mesh**: register in source_rationale.yaml + test_topology.yaml.

### BATCH 2 — `detect_parameter_drift` + tests (~4-6h)

**Function**:
```python
def detect_parameter_drift(
    parameter_history: list[dict],
    bucket_key: str,
    *,
    drift_threshold_multiplier: float = 1.5,
    critical_ratio_cutoff: float = 2.0,
    min_windows: int = 4,
) -> ParameterDriftVerdict:
    """Detect parameter trajectory drift by ratio test on parameter
    movement vs trailing baseline (mirror EO BATCH 2 + WP BATCH 2 pattern).

    For EACH of (A, B, C) coefficients independently:
      ratio = |current - trailing_mean| / trailing_std
    drift_detected if ANY coefficient's ratio > drift_threshold_multiplier.
    Severity 'critical' if ratio >= critical_ratio_cutoff for ANY coeff.

    insufficient_data graceful: trailing_std <= 0 (parameters constant) →
    insufficient_data (no movement to detect drift against).
    """
```

ReturnsParameterDriftVerdict dataclass mirroring ReactionGapVerdict shape:
- kind: Literal["drift_detected", "within_normal", "insufficient_data"]
- severity: Literal["warn", "critical"] | None
- evidence dict: per-coefficient ratios + trailing_means + thresholds + n_windows

**Tests** (~6): synthetic A-drift / B-drift / C-drift / multi-coef drift / steady / insufficient.

### BATCH 3 — Weekly runner + e2e tests + AGENTS.md (~3-5h)

**File**: `scripts/calibration_observation_weekly.py` (NEW, ~300-400 LOC). Mirror of `ws_poll_reaction_weekly.py` shape. Output `docs/operations/calibration_observation/weekly_<date>.json`. Exit 1 if any bucket drift_detected. New AGENTS.md for the dir.

**Per-bucket threshold defaults dict** (LOW-DESIGN-WP-2-2 pattern carry-forward):
- HIGH metric_buckets (HKO/CWA/JMA fast-shifting): tighter (multiplier=1.3)
- LOW metric_buckets: standard (multiplier=1.5)
- New buckets (n < 30): suppress drift detection until maturity

CLI: --end-date / --window-days / --drift-threshold-multiplier / --critical-ratio-cutoff / --override-bucket KEY=VALUE / --db-path / --report-out / --stdout. Mirror EO/AD/WP flags. sys.path bootstrap pre-applied (LOW-OPERATIONAL-WP-3-1 lesson).

**Tests** (`tests/test_calibration_observation_weekly.py`, ~200-300 LOC, 5-6 e2e):
1. structural shape contract
2. empty_db + JSON round-trip + exit 0
3. drift_detected propagates exit 1
4. per-bucket override flips verdict
5. custom report-out + --stdout
6. (optional) bootstrap stats surfaced in JSON

## §3 Risk assessment per batch — HIGHER than EO/AD/WP

| Batch | Risk | vs EO/AD/WP equivalent | Mitigation |
|---|---|---|---|
| 1 | MEDIUM (vs LOW for sibling) | Adds list_active_platt_models_v2 to store.py (HIGH-MEDIUM module per AGENTS.md) — first read-side surface addition into the live calibration module | Pure-SELECT, no INSERT/UPDATE/DELETE; explicit @K1_read_only decorator-style docstring; isolated in NEW src/state/calibration_observation.py module; tests pin behavior |
| 2 | LOW (same as EO/AD/WP BATCH 2) | Pure-Python statistical detector over BATCH 1 outputs | Reuse EO BATCH 2 ratio-test pattern; same insufficient_data graceful behavior |
| 3 | LOW-MEDIUM (same as sibling) | CLI + JSON + new dir + script_manifest | Direct mirror of ws_poll_reaction_weekly.py shape |

**Cross-batch risk:** if operator chooses PATH B (decision-log JOIN attribution) or PATH C (writer extension), BATCH 1 design changes substantially; will not start BATCH 1 until PATH confirmed.

**HIGH-RISK surfaces explicitly identified — will NOT modify in this packet:**
- `src/calibration/platt.py` (HIGH per AGENTS.md — core engine)
- `src/calibration/manager.py` L1-end (HIGH per AGENTS.md — controls when calibration applies; live execution path)
- `src/calibration/retrain_trigger.py` (HIGH per AGENTS.md — operator-gated promotion seam)
- `src/calibration/blocked_oos.py` (shadow per AGENTS.md — but governance-packet promotion is operator territory)
- `src/calibration/effective_sample_size.py` (MEDIUM)
- `src/calibration/drift.py` (MEDIUM — existing detector; my detector is parametrically different and lives in src/state/calibration_observation.py)
- `architecture/2026_04_02_architecture_kernel.sql` (K0 frozen)
- `src/state/schema/v2_schema.py` (schema migration territory)
- ALL writer functions in store.py (add_calibration_pair, add_calibration_pair_v2, save_platt_model, save_platt_model_v2, deactivate_model, deactivate_model_v2)

**MEDIUM-RISK surfaces I MIGHT touch — only if KEY OPEN QUESTION #2 option (a) approved:**
- `src/calibration/store.py` — ONE NEW read function `list_active_platt_models_v2(conn) -> list[dict]` + ONE companion `list_active_platt_models_legacy(conn)` for bridge coverage. ~30 LOC each, pure SELECT, no mutation. Test coverage in tests/test_calibration_observation.py (3 tests pinning the read function).

## §4 Discipline pledges (carry-forward EO+AD+WP lessons)

- ARCH_PLAN_EVIDENCE = `docs/operations/task_2026-04-27_harness_debate/round3_verdict.md` for every architecture/** edit
- Imports consolidated to top of file (LOW-CAVEAT-EO-2-1 lesson; cite in module docstring)
- Boundary tests for thresholds (LOW-CAVEAT-EO-2-2 lesson)
- Co-tenant safe staging — defensively unstage anything not mine (AD BATCH 1 INV-09 case)
- Operator-empathy AGENTS.md "known-limitations" section (AD pattern)
- sys.path bootstrap pre-applied to BATCH 3 weekly runner (LOW-OPERATIONAL-WP-3-1 lesson) + regression test extension
- Per-strategy/per-bucket threshold dict + --override-bucket flag (LOW-DESIGN-WP-2-2 lesson)
- UPSTREAM-CLIPPING INVARIANT note in module docstring if any defensive math (LOW-NUANCE-WP-2-1 lesson)
- Per BATCH: SendMessage `BATCH_X_DONE_CALIBRATION_HARDENING files=<paths> tests=<X passed Y failed> baseline=<status> planning_lock=<receipt>`
- NO commits without critic-gate APPROVE
- 26-cycle anti-rubber-stamp critic discipline applies per BATCH

## §5 Out-of-scope (per dispatch — will NOT touch)

- ANY mutation of `platt_models` / `platt_models_v2` / `calibration_pairs` / `calibration_pairs_v2` tables (writer-side change)
- Modifying `src/calibration/retrain_trigger.py` decision logic (touches actual retrain authorization)
- Modifying `src/calibration/blocked_oos.py` policy
- Modifying calibration store schema (`src/state/schema/v2_schema.py` + SQL kernel)
- α-fusion weight tuning (deferred — implies actual parameter changes)
- double-bootstrap CI tightness adjustment (deferred — implies new statistical test framework)
- Modifying `src/calibration/platt.py` (core fit engine)
- Modifying `src/calibration/manager.py` (live routing logic)
- Modifying `src/calibration/drift.py` (existing detector — parametrically different from my BATCH 2 ask)
- LEARNING_LOOP packet (Week 21+; separate)
- WS_PROVENANCE_INSTRUMENTATION packet (deferred; separate)

**I am committing to MEASUREMENT-ONLY in BATCH 1+2+3.** No live calibration logic changes. No retrain decisions made. No model promotion gates touched. Pure shadow read + JSON report emit, classified as `derived_calibration_observation_projection` per the source_rationale.yaml authority_role enum (mirror EO/AD/WP entries).

## §6 Open clarifications for team-lead (defaults if no specific guidance)

1. **PATH choice for BATCH 1 attribution axis** (KEY OPEN QUESTION #1):
   - PATH A (per-bucket-key snapshot — drops "(city, target_date, strategy_key)" framing from contract because data substrate doesn't support it without writer-side change)
   - PATH B (synthetic strategy_key attribution via decision-log JOIN — recall-limited heuristic; trade_decisions.calibration_model_version is the only available join key)
   - PATH C (writer extension — out-of-scope per dispatch)
   - **Default: PATH A** (precision-favored, mirror WS_POLL precedent). PATH B risks invented-attribution critique.

2. **Add list-all read function to store.py** (KEY OPEN QUESTION #2):
   - Option (a) ADD `list_active_platt_models_v2(conn) -> list[dict]` + `list_active_platt_models_legacy(conn) -> list[dict]` to src/calibration/store.py (~30 LOC each, pure SELECT, tested)
   - Option (b) keep store.py untouched; issue raw SQL inside src/state/calibration_observation.py (creates parallel surface)
   - **Default: Option (a)** — extends canonical surface in correct module; sibling-coherent with EO+AD pattern of canonical-read additions. Risk MEDIUM but mitigated by pure-SELECT + tests.

3. **Drift detection algorithm for BATCH 2** (KEY OPEN QUESTION #3):
   - (i) Ratio test on movement vs trailing baseline (mirror EO BATCH 2 + WP BATCH 2 — coherent with siblings)
   - (ii) KL-divergence on bootstrap distributions (more powerful but novel for this packet — adds review surface)
   - (iii) Both — primary ratio test + KL as secondary evidence
   - **Default: (i)** — sibling-coherent, ratio-test pattern is well-tested across 26 critic cycles. KL deferred unless dispatch wants it.

4. **Per-coefficient drift OR aggregate drift in BATCH 2**:
   - Per-coefficient: drift_detected if ANY of A, B, C ratio > threshold (recall-favored)
   - Aggregate (e.g., L2-norm of (A, B, C) movement): drift_detected if combined movement > threshold (precision-favored)
   - **Default: per-coefficient with per-coefficient-evidence in verdict** (mirrors WP BATCH 2 multi-axis surfacing).

5. **Reading legacy platt_models AND platt_models_v2, or v2-only?**
   - v2-only: cleaner, but legacy buckets that haven't been migrated to v2 are invisible
   - Both: full coverage; legacy entries get explicit `source: 'legacy'` field
   - **Default: BOTH** with explicit source field, matching get_calibrator's v2-then-legacy fallback pattern (manager.py L42-62 dedup logic).

6. **Bootstrap parameter spread in BATCH 1 report shape**:
   - Surface bootstrap_A_std / bootstrap_B_std / bootstrap_C_std + percentile bands (5/95) per bucket
   - **Default: yes** — measures DBS-CI tightness without tuning (KEY OPEN QUESTION #5 resolution); operator-actionable signal.

7. **Critical-ratio-cutoff default for BATCH 2** (per critic-precedent):
   - WP used 2.0; same default here? Or tighter for HIGH-risk packet (e.g., 1.8)?
   - **Default: 2.0** — sibling-coherent. Operator can override per-bucket via BATCH 3 --override-bucket flag.

Will idle after BOOT_ACK_EXECUTOR_CALIBRATION_HARDENING. Will execute BATCH 1 only after explicit GO_BATCH_1_CALIBRATION_HARDENING from team-lead, with answers to §6 clarifications (or default-to-recommendation if no specific guidance).

End of boot.
