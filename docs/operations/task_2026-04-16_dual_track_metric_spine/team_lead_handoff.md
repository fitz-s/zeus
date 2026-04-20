# Team-Lead Handoff (post-P9C + e2e audit, 2026-04-19)

**Written**: 2026-04-18 post Phase 5 + 6 + 7A. **Updated 2026-04-19 post P9C closure + post-close e2e independent audit.** Gen-Verifier mode. Team-lead (Opus, main context) + rotating persistent critic (beth retired after P5fix/P7A/P7B; carol retired after P8/P9A/P9B; **dave active** cycle 1 after P9C) + ephemeral subagents.

## ⚠️ CRITICAL POST-COMPACT FIRST READ

**The dual-track refactor is STRUCTURALLY COMPLETE but has a CRITICAL pre-existing production bug (monitor_refresh NameError, HIGH+LOW Day0 both affected) + Gate C was never actually closed (v2 tables empty; HIGH still reads legacy).**

**Before any other work, read**:
`docs/operations/task_2026-04-16_dual_track_metric_spine/e2e_audit/synthesis_and_remediation_plan.md`

That file contains the full compact remediation plan (R1-R13) with file:line citations + prioritized packet recommendations (P10A/B/C). Supporting audits at same directory:
- `architect_end_state_audit.md` — structural audit (PARTIAL verdict, "ship seaworthy, cargo never loaded")
- `runtime_trace.md` — runtime trace (3 silent-failure risks + discriminating probe for ingest metric stamp)

## IMMEDIATE NEXT ACTIONS (post-compact, in order)

0. **NEW** — Read `e2e_audit/synthesis_and_remediation_plan.md` — durable compact summary of the two-agent independent audit post-P9C. Single read that resurrects full dual-track end-state.
1. Read `~/.claude/agent-team-methodology.md` — operating manual. §"Critic role" (L0.0 peer-not-suspect, 6-tier hypothesis ordering).
2. Read `~/.claude/CLAUDE.md` — global Fitz methodology, Four Constraints, Code Provenance.
3. Read THIS file IN FULL.
4. Read `docs/authority/zeus_dual_track_architecture.md` §2/§5/§6/§8 + `zeus_current_architecture.md` §13-§22.
5. Read the 10 learnings docs (5 from `zeus-dual-track` retirement, 5 from `zeus-dual-upgrade-v3` pre-compact) at `phase5_evidence/phase5b_to_phase5c_*.md` + `phase5_evidence/phase5_to_phase6_*.md` — durable multi-phase mental model.
6. Read critic verdict docs: `phase5_evidence/critic_alice_5B_verdict.md` + `critic_beth_phase5fix_wide_review.md` + `critic_beth_phase5c_wide_review_final.md` — structural antibody history.
7. `git log --oneline -6` — confirm Phase 5 top 5 commits.
8. Check `~/.claude/teams/zeus-dual-upgrade-v3/` — team should exist (OMC session-end patch keeps it alive). If missing, re-spawn per § "Team bootstrap (if needed)".
9. Resume team with a brief `phase6 open` SendMessage if existing, else spawn fresh.

## Branch + commit state

Branch: `data-improve`. Top of `origin/data-improve` = `f2ffcad`. All pushed.

```
f2ffcad docs(phase10b-close): critic-dave retirement verdict PASS-WITH-RESERVATIONS
f632a9f fix(phase10b): P10A R-CK regression via missing temperature_metric in _candidate + v2 contract doc
8d46f44 feat(phase10b): DT-Seam Cleanup — 5 structural seams + 13 antibody tests
81294d2 feat(phase10a): independent hygiene fix pack — R1 monitor rename + B071 + B091-lower + S5 doc flip
3b306c0 Sync workspace artifacts without touching Phase 10A code
630a1e6 docs(e2e-audit): post-P9C independent verification + compact remediation plan
0a760bb docs(phase9c-close): DUAL-TRACK MAIN LINE CLOSED + critic-dave artifacts
d516e6b fix(phase9c): ITERATE resolution — two-seam closure for L3 + DT#7 wire antibody
114a0f5 feat(phase9c): dual-track main-line closure — L3 CRITICAL + 8 structural items
69978af docs(phase9b-close): Phase 9B CLOSED at b73927c + critic-carol retirement + dave onboarding
e3a4700 docs(phase6): microplan — Strategy A internal M1-M4 milestones
        ^ ACTUAL CONTENT: full Phase 6 implementation (Day0 split + DT#6 + B055
          absorption). Commit-message mislabeling via team-lead git coordination
          error; content reviewed + PASS by critic-beth.
          User-deferred commit-msg amend (force-push decision).
```

**Phase 5 COMPLETE.** Gate D PASSED via R-AZ-3 structural antibody.
**Phase 6 COMPLETE** at `413d5e0` (impl + ITERATE fix). Closure journey:
- `e3a4700`: full Phase 6 implementation (mislabeled "docs(phase6): microplan" via team-lead git-index coordination error — user-ruled accept cosmetic mislabeling, no force-push amend)
- `413d5e0`: ITERATE MAJOR-1 fix — Day0Signal class-level TypeError re-guard + R2 antibody test upgrade to dual-assertion

Silent-corruption category eliminated at TWO layers:
1. `RemainingMemberExtrema` dataclass (`__post_init__` raises on both-None → MAX/MIN alias unconstructable)
2. `Day0Signal.__init__` class guard (`TypeError` on LOW → direct construction with wrong metric refused)

Defense-in-depth: router seam (Day0Router always dispatches LOW→Day0LowNowcastSignal) + class seam (Day0Signal refuses LOW directly). Fitz P4 category-impossibility preserved at both layers.

critic-beth authoritative verdict at `phase5_evidence/critic_beth_phase6_wide_review.md` (includes ITERATE finding + re-verify PASS addendum). Superseded initial PASS at `phase6_evidence/critic_beth_phase6_wide_review.md` preserved for audit trail.

## Phase order post-P7A

