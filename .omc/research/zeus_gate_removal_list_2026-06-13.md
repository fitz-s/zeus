# ═══ GATE-MASS COLLAPSE — RESUMABLE PLAN OF RECORD ═══
# Created: 2026-06-13 | Multi-session. A fresh session reads THIS to continue without re-deriving.
#
# SOURCE DOCS (the complete refactor context):
#  - .omc/research/tangle_simplify_deepmap_2026-06-13.md  (6-lens map: K=6 roots, 5 floors, ambient tax, consensus scorecard)
#  - .omc/research/zeus_remediation_plan_FINAL_2026-06-13.md  (tiered remediation plan, post-critique)
#  - docs/evidence/t0_live_problem_report_2026-06-13.md  (live T0 risks: CHECK gun, WAL, partition)
#  - THIS doc (gate-mass classification + safe-removal list, below)
#
# OPERATOR AUTHORIZATION (already given):
#  - 2026-06-09 (config/settings.json _edli_live_scope_note L76): "全部打开，这些shadow only的策略一辈子都不会
#    主动打开，把这些gate都删了" = turn all on; shadow-only strategies will NEVER be turned on; DELETE these gates.
#  - 2026-06-13: operator removed the FABRICATED "flags flip only on operator word" law (memory:
#    flags-no-blanket-operator-word-law). Shadow/dead/redundant flags are REMOVABLE in refactor without per-flag
#    operator word. Only real constraint = ARM condition (verified-correct before live).
#
# RULES: relationship-test (byte-identity / zero-emitter antibody) BEFORE each removal. Re-probe EACH item against
#  live code before touching it (§6 proved this workflow had 4 wrong reads). NEVER touch the honest-K keep-set (§1).
#  NO new gates/flags/artifacts — removal only. Hold Tier C/D/E + all §6 traps for explicit per-item operator OK.
#
# ── EXECUTION PROGRESS ──
#  [DONE] WAVE 1 (A1-A4): 4 dead config flags removed (no_trade_regret_enabled, reports_enabled,
#         forecast_complete_live_enabled, reactor_prune_enabled — 0 live readers); pinning test-asserts deleted;
#         settings.json valid; 48 tests green. WORKING TREE ONLY (live on next daemon restart). NOT committed.
#  [LEARNED] Enum-member removal (A5-A16) is NOT a quick delete: NoTradeReason/RejectionReason are StrEnum but the
#         no_trade_events CHECK constraint is GENERATED from them, so removing a member changes the
#         scripts/check_schema_fingerprint pin AND would create code-vs-live CHECK drift unless the live CHECK is
#         migrated too. Tried 2 members + REVERTED (fingerprint OK restored, 116 members). Reclassified to the
#         schema-change wave below. Do NOT remove members alone.
#  [DONE] WAVE 2B-exit (operator: delete all 7 Tier-B): exit triple DELETED — exit_policy_shadow/exit_policy/exit_belief/
#         exit_calibration_alarm.py (4 modules ~47KB) + cycle_runtime shadow block (3670-3716) + 3 config keys + _note +
#         2 dedicated tests. Byte-identical (shadow was telemetry-only; live portfolio.evaluate_exit untouched). schema
#         fingerprint OK. NOTE: tests/test_exit_lifecycle_chain_truth_void.py (4) + test_lifecycle flash_crash (1) FAIL on
#         clean HEAD too = PRE-EXISTING, not caused by this change (verified via git-stash compare).
#  [DONE+VERIFIED] WAVE 2C: 4 ERA q-shadow flags (neff_width/james_stein/horse_race/selection_eb) REMOVED — all 4 config
#         flags + accessors gone; james_stein_blend.py + horse_race_kelly.py deleted; selection_eb EB-computation RETAINED
#         (call site hardcodes authority_on=False; BH/FDR is the unconditional live gate; SELECTION_EB_UNLICENSED absent from
#         src/). Workflow re-probe wf_6951a113 (2026-06-14): zero dangling live refs, imports clean, 1625 tests collect,
#         scoped money-path 9-pass/1-skip, schema fingerprint OK. WORKING TREE ONLY — NOT committed.
#  [GATED-REPROBE] A17-A19 RECLASSIFIED 2026-06-14 — NOT free dead code (re-probe corrected the plan's earlier read):
#         A17/A18 (evaluator.py:4337-4347 / 4400-4411) are the dead elif branches of full_transport_live_enabled, BUT that
#         flag is a STAGED feature with a documented promote checklist (config _note: promote_model_bias_ens_v2.py), not a
#         never-ship shadow. Delete-only-the-elif orphans the resolver call + silently breaks the promote path (half-baked);
#         delete-whole-FT-wiring is a 'FT-never-promotes' decision = operator territory (same class as the EMOS-ladder §6 KEEP).
#         A19 (6358-6361) is NOT byte-identical: appends oracle_penalty_observed_Nx to applied_validations, persisted to
#         trade_decisions.applied_validations_json (no code consumer, but operator-SQL telemetry). ALL THREE need operator OK.
#  [GATED-SCHEMA] Enum/reason-member trim (A5-A16; SHOULDER family needs scaffold-deadness verify first): coordinate
#         enum edit + no_trade_events CHECK migration + fingerprint re-pin in ONE wave (the T0.1 forecast_posteriors pattern).
#  [DONE+VERIFIED] WAVE C/D (operator authorized "all" 2026-06-14, proof-workflow wf_a216fb16 per-item first):
#         C1+C2 (events/reactor.py redundant lcb-consistency + capital-efficiency re-checks — byte-identical, upstream
#         candidate_evaluation gates already enforce; buy_no Shanghai stanza preserved). D1+D2 (is_reentry_blocked 20-min +
#         is_token_on_cooldown 1-hr time-bans deleted from portfolio.py + evaluator consumers; enum members REENTRY_BLOCKED/
#         TOKEN_COOLDOWN KEPT to avoid schema-CHECK drift; tests repointed; antibody guards added). D3 (source-quality km
#         haircut), D4 (DDD Rail-2 km discount — Rail-1 HALT kept), D5 (global-heat risk_throttle — only the heat branch;
#         gross/variance branches were NOT redundant, plan WRONG_READ corrected). SIZING IMPACT (operator no-caps law,
#         intended): partial-run +~2x (D3), DISCOUNT +0-10% (D4), heat>0.25 +~2x (D5); can compound. Verified: zero net-new
#         test failures vs HEAD (4 pre-existing confirmed via stash), schema fingerprint unchanged, money-path green. Goes
#         live on daemon restart (operator-gated). Tier E (shoulder $2000 cap split) + soft-anchor 5→1 still pending.
#  [DONE+VERIFIED] CLEANUP BATCH (operator-authorized 2026-06-14, FT-depth investigated first): A19 oracle-telemetry no-op
#         (e52168e9), tail-shape SHADOW dead log-branch (5eddd8a9), shoulder cluster-cap Gate-2 $2000 notional cap SPLIT —
#         Gate-1 correlation kept (013b0831), and FT full_transport whole-wiring removal — evaluator+monitor_refresh+flag+2
#         test files (06e28761). FT verdict was REMOVE not hold: full_transport_v1 = 0 DB rows, superseded by live
#         edli_per_city_v1, U1-RETIRE, promote script doesn't exist. All byte-identical/direction-neutral; zero net-new test
#         failures, schema fingerprint unchanged. NOT committed to remote.
#  [SUPERSEDED] This plan's per-item classifications were re-investigated at FT-depth 2026-06-14 (workflows wh3gjgo1j +
#         worfmboc9) — several plan reads were WRONG (soft-anchor NOT byte-collapsible; crosscheck undercounted; §6 B7/B8
#         wrong file/line; A3 boot-guard already deleted). The investigation outputs are the authority now, not this doc.
#  [INVESTIGATED-PENDING-DECISION] Remaining: (a) schema-enum wave — 8 dead members need a destructive no_trade_events CHECK
#         rebuild + fingerprint re-pin (operator chose 'investigate migration first'); (b) rulings held for deeper probe —
#         shadow-candidate framework (promote/delete), crosscheck relax-vs-keep, EQE promotion, A3 CI-artifact. KEEP set
#         confirmed: soft-anchor 5 flags + 13 §6 traps (load-bearing).
#  [NEVER] §6 traps (8 items, KEEP): market_anchor=True, bias_decay_kelly_haircut=True(fallback-live), edli_arm_gate_emit
#          (fail-close boot guard), emos ladder rungs, buy_no receipt re-check (Shanghai provenance), CROSSCHECK×5
#          (operator ruling), shoulder Gate-1 correlation, + 8 untraced UNCERTAIN. DO NOT REMOVE.
# ════════════════════════════════════════════════════════════════════════════

