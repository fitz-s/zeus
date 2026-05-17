# Phase 0.H Critic Decision

Reviewer: code-reviewer (Phase 0.H critic, sonnet tier per K1-cleanup rule)
Date: 2026-05-06
Branch: topology-redesign-2026-05-06

---

## Section 2.0.H Acceptance Table

| Criterion | Phase-0 floor | Status | Evidence |
|---|---|---|---|
| Capability catalog drafted | yes | PASS | architecture/capabilities.yaml 16 entries, catalog_size: 16, schema_version: 1 |
| Fossil profile retirement complete | yes | PASS | topology.yaml 61 to 24 profiles (37 deleted, -4626 LOC); evidence/phase0_d_fossil_audit.md confirms methodology |
| Capability tagging spike (1 capability) | yes | PASS | harvester.py:1072-1074 @capability("settlement_write") + @protects("INV-02","INV-14") on _write_settlement_truth |
| Shadow router agreement 7d | >=90% | FAIL | evidence/shadow_router/agreement_2026-05-06.jsonl: 1 record, agreement=False, classification=NEW_ONLY; no 7-day window exists |
| Replay-correctness scaffold runs | yes | PARTIAL | scripts/replay_correctness_gate.py exists; seeded injection works; CI lane exists; BUT chronicle table empty in all production DBs |
| 20-hour replay friction baseline measured | yes | PARTIAL | evidence/baseline/20h_replay_friction.md exists; wrong fixture (90.74h claude session not codex/PR67 per invariants reclassification); operator accepted as upper-bound |
| 6 ADRs signed | yes | FAIL | All 6 ADRs carry operator_signature: pending; IMPLEMENTATION_PLAN section 0 hard-requires signatures before src/architecture/ changes; 0.D and 0.E modified governed paths pre-signature |

---

## Findings

### HIGH

H-1: All 6 ADRs unsigned - governance precondition breached
Files: docs/operations/task_2026-05-06_topology_redesign/adr/ADR-{1..6} line 7
IMPLEMENTATION_PLAN section 0 is unambiguous: signed ADRs are a hard precondition for any src/architecture/ code change. Phase 0.D modified architecture/topology.yaml and architecture/digest_profiles.py; Phase 0.E modified src/architecture/decorators.py and applied decorators to src/execution/harvester.py - all before any signature. ADR-2 explicitly gates Phase 3 structural deletion on >=90% shadow agreement over >=7 days; executing Phase 0.D warm-up deletions under an unsigned ADR-2 means the deletion reversibility contract is untested governance, not ratified policy.
Fix: operator signs ADRs 1-6 (or issues section 9 charter override with <=14d expiry for 0.D/0.E work) before Phase 1 dispatch.

H-2: Shadow router agreement criterion not met - 1 data point, agreement=False, classifier uncalibrated
File: evidence/shadow_router/agreement_2026-05-06.jsonl
Acceptance table floor requires >=90% agreement over >=7 days. Current state: 1 smoke-test record, agreement: false, classification: NEW_ONLY. The classifier returns NEW_ONLY because legacy --route-card-only output lacks capability names; every comparison will be NEW_ONLY until the classifier is calibrated or the legacy output format is updated. The 7-day clock has not started and cannot start in current state.

H-3: Two FAIL rows violate IMPLEMENTATION_PLAN section 0 no-partial-GO constraint
File: docs/operations/task_2026-05-06_topology_redesign/ultraplan/# IMPLEMENTATION_PLAN.md around line 196
Section 0 states No partial GO. Rows for shadow agreement and ADR signatures are both FAIL. Both must be resolved before Phase 1 dispatch per the plan's own hard constraint.

### MEDIUM

M-1: capabilities.yaml settlement_write cites state/zeus.db but canonical event DB is state/zeus_trades.db
File: architecture/capabilities.yaml lines 89-90
Phase 0.G deviation confirmed zeus_trades.db is canonical. settlement_write hard_kernel_paths lists state/zeus.db and state/zeus-world.db but NOT state/zeus_trades.db. Multiple other capabilities (backtest_diagnostic_write, calibration_persistence_write, calibration_decision_group_write, decision_artifact_write, canonical_position_write) also reference state/zeus.db and need individual audit before Phase 1 canonicalizes this file.

M-2: Two UNVERIFIED hard_kernel_paths in live_venue_submit capability (Tier-0 execution surface)
File: architecture/capabilities.yaml lines 281-282
src/execution/venue_adapter.py and src/execution/live_executor.py are marked UNVERIFIED: not on disk. A capability card for live venue submission with phantom paths cannot be trusted as an enforcement boundary. Fix: resolve or remove before Phase 1 canonicalization.

M-3: Replay gate structurally valid but production-hollow; CI lane will break on first live trade
Files: scripts/replay_correctness_gate.py, evidence/replay_baseline/2026-05-06.json
Baseline hash covers 3767 events from zeus_trades.db 7-day window, but zeus_trades.db has no live trades. First live trade invalidates the hash and requires re-baseline; CI lane will be broken by design until Phase 4 adds a fixture or remote DB access. Expected per plan but needs explicit carry-forward.

M-4: Shadow classifier calibration has no documented plan or timeline
File: evidence/shadow_router/agreement_2026-05-06.jsonl; agent_registry deviation note
NEW_ONLY classification persists until topology_doctor emits capability names in --route-card-only mode OR classifier handles name-absent legacy output. No owner or timeline documented. The Phase 3 shadow >=90%/7d gate (which ADR-2 requires before structural deletion) is unreachable until this is fixed.