1. ~~**Phase 6**~~ **COMPLETE** at `413d5e0`. Day0 split delivered + critic PASS. See "Phase 6 closure" below.
2. ~~**Phase 7A**~~ **COMPLETE** at `c496c36` + `a872e50`. Metric-aware rebuild cutover + delete_slice metric scoping + CRITICAL-1 read-side fix + MAJOR-1 schema DEFAULT restoration + MAJOR-2 backfill contract gate. See "Phase 7A closure" below.
3. ~~**Phase 7B**~~ **COMPLETE (5/6)** at `6fc41ec`. Naming hygiene: metric_specs extracted + alias removed + manifest registered + schema dropped + R-AZ-2 rewritten. See "Phase 7B closure" below. Item 2 (_tigge_common extract) deferred to P7B-followup — planner's "15 safe helpers" claim was inaccurate (module-level constants differ).
4. ~~**Phase 7B-followup**~~ **COMPLETE** at `2adcbc9`. Item 2 (_tigge_common extract — 13 helpers + CityManifestDriftError + compute_required_max_step/compute_manifest_hash) + naming_conventions hygiene (extract_ prefix + refit_platt_v2 exception + _tigge_common manifest entry). Net +318/-364 LOC. 89/89 targeted tests GREEN. Scout 2026-04-18 confirmed: `run_replay` actually lives at `src/engine/replay.py:1932` (not `src/main.py` as earlier handoff said); evaluator is already metric-aware (no P8 work there); `monitor_refresh.py` has zero `temperature_metric` references (non-Day0 LOW wiring still owed); settlement writer does not exist in code (settlements arrive externally).
5. ~~**Phase 8 route A**~~ **COMPLETE** at `6ffefa4` (critic-carol first-try PASS, 2026-04-18). Scope: S1 `run_replay` public-entry `temperature_metric` threading + S2 `cycle_runner.py:180-181` DT#6 rewire to `riskguard.tick_with_portfolio`. 4/4 R-BP/R-BQ antibodies GREEN. Full regression 144/1846 (zero new failures vs 144/1842 baseline; +4 from antibodies). Contract: `phase8_contract.md`. Route A honored: code-only, no TIGGE data import, v2 tables zero-row, Golden Window preserved. See "Phase 8 closure" below. Gate E code-prerequisites complete; Gate E data-evidence blocks on future Golden Window lift (P9/later).
6. ~~**Phase 9A**~~ **COMPLETE** at `7081634` (critic-carol cycle 2 first-try PASS, 2026-04-18). Absorbs all 4 P8 MAJOR observability forward-log items + adopts DT#6 Interpretation B in authority doc. Scope: S1 `entries_blocked_reason` DATA_DEGRADED + S2 L195 overwrite comment + S3 run_replay mode+metric warning + S4 R-BQ.1 structural hardening (drops silent-return + text-match) + S5 DT#6 §B law clarification + S6 R-BS.1/2 save_portfolio roundtrip + S7 R-BT entries_blocked_reason antibody + S8 R-BU.1/2 mode+metric warning pair. 9/9 antibodies GREEN. Full regression 144/1851 (+5 exact match, zero new failures). 2 MINOR (R-BS.2 vacuous assert + DT#6 §B aspirational doc) patched in phase9a-close commit. See "Phase 9A closure" below.
7. ~~**Phase 9B**~~ **COMPLETE** at `0974a62` (impl) + `b73927c` (ITERATE fix). critic-carol cycle 3 — **streak broke as predicted** (P7B+P8+P9A=3 PASS, P9B=ITERATE). Adversarial opening surfaced CRITICAL-1: DT#2 marker was inert (no runtime consumer read `exit_reason="red_force_exit"`). ITERATE fix wired marker into `evaluate_exit` short-circuit + added R-BY/R-BY.2 relationship antibody pair. Re-verify PASS. 5/5 antibodies GREEN (R-BV/R-BW/R-BX/R-BY/R-BY.2). Regression 144/1856/93 (+5 from P9B antibodies, zero new failures). critic-carol **retires** after 3 cycles (P8/P9A/P9B); P9C opens with critic-dave. See "Phase 9B closure" below.
8. ~~**Phase 9C**~~ **COMPLETE** at `114a0f5` (feat) + `d516e6b` (ITERATE fix). **DUAL-TRACK MAIN LINE SCAFFOLD COMPLETE** (per critic-dave cycle-1 re-verify; data migration deferred to post-Golden-Window). Scope delivered: S1 L3 CRITICAL `get_calibrator` metric-aware + new `load_platt_model_v2` reader + 4 callers (**the blocker that made LOW deployment impossible pre-P9C** — calibration read side was metric-blind) + S2 A3 `Day0LowNowcastSignal.p_vector` + S3 A1 `_forecast_rows_for` conditional v2 read + S4 A4 DT#7 gate wired to evaluator + S5 B1 `--temperature-metric` CLI flag + S6 B3 `save_portfolio` source audit tag + S7 C2 R-BY.2 strengthen + S8 L2 dedicated `tests/test_phase9c_gate_f_prep.py` + S9 L1 settlements_v2 external-only policy doc. critic-dave cycle 1 returned ITERATE with 2 MAJOR findings (R-CC.2 checkbox antibody at helper boundary + `_fit_from_pairs` legacy-save latent bomb — write-side twin of L3); ITERATE fix at `d516e6b` added `_fit_from_pairs` metric-aware guard (LOW → None early-return, write-side two-seam closed) + R-CC.3 AST-walk structural antibody (evaluator gate wire) + R-CG.1/2/3 paired antibodies (LOW-skip positive/surgical/HIGH-unchanged). Re-verify PASS. 17 P9C antibodies GREEN. Full regression 144/1873/93 (+17 from pre-P9B; zero new failures). See "Phase 9C closure" below.
9. ~~**Phase 10A**~~ **COMPLETE** at `81294d2`. Independent hygiene fix pack: R1 monitor_refresh NameError rename + B071 token_suppression history+view + B091-lower decision_time_status evaluator extension + S5 doc flip (14 bug rows). 21 antibodies GREEN (R-CH/R-CI/R-CJ/R-CK families including 2 ITERATE-fix wiring antibodies). Full regression 142/1894/93 (−2 failed, +21 passed vs P9C baseline). See "Phase 10A closure" below.
10. ~~**Phase 10B**~~ **COMPLETE** at `8d46f44` (feat) + `f632a9f` (ITERATE fix). DT-Seam Cleanup — 5 structural seams (R3 replay WHERE + R4 oracle_penalty keying + R5 Literal annotations + R9 FDR metric-aware + R11 v2 row-count sensor). 13 antibodies GREEN (R-CL/R-CM/R-CN/R-CO/R-CP families). Full regression 144/1905/93 (+13 passed vs P10A baseline; zero new failures). critic-dave cycle 3 PASS-WITH-RESERVATIONS → **dave retires**. critic-eve slot open. See "Phase 10B closure" below.

## Phase 10B closure

**Commits**: `8d46f44` feat(phase10b): DT-Seam Cleanup — 5 structural seams + 13 antibody tests → `f632a9f` fix(phase10b): P10A R-CK regression via missing temperature_metric in _candidate + v2 contract doc. critic-dave cycle 3 (retirement cycle) PASS-WITH-RESERVATIONS.

**Delivered (5 S-items, all per contract v2)**:
- **S1 R3** — `src/engine/replay.py:309` metric-aware legacy fallback WHERE clause. `col = "forecast_low" if temperature_metric == "low" else "forecast_high"`. LOW replay with v2-empty Golden Window now falls through to the correct column instead of HIGH-only filter.
- **S2 R4** — `src/strategy/oracle_penalty.py` cache re-keyed from `dict[str, OracleInfo]` → `dict[tuple[str, str], OracleInfo]` keyed by `(city, temperature_metric)`. `data/oracle_error_rates.json` schema extended to `{city: {high: {...}, low: {...}}}` nested metric dimension. Legacy flat-shape JSON migrated as `(city, "high")` entries on load. `get_oracle_info(city, temperature_metric)` kwarg added. 3 evaluator.py call sites pass `position.temperature_metric`. Delivers 2/3 of DT#7.
- **S3 R5** — `temperature_metric: Literal["high", "low"] = "high"` at all 9 explicit runtime seams (allowlist per contract v2). 3/9 seams have runtime assert enforcement; 6/9 Literal annotation only. Forward-logged to P10C for full MetricIdentity migration. Trade-off accepted and documented per dave L26.
- **S4 R9** — `make_hypothesis_family_id` / `make_edge_family_id` extended with `temperature_metric: Literal["high", "low"]` required kwarg. Tuple now includes metric dimension. Evaluator.py call sites pass candidate metric. Test `test_fdr_family_key_is_canonical` EXTENDED (not activated) with metric-discriminating assertion.
- **S5 R11** — `src/observability/status_summary.py` gains `_get_v2_row_counts(conn)` helper querying 5 v2 tables; emits `v2_row_counts` into status payload. Discrepancy flag fires when `platt_models_v2=0 AND dual_track_closure: true`. Closes meta-immune-system gap P9C fired without.

**ITERATE fix** (`f632a9f`): executor autocommitted `8d46f44` before critic review; team-lead wide-review pre-push caught R-CK helper regression from P10A — `_candidate` helper in evaluator was missing `temperature_metric` thread for new FDR/oracle_penalty callers. Fix applied before push. critic-dave L22 candidate memory (executor-without-critic coordination fault pattern).

**Antibodies installed (13 total for P10B)**:
- R-CL.1/2: replay legacy WHERE (LOW column selection + HIGH pair-negative)
- R-CM.1/2/3: oracle_penalty (city, metric) isolation + cache invalidation + legacy JSON migration
- R-CN.1/2: MetricIdentity 9-seam allowlist Literal annotations + AST allowlist-scoped gate
- R-CO.1/2: FDR family_id metric-aware EXTEND + evaluator caller AST
- R-CP.1/2: v2 row-count sensor + discrepancy flag consumer (3 additional from S4 FDR side-effect unblocks)

**Regression evidence (2026-04-19)**:
- Full: 144 failed / 1905 passed / 93 skipped / 7 subtests
- Baseline at P10A (`81294d2`): 144/1892 verified by critic-dave (team-lead cited 142/1894 — off by 2; delta math holds)
- Delta: failed +0 / passed +13 / skipped 0 — exactly matches 13 P10B antibodies

**Hard constraints preserved**:
- No TIGGE import / no v2 table writes / no SQL DDL on v2
- No `_TRUTH_AUTHORITY_MAP` change / no Kelly strict migration / no Day0 LOW activation
- No B067/B074 touch (dropped from scope; stale bug claim / architect YELLOW)
- Golden Window intact

**critic-dave cycle-3 PASS-WITH-RESERVATIONS rationale**:
- Zero CRITICAL, zero MAJOR; 3 MINOR (contract citation stale, regression count off by 2, 6/9 seams lack runtime enforcement)
- Regression reproduction confirmed independently: 144/1905/93 on 3 runs
- No-regression delta +13 exact match to antibody count
- R-CP checkbox risk pre-framed as operator-read escape hatch (L24 carve-out: documented escape hatch is valid)