All three confirmed DEAD — none of the nonzero refs is a live emitter (an actual `return NoTradeReason.X` / assignment in the decision path):

- **SHOULDER_STRESS_FAIL**: 5 refs in `stress_scenarios.py` are all **docstrings/comments** describing a SCAFFOLD that "always returns NaN in thin mode"; 1 ref in `no_trade_events_schema.py:158` is a **schema comment**. Zero live emitters. The file is not imported by the evaluator. **DEAD confirmed.**
- **LIQPROV_HEARTBEAT_ABSENT**: the single ref at `candidates/__init__.py:286` is a **routing-metadata dict key**, not an emit. Confirmed (the actual emitter file emits only `LIQPROV_ADVERSE_SELECTION_UNWIRED`). **DEAD confirmed.**
- **LIVE_CANARY_DISABLED**: the single ref at ERA:1682 is the **deletion comment** ("the LIVE_CANARY_DISABLED gate ... is DELETED"). Confirmed no emitter. **DEAD confirmed.**

I now have verified ground truth on every contested item. Producing the synthesis.

---

# Zeus Gate-Mass Collapse: The Safe-Removal List (re-probed live, 2026-06-13)

**Verdict up front:** Across the ~174 ERA seams + 172 reason-enum members + ~40 flags audited by six classifiers, the honest decision path is **K ≈ 13 gate-classes** (the 5 K-decisions + ~8 genuine risk/settlement/infra gates). **31 items are provably removable** test-first. **~20 are UNCERTAIN and stay KEEP.** Two prior-classifier reads were factually wrong against live config and I have demoted them to KEEP (see §6).

