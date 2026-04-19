# Team-Lead Handoff (post-P7A, 2026-04-18)

**Written**: 2026-04-18 post Phase 5 + 6 + 7A complete (Gen-Verifier mode). Team infrastructure RETIRED — `TeamDelete` applied. New composition: team-lead (Opus, main context) + critic-beth (Opus, persistent via disk-durable learnings + fresh spawn per phase) + ephemeral subagents (Sonnet/Haiku per task shape). Supersedes all earlier handoffs.

## IMMEDIATE NEXT ACTIONS (post-compact, in order)

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

Branch: `data-improve`. Top of `origin/data-improve` = `e3a4700`. All pushed.

```
e3a4700 docs(phase6): microplan — Strategy A internal M1-M4 milestones
        ^ ACTUAL CONTENT: full Phase 6 implementation (Day0 split + DT#6 + B055
          absorption). Commit-message mislabeling via team-lead git coordination
          error; content reviewed + PASS by critic-beth.
          User-deferred commit-msg amend (force-push decision).
df1cc71 docs(phase6): contract — Day0 split + two-file co-landing
c001dda docs(operating-contract): P5 structural learnings → Phase 6 protocol
5e5fbf6 docs(phase5-close): handoff text consistency
ecf50bd docs(phase5-close): rebuild_v2 spec kwarg + R-AZ un-xfail + learnings
59e271c fix(phase5C): remove SQL metric filter from legacy forecasts query
821959e Phase 5C: replay MetricIdentity + Gate D core antibody + B093 half-1
3f42842 fix(phase5B-pack): 7 cross-team findings + R-AP..R-AU antibodies
c327872 Phase 5B: low historical lane + ingest contract gate + B078 absorbed
977d9ae Phase 5A: truth-authority spine + MetricIdentity view layer
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
8. **Phase 9C** — Gate F activation prep: `Day0LowNowcastSignal.p_vector` proper implementation (currently lazy HIGH delegate — unacceptable for live LOW), `monitor_refresh.py` LOW wiring (non-Day0), B093 half-2 (`_forecast_rows_for` → `historical_forecasts_v2`; blocks on Golden Window lift), `--temperature-metric` CLI flag, second-seam data-closure tests. critic-dave cycle 1 (fresh spawn) with critic-carol learnings inherited.

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