**critic rotation**: critic-dave retires after 3 cycles per rotation convention (P9C/P10A/P10B). critic-eve slot open for P10C or any future phase. L17-L26 inherited by critic-eve.

**Post-P10B forward-log** (explicit deferrals — all architect-gated or user-ruled):
- B055 (DT#6 architect packet)
- B099 (DT#1 architect packet)
- R10 Kelly strict ExecutionPrice migration (breaking; needs user ruling)
- R12 H7 144-failure triage (user ruling)
- R13 `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` semantic re-decision
- monitor_refresh LOW plumbing (surfaces under R5 if Type regression appears; xfail-with-ticket strategy)
- Pre-existing `temperature_metric: str` outside the 9-seam allowlist (P10C blanket migration)
- Full `MetricIdentity` wrapper at runtime seams (P10C — Literal is P10B stopgap per L26)
- Gate F (Day0 LOW limited activation) — requires Golden Window lift + live data stream
- R6 Gate C resolution (user ruling — doc-only vs data-migration)

**Evidence dir**: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/`
- `critic_dave_phase10b_wide_review.md` — cycle-3 retirement verdict + L22-L26

## Phase 10A closure

**Commit**: `81294d2` feat(phase10a): independent hygiene fix pack — R1 monitor rename + B071 + B091-lower + S5 doc flip. critic-dave cycle 2 ITERATE → re-verify PASS.

**Delivered (4 code items + 1 doc flip)**:
- **S1 R1 CRITICAL** — `src/engine/monitor_refresh.py:355,405` `remaining_member_maxes` → `extrema.maxes`. Two-line rename that eliminates the NameError silently swallowed at L614. HIGH+LOW Day0 refresh equally affected pre-fix. Sibling check: zero `\bremaining_member_maxes\b` hits in `src/`, `scripts/` post-fix.
- **S2 R2 antibody-only** — No code change. Scout confirmed probe PASS: `extract_tigge_mn2t6_localday_min.py` already stamps `temperature_metric='low'` at L20/L101/L356 with validation at L411. Antibody R-CI.1-3 locks the contract.
- **S3 B071** — `src/state/db.py:3308-3388` (`record_token_suppression`): mutable upsert replaced with append-only `token_suppression_history` table + derived `token_suppression_current` view (mirror of B070 pattern). `with conn:` wrapper on dual-write per critic-dave MINOR-1 ITERATE fix. `scripts/migrate_b071_token_suppression_to_history.py` idempotent migration script. 3-state `(auto→manual→auto)` sequence now reconstructible from history alone.
- **S4 B091-lower** — `src/engine/evaluator.py:1271-1286` fabrication site extended with `decision_time_status: Literal["OK", "FABRICATED_SELECTION_FAMILY", "UNAVAILABLE_UPSTREAM"]`. Reuses P9C `decision_time_status` vocab from `src/engine/replay.py:345,418` — no parallel vocabulary. Schema column `decision_time_status TEXT` on `selection_family_fact` with idempotent ALTER guard. ITERATE fix added R-CK.5/6 AST-walk wiring antibodies after dave found R-CK.1-4 were ORM-edge-only (called `_record_selection_family_facts` directly, not `evaluate_candidate` — L17 antibody-theater pattern).
- **S5 doc flip** — 14 bug rows flipped in `docs/to-do-list/zeus_bug100_reassessment_table.csv`. Spot-verified by critic-dave: B050→`057979c` (PASS), B078→`c327872` (PASS), B063→`94cc1f9` (PASS), B100 SAVEPOINT at `db.py:965-1018` (PASS within line-drift tolerance).

**Antibodies installed (21 total for P10A)**:
- R-CH.1/2/3: monitor_refresh extrema.maxes rename (positive + count + surgical-revert pair)
- R-CI.1/2/3: ingest metric stamp lock (INSERT captures `temperature_metric='low'` × 3 paths)
- R-CJ.1/2/3/4/5: token_suppression 3-state sequence + view + history + migration idempotency + caller coverage
- R-CK.1/2/3/4: evaluator decision_time_status ORM-edge tests (FABRICATED + OK + status-range + DB column)
- R-CK.5/6 (ITERATE-fix): AST-walk structural wiring antibodies (both-assignment-nodes + kwarg-thread to record call)

**Regression evidence (2026-04-19)**:
- Full: 142 failed / 1894 passed / 93 skipped / 7 subtests (at commit `81294d2`)
- Pre-P10A baseline 144/1873/93 → delta −2 failed / +21 passed
- +21 decomposes: 16 antibodies (R-CH.1-3 + R-CI.1-3 + R-CJ.1-5 + R-CK.1-4) + 2 R-CK.5/6 wiring + 3 S1 rename side-effect unblocks
- −2 failed: two flaky-race tests resolved by `with conn:` transaction wrap

**Hard constraints preserved**:
- No TIGGE import / no v2 table writes / no SQL DDL on v2
- No evaluator DT#7 gate change / no monitor_refresh LOW plumbing change
- No `_TRUTH_AUTHORITY_MAP` change / no `kelly_size` signature change
- No `except Exception` narrowing / Golden Window intact

**critic-dave cycle-2 ITERATE rationale**:
- MAJOR #1: R-CK antibodies were ORM-edge (called `_record_selection_family_facts` directly; surgical-revert of evaluator assignment PASSED). Fixed by R-CK.5/6 AST-walk + kwarg-thread antibodies. L17 documented.
- MINOR #1: `record_token_suppression` dual-write lacked `with conn:` wrapper. Fixed.
- MINOR #2: R-CH.2 `>=3` count check is adequate (count drops if semantic flip occurs). Acknowledged; no action.

**Durable learnings from cycle 2**:
- L17: ORM-edge tests lock the ORM edge, not the caller wiring. Antibody MUST call top layer OR AST-assert the full wiring chain.
- L18: UNCOMMITTED_AGENT_EDIT_LOSS — re-read from disk after any edit to staged files before regression run.
- L19: Dual-write without explicit `with conn:` is an unchecked caller-assumption (two-seam violation per Fitz Constraint #2).

**Evidence dir**: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/`
- `critic_dave_phase10a_wide_review.md` — cycle-2 review + ITERATE findings + re-verify PASS addendum

## Phase 7A closure

**Commits**: `a872e50` (impl) + `c496c36` (ITERATE fix).

**Delivered**:
- `scripts/rebuild_calibration_pairs_v2.py`: METRIC_SPECS iteration via new `rebuild_all_v2` driver; `_delete_canonical_v2_slice` + `_collect_pre_delete_count` metric-scoped; `_process_snapshot_v2` L298 write-time `metric_identity=spec.identity` (not hardcoded HIGH); `_fetch_verified_observation` read-side metric dispatch (`observed_value` alias); outer SAVEPOINT atomicity
- `scripts/refit_platt_v2.py`: `refit_all_v2` driver, METRIC_SPECS iteration, explicit `metric_identity` required
- `scripts/backfill_tigge_snapshot_p_raw_v2.py` NEW (351 LOC): metric-aware p_raw_json backfill, `assert_data_version_allowed` contract gate, dry-run safety pattern
- `src/state/schema/v2_schema.py`: 3 new columns (contract_version, boundary_min_value, unit); cross-pairing NOT NULL category-impossibility restored on 4 columns (observation_field / physical_quantity / fetch_time / model_version)
- `tests/test_phase7a_metric_cutover.py` NEW: R-BH..R-BO (17 tests) — 3 bug-class + 5 antibodies + 3 iteration antibodies

**Acceptance delivered** (user's master-plan criteria):
- bucket key / query / unique key 都带 metric ✓ (write + read + delete + count)
- high / low 可以同城同日共存 ✓ (per-metric scoping all paths)
- bin lookup 永不跨 metric union ✓ (category-impossibility at SQL seam + Python seam + function signatures)

**Structural antibodies installed**:
- `_fetch_verified_observation` column dispatch (CRITICAL-1 at read seam)
- Schema NOT-NULL on cross-pairing columns (MAJOR-1 at SQL seam)
- `assert_data_version_allowed` gate in backfill (MAJOR-2, belt-and-suspenders pattern inherited)
- R-BM: `_fetch_verified_observation(spec=LOW)` end-to-end SQL dispatch proven
- R-BN: schema INSERT without required columns → IntegrityError
- R-BO: backfill quarantined data_version → DataVersionQuarantinedError

**Critic-beth durable memory** (Gen-Verifier insight):
- P3.1 methodology extended with forward-facing vocabulary: `_requires_explicit_|_must_specify_|_no_default_` — caught new-contract antibodies beyond just stale-guard antibodies
- "Two-seam principle" learned: when fixing a write-side bug, ALWAYS audit the symmetric read-side. L0.0 self-correction surfaced CRITICAL-1 read-side only on second look
- Mirror-test detection heuristic: `try/except: pass` + positive assertion = structurally accidental green

**Regression**: 125 failed / 1805 passed / 90 skipped (+6 passed vs pre-P7A 125/1799 baseline; zero new failures).

**Forward-log (to P7B)**:
- MAJOR-3 (pre-existing from P5C): `test_R_AZ_2_low_rebuild_writes_only_low_rows` mirror test
- MINOR-1: contract_version / boundary_min_value schema columns undocumented
- MINOR-2: CalibrationMetricSpec + METRIC_SPECS should extract to `src/calibration/metric_specs.py`
- P6 carryover: remaining_member_maxes_for_day0 alias removal; _tigge_common.py extraction; script_manifest.yaml 5 scripts

## Phase 9C closure — Dual-Track Main-Line Scaffold Complete, Data Migration Pending

**Commits**: `114a0f5` feat(phase9c) → `d516e6b` fix(phase9c): ITERATE resolution. critic-dave cycle 1 (fresh spawn, general-purpose agent type per carol cycle-3 methodology fix, adversarial-opening per rotation convention) delivered ITERATE → team-lead fixed → re-verify PASS. (structural readiness; v2 data migration deferred to post-Golden-Window)

**Delivered (9 structural items + 4 ITERATE-fix items)**:

Core structural gaps closed:
- **S1 L3 CRITICAL** — `get_calibrator` metric-aware + new `load_platt_model_v2` reader. Pre-P9C state: calibration write side (save_platt_model_v2) was metric-aware since Phase 5 but the read side was metric-BLIND — LOW candidates would silently receive HIGH Platt models. This was THE structural blocker that made LOW deployment impossible regardless of what other pieces landed. 4 callers updated (monitor_refresh×2, replay, evaluator).
- **S2 A3** — `Day0LowNowcastSignal.p_vector(bins, n_mc, rng)` added; AST-parsed antibody R-CA.2 locks the non-import from day0_high_signal (R-BE invariant).
- **S3 A1** — `_forecast_rows_for` conditional historical_forecasts_v2 read + schema translation (v2 per-row metric-partitioned → legacy dual-column shape for downstream compat); B093 half-2 code-ready.
- **S4 A4** — DT#7 `boundary_ambiguous_refuses_signal` wired into evaluator candidate gate via new `_read_v2_snapshot_metadata` helper; pre-Golden-Window v2-empty → gate dormant; post-data-lift → gate fires on boundary-ambiguous rows.
- **S5 B1** — `--temperature-metric` CLI flag on `scripts/run_replay.py`.
- **S6 B3** — `save_portfolio(source="internal")` param + 2 callers tagged ("cycle_housekeeping" + "harvester_settlement"); DT#6 §B Interpretation B observability.
- **S7 C2** — R-BY.2 strengthen (Day0 without red marker runs normal logic; closes asymmetric-discrimination gap from critic-carol cycle-3 L15).
- **S8 L2** — dedicated `tests/test_phase9c_gate_f_prep.py` file (per critic-carol cycle-3 L2 convention correction).
- **S9 L1** — settlements_v2 external-only policy doc appended to `zeus_dual_track_architecture.md §4` (Zeus code does NOT contain internal settlements_v2 writer; live settlement truth arrives via Polymarket oracle + WU observations through existing legacy writers).

Write-side two-seam closure (critic-dave cycle-1 MAJOR-2 fix, commit d516e6b):
- **`_fit_from_pairs` metric-aware guard** — early-return None for non-HIGH; LOW on-the-fly refit CANNOT reach legacy metric-blind `save_platt_model`. Pre-fix: a LOW refit would pollute legacy `platt_models`; a HIGH v2-miss legacy fallback could silently read that LOW model AS HIGH. Classic two-seam violation (beth L1 pattern). 2 call sites in get_calibrator updated to pass metric.

Relationship antibody upgrade (critic-dave cycle-1 MAJOR-1 fix, commit d516e6b):
- **R-CC.3** — AST-walk structural antibody: inspects evaluate_candidate function body, requires `_read_v2_snapshot_metadata` + `boundary_ambiguous_refuses_signal` call sites + DT7 rejection reason string. Replaces the previous R-CC.2 checkbox antibody at the helper boundary; now the evaluator wire seam itself is guarded.
- **R-CG.1/2/3** — paired antibodies: LOW returns None + monkeypatched save_platt_model captures zero LOW calls + HIGH path unchanged.

**Antibodies installed (17 total for P9C)**:
- R-BZ.1/2/3: get_calibrator metric discrimination (positive / paired / backward-compat default)
- R-CA.1/2: Day0LowNowcastSignal.p_vector behavior + no-HIGH-import
- R-CB.1/2: _forecast_rows_for v2 + legacy fallback
- R-CC.1/2: _read_v2_snapshot_metadata helper boundary
- R-CC.3 (ITERATE-fix): AST-walk evaluator DT#7 wire structural
- R-CD.1: CLI flag
- R-CE.1/2: save_portfolio source tag
- R-BY.2 (strengthened): Day0 without marker runs normal logic
- R-CG.1/2/3 (ITERATE-fix): _fit_from_pairs LOW-skip paired (negative/surgical/positive)

**Regression evidence (2026-04-19)**:
- Full: 144 failed / 1873 passed / 93 skipped / 7 subtests
- Pre-P9C baseline 144/1856/93 → post-P9C (feat only) 144/1869/93 (+13) → post-ITERATE-fix 144/1873/93 (+4 from R-CC.3 + R-CG.1/2/3; zero new failures at each step)
- Deltas exact match to antibody counts at every commit

**Hard constraints preserved**:
- No TIGGE data import
- No v2 table writes (v2 remains zero-row per Golden Window)
- No SQL DDL
- No breaking kelly_size signature (B2 deferred)
- No _TRUTH_AUTHORITY_MAP value change (B4 deferred)
- No monitor_refresh logic changes (already metric-aware via position.temperature_metric)
- Golden Window intact

**critic-dave cycle-1 PASS rationale (re-verify)**:
- All probes passed: R-CC.3 surgical-revert fires correctly (structural); _fit_from_pairs guard is first statement in function body (load-bearing); regression math exact 144/1873/93; redundancy grep confirms only one legacy `save_platt_model` caller (now guarded); no secondary LOW→legacy leak paths
- Direct answer to "Is dual-track main line actually closed?": YES. READ side (L3 metric-aware) + WRITE side (`_fit_from_pairs` guard) + DT#7 evaluator wire all have structural antibodies.

**critic rotation status**: critic-dave cycle 1 complete. 2 cycles remaining before her rotation per 3-cycle convention. Next critic cycle opens at post-dual-track cleanup packet (if scoped) or any new non-dual-track phase.

**Methodology trend across 9 phases**:
- P6 (beth cycle 1): ITERATE on MAJOR-1 → fix → PASS
- P7A (beth cycle 2): ITERATE on 3 findings → fix → PASS
- P7B (beth cycle 3): first-try PASS
- P8 (carol cycle 1): first-try PASS
- P9A (carol cycle 2): first-try PASS
- P9B (carol cycle 3): ITERATE on CRITICAL-1 (streak broke at 3 exactly as predicted) → fix → PASS
- P9C (dave cycle 1): ITERATE on 2 MAJOR (adversarial opening found two-seam violation static review would miss) → fix → PASS

**Post-dual-track forward-log** (explicit deferrals, not gaps):
- B2 strict ExecutionPrice-only kelly_size migration (polymorphic preserved; 10+ bare-float callers still in repo)
- B4 `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` semantic re-decision
- DT#7 clauses 1+2 (leverage reduction + oracle penalty isolation) — require runtime boundary_ambiguous plumbing not yet present
- On-the-fly refit metric-aware write path (currently LOW returns None; future phase may route to save_platt_model_v2 if LOW pairs + v2 migration align)
- B093 full (legacy forecasts table deprecation) — requires v2 data population
- Day0 low limited activation (Gate F) — requires Golden Window lift + live data stream

## Phase 9B closure

**Commits**: `0974a62` feat(phase9b): risk-critical DT closure (DT#2 + DT#5 + DT#7) → `b73927c` fix(phase9b): ITERATE resolution — DT#2 actuator wiring (CRITICAL-1 resolved). critic-carol cycle 3 PASS on re-verify.

**Delivered**:
- **DT#2 R-BV + R-BY/R-BY.2**: `_execute_force_exit_sweep` in cycle_runner marks non-terminal positions with `exit_reason="red_force_exit"` + `Position.evaluate_exit` short-circuits to `ExitDecision(True, "RED_FORCE_EXIT", urgency="immediate")` when marker present + Day0 excluded. Closes zeus_current_architecture.md §17 "sweep active positions toward exit" law. Pre-P9B was entry-block-only (Phase 1 scope).
- **DT#5 R-BW**: `kelly_size` polymorphic `float | ExecutionPrice` with internal `assert_kelly_safe()` when typed. evaluator.py L187 upgraded to pass full ExecutionPrice object. Closes §20 law (INV-21). Bare-float backward compat preserved for unit tests + legacy replay.py:1300.
- **DT#7 R-BX**: `src/contracts/boundary_policy.py::boundary_ambiguous_refuses_signal(snapshot_dict)` — named contract function for clause 3 "refuse boundary-ambiguous as confirmatory signal". Evaluator wiring P9C (blocks on monitor_refresh LOW plumbing; function is orphan by design, forward-logged).
- **Stale-antibody flips** (per critic-beth cycle-3 P3.1 methodology): 2 pre-P9B antibodies flipped (`..._currently_bare_float_annotation` → `..._accepts_execution_price`; `..._force_exit_review_scope_is_entry_block_only` → assertion changed to `sweep_active_positions`).

**Antibodies installed (5/5 GREEN)**:
- R-BV sweep mechanism (function-level)
- R-BY sweep → evaluate_exit relationship (cross-module)
- R-BY.2 Day0 exclusion pair-negative
- R-BW kelly triple-case (float BC / non-compliant raise / compliant size)
- R-BX boundary_ambiguous_refuses_signal contract

**critic-carol 3-cycle PASS → ITERATE → PASS history**:
- **Cycle 1** (P8): first-try PASS, 4 MAJOR forward-log, 7 durable learnings
- **Cycle 2** (P9A): first-try PASS, 0 MAJOR, 2 MINOR fixed in close, 8 durable learnings (4 new)
- **Cycle 3** (P9B): **ITERATE** on CRITICAL-1 inert-marker (adversarial opening surfaced static-review blind spot), team-lead fixed option (a) at b73927c, re-verify PASS. 16 total durable learnings (L1-L16), retirement reflection.

**Methodology outcomes validated**:
- **3-streak complacency warning (cycle-2 L6)** → PROVEN: streak broke exactly at cycle 3, adversarial opening caught what thorough would miss
- **Runtime-probe methodology (cycle-3 L9)** → the decisive audit for marker-based refactors — grep + code-path analysis alone missed CRITICAL-1; running a position through evaluate_exit with a healthy context exposed inert marker pathology
- **Paired-antibody pattern (cycle-1 L7)** → R-BY + R-BY.2 demonstrated value again (positive + Day0 exclusion negative)
- **Relationship vs function antibody distinction (cycle-3 L9)** → R-BV alone was insufficient (function-level: marker written). R-BY completes the cross-module picture (sweep → evaluate → decision).

**P9B forward-log → P9C (critic-dave cycle 1 opens; onboarding brief at `phase9_evidence/critic_dave_onboarding_brief.md`)**:
- DT#7 full enforcement (evaluator wiring + leverage reduction + oracle penalty isolation)
- monitor_refresh.py LOW wiring
- Day0LowNowcastSignal.p_vector proper impl pre-Gate F
- B093 half-2 replay → historical_forecasts_v2 (blocks on Golden Window lift)
- --temperature-metric CLI flag on scripts/run_replay.py
- Second-seam data-closure tests for R-BP (forecast_low column selection)
- `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` re-audit
- `save_portfolio` `source` param for DT#6 B enforcement
- `tick_with_portfolio` persistence contract decision
- **(new from P9B cycle-3 re-verify L15 + P7)**: Broader inert-marker scan across `exit_reason` / `chain_state` / other marker fields — audit each for symmetric producer↔consumer pairs
- **(new from P9B cycle-3 re-verify L15)**: R-BY.2 antibody-strength note — current fixture has asymmetric discrimination (catches Day0→RED misrouting but not inverse Day0-fails-own-logic case). Not a fix defect; antibody-strengthening task for dave in P9C.
- Strict-ExecutionPrice-only kelly_size migration (remove bare-float BC path; upgrade replay.py:1300 + math tests)

**Critic rotation**: critic-carol retires after 3 cycles per rotation convention (P8/P9A/P9B). critic-dave opens P9C cycle 1. His onboarding brief inherits critic-beth 3-cycle learnings + critic-carol 3-cycle learnings + the "3-streak PASS prior not evidence" calibration note.

**Evidence dir**:
- `phase9_evidence/critic_carol_phase9b_wide_review.md` — initial adversarial-opening review with CRITICAL-1
- `phase9_evidence/critic_carol_phase9b_reverify.md` — re-verify PASS on b73927c
- `phase9_evidence/critic_carol_phase9b_learnings.md` — 16 L-numbered learnings + retirement reflection (L14 surgical-revert antibody, L15 antibody asymmetry observation, L16 runtime-probe-is-the-whole-value)
- `phase9_evidence/critic_dave_onboarding_brief.md` — 20-min reading order for dave + "prior not evidence" streak calibration

## Phase 9A closure

**Commit**: `7081634` feat(phase9a): P8 observability absorption + DT#6 Interpretation B clarified (plus close-fix micro-edits for critic-carol cycle-2 MINORs). critic-carol **cycle 2 first-try PASS**.

**Delivered (8 items, all GREEN first-cycle)**:
- S1 — `cycle_runner.py:281` widens elif tuple to include `RiskLevel.DATA_DEGRADED` → closes MAJOR-1 observability gap
- S2 — `cycle_runner.py:195` intentional-overwrite comment → closes MINOR-4
- S3 — `replay.py` mode+metric mismatch warning when `temperature_metric != "high"` with sweep/audit lanes → closes MINOR-2
- S4 — R-BQ.1 structural hardening: drops silent-return + replaces literal-text-match with "ANY RuntimeError = violation" → closes MAJOR-3 + MAJOR-4 (critic-carol's cycle-1 two text-match translation-loss concerns)
- S5 — `zeus_dual_track_architecture.md §6 DT#6` appends "Interpretation B" section: "read-only" ≠ "no cache refresh"; `PortfolioState.authority` runtime-derived; `tick_with_portfolio` ephemeral-advisory; three-signal redundancy (portfolio_degraded + entries_blocked_reason + risk_level); `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` flagged for periodic review → closes MAJOR-2
- S6 — R-BS.1/2 save_portfolio(degraded) roundtrip antibodies (R-BS.2 strengthened post-cycle-2 critique to actually assert truth_found, not just save_path.exists)
- S7 — R-BT entries_blocked_reason DATA_DEGRADED antibody (probed by surgical-revert: test correctly fails when fix is reverted)
- S8 — R-BU.1/2 mode+metric warning pair (paired negative/positive, per critic-carol cycle-1 L7 paired-antibody pattern)

**Acceptance delivered**:
- 9/9 P8+P9A antibodies GREEN (4 original + R-BS.1, R-BS.2 strengthened, R-BT, R-BU.1, R-BU.2)
- Targeted P5/P6/P7A/P7B/P8 suites unchanged-green
- Full regression 144/1851 (post-P8 baseline 144/1846; +5 exact match to new antibodies; zero new failures)
- Hard constraints preserved: no TIGGE import, no v2 writes, no DDL, no evaluator/monitor_refresh changes, Golden Window intact
- DT#2/#5/#7 (P9B) + Gate F (P9C) scope-bleed: zero

**critic-carol cycle-2 PASS rationale**:
- 0 CRITICAL / 0 MAJOR / 2 MINOR / 4 gaps (all P9B/P9C-scoped or intentional trade-offs)
- Pre-commitment predictions 2.5/5 hit — cycle-2 lower rate reflects that commit was specifically engineered to close her cycle-1 concerns (L5 learning: low hit-rate is signal commit is strong)
- Self-audit on cycle-1 findings: 4/4 MAJORs addressed with structural fixes (not patch stand-ins) per surgical-revert probes
- Empirical probe validation: R-BQ.1 hardening CONFIRMED structural (novel RuntimeError text correctly triggers test fail; reverting fix correctly breaks R-BT)
- 2 cycle-2 MINORs (R-BS.2 vacuous + DT#6 §B aspirational doc) fixed in-commit via close-patch: R-BS.2 now asserts `truth_found` unconditionally; DT#6 §B language softened "MAY...PROVIDED" → "MAY... SHOULD restrict..." + explicit "no runtime enforcement" + design-debt note

**Methodology trend — 3-pass streak noted**:
- P7B (critic-beth cycle 3) + P8 (critic-carol cycle 1) + P9A (critic-carol cycle 2) = 3 consecutive first-try PASSes
- critic-carol cycle-1 learnings explicitly warned about 3+ streak complacency risk
- cycle-2 learnings add 8 durable patterns (empirical-probe text-match skepticism, checkbox-antibody fingerprint, aspirational-doc translation-loss, self-audit bias, predictions-missing-is-signal, surgical-revert > baseline-restore)
- **Explicit recommendation to cycle 3 (critic-carol P9B)**: open in ADVERSARIAL mode for first 15 minutes to counter streak complacency before falling back to THOROUGH
- **Explicit recommendation for P9C rotation to critic-dave**: fresh spawn with cycle-beth + cycle-carol learnings inherited; brief must include note "3-pass streak from carol — prior not evidence"

**Evidence dir convention clarified** (cycle-2 artifact discovery):
- `phase7_evidence/` — critic-beth cycles 1-3 (P5fix, P5C, P7A, P7B)
- `phase8_evidence/` — critic-carol cycle 1 (P8)
- `phase9_evidence/` — critic-carol cycle 2+ (P9A, P9B) + critic-dave cycle 1+ (P9C)

## Phase 8 closure

**Commit**: `6ffefa4` feat(phase8): code-ready LOW shadow — run_replay metric threading + DT#6 rewire. critic-carol **first-try PASS** (cycle 1, fresh spawn replacing critic-beth after her 3-cycle run).

**Delivered** (Route A, code-only):
- S1 — `src/engine/replay.py:1932` `run_replay(temperature_metric: str = "high")` public kwarg; threaded to `_replay_one_settlement` at L2001 call site (which already accepted the kwarg since P5C but was never called with it)
- S2 — `src/engine/cycle_runner.py:180-181` removes `raise RuntimeError(...)` on `portfolio_loader_degraded=True`; replaces with `logger.warning` + `summary["portfolio_degraded"]=True` + `risk_level = tick_with_portfolio(portfolio)` + summary refresh. DT#6 law (`zeus_dual_track_architecture.md §6`): process MUST NOT raise; downstream entry gates honor `risk_level != GREEN`
- `tests/test_phase8_shadow_code.py` NEW — R-BP.1/2 (run_replay metric threading both directions) + R-BQ.1/2 (DT#6 no-raise + tick invocation). 4/4 GREEN first-pass

**Acceptance delivered**:
- Hard constraints: all 7 honored (no v2 writes, no DDL, no TIGGE, no evaluator/monitor_refresh/settlement-writer changes, kwarg-only signature change, no `raise RuntimeError` reintroduction, Golden Window preserved)
- Regression: targeted 20 failed / 231 passed (pre-P8 baseline 20/227, delta = +4 from R-BP/R-BQ); full 144/1846/95/7 (pre-P8 baseline 144/1842, zero new failures)
- critic-carol pre-commitment predictions: 5/5 hit

**Structural antibodies installed**:
- R-BP.1 `test_run_replay_threads_temperature_metric_low_to_replay_one_settlement` — semantic capture of metric threading via monkeypatched target
- R-BP.2 `test_run_replay_default_temperature_metric_is_high_backward_compat` — every pre-P8 caller preserved
- R-BQ.1 `test_run_cycle_degraded_portfolio_does_not_raise_runtime_error` — DT#6 law: no raise on degraded portfolio
- R-BQ.2 `test_run_cycle_degraded_portfolio_calls_tick_with_portfolio` — positive-side confirmation: replacement mechanism fires exactly once with the degraded PortfolioState

**critic-carol PASS rationale**:
- 0 CRITICAL / 4 MAJOR / 4 MINOR / 6 test-gap
- All 4 MAJORs are observability/antibody-hygiene concerns (deferrable to P9, not P8 blockers)
- Fitz Four-Constraints scoring: structural decisions PASS (2 decisions clean delivery); translation loss ~65-75% antibody immunity; immune system partial (observability gap); data provenance N/A direct
- Methodology trend: **P7B + P8 = 2 consecutive first-try PASSes** under Gen-Verifier; pattern converging on contract-first + pre-commitment + pair-based antibody design

**P8 forward-log → P9 (from critic-carol's review)**:
1. MAJOR-1: Code `entries_blocked_reason = "risk_level=DATA_DEGRADED"` or `"portfolio_loader_degraded"` branch in `cycle_runner.py:281` (currently DATA_DEGRADED falls through to `None`)
2. MAJOR-2: Decide persistence contract for `riskguard.tick_with_portfolio` — either persist to `risk_state.db` to align `status_summary.json` readers, OR document as ephemeral advisory
3. MAJOR-3: Harden R-BQ.1 to drop silent-return path at test_phase8_shadow_code.py:236 (after a downstream RuntimeError, the assertion at L239 is skipped)
4. MAJOR-4: Replace R-BQ.1 text-match antibody (literal pre-P8 string `"Portfolio loader degraded: DB not authoritative"`) with structural "no RuntimeError escapes branch" assertion
5. MINOR: `scripts/run_replay.py` CLI expose `--temperature-metric` flag
6. MINOR: Warn when `temperature_metric != "high"` combined with `wu_settlement_sweep` / `trade_history_audit` modes (silently dropped today)
7. MINOR: Fix duplicate `4.` numbering in this handoff (done in this close commit)
8. MINOR: Add comment at `cycle_runner.py:195` that `summary["risk_level"]` overwrite is intentional
9. TEST-GAP: End-to-end test that `_forecast_reference_for(metric="low")` actually selects `forecast_low` column (second-seam test, blocks on data presence)
10. TEST-GAP: `status_summary.json` reflects DATA_DEGRADED during degraded cycle
11. TEST-GAP: `entries_blocked_reason` output for degraded path
12. TEST-GAP: No rollback path if `tick_with_portfolio` itself raises; no try/except around DT#6 branch
13. TEST-GAP: `portfolio_dirty`/`tracker_dirty` stays False in degraded mode (interpretation A/B ambiguity — contract §L26-27 unclear whether "read-only" forbids `save_portfolio` JSON-refresh)
14. TEST-GAP: `risk_state.db` unchanged after DT#6 branch

**Ambiguity for user ruling**:
- Contract interpretation question: "monitor/exit/reconciliation continue read-only" — does this forbid `save_portfolio(degraded_portfolio)` JSON refresh (Interpretation A) or permit it (Interpretation B)? Under A, current code at cycle_runner L333 violates contract; under B, current code is correct.

**critic rotation note** (new methodology crystallized during P8):
- critic-beth retired after 3 cycles to prevent over-familiarity
- critic-carol (cycle 1) inherited disk-durable learnings; fresh pre-commitment predictions hit 5/5 but identified an ORTHOGONAL finding class (observability drift) — rotation was valuable
- Suggest next rotation at end of P9 (critic-carol → critic-dave). Three-cycles-per-critic keeps compounding learnings while preventing blind spots.

## Phase 7B closure

**Commit**: `6fc41ec` feat(phase7b): naming hygiene (5/6). critic-beth PASS first-try (cycle 3, sniper Gen-Verifier 3-for-3).

**Delivered (5/6)**:
- Item 1: NEW `src/calibration/metric_specs.py` — CalibrationMetricSpec + METRIC_SPECS central home; 3 scripts (rebuild/refit/backfill) import from here; cross-script import via `scripts.rebuild_calibration_pairs_v2` eliminated
- Item 3: `remaining_member_maxes_for_day0` shim deleted; 9 callers migrated (including 5 silently-broken monkeypatch sites from pre-P7B — they were patching a non-existent attribute post-P6 rename; now patching the real `remaining_member_extrema_for_day0` with correct `RemainingMemberExtrema` dataclass return)
- Item 4: 5 scripts registered in `architecture/script_manifest.yaml` (extract_tigge_mx2t6/mn2t6, rebuild_calibration_pairs_v2, refit_platt_v2, backfill_tigge_snapshot_p_raw_v2)
- Item 5: `contract_version` + `boundary_min_value` ADD COLUMN dropped from v2_schema (no live consumer); `unit` retained (backfill uses)
- Item 6: `test_R_AZ_2_low_rebuild_writes_only_low_rows` rewritten as real invariant check via `_fetch_eligible_snapshots_v2` (was try/except:pass mirror test)

**Deferred (1/6)**:
- Item 2: `_tigge_common.py` extraction. Planner's "15 safe mechanical helpers" was overstated — `_output_path` depends on `OUTPUT_FILENAME_PREFIX`/`OUTPUT_SUBDIR`, `_find_region_pairs` depends on `PARAM`, all of which DIFFER per extractor. Safe subset = 9 or 13 helpers + shared constants + `CityManifestDriftError` class. Deserves own focused commit (P7B-followup) before P8 opens.

**Acceptance**:
- 14 files +167/-76
- Full regression: 125 failed / 1805 passed / 90 skipped (flat vs pre-P7B baseline; zero new failures)
- P5/P6/P7A targeted tests unaffected
- critic-beth PASS first-try (first phase this happens in sniper Gen-Verifier era)

**Forward-log (from critic's P7B review — 4 MINOR + 1 deferred)**:
1. P7B-followup: Item 2 (_tigge_common 9-or-13-safe-helpers extract). Suggested owner: team-lead. Suggested ETA: before P8 opens.
2. P7B-followup or P8 hygiene: add `extract_` to `architecture/naming_conventions.yaml` `allowed_prefixes` OR add explicit exceptions for `extract_tigge_mx2t6_localday_max.py` + `extract_tigge_mn2t6_localday_min.py` + `refit_platt_v2.py` (3 new `script_long_lived_bad_name` errors surfaced post-manifest-registration).
3. P8 test-debt: `test_runtime_guards::test_day0_no_remaining_forecast_hours_is_pre_vector_traceable` now fails at a DEEPER assertion (SIGNAL_QUALITY vs MARKET_FILTER) — real latent bug exposed by P7B monkeypatch repair. Trace Day0 rejection ordering.
4. Optional cosmetic: rename R-AZ-2 test from "writes only low rows" to "low eligibility excludes high rows" (tests the eligibility seam per new impl).

**Structural antibodies installed**:
- Central `src/calibration/metric_specs.py` (INV: one source of truth for METRIC_SPECS iteration)
- Zero-cross-script-import in `scripts/` (architectural discipline restored)
- Alias-removal enforced by grep (INV: `remaining_member_maxes_for_day0` symbol nonexistent outside comments)
- 5 new scripts audit-registered (dangerous_if_run + target_db + apply_flag metadata)
- Schema category-impossibility preserved at SQL seam (4 NOT NULL columns, no silent DEFAULTs)
- R-AZ-2 no longer a mirror test (try/except:pass + stale kwarg antipattern eliminated)

**Gen-Verifier cycle stats**:
- P6: 1 ITERATE + 1 re-review (2 cycles)
- P7A: 1 ITERATE (3 findings) + 1 re-review (2 cycles)
- P7B: 1 PASS on first review (1 cycle)
- Trend: critic-accumulating-memory + team-lead-discipline → fewer cycles per phase

## Phase 6 closure

**Commit**: `e3a4700` (misnamed "docs(phase6): microplan" — content is full impl; amend pending user force-push ruling).

**Delivered**:
- 3 new signal classes: `Day0HighSignal`, `Day0LowNowcastSignal`, `Day0Router`
- `RemainingMemberExtrema` dataclass with `for_metric` constructor + both-None guard
- `day0_window.py`: renamed `remaining_member_maxes_for_day0` → `remaining_member_extrema_for_day0` (legacy alias kept for backward-compat, removal is P7 chore)
- `evaluator.py:784` + `monitor_refresh.py:294` callsites migrated to `Day0Router.route(Day0SignalInputs(...))`
- `day0_signal.py:85-91` LOW NotImplementedError guard REMOVED
- `riskguard.py`: `tick_with_portfolio` DT#6 + B055 absorption path
- `portfolio.py`: existing degraded-path coverage confirmed correct (no new code needed)
- `tests/test_phase6_day0_split.py`: 19 tests, R-BA..R-BG, all GREEN

**Acceptance**:
- R-BA..R-BG: 19/19 GREEN
- Full regression: 138 failed / 1801 passed (flat vs P5 baseline 137/1783; net +18 passed via dead-guard unblock, no new failures)
- `grep NotImplementedError src/signal/day0_signal.py` → zero hits
- AST walk on `day0_low_nowcast_signal.py` → only `__future__` + `numpy` imports (R-BE clean)
- critic-beth wide-review PASS

**Structural antibodies installed**:
- `RemainingMemberExtrema.__post_init__` — both-None raises → MAX/MIN alias unconstructable (Fitz P4 category-impossibility)
- `Day0Router.route` — causality-guard at construction; LOW + `N/A_CAUSAL_*` routes through nowcast, not historical Platt
- `Day0LowNowcastSignal` — does not import `day0_high_signal` (R-BE AST invariant)
- `riskguard.tick_with_portfolio` — DT#6 single-degraded-state transition (not two paths for authority-loss + B055-staleness)

**Coordination-error learning**:
Team-lead accidentally `git add` + `git commit` captured exec-kai's parallel-staged WIP into a commit intended only for the microplan doc. Root cause: git index is shared mutable state between team-lead and exec; team-lead assumed private. Lesson logged for operating contract amendment:

> **P1.1 (to be added)**: Before `git add`, team-lead runs `git status --short`. Any unexpected staged or modified files in index are isolated via `git stash -u` or `git reset HEAD -- <file>` before intentional stage. Shared git index requires explicit coordination, not assumption.

> **P2.1 (to be added)**: Exec "ready for commit" announcement precedes any `git add`. Team-lead owns the commit boundary; exec hands over a staged-file list + diff in SendMessage, team-lead verifies then stages + commits. No parallel staging.

## Phase 6 scope

### Primary deliverable: Day0Signal split

- **New module** `src/signal/day0_high_signal.py` — extract `Day0Signal` (current monolith at `src/signal/day0_signal.py:L80-91` raises `NotImplementedError` for LOW metric).
- **New module** `src/signal/day0_low_nowcast_signal.py` — low-track nowcast path built on `low_so_far`, `current_temp`, `hours_remaining`, remaining forecast hours. NOT a historical Platt lookup.
- **New module** `src/signal/day0_router.py` — routes by `(metric, causality_status)`:
  - HIGH → `Day0HighSignal`
  - LOW + causal OK → `Day0LowNowcastSignal` (historical path forbidden)
  - LOW + causal `N/A_CAUSAL_DAY_ALREADY_STARTED` → nowcast path (not historical Platt)

### CRITICAL co-landing imperative (TWO files, per scout-gary)

**BOTH `src/engine/evaluator.py:825` AND `src/monitor_refresh.py:306` MUST be fixed in the same commit that removes `Day0Signal.__init__` NotImplementedError guard for LOW.** Current state: both sites pass MAX array as MIN input (`remaining_member_extrema` → `member_mins_remaining` per exec-juan's detail). Today DEAD because guard raises NotImplementedError for LOW at `day0_signal.py:L85-92`. When guard removes, silent corruption lights up. Decoupling these fixes (or fixing only one of the two) is the primary structural risk for Phase 6. scout-finn's original 5B learnings only flagged evaluator; scout-gary's P5→P6 learnings added monitor_refresh. Both must be in the co-landing commit.

### DT#6 graceful-degradation law

- `load_portfolio()` authority-loss path must NOT kill entire cycle with RuntimeError.
- Legal behavior: disable new-entry paths; keep monitor / exit / reconciliation running read-only; surface degraded state to operator.
- Integrates with `PortfolioState.authority` field landed 5A (`977d9ae`).
- Absorbs B055 (riskguard trailing-loss 2h staleness).

### Out-of-scope for Phase 6

- Day0 low-track live trading activation — Phase 9 (Gate F).
- `run_replay` public-entry metric threading — Phase 8 (LOW shadow).
- `rebuild_v2` full METRIC_SPECS iteration + R-AZ-1/2 un-xfail — Phase 7.
- `_tigge_common.py` extraction — Phase 7.

## Team `zeus-dual-upgrade-v3` — retained through P5

| Role | Name | Model | Status |
|---|---|---|---|
| critic | critic-beth | opus | retained; L0.0 discipline proven across fix-pack + 5C |
| scout | scout-gary | sonnet | retained; landing-zone + latent-issue discipline proven |
| testeng | testeng-hank | sonnet | retained; one scope-override process-note logged, no discipline finding |
| executor | exec-ida | sonnet | retained; fix-pack owner + 5C CRITICAL-1 cleanup; one out-of-scope Python 3.14 fix with note |
| executor | exec-juan | sonnet | retained; 5C replay owner; schema validation discipline exemplary |

### Process notes from P5 (for Phase 6 brief reference)

- **Scope-ruling authority** = team-lead only. testeng + critic can recommend/push back via a2a; executors must escalate conflicting rulings, not act on peer a2a that contradicts team-lead.
- **Out-of-scope incidental fixes**: executor should ASK team-lead first (Option A) OR proceed with LOUD flag requesting ruling (Option B). exec-ida's `main.py:330` fix was Option B quality (transparent after) but should have been Option A (ask first).
- **Multi-agent disk settling delay**: default hypothesis when disk disagrees with teammate report is concurrent-write or memory lag, NOT discipline breach. Verified 4+ times across P5.

## Durable structural antibodies installed (P5)

- `PortfolioState.authority: Literal[...]` (5A) — fail-closed default.
- `ModeMismatchError` + `mode=None` strict rejection (5A + fix-pack).
- `MetricIdentity` view layer row + top-level emission (5A).
- `validate_snapshot_contract` inline 3-law gate at ingest writer path (5B).
- `CalibrationMetricSpec` + `METRIC_SPECS` pattern for metric-aware rebuild/refit (5B).
- `assert_data_version_allowed` positive-allowlist (fix-pack, converting from quarantine-block).
- `_process_snapshot_v2(spec=...)` per-spec cross-check (fix-pack).
- `_LOW_LANE_FILES` frozenset explicit check vs substring (fix-pack).
- `observation_client._require_wu_api_key()` lazy callsite guard (fix-pack) — unblocked SystemExit-poisoned test collection.
- `R-AM.4` AST-walk assertion: ingest MUST NOT import from `scripts.scan_*` (5B scanner isolation).
- Replay typed-status fields (5C B093 half-1).
- `_decision_ref_cache` metric-aware key tuple (5C).
- `save_platt_model_v2::model_key` per-metric scope (R-AZ-3 GREEN, Gate D core).

## R-letter namespace ledger

Locked:
- R-A..R-P: Phases 1-4
- R-Q..R-U: Phase 4.5
- R-AA: Phase 4.6
- R-AB..R-AE: Phase 5A (locked at `977d9ae`)
- R-AF..R-AO: Phase 5B (locked at `c327872`)
- R-AP..R-AU: Phase 5B-fix-pack (locked at `3f42842`)
- R-AV..R-AZ: Phase 5C (locked at `821959e` / `59e271c` / `ecf50bd`). **R-AZ-1/2 GREEN on merit** via `rebuild_v2(spec=...)` landed in `ecf50bd`; xfail markers removed. No Phase 7 un-xfail work remains.

Reserved:
- R-V..R-Z: future Phase 6/7/9 drafts per emma final_dump rename (pre-P5 ruling)

Available for Phase 6:
- R-BA onwards (new range), plus R-V..R-Z reserved slots if those plans don't re-emerge.

## Regression state post-P5

- 5A tests: 21/21 GREEN
- 5B tests: 41/41 GREEN
- Fix-pack tests: 14/14 GREEN
- 5C tests: 12/12 GREEN (R-AZ-1/2 un-xfailed after exec-ida's post-5C `rebuild_v2(spec=...)` landing)
- **Total P5-specific: 88/88 GREEN** (Gate D fully covered)
- Full regression: 137 failed / 1749 passed / 94 skipped (post-59e271c). Note: 137 failed baseline post-unblock; majority are pre-existing hidden failures exposed by R-AT lazy-import.

## Forward-log (Phase 6+)

1. `evaluator.py:825` MAX→MIN fix CO-LANDING with Day0Signal guard removal (Phase 6). Silent-corruption risk if decoupled.
2. `rebuild_v2` spec param + METRIC_SPECS iteration (Phase 7) → un-xfail R-AZ-1/2.
3. `run_replay` public-entry (L1933 + L2002) metric threading (Phase 8 / shadow activation).
4. Replay migration `forecasts` → `historical_forecasts_v2` (Phase 7 B093 half-2) → enables SQL metric filter.
5. `_tigge_common.py` shared-helper extraction (Phase 7) → 12 duplicated helpers across mx2t6 + mn2t6 extractors.
6. INV-21 / INV-22 zero coverage (Phase 9 risk-layer packet): DT#5 Kelly executable-price + DT#3 FDR family canonicalization.
7. Phase 2-4 test-file provenance header retrofit (chore commit).
8. 2 hardcoded absolute paths in Zeus core scripts (chore commit).
9. Triage pass on 137 pre-existing test failures (separate, post Phase 6).

## Zero-Data Golden Window (STANDING)

Still active. v2 tables zero rows. No real ingest/batch extraction. Smoke ≤1 GRIB → `/tmp/`. Structural fixes free. User lifts when Phase 8 shadow opens.

## Paper mode retired (STANDING antibody)

`ACTIVE_MODES=("live",)` in `src/config.py`. Antibody msg in `mode_state_path`. **DO NOT re-add paper mode.**

## Phase 6 opening brief sketch (for team upon re-engagement)

The fresh engagement of the retained team for Phase 6 uses short briefs pointing at:
- This handoff doc §"Phase 6 scope"
- 10 learnings docs (5 from zeus-dual-track retirement, 5 from zeus-dual-upgrade-v3 pre-compact)
- `docs/authority/zeus_dual_track_architecture.md` §5 (Day0 causality law) + §6 (DT#1-7)
- Current `src/signal/day0_signal.py` state
- `evaluator.py:825` co-landing imperative

Role assignments (proposed, team-lead ruling on re-engagement):
- exec-ida: DT#6 graceful-degradation + PortfolioState.authority integration (extends her 5A seam ownership)
- exec-juan: Day0 split + evaluator.py:825 co-landing (extends his 5C replay/runtime ownership)
- testeng-hank: R-letter drafting for Day0 split + DT#6 + B055
- critic-beth: wide review + L0.0 discipline
- scout-gary: Day0 landing-zone scan pre-implementation

## Team bootstrap (if team missing post-compact)

If `~/.claude/teams/zeus-dual-upgrade-v3/` is gone (OMC patch failed):
- Team name: retain `zeus-dual-upgrade-v3` (the persistent name covers P5→P9)
- Spawn same 5 roles with names from retained team (continuity) OR fresh names (full restart)
- Each brief mandates reading: methodology + root AGENTS + dual-track architecture + THIS handoff + 10 learnings docs + relevant critic verdict docs
- Gate start-of-work on explicit "reads complete" ack

## Standing do-nots (post-compact reminders)

- Don't trust teammate claims without single-quick disk-verify on critical path.
- Don't over-verify — single check, not spiral. Multi-agent disk settling is real.
- Don't re-add paper mode.
- Don't push full-batch extraction without user approval.
- Don't decouple `evaluator.py:825` fix from Phase 6 guard removal.
- Don't bundle Phase 6 with Phase 7 scope (keep commits atomic).
- Don't let testeng override team-lead scope rulings via a2a.
- Don't spend >2x the expected token budget on a single sub-phase without escalating to user.

## OMC session-end hook

Still patched. `OMC_ENABLE_SESSION_END=1` restores original. Re-apply if `omc update` runs.

## Status files on disk

- This file (authoritative post-P5 handoff).
- P5 evidence: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase5_evidence/` — 3 onboarding docs + 3 wide-review docs + 1 verdict doc + 1 landing-zone scan + 1 DT-v2 triage + 10 learnings docs + Phase 5A/5B/5C critic verdicts.
- Coordination handoff: `docs/to-do-list/zeus_dt_coordination_handoff.md` (B069/B073/B077/B078 ✅; B093 ✅ half-1 5C, half-2 Phase 7; B091 ✅; B055 ⏳ Phase 6; B099 ⏳ DT#1 architect packet; B063/B070/B071/B100 ⏳ Section A bugs).
- Methodology (global): `~/.claude/agent-team-methodology.md`.
- Global rules: `~/.claude/CLAUDE.md`.

Phase 5 fully closed. Team retained. Compact-ready. Phase 6 opens post-compact with same team.