---

## 1. THE HONEST-K KEEP-SET — do not touch (protects the money path)

The 5 honest sub-decisions, each with its **primary** enforcement site:

| K | Decision | Primary live site(s) — KEEP |
|---|---|---|
| **K1** belief exists & valid | `P_RAW_INVALID` evaluator.py:4422; `p_cal` evaluator.py:4788; `Q_LCB_INVALID` ERA:6905; `live_lcb_consistency_admissible` candidate_evaluation.py:121; FDR_REJECTED ERA:2883 / evaluator.py:5579; connection-null guards ERA:2200/2202/2204; calibration-authority gates ERA:855/862/869 |
| **K2** real quote present & current | `EXECUTABLE_NATIVE_ASK_MISSING` ERA:2531 / rejection_reasons:73; `NATIVE_QUOTE_MISSING` ERA:6850; `execution_price>0` candidate_evaluation.py:155; staleness ERA:2348; `EVENT_BOUND_MARKET_PHASE_CLOSED` ERA:2384 |
| **K3** log-growth > 0 | `TRADE_SCORE_NON_POSITIVE` ERA:2785; `live_capital_efficiency_admissible` candidate_evaluation.py:108; `economic_floor` evaluator.py:6484 |
| **K4** Kelly > 0 | `KELLY_REJECTED`/`KELLY_PROOF_MISSING` ERA:3131; `kelly_pass & size>0` reactor.py:2087; `size < min_notional` evaluator.py:6513; phase_aware_kelly_multiplier evaluator.py:~6300 |
| **K5** arm + direction valid | `OPERATOR_ARM_REQUIRED` ERA:1711; `real_order_submit_enabled` (master arm); `direction_law_reason` ERA:7605; `final_intent_id` reactor.py:2089; `EXECUTOR_BOUNDARY_MISSING` ERA:1695; `EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED` ERA:1687 |

