# LEARNING_LOOP packet — executor boot

Created: 2026-04-29
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27 (LONG-LAST)
Judge: team-lead
Source dispatch: DISPATCH_LEARNING_LOOP_PACKET (R3 §3 weeks 21-24, FIFTH and FINAL edge packet)
Plan-evidence basis: docs/operations/task_2026-04-27_harness_debate/round3_verdict.md §3
                     + docs/operations/task_2026-04-26_ultimate_plan/ULTIMATE_PLAN.md §4 #4
Reuse note: same K1 read-only patterns + same 3-batch + critic-gate cadence as
EO + AD + WP + CALIBRATION_HARDENING. HIGHEST risk among the 5 — touches the
end-to-end re-fit pipeline observation surface.

## §0 Read summary

| Source | What I learned |
|---|---|
| AGENTS.md root (skim) | Standard zeus root authority order; no LEARNING_LOOP-specific overrides |
| round3_verdict.md §3 weeks 21-24 | "LEARNING_LOOP + Tier 3 wrap (25-35% harness / 65-75% edge); All 5 edge packets shipping or shipped". Operator allocates harness substrate during this window for guardrails. |
| ULTIMATE_PLAN.md §4 #4 (L309-311) | Packet definition: "LEARNING_LOOP_PACKET — settlement-corpus → calibration update → parameter-drift → re-fit pipeline. Apr26 §11 corpus deferred; high/low split + DST resolved fixtures need owners. Apr26 Phase 4 silently dropped." |
| ULTIMATE_PLAN.md evidence/multi_review/MULTI_REVIEW_SYNTHESIS.md §6 | "0/20 cards improve edge. Forecast / calibration / learning legs of the money path entirely absent. Apr26 Phase 4 (settlement corpus, high/low split, DST resolved fixtures) silently dropped" — confirms the gap LEARNING_LOOP fills |
| ULTIMATE_PLAN.md evidence/multi_review/trading_correctness_report.md §settlement | "Forecast/calibration/edge/learning are entirely absent. Apr26 §16 defers them; plan inherits that gap without flagging it as residual" + "Apr26 §11 #3 (exchange resolution snapshot preservation, UMA dispute-window) rerouted to data-readiness — no slice card owns it" |
| src/calibration/AGENTS.md L14-22 (re-grep'd content per cycle-29 cite-discipline) | retrain_trigger.py marked HIGH — "Operator-gated retrain/promotion wiring + frozen-replay gate" — live calibration promotion seam. blocked_oos.py + effective_sample_size.py shadow-only. drift.py MEDIUM (HL χ² test). |
| src/calibration/AGENTS.md L41-46 Active vs Shadow | Active routing: platt.py, manager.py, store.py — live execution path; changes need governance packet. Shadow: blocked_oos.py, effective_sample_size.py — additive metrics in status_summary; never live blockers. PROMOTION OF SHADOW METRIC TO LIVE BLOCKER REQUIRES 30+ DAYS OF PARALLEL DATA + EXPLICIT OPERATOR APPROVAL + GOVERNANCE PACKET. |
| src/calibration/store.py:364-382 canonical_pairs_ready_for_refit | Returns bool: TRUE iff ALL VERIFIED calibration_pairs rows are bin_source='canonical_v1' AND have decision_group_id NOT NULL/empty. Pure-SELECT; K1-compliant. ALREADY EXISTS — this is the "is the pair corpus ready for refit" check at HEAD. |
| src/calibration/retrain_trigger.py L39-45 RetrainStatus enum | DISABLED / ARMED / RUNNING / COMPLETE_REPLAYED / COMPLETE_DRIFT_DETECTED. K1-readable enum. |
| src/calibration/retrain_trigger.py:177-190 status() | Returns DISABLED unless BOTH operator artifact AND ENV_FLAG_NAME=ZEUS_CALIBRATION_RETRAIN_ENABLED are present. Pure-SELECT (file-system + env check); K1-readable. |
| src/calibration/retrain_trigger.py:242-264 calibration_params_versions schema | KEY FINDING: APPEND-ONLY history table for retrain attempts. Schema: version_id (autoincrement), fitted_at, corpus_filter_json, params_json, fit_loss_metric, confirmed_trade_count, frozen_replay_status CHECK ∈ ('PASS','FAIL','SKIPPED'), frozen_replay_evidence_hash, promoted_at, retired_at, operator_token_hash, temperature_metric CHECK ∈ ('high','low'), cluster, season, data_version, input_space. **THIS IS THE APPEND-ONLY HISTORY THAT CALIBRATION_HARDENING BATCH 3 boot's HEAD-substrate-limitation finding said was MISSING. It exists, scoped to retrain_trigger.py.** |
| src/calibration/retrain_trigger.py:395-499 trigger_retrain | The actual retrain entry point. Operator-gated via arm() + frozen_replay_runner + corpus_filter validation. Inserts INTO calibration_params_versions (fail-closed on replay, promote on PASS) + DELETE-then-INSERT into platt_models_v2. **This is the K3 HIGH-RISK surface I MUST NOT TOUCH per dispatch.** |
| src/state/db.py:527-543 calibration_pairs schema | columns: id, city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value, decision_group_id, bias_corrected, authority CHECK ∈ ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'), bin_source. Per-pair, NOT per-decision-group (decision_group_id correlates rows). |
| src/state/db.py:545-565 calibration_decision_group schema | "Independent forecast-event units derived from calibration_pairs. Behavior-neutral substrate: active Platt routing still uses existing pair APIs until a later cutover packet explicitly switches maturity." Carries: group_id PK, city, target_date, forecast_available_at, cluster, season, lead_days, settlement_value, winning_range_label, bias_corrected, n_pair_rows, n_positive_rows, recorded_at. **THIS is the better surface for per-bucket corpus coverage tracking — already exists, K1-readable.** |
| sibling boots EO/AD/WP/CALIBRATION (re-read) | All 4 chose PATH A precision-favored framing. CALIBRATION explicitly chose PATH A "per-bucket-key snapshot" + dropped "(city, target_date, strategy_key)" axis from contract because data substrate didn't support it. SAME framework applies here. |

## §1 KEY OPEN QUESTIONS (the load-bearing findings)

### KEY OPEN QUESTION #1 — calibration_params_versions IS the append-only history I claimed was missing in CALIBRATION packet

**CALIBRATION_HARDENING BATCH 3 boot AGENTS.md known-limitations stated** (verbatim):
> "HEAD substrate has no append-only Platt history table. Each historical-window snapshot returns the CURRENTLY-active fit (because the platt_models_v2 UNIQUE constraint is on (..., is_active=1) — prior fits are deactivated, not preserved)."

**THIS IS WRONG.** I missed `calibration_params_versions` (src/calibration/retrain_trigger.py:242-264). It IS append-only:
- version_id is AUTOINCREMENT (no UNIQUE on is_active)
- promoted_at + retired_at columns track lifecycle (NULL retired_at = currently active; non-NULL = previously active, kept for audit)
- INSERT at every retrain attempt (PASS→promoted, FAIL→COMPLETE_DRIFT_DETECTED)
- UPDATE only sets retired_at on prior live row (never DELETEs)

**Implication for LEARNING_LOOP**:
- BATCH 1 can JOIN calibration_params_versions for genuine multi-fit parameter-trajectory history (not the flat-line same-active-row substrate of CALIBRATION BATCH 3)
- The CALIBRATION packet's `insufficient_data` defense was honest given my (incomplete) substrate read at the time, but on the proper substrate, drift detection becomes much more meaningful
- I will SURFACE this finding in LEARNING_LOOP boot evidence + AGENTS.md as a "previously-misunderstood substrate" honest-disclosure note

**This is exactly the LOW-CITATION-CALIBRATION-3-1 sustained discipline note from cycle 29: grep-verify CONTENT not just line ranges.** I cited "platt_models_v2 UNIQUE on is_active=1 means no append-only history" without grep-tracing the FULL retrain pipeline. Lesson reinforced.

### KEY OPEN QUESTION #2 — What does "settlement corpus → calibration update" pipeline look like end-to-end?

Tracing the data flow at HEAD:

1. **Settlement event** → position_events (`POSITION_SETTLED` or similar) with settled_at + settlement_value
2. **Harvester** (scripts/generate_calibration_pairs.py — re-grep'd; not yet read) reads settled positions and emits 11 pairs per market into calibration_pairs (1 outcome=1, 10 outcome=0 per the spec; per src/calibration/store.py:84)
3. **Pair authority** is set to UNVERIFIED initially; promoted to VERIFIED via a separate audit step (canonical_pairs_ready_for_refit gates this)
4. **Decision-group derivation** → calibration_decision_group rows recorded for each independent forecast-event unit (per db.py:545-565 schema; "behavior-neutral substrate")
5. **Operator decides to refit** → arm() + ZEUS_CALIBRATION_RETRAIN_ENABLED env var + evidence path matching ARTIFACT_PATTERN
6. **trigger_retrain** loads CONFIRMED corpus via load_calibration_trade_facts + frozen-replay → INSERT calibration_params_versions + UPDATE retired_at on prior live + DELETE-then-INSERT platt_models_v2

**Stage-by-stage observable points (K1 read-only)**:

| Stage | Persisted state | Read function (existing or NEEDED) |
|---|---|---|
| Settlement event arrival | position_events | (out-of-scope; ATTRIBUTION_DRIFT covers it) |
| Calibration pairs written | calibration_pairs (rows with bin_source='legacy' or 'canonical_v1', authority='UNVERIFIED' or 'VERIFIED') | get_pairs_for_bucket (already exists L207); count by authority+bin_source (NEW SQL needed) |
| Decision groups written | calibration_decision_group | get_decision_group_count (already exists L330); list-all NEEDED for per-bucket coverage |
| Pair corpus ready for refit? | (computed) | canonical_pairs_ready_for_refit (already exists L364) — returns bool, not per-bucket |
| Retrain status | (file-system + env) | retrain_trigger.status() (already exists L177) |
| Retrain history | calibration_params_versions | (NEW NEEDED — no public-API list_recent_retrain_versions exists) |
| Currently-active model | platt_models / platt_models_v2 | list_active_platt_models_v2 / _legacy (added in CALIBRATION BATCH 1) |

### KEY OPEN QUESTION #3 — What's the right scope of "stall detection"?

Dispatch §BATCH 2 mentions:
> "(a) corpus is growing but pairs aren't being added, (b) pairs ARE ready but retrain hasn't fired, (c) parameter drift is detected (BATCH 2 of CALIBRATION_HARDENING) but no refit triggered"

Each of these is a different signal:

(a) **corpus-vs-pairs lag** — settlement events vs calibration pair writes. Detectable via JOIN(position_events × calibration_pairs by city+target_date) — but adds K3 cross-coupling. SAFER alternative: count pairs added in last N windows vs prior trailing windows. If ratio drops, "stall" candidate.

(b) **pairs-ready-but-no-retrain** — canonical_pairs_ready_for_refit returns TRUE + retrain_status() == DISABLED for >N weeks. Operator-actionable: "you have data, run a retrain."

(c) **drift-detected-but-no-refit** — CALIBRATION BATCH 2 detected drift in week K, week K+1 still no NEW row in calibration_params_versions. CROSS-PACKET integration; reuses calibration_observation.detect_parameter_drift output.

**Default plan**: support all 3 detectors as separate stall_kind values in a single ParameterStallVerdict dataclass. Operator gets per-bucket per-stall-kind output. Honest precision-favored — only flag when the substrate actually supports the detection.

### KEY OPEN QUESTION #4 — What's persisted vs ephemeral in the loop?

| Item | Persistence | K1-readable? |
|---|---|---|
| Settlement events | position_events (canonical) | yes via query_authoritative_settlement_rows |
| Calibration pairs | calibration_pairs (canonical) | yes via get_pairs_for_bucket / get_pairs_count |
| Decision groups | calibration_decision_group (canonical, behavior-neutral) | partial — get_decision_group_count exists; NO list-all |
| Retrain attempts (PASS+FAIL) | calibration_params_versions (append-only) | NO public reader — must add ONE pure-SELECT function (mirror of CALIBRATION BATCH 1 list_active_platt_models_v2 pattern) |
| Retrain status (current) | env var + file system | yes via retrain_trigger.status() — pure-read |
| Active Platt models | platt_models[_v2] (UNIQUE on is_active=1) | yes via list_active_platt_models_v2 (CALIBRATION BATCH 1) |
| Frozen-replay results | calibration_params_versions.frozen_replay_status + evidence_hash | yes — same NEW reader as retrain attempts |

**Implication**: BATCH 1 needs ONE NEW pure-SELECT function in retrain_trigger.py: `list_recent_retrain_versions(conn, limit=100) -> list[dict]`. K1-compliant; mirrors CALIBRATION BATCH 1's store.py read additions. CRITIC PRE-FLAG: retrain_trigger.py is K3 HIGH per src/calibration/AGENTS.md L19; the read function is pure-SELECT additive, no impact on arm/trigger_retrain live behavior.

### KEY OPEN QUESTION #5 — DST/HIGH/LOW split mention

Dispatch + ULTIMATE_PLAN §4 #4 mentions: "high/low split + DST resolved fixtures need owners. Apr26 Phase 4 silently dropped."

These are SUBSTRATE GAPS in the test fixtures, not code modules:
- "high/low split" = HIGH track vs LOW track Platt models share the same calibration_pairs table; only platt_models_v2 carries temperature_metric. The split happens at READ time via load_platt_model_v2 + get_pairs_for_bucket(metric=...) NotImplementedError gate.
- "DST resolved fixtures" = test fixtures that exercise the DST hour-skip behavior on settlement_value rounding (per HKO oracle_truncate per src/contracts/settlement_semantics.py).

**Out of scope** for LEARNING_LOOP measurement-only packet. Mentioned in AGENTS.md known-limitations as "Apr26 §11 corpus + Phase 4 fixture work — operator-decision; future packet."

## §2 Per-batch design sketch (PATH A measurement-only)

### BATCH 1 — `compute_learning_loop_state_per_bucket` + tests (~6-10h)

**Files**:
- NEW: `src/state/learning_loop_observation.py` (~280-340 LOC)
- ADD: 1 NEW pure-SELECT function `list_recent_retrain_versions(conn, limit=100)` in `src/calibration/retrain_trigger.py` (~30 LOC + docstring)
- NEW: `tests/test_learning_loop_observation.py` (~250-330 LOC, ~10 tests)

**Function signature**:
```python
def compute_learning_loop_state_per_bucket(
    conn, window_days=7, end_date=None,
) -> dict[str, dict[str, Any]]:
    """K1-compliant read-only. Returns per-bucket-key dict:
    {
        bucket_key: str,           # mirrors CALIBRATION BATCH 1 keying
        source: 'v2' | 'legacy',
        # Calibration-pair stage
        n_pairs_total: int,
        n_pairs_verified: int,
        n_pairs_canonical: int,    # bin_source='canonical_v1' subset
        n_decision_groups: int,
        # Retrain stage
        retrain_status: str,       # DISABLED|ARMED (process-level, not per-bucket)
        n_retrain_attempts_in_window: int,  # calibration_params_versions
        n_retrain_passed_in_window: int,
        n_retrain_failed_in_window: int,
        last_retrain_attempted_at: str | None,
        last_retrain_promoted_at: str | None,
        days_since_last_promotion: int | None,
        # Active model stage (reuse CALIBRATION BATCH 1)
        active_model_fitted_at: str | None,
        active_model_n_samples: int,
        # Sample quality
        sample_quality: 'insufficient' | 'low' | 'adequate' | 'high',
        window_start, window_end,
    }
    """
```

**Tests** (`tests/test_learning_loop_observation.py`, ~10 tests):
1. structural shape contract (10 fields per bucket)
2. empty_db safety
3. n_pairs_verified vs n_pairs_canonical distinction
4. retrain_status pure-read (DISABLED when env unset; uses real process env, monkeypatch friendly)
5. n_retrain_attempts_in_window correctly windowed
6. last_retrain_promoted_at nil-on-no-history
7. days_since_last_promotion math (with synthetic timestamps)
8. v2 + legacy bucket dedup (mirror CALIBRATION BATCH 1 dedup pattern)
9. sample_quality boundaries (10/30/100, sibling-coherent)
10. list_recent_retrain_versions reader filter (limit=100, ORDER BY fitted_at DESC)

**Mesh**: register in source_rationale.yaml + test_topology.yaml.

### BATCH 2 — `detect_learning_loop_stall` + tests (~4-6h)

**Function**:
```python
def detect_learning_loop_stall(
    history: list[dict[str, Any]],
    bucket_key: str,
    *,
    pair_growth_threshold_multiplier: float = 1.5,  # corpus-vs-pairs ratio
    days_pairs_ready_no_retrain: int = 30,           # pairs-ready-no-retrain
    days_drift_no_refit: int = 14,                   # drift-no-refit
    min_windows: int = 4,
) -> ParameterStallVerdict:
    """Detect 3 stall kinds:
    - corpus_vs_pair_lag: pair growth in current window << trailing baseline
    - pairs_ready_no_retrain: canonical_pairs_ready=TRUE for > days_pairs_ready_no_retrain
    - drift_no_refit: drift_detected (caller-provided) for > days_drift_no_refit
    """
```

ParameterStallVerdict dataclass:
- kind: Literal["stall_detected", "within_normal", "insufficient_data"]
- stall_kinds: list[str]  # subset of ["corpus_vs_pair_lag", "pairs_ready_no_retrain", "drift_no_refit"]
- severity: Literal["warn", "critical"] | None
- evidence: dict (per-kind details + thresholds)

**Tests** (~6): synthetic corpus_vs_pair_lag / pairs_ready_no_retrain / drift_no_refit + steady + insufficient + multi-kind.

### BATCH 3 — Weekly runner + e2e tests + AGENTS.md (~3-5h)

**File**: `scripts/learning_loop_observation_weekly.py` (NEW, ~330-400 LOC). Mirror of `scripts/calibration_observation_weekly.py` shape. Output `docs/operations/learning_loop_observation/weekly_<date>.json`. Exit 1 if any bucket has stall_detected.

**Per-bucket threshold defaults dict** (LOW-DESIGN-WP-2-2 carry-forward):
- HIGH temperature_metric: tighter thresholds across all 3 stall_kinds
- LOW: standard
- legacy: standard
- insufficient: SUPPRESS

**CLI**: --end-date / --window-days / --pair-growth-threshold / --days-pairs-ready / --days-drift-no-refit / --override-bucket KEY=VALUE / --db-path / --report-out / --stdout. Mirror precedent. sys.path bootstrap pre-applied (LOW-OPERATIONAL-WP-3-1 lesson).

**Tests** (~6 e2e): structural shape / empty / stall propagates exit 1 / per-bucket override / report-out + stdout / extends 5-runner regression in test_ws_poll_reaction_weekly.py.

**AGENTS.md**: derived-context auth class + KNOWN-LIMITATIONS (PATH A; cross-packet drift integration deferred; HEAD substrate IS now properly understood — calibration_params_versions append-only history exists; Apr26 §11 corpus + Phase 4 fixtures out-of-scope) + per-bucket threshold rationale TABLE + operator runbook + how-to-regenerate.

## §3 Risk assessment per batch — HIGHEST among the 5 packets

| Batch | Risk | vs prior 4 packets equivalent | Mitigation |
|---|---|---|---|
| 1 | MEDIUM-HIGH (vs MEDIUM for CALIBRATION) | Adds list_recent_retrain_versions to retrain_trigger.py (HIGH per AGENTS.md L19 — operator-gated retrain seam). 2nd time touching K3 calibration code; first time touching the live retrain promotion file. | Pure-SELECT, no INSERT/UPDATE/DELETE, no impact on arm/trigger_retrain live behavior; isolated NEW src/state/learning_loop_observation.py; tests pin behavior; CRITIC PRE-FLAG explicit |
| 2 | LOW-MEDIUM (vs LOW for CALIBRATION/WP) | Pure-Python detector but 3 stall_kinds vs 1 — wider state space; integration with CALIBRATION BATCH 2 drift output requires drift_detected as caller-provided input, not direct cross-module read | All 3 detectors are independent + composable; insufficient_data graceful per kind |
| 3 | LOW-MEDIUM (same as sibling) | CLI + JSON + AGENTS.md mirror | Direct mirror of calibration_observation_weekly.py shape |

**Cross-batch risk:** highest among 5. Calibration_params_versions read addition lives in HIGH-RISK retrain_trigger.py module. CRITIC 30th cycle should verify: read function is purely additive; no impact on existing arm/trigger_retrain control flow; no cross-coupling to platt_models_v2 writer; no schema mutation.

**HIGH-RISK surfaces explicitly identified — will NOT modify:**
- `src/calibration/retrain_trigger.py` arm() / trigger_retrain() / _insert_version() / _ensure_versions_table() / load_confirmed_corpus() — all the WRITER functions stay untouched
- `src/calibration/{platt,manager,blocked_oos,drift,effective_sample_size}.py` — K3 active surfaces NOT modified
- `src/state/db.py` schema definitions — NO changes
- All writer functions in store.py (add_calibration_pair_v2, save_platt_model_v2, deactivate_model_v2, etc.) — NOT modified

**MEDIUM-RISK surfaces I MIGHT touch:**
- `src/calibration/retrain_trigger.py` — ONE NEW pure-SELECT function `list_recent_retrain_versions(conn, limit=100) -> list[dict]` (~30 LOC + docstring + 1 test)

## §4 Discipline pledges (carry-forward all prior packet lessons)

- ARCH_PLAN_EVIDENCE = `docs/operations/task_2026-04-27_harness_debate/round3_verdict.md` for every architecture/** edit
- Imports consolidated to top of file (LOW-CAVEAT-EO-2-1)
- Boundary tests for thresholds (LOW-CAVEAT-EO-2-2)
- Co-tenant safe staging — defensively unstage anything not mine (AD BATCH 1 INV-09 case + CALIBRATION BATCH 3 stash-and-patch precedent)
- Operator-empathy AGENTS.md "known-limitations" section (sibling pattern)
- sys.path bootstrap pre-applied to BATCH 3 weekly runner (LOW-OPERATIONAL-WP-3-1)
- Per-bucket threshold dict + --override-bucket flag (LOW-DESIGN-WP-2-2)
- bootstrap_usable_count distinct from bootstrap_count (LOW-NUANCE-CALIBRATION-1-2 pattern carry-forward — surface both raw count AND validly-aggregated count when applicable)
- **GREP-VERIFY CITE CONTENT not just line numbers** (LOW-CITATION-CALIBRATION-3-1 sustained discipline note from cycle 29 — every cite to manager.py / store.py / retrain_trigger.py L<N> must be grep-verified at write-time for the actual content matching the claim)
- LOW-CITATION-CALIBRATION-3-1 explicit fix in BATCH 1 commit message: cite to src/calibration/AGENTS.md must reference the actual content (re-verified L14-22 in §0 above is correct for the danger-level table)
- LOW-DOCSTRING-CALIBRATION-3-2: trivial s/3/4/g fix in tests/test_ws_poll_reaction_weekly.py:378-389 docstring (now iterates 4 runners) — fold into BATCH 3 commit alongside the new 5-runner extension
- **HONEST DISCLOSURE in BATCH 1 module docstring + AGENTS.md: previous CALIBRATION packet's "no append-only history" finding was WRONG — calibration_params_versions exists at retrain_trigger.py:242-264. Cite the gap and the correction.**
- Per BATCH: SendMessage `BATCH_X_DONE_LEARNING_LOOP files=<paths> tests=<X passed Y failed> baseline=<status> planning_lock=<receipt>`
- NO commits without critic-gate APPROVE
- 29-cycle anti-rubber-stamp critic discipline applies per BATCH (becomes 30/31/32 after this packet)

## §5 Out-of-scope (per dispatch — will NOT touch)

- ANY mutation of `platt_models` / `platt_models_v2` / `calibration_pairs[_v2]` / `position_events` / `calibration_params_versions` / `venue_trade_facts` tables (writer-side change)
- Modifying `src/calibration/retrain_trigger.py` arm / trigger_retrain / _insert_version / _ensure_versions_table / load_confirmed_corpus / _filter_corpus_identity / _signed_operator_token_hash (control + write surfaces)
- Modifying `src/calibration/{platt,manager,blocked_oos,drift,effective_sample_size}.py` (K3 active surfaces)
- Modifying calibration store schema (`src/state/db.py` / `src/state/schema/v2_schema.py`)
- Modifying `src/calibration/store.py` writer functions
- Schema migrations
- Actual refit triggering / parameter-update logic
- α-fusion + DBS-CI + KL-divergence detectors (still all deferred from prior packets)
- Apr26 §11 corpus + Phase 4 high/low split + DST resolved fixtures (operator-decision; future packet)
- LEARNING_LOOP_TRIGGERING (separate packet — operator-authorized; would modify retrain_trigger.py arm/trigger paths)

**I am committing to MEASUREMENT-ONLY in BATCH 1+2+3.** No live retrain logic changes. No retrain decisions made. No model promotion gates touched. Pure shadow read + JSON report emit, classified as `derived_learning_loop_state_projection` per the source_rationale.yaml authority_role enum (mirror sibling EO/AD/WP/CALIBRATION entries).

## §6 Open clarifications for team-lead (defaults if no specific guidance)

1. **Add list_recent_retrain_versions to retrain_trigger.py?** (KEY OPEN QUESTION #4):
   - Option (a) ADD `list_recent_retrain_versions(conn, limit=100) -> list[dict]` to src/calibration/retrain_trigger.py (~30 LOC + docstring + 1 test). Pure SELECT.
   - Option (b) raw SQL inside src/state/learning_loop_observation.py — creates parallel surface; doesn't extend canonical reader.
   - **Default: Option (a)** — sibling-coherent with CALIBRATION BATCH 1 pattern (canonical-read additions to the right module). Risk MEDIUM-HIGH but mitigated by pure-SELECT + tests + CRITIC PRE-FLAG.

2. **3 stall_kinds in BATCH 2 OR start with 1?** (KEY OPEN QUESTION #3):
   - 3 stall_kinds (corpus_vs_pair_lag + pairs_ready_no_retrain + drift_no_refit) — full coverage; wider state space
   - 1 stall_kind (pairs_ready_no_retrain only — most operator-actionable + no cross-packet integration) — narrower, simpler
   - **Default: 3** with composable flag + per-kind insufficient_data graceful. Honestly precision-favored per kind.

3. **drift_no_refit cross-packet integration**:
   - Read calibration_observation.detect_parameter_drift output via direct module call (cross-module coupling)
   - Pass drift_detected as caller-provided argument (BATCH 3 weekly runner orchestrates the join)
   - **Default: caller-provided** — keeps detect_learning_loop_stall pure-Python with no cross-module DB reads.

4. **Window default for retrain history**:
   - 7 days (sibling weekly windowing)
   - 28 days (4-week trailing for retrain frequency analysis)
   - **Default: 7 days for current-window stats; 28 days for `n_retrain_attempts_in_window` aggregation** (mixed). Operator-tunable via --window-days flag.

5. **ParameterStallVerdict severity tiers**:
   - warn at 1.5x ratio / 30 days no-retrain / 14 days drift-no-refit
   - critical at 2.0x ratio / 60 days no-retrain / 30 days drift-no-refit
   - **Default: above defaults** — sibling-coherent with WP/CALIBRATION 1.5/2.0 ratio precedent.

6. **HONEST DISCLOSURE about CALIBRATION packet substrate misread**:
   - Surface in BATCH 1 module docstring + AGENTS.md (transparent learning)
   - Add a small note to docs/operations/calibration_observation/AGENTS.md too (cross-link the correction)
   - **Default: BOTH** — operator-empathy + cite-discipline reinforcement. The fact that I missed calibration_params_versions in the prior packet is exactly the lesson cycle 29 LOW-CITATION-3-1 was teaching. Honest disclosure is the cure.

Will idle after BOOT_ACK_EXECUTOR_LEARNING_LOOP. Will execute BATCH 1 only after explicit GO_BATCH_1_LEARNING_LOOP from team-lead, with answers to §6 clarifications (or default-to-recommendation).

End of boot.
