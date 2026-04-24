# Critic-beth durable learnings — Phase 7B (cycle 3)

**Continuity**: reads prior learnings in `critic_beth_phase7a_learnings.md`. This doc appends post-P7B observations.

## Patterns confirmed in cycle 3

1. **Deferral-with-rationale > half-landing**. Team-lead's choice to defer Item 2 rather than land a broken 15-helper extract prevented a real bug category. The planner's "pure mechanical helpers" claim was incorrect (4 module-level constants differ between extractors; 2 helpers depend on them). **Lesson for future planners**: "mechanical extract" claims MUST be verified against module-level globals, not just function bodies. Add to planner checklist: "grep module-level CONSTANTS and verify parallel-identical across target files before declaring extract safe".

2. **Broken monkeypatches are silent GREEN**. Cycle 2 learning reconfirmed: `test_runtime_guards::test_day0_no_remaining_forecast_hours` was a mirror test pre-P7B because monkeypatch targeted a non-existent attribute. P7B restored the real seam — test now fails at a DEEPER assertion (SIGNAL_QUALITY expected, MARKET_FILTER actual). **This is a WIN for test reality, not a regression**. Future phases should expect: "fixing a broken monkeypatch often reveals the real code's actual behavior, which may differ from the test's expectation."

3. **Commit-message aspirational wording risk**. "Addresses test_topology_scripts_mode_covers_all_top_level_scripts" implies resolution; actual result was 6→4 errors (test still fails). **Future commit messages should use "partially addresses" or "reduces scope of"** when the acceptance gate was qualified (gates 2 used "may drop... Bonus if they do"). Critic must flag commit-message-overclaim-vs-actual as a standing check.

## Methodology refinements

4. **Baseline-restore grep is high-ROI**. Reproduced all 6 "pre-existing failures" in ~10s via `git checkout <baseline> -- tests/` + pytest. Cost trivial; value high (prevents false-positive regression findings). Keep as standard Cycle-N technique when commit claims "N new failures / M total passed".

5. **Contract-forbidden grep wording matters**. P7B contract gate 4 said "`grep -rn 'from scripts.rebuild_calibration_pairs_v2 import' scripts/`" — **scoped to scripts/**, not src/tests. Non-scripts hits (tests/test_phase7a_metric_cutover.py: 7 hits; tests/test_phase5b_low_historical_lane.py: 8 hits) are technically permitted by that literal wording. **Critic must read scope qualifiers literally** before flagging.

6. **Extend P3.1 vocabulary further** (cycle 1 added `_requires_explicit_|_must_specify_|_no_default_`). Cycle 3 candidates to add: `_refuses_until_|_latent_|_silent_|_accidental_green`. These are metacognitive tags — tests and docs using them often hint at antibodies the author themselves flagged as suspect.

## Carry forward to P8

### Readiness for LOW shadow activation

P6/P7A/P7B all closed. **Next critic's first-pass should be full read-side audit of ingest/settlement/evaluator for every HIGH-hardcoded seam** (carried from cycle 1 two-seam-principle learning). Known seams:
- `_fetch_verified_observation` ✅ fixed P7A CRITICAL-1
- `add_calibration_pair_v2` ✅ metric_identity kwarg pattern (P5)
- `save_platt_model_v2` ✅ metric_identity kwarg pattern (P5)
- Still to audit at P8: settlement-writer path, run_replay read path, evaluator LOW-side wiring

### Test-debt ticket candidates from P7B

1. test_runtime_guards::test_day0_no_remaining_forecast_hours downstream gap (MARKET_FILTER rejection order). **Now a real failing test** thanks to P7B monkeypatch repair. P8 debt.
2. test_runtime_guards::test_day0_observation_path_reaches_day0_signal — `Day0Signal` AttributeError (NOT related to Item 3; monkeypatch construction needs `Day0Signal` imported into evaluator_module namespace — could be a test-refactor artifact from P6).
3. 3 topology_doctor `script_long_lived_bad_name` errors — `naming_conventions.yaml` update or exceptions addition. P7B-followup or P8 hygiene.

### Item 2 scheduling

"P7B-followup commit" has no assigned owner/ETA. **Forward-log should name a date**. Suggest: before P8 opens. Rationale: _tigge_common extraction is zero-risk cleanup once the scope is correctly scoped (9 or 13 safe helpers not 15). Doing it before P8 prevents "still pending" accumulation.

## Meta

Cycle 3 total review time was ~25% of cycle 1. Pattern confirmed from cycle 2: thorough-cycle-1 review with pre-commitment predictions pays multiplicative dividends across subsequent reviews. Pre-commitment predictions 5/5 hit — methodology is stabilizing as reliable (3-for-3 cycles).

**Sniper Gen-Verifier model validated across 3 phases (P6, P7A, P7B)**. Cost breakdown:
- P6: 1 ITERATE + 1 re-review (2 cycles)
- P7A: 1 ITERATE (3 findings) + 1 re-review (2 cycles)
- P7B: 1 PASS on first review (1 cycle)

Trend: critic-accumulating-memory + team-lead-generator-discipline → fewer cycles per phase. The pattern library grows, coverage grows, friction drops.

## Addendum — Write/Edit blocked for critic subagent this invocation

Note for future critics: when spawned as Opus critic via `oh-my-claudecode:critic`, the Write/Edit tools may be blocked. Team-lead must persist the verdict + learnings to disk on critic's behalf. Verdict content should be returned in the final message for team-lead to archive. Consider flagging this as an OMC agent-definition check: critic's wide-review deliverables are disk artifacts, tools should align with that deliverable.

---

*Authored*: critic-beth (Opus, sniper-mode Gen-Verifier), 2026-04-18 cycle 3
*Preserved-by*: team-lead (Write/Edit on critic's behalf)
*Next cycle opens*: P8 shadow mode or P7B-followup (Item 2) per user ruling