**Genuine risk / settlement / infra gates (also KEEP):** all of `riskguard.py` (Brier/fill-rate/drawdown/staleness/provenance — uniformly honest), `RISK_GUARD_BLOCKED`, `POLICY_GATED`, `ORACLE_BLACKLISTED`, `DDD Rail 1 HALT`, settlement-coverage verdict+arm-block+shrink trio (ERA:9546/9672/12477 — distinct pipeline stages, **all TRUE & live**), `DAY0_ORACLE_ANOMALY_PAUSED`, source-truth gate (reactor.py:1427), executable-snapshot gate, the `_assert_no_submit_lane_invariant` antibody, all submit-time recapture re-checks (PRICE_MOVED / EDGE_REVERSED / FAMILY_REVERSED), and the taker-depth-authority assertion ERA:540 (guards the TAKER path — **not** a maker-strangler).

---

## 2. THE SAFE-REMOVAL LIST (ranked ascending risk)

> Every item below was re-probed against live `config/settings.json` and live source. Each carries the relationship test that must go green **before** removal.

### Tier A — DEAD (flag-off + zero readers, or dead enum). Lowest risk.

| # | Site | Proof (verified live) | Relationship test |
|---|---|---|---|
| A1 | `config:edli.reactor_prune_enabled` (=True but ignored) + main.py:6929-6934 | Code comment: "the legacy reactor_prune_enabled flag is **ignored**"; unconditional return before any check. | Remove key + dead comment block; assert no conditional branches on it; full money-path suite green. |
| A2 | `config:edli.no_trade_regret_enabled` | **0** src/ readers; only `test_edli_online_invariants.py:59` asserts presence. | Remove key + delete the presence-assertion; `test_edli_online_invariants` still passes. |
| A3 | `config:edli.reports_enabled` | **0** src/ readers; only `test_edli_online_invariants.py:60` asserts presence. | Remove key + delete presence-assertion; suite green. |
| A4 | `config:feature_flags.forecast_complete_live_enabled` (=absent/False) | `grep forecast_complete_live_enabled src/` = **0**. Key has no behavioral reader. | Assert the string is absent from src/; remove key. |
| A5 | NoTradeReason `SUBSTRATE_TOPOLOGY_INCOMPLETE` (no_trade_reason.py:122) | **0** non-def emitters (verified). | AST-scan: enum member appears in no `return/=` site outside the enum module; no row in `no_trade_events` ever carries it. |
| A6 | NoTradeReason `SNAPSHOT_CAPTURE_SEMANTIC_MISMATCH` (:123) | **0** non-def emitters. | Same antibody test. |
| A7 | NoTradeReason `DAY0_NOWCAST_NOT_AUTHORIZED` (:99) | **0** non-def emitters. | Same. |
| A8 | NoTradeReason `SHOULDER_STRESS_FAIL` (:131) | 6 refs are **all docstrings/schema-comments** in a SCAFFOLD (`stress_scenarios.py` returns NaN in thin mode, not imported by evaluator). **0** live emitters. | Same; assert `stress_scenarios` not imported by evaluator. |
| A9 | NoTradeReason `SHOULDER_REGIME_MISMATCH` (:132) | **0** non-def emitters. | Same. |
| A10 | NoTradeReason `SHOULDER_NATIVE_NO_DEPTH_INSUFFICIENT` (:133) | **0** non-def emitters. | Same. |
| A11 | NoTradeReason `SHOULDER_DAY0_BOUND_NOT_ELIMINATED` (:134) | **0** non-def emitters; test is `xfail`. | Same; remove the xfail test. |
| A12 | NoTradeReason `LIQPROV_HEARTBEAT_ABSENT` (:141) | Single ref is a **routing-dict key** (`candidates/__init__.py:286`), not an emit; emitter file emits only `LIQPROV_ADVERSE_SELECTION_UNWIRED`. | Same; remove the routing-dict entry too. |
| A13 | RejectionReason `LIVE_CANARY_DISABLED` (:225) | Single ref is the **deletion comment** ERA:1682 ("gate is DELETED"). **0** emitters. | Same antibody test on `no_trade_regret_events`. |
| A14 | RejectionReason `MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE` (:251) | **0** non-def emitters. | Same. |
| A15 | RejectionReason `FSR_SOURCE_RUN_NOT_COMPLETE` (:153) | **0** non-def emitters. | Same. |
| A16 | RejectionReason `LEGACY_INJECTED_TEST_SUBMIT` (:308) | Docstring: "test-only … never a live reason". | Assert no production row carries it. |
| A17 | FT error-model branch, period_extrema, evaluator.py:4337-4355 | `full_transport_live_enabled=False` (verified) → `_resolve_ft_error_model_for_entry` returns None at evaluator.py:3508 → `elif _ft_model is not None:` **unreachable**. | Mock resolver→None (mirrors live); assert identical EdgeDecision list with the elif block deleted. |
| A18 | FT error-model branch, ens-signal, evaluator.py:4400-4414 | Same flag/root cause. | Same test, ens path. |
| A19 | Oracle-penalty no-op shell, evaluator.py:6358-6361 | Code comment: "intentionally **no live behavior** — kept as a no-op shell … Removable in follow-up cleanup PR." | With `oracle.penalty_multiplier=0.8`, assert byte-identical EdgeDecision after removal. |