### LOW

L-1: T2/T3 route card token budget tests use xfail strict=False - silent XPASS risk
File: tests/test_route_card_token_budget.py lines 46-77
strict=False means accidental early pass goes unreported. These are Phase 3 acceptance gates; strict=True would catch regression in either direction. Low severity because explicitly deferred.

L-2: False block rate baseline methodology caveat undermines cited 2.6/day figure
File: evidence/baseline/false_block_rate.md
115 of 161 commits unclassified; true bypass rate unknowable. The operator-cited 2.6/day was from a different measurement window. Carry-forward to Phase 1 to avoid treating this as authoritative.

L-3: Replay fixture wrong target - acknowledged, carry-forward already recorded
File: evidence/baseline/20h_replay_friction.md; invariants.jsonl reclassification entry
Operator accepted as upper-bound. Phase 5 dispatch must explicitly reference codex/PR67 worktree session. No Phase 1 action needed.

---

## Probe Answers

1. Token cost 250,205 vs 220k briefing: methodology sound (tiktoken cl100k_base, per-task bootstrap enumerated). Validates redesign motivation more strongly. 30k post-cutover target remains Phase-5 success gate. No invalidation.

2. Wrong replay fixture: operator classified NOT_A_DEFECT; 90.74h is overshoot upper-bound. Phase 0.A baseline still serves its acceptance purpose. Phase 5 must locate codex/PR67 worktree session explicitly.

3. hard_kernel_paths on-disk: 25 of 27 paths verified present. Two UNVERIFIED paths (venue_adapter.py, live_executor.py) flagged in-file. All protects_invariants references (INV-02,04,05,06,08,10,14,15,21) verified in architecture/invariants.yaml.

4. zeus.db vs zeus_trades.db: settlement_write capability lists zeus.db not zeus_trades.db. Medium finding M-1. Multiple other capabilities need individual audit.

5. Private function AST-walk: test_capability_decorator_coverage.py walks all FunctionDef including private names (no name filter). _write_settlement_truth at harvester.py:1074 carries the decorator and will be found. settlement_write is in _PHASE0E_PASS set, not xfail. Consistent.

6. T2/T3 xfail: reasonable forward-guard. Minor concern: strict=False allows silent XPASS. See L-1.

7. Shadow router NEW_ONLY: will produce 100% disagreement on every run until calibrated. 7-day clock has not started. See H-2 and M-4.

8. Replay gate empty chronicle: gate machinery valid; seeded injection caught deliberate mismatch. Production validity requires live trades. Expected; carry to Phase 4.

9. ADR-2 scope vs 0.D: ADR-2 explicitly states Phase 0.D warm-up required. 37 deletions match ADR-2 intent. Concern is not overshoot - it is the ADR authorizing this work is unsigned.

10. ADR-3 scope vs 0.E: 0.E applied @capability/@protects decorators - ADR-1 scope (primitive choice, decorator system). ADR-3 covers LiveAuthToken phantom type discipline (Phase 4). 0.E did NOT preempt ADR-3; no scope violation.

---

## Operator Decisions Surfaced (genuinely beyond critic scope)

OD-1: ADR ratification - sign all 6 or issue section 9 charter override
0.D and 0.E executed under unsigned ADRs. Operator must choose:
  (a) Sign ADRs 1-6 retroactively, ratifying 0.D/0.E as compliant with ADR intent; OR
  (b) Issue section 9 charter override (<=14d expiry) covering specific pre-signature work in 0.D and 0.E; OR
  (c) Roll back 0.D and 0.E via git revert and re-execute after signatures.
ADR-2 and ADR-3 are the directly affected ADRs.

OD-2: Shadow agreement gap - accept FAIL row or issue section 9 override
The 7-day/>=90% criterion is at 0 days in. Operator must either:
  (a) Issue section 9 charter override accepting 0-day smoke test as Phase-0 floor evidence (with explicit Phase 3 re-gate and calibration plan); OR
  (b) Hold Phase 1 until 7 days of shadow data accumulate and classifier is fixed.

---

## Positive Observations

- Provenance headers present on all new files - methodology discipline followed.
- AST-walk test design correct: no name filtering means private functions found without special-casing.
- Fossil audit methodology (grep src/ zero hits = safe to delete) appropriate for warm-up phase; every deletion individually documented.
- invariants.jsonl governance trail thorough - SendMessage failure mode documented, reclassification recorded, carry-forwards explicit.
- Phase 0.G correctly identified zeus_trades.db vs zeus.db discrepancy and flagged as deviation rather than proceeding silently.

---

## GO / NO-GO Recommendation

RECOMMEND NO-GO conditional on operator decisions OD-1 and OD-2.

Two hard acceptance criteria are unmet: ADR signatures (all 6 pending) and shadow router agreement (0 days of data, classifier not calibrated). IMPLEMENTATION_PLAN section 0 states No partial GO. The technical work of 0.A through 0.G is structurally sound; the blocker is governance, not execution quality.

Conditions for conditional GO (all three required):
1. Operator signs ADRs 1-6 OR issues section 9 charter override covering 0.D/0.E pre-signature work.
2. Operator issues section 9 charter override for shadow agreement criterion with documented calibration plan and timeline for when 7-day clock starts.
3. M-1 (zeus.db vs zeus_trades.db in settlement_write capability) resolved before Phase 1 canonicalizes capabilities.yaml.