### Tier B — SHADOW (default-OFF experimental, operator-banned). Low risk.

> **Verified config-FALSE** (or absent): these four flags shadow-compute and return the original on the live path.

| # | Site | Proof (verified config value) | Relationship test |
|---|---|---|---|
| B1 | `replacement_neff_width_correction_enabled` ERA:9779/10461 | config **False**. Computes neff-corrected width, returns standard q_lcb. | For every fixture candidate: `receipt.proof.q_lcb` (flag=False) == (flag-absent). neff column never in live q path. |
| B2 | `replacement_q_james_stein_enabled` ERA:9793/8086 | config **False**. Shadow-logs `q_js`/`lambda_js`, live q_point unchanged. | `receipt.proof.q_point` (False) == (absent). JS blend never in live q_point. |
| B3 | `replacement_horse_race_kelly_enabled` ERA:9808/7255 | config **False**; code comment "SHADOW-ONLY this pass". | `receipt.kelly_fraction` (False) == (absent). |
| B4 | `replacement_selection_eb_shrinkage_enabled` ERA:2048/2817 | config **False**. EB shrinkage shadow-logged; FDR is the live gate. | `SELECTION_EB_UNLICENSED` never fires when FDR passes; selection scores (False) == (absent). **Retain the EB *computation* block** as the eventual FDR replacement — remove only the flag-guard + shadow path. |
| B5 | `replacement_exit_policy_enabled` + `_belief_blend_enabled` + `_calibration_alarm_enabled` (exit_policy_shadow.py:80-82; cycle_runtime.py:3680-3684) | all three config **False**. `should_exit` byte-identical; shadow result never applied. | Remove all three at the shared call site; assert exit decisions unchanged. (Collapse together — one structural decision.) |

### Tier C — REDUNDANT_COLLAPSE (precedent-duplicate). Medium risk; surgical.

| # | Site | Proof (verified) | Relationship test |
|---|---|---|---|
| C1 | reactor.py:2099 `live_lcb_consistency_rejection_reason(receipt.q_live, receipt.q_lcb_5pct)` | **Identical function** already gated in `candidate_evaluation.py:121` (`q_posterior`, `q_lcb_5pct`). Field comments confirm receipt fields are verbatim copies. Any receipt exists only because its candidate was `admitted=True`. | For every `admitted` candidate: function on its q_posterior/q_lcb_5pct is None; receipt copies → re-check is None. Removal byte-identical. **Wiring bugs fixed in wiring, not masked by re-gate.** |
| C2 | reactor.py:2104 `live_capital_efficiency_rejection_reason(receipt.q_lcb_5pct, receipt.c_fee_adjusted, receipt.trade_score)` | **Identical function** already gated at `candidate_evaluation.py:108`. | Same property. **CAVEAT:** removal touches the *shared* `if has_live_admission_inputs:` block that also hosts the buy_no check (D-trap below) — remove only the two confirmed-redundant `if … return` stanzas, leave the buy_no stanza intact. |

### Tier D — ARTIFICIAL_THROTTLE (operator-banned caps/time-bans/q-haircuts). Higher risk — confirm direction-neutrality.

| # | Site | Proof | Relationship test |
|---|---|---|---|
| D1 | `is_reentry_blocked` 20-min ban — portfolio.py:3274, evaluator.py:5994 | Hardcoded 20-min post-reversal timer; not derived from belief/quote/edge/Kelly/arm. Operator law bans time-bans. | Reversal-exit 10 min ago + candidate passing all K=5 → `traded` outcome **identical** with gate removed (reversal doesn't change belief/quote/edge/Kelly/direction). |
| D2 | `is_token_on_cooldown` 1-hr ban — portfolio.py:3293, evaluator.py:6011 | Hardcoded 1-hr post-fill-failure timer. Operator law bans time-bans. | EXIT_FAILED 30 min ago + candidate passing K=5 → `traded` identical with gate removed. |
| D3 | `source_quality_haircut` km multiply — evaluator.py:6370 | `km *= partial_run_kelly_haircut`. Continuous q-haircut on data-availability; operator law: sizing = q_lcb+Kelly only. | Partial-run candidate: removing the multiply (keep the binary `source_quality_policy_rejection` gate) leaves `traded=T/F` unchanged; only continuous magnitude changes. |
| D4 | DDD **Rail 2** discount km multiply — evaluator.py:6369 | `km *= (1 - ddd_discount)`. Continuous coverage-haircut. **Rail 1 HALT stays (honest).** | `action='DISCOUNT'` candidate: removing the multiply (keep Rail 1) leaves binary outcome unchanged. |
| D5 | `risk_throttle *= 0.5` ×3 — evaluator.py:6221-6230 | Double-applies `portfolio_heat` that Kelly already ingests at evaluator.py:6243 (`dynamic_kelly_mult(portfolio_heat=current_heat)`). Redundant 2nd multiplier on the same quantity. | `current_heat=0.30`: removing the throttle block leaves `traded=T/F` identical (Kelly already shrinks under heat). |

### Tier E — TAKER_STRANGLER_FIX (split honest gate from banned cap). Highest risk of this list — requires split, not delete.

| # | Site | Proof | Relationship test |
|---|---|---|---|
| E1 | `SHOULDER_CLUSTER_CAP_EXCEEDED` — shoulder_cluster_cap.py:34 (`=2000.0`) and :238 (`if projected_total > 2000: refuse`) | Two gates fused under one reason: **Gate 1** cross-city correlation check (honest dependency — KEEP); **Gate 2** $2000 notional hard cap (operator-banned). | Family with `projected_total>2000` but correlation-check passes + K=5 clear → trade **accepted** after Gate-2 removal; assert Gate 1 still blocks when correlation fails. **Split, do not delete the reason.** |

---

## 3. PRECEDENT-FAMILIES TO COLLAPSE (kills the ratchet — highest leverage)

| Family | N sites | Collapses to | Collapse relationship test |
|---|---|---|---|
| **Soft-anchor 5-flag ladder** (all TRUE, verified) — `…soft_anchor_{shadow,veto,trade_authority,kelly_increase,direction_flip}_enabled`, runtime_policy.py:11-22 | 5 | **1** flag `replacement_live_authority_enabled` (= TRADE_AUTHORITY rung). runtime_policy.py:293-300 confirms it is already FLAG-ONLY (evidence-gate removed). | All-5-TRUE → status `LIVE_AUTHORITY`; with only the 1 authority flag → status still `LIVE_AUTHORITY` and `effective_q_lcb` byte-identical across ≥10 fixture posteriors. |
| **q-correction shadow flags** | 5 (market_anchor TRUE, other 4 FALSE) | **Market-anchor stays live**; the 4 FALSE collapse via Tier-B removals (B1-B4). | Per B1-B4. (Do **not** touch market_anchor — verified live.) |
| **Exit triple-flag** | 3 (all FALSE) | **0** (remove all, Tier B5) | Per B5 — single call site, identical `should_exit`. |
| **Anti-churn layers 5/6/7** | 3 | **1** honest predicate: keep `ALREADY_HELD_SAME_TOKEN` (layer 7 dedup — honest); remove layers 5+6 (Tier D1/D2). | Per D1/D2; layer-7 inflight dedup retained. |
| **Crosscheck/MODEL_CONFLICT** | 5 (evaluator.py:5037/5061/5088/5177/5777) | **collapse the 3 data-unavailable variants → 1 `CROSSCHECK_DATA_UNAVAILABLE`**; keep the 2 genuine-conflict gates. | **UNCERTAIN — KEEP for now** (operator must rule whether GFS-unavailable is honest signal-gap or outage). Listed as a *candidate* collapse, not an executable removal. |
| **Tail-shape SHADOW/HARD enum split** | 2 (probability_sanity.py:728/738) | **1** (drop the SHADOW member; HARD has the live emitter at evaluator.py:5864) | Shadow branch produces **0** EdgeDecision rows (log-only) — verified by C-cluster. Confirm `tail_discrepancy_mode` default before executing. **UNCERTAIN — KEEP** pending that config read. |

**Plus the EQE / probability-sanity-shadow env-var shadow path** (evaluator.py:5348-5367 log-only; EQE list `ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED` default `'0'`) — Tier-B-class, removable with the same byte-identity test, but **not** independently re-verified here; treat as Tier-B-pending.

---

## 4. COUNT THE COLLAPSE (N → K)

| Bucket | Count |
|---|---|
| **Honest-K gate-classes (KEEP)** | **~13** (5 K-decisions + ~8 risk/settlement/infra/antibody classes) |
| **Provably removable now** (test-first) | **31** — Tier A:19, Tier B:7 (B5 counts 3 flags), Tier C:2, Tier D:5, Tier E:1-split *(B5 + E1 are multi-part)* |
| **Precedent-family flag collapse** | **5 flags → 1** (soft-anchor) + 3→0 (exit) + the 4 FALSE q-shadow flags |
| **UNCERTAIN — stay KEEP** | **~20** (see §6) |

**The N→K headline:** the operator's ~174 seams + 172 reason members + ~40 flags reduce to **K ≈ 13 honest gate-classes**. The single largest disease vector is the **shadow-flag / staging-ladder family** (5 soft-anchor + 4 false q-shadow + 3 exit + 2 EMOS-staging-absent) — collapsing those alone removes ~14 flag-branches and the deployment model ("default-OFF shadow compute") that teaches every later session to add one more.

---

## 5. SMALLEST-SAFEST FIRST REMOVAL (worked example to greenlight)

**Execute A1 first: delete the `reactor_prune_enabled` dead branch.**

- **Why it's the floor of the risk ladder:** the code *already ignores the flag* — main.py:6934 verbatim: `# the legacy reactor_prune_enabled flag is ignored.` with an unconditional `return` before any flag check. Removing it changes **zero** runtime behavior by construction; it is pure config + dead-comment deletion.
- **Green-before-remove test:**
  ```python
  def test_reactor_prune_flag_is_inert():
      # main.py reaches the unconditional return regardless of the flag value
      for val in (True, False):
          settings = load_settings(); settings["edli"]["reactor_prune_enabled"] = val
          assert reactor_prune_path(settings) is REACTOR_PRUNE_NOOP  # same sentinel both ways
      # and the key has no other reader
      assert grep_count("reactor_prune_enabled", "src/") == 0  # after comment removal
  ```
- **Action:** delete the key from `config/settings.json` and the dead comment block at main.py:6929-6934; run the money-path suite. Zero decision-stream delta.

Then proceed up Tier A (the 12 dead enums A5-A16 batch under one antibody PR), then Tier B.

---

## 6. EXPLICIT SAFETY CAVEAT — looks removable, but KEEP until operator confirms (the traps)

These were flagged for removal by a classifier but **fail the live re-probe** or carry live-decision risk. **Do not remove.**

1. **`replacement_q_market_anchor_enabled`** — Cluster 1 said SHADOW_REMOVE. **WRONG: config = `True`, LIVE.** The market-anchor cap is a *permanent one-sided conservative correction* per authority doc. **KEEP.**
2. **`bias_decay_kelly_haircut_enabled`** — Cluster 1 said SHADOW_REMOVE (claimed default-False). **WRONG: config = `True`.** It is inert on the primary EMOS lane (ERA:11544 early-return for `q_source∈{emos,raw_honest}`) but **live on the DAY0/emos-miss fallback** — removing it changes fallback-lane sizing. **KEEP.**
3. **`edli_arm_gate_emit_enabled`** — Cluster 2 said DEAD (claimed consumer is a no-op). **WRONG:** main.py:6660 documents **"default True so it fail-closes"**; the consumer `_assert_edli_arm_gate_artifact` is a **FATAL boot guard** actively called at main.py:1154. Config sets it False but the code default is True and it is a live arm-gate antibody. **KEEP.**
4. **`edli_emos_shadow_ledger_enabled` / `edli_emos_ci_live_enabled`** — absent from config (code default False). They look like Tier-B shadow flags, **but** they are the *unfinished rungs* of the EMOS-promotion ladder (the live `edli_emos_sole_calibrator_enabled=True` is the promoted middle rung). Removing the rungs removes the promotion path. **KEEP** — collapse the EMOS ladder only as a deliberate operator promotion, not a delete.
5. **`live_buy_no_conservative_evidence` receipt re-check** (reactor.py:2109-2126) — looks like the 3rd member of the redundant-collapse family, **but** `same_bin_yes_posterior` and `settlement_coverage_status` are carried on the receipt from a *different provenance path* (the Shanghai 2026-06-11 fix). Receipt values may differ from candidate. **KEEP.** (This is why C1/C2 removal must be surgical — leave this stanza inside the shared `if has_live_admission_inputs:` block.)
6. **CROSSCHECK_UNAVAILABLE ×5 / MODEL_CONFLICT** — operator must rule: honest two-model signal-gap (KEEP) vs GFS infrastructure outage (allow ENS-only). **KEEP pending ruling.**
7. **Shoulder cluster cap — Gate 1 (correlation)** — only **Gate 2** ($2000 cap) is removable; Gate 1 is an honest regime-correlation dependency. **Never delete the reason code or Gate 1.**
8. **`quote_fresh` (candidate_evaluation.py:161), `passed_prefilter` (:159), `STRATEGY_KEY_UNCLASSIFIED`, `source_quality_policy_rejection` binary gate, `MONEY_PATH_TRANSIENT_EXHAUSTED` requeue-cap, `threshold_multiplier` divider, receipt-schema self-check ERA:3624, L7622 coverage-unlicensed-tail** — all UNCERTAIN across classifiers (field-provenance or requeue-cap semantics untraced). **KEEP** until each gets its own trace.

**Bottom line for the operator:** greenlight **Tier A (A1 first)** today — all 19 are dead/inert with green byte-identity tests and zero live-decision risk. Tier B (4 verified-FALSE shadow flags + exit triple) next. Hold Tier C/D/E and every §6 trap for explicit per-item confirmation; the 4 wrong prior-classifier reads in §6 are exactly the kind of "called inert code live" error the operator warned about.