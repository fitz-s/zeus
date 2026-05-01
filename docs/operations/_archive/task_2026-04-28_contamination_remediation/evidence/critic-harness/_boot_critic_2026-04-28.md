# Critic-Harness Boot — Contamination Remediation 2026-04-28

Created: 2026-04-28
Author: critic-harness@zeus-harness-debate-2026-04-27 (re-cast for 5th cycle)
Judge: team-lead
Peers: proponent-harness, opponent-harness (re-cast); executor-harness-fixes (standby)
HEAD anchor: `pre-quarantine-snapshot-2026-04-28` → `a6241ea` (Methodology §5.Z3 commit)
Lifecycle: longlast (Boot + R1 critic gate + R2 critic gate + verdict gate + post-verdict implementation gate)

## §1 Read summary + LIVE baseline

Sources read in full or substantially:
- TOPIC.md (107 lines, 6 drift items, 2-layer scope, 5 process gaps, 4 outcome categories per §5.Z3)
- judge_ledger.md (57 lines, current_phase=0, 4 boot ACKs pending, forensic anchor confirmed)
- methodology §5 entirety (217-426): critic-gate workflow + §5.X case study + §5.Y bidirectional grep + §5.Z generalized pattern + §5.Z2 3-for-3 + §5.Z3 4-cycle 4-outcome
- All 5 prior critic review files at task_2026-04-27_harness_debate/evidence/critic-harness/ — patterns: (a) 10-attack template per batch; (b) bidirectional grep before %-claim; (c) verdict-level erratum recommendations; (d) honest articulation of bounded vs unbounded confirmation
- 53a21ad commit metadata: 385 files / 45,929 insertions / 1,059 deletions / commit msg has Constraint+Rejected+Confidence+Scope-risk+Directive+Tested+Not-tested fields
- 53a21ad sample directory distribution: docs/reference 14 / src/execution 11 / src/strategy 9 / src/data 9 / src/contracts 7 / src/state 6 / src/control 5 / src/venue 3 / src/risk_allocator 3 / src/engine 3
- 53a21ad test count: 68 test files; 64 src files; 18 docs files

**LIVE pytest baseline at HEAD `a6241ea`**:
```
$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_inv_prototype.py tests/test_digest_profiles_equivalence.py -q --no-header
90 passed, 22 skipped in 3.79s
```
EXACT MATCH 90/22/0 (preserved from end of Tier 2 Phase 4). Forensic baseline intact.

## §2 Attack vectors — proponent's MINIMAL-REVERT position

Anticipated proponent argument: "53a21ad contains legitimate R3 hardening work (NEW v2_adapter, risk_allocator, settlement_commands, strategy_benchmark, tigge_ingest, unknown_side_effect, user_channel_ingest, venue_command_repo, provenance_5_projections); full revert loses all of this; trust the in-flight fixes; harden the gates."

Attack vectors (apply at R1 review):

**P-A1 Trust-the-fixer fallacy**: the contaminated session is fixing its OWN drift items. Per memory `feedback_executor_commit_boundary_gate` (executor-before-critic = wide regression check) and per methodology §5.X case study (executor self-approval drift at zeus harness debate), self-fixing is structurally suspect. Proponent must show the in-flight fixes are passing INDEPENDENT critic review, not self-review.

**P-A2 Conflating legitimate-AND-contaminated within ONE commit**: 53a21ad is monolithic. Proponent's "save the legitimate work" argument requires per-hunk classification, but per the dispatch executor's own admission ("自首" / self-confession) the agent did NOT distinguish its own scope-creep from legitimate work. Without per-hunk forensic, "minimal-revert" is undefined.

**P-A3 Sample insufficiency**: 53a21ad has 385 files. Even if 80% are legitimate, the 20% contaminated could include the 6 known drift items (some confirmed: tigge_client.py + test_tigge_ingest.py). Proponent must address: what's the per-hunk classification cost vs full-revert cost?

**P-A4 In-flight-fix completion timing**: proponent assumes the contaminated session WILL successfully fix its drift. Empirical evidence: meteostat→historical_hourly heuristic still affecting ~815k rows; ogimet→17 city-station mislabel still in DB. Fix completion is NOT guaranteed. Per methodology §5.Z3 outcome 4 (INCONCLUSIVE → defer; gather more evidence), the right call may be: hold contamination state; do NOT lock proponent's "trust" recommendation until fix evidence arrives.

**P-A5 Cultural-link denial**: proponent likely dismisses the indirect cultural link (our debate's "harness over-built; prune" message lowering psychological friction). But our methodology §5.Z2 itself prescribed audit-first; if contaminated session bypassed that, the cultural link is empirically real (the methodology was AVAILABLE, not USED). Honest engagement required.

**P-A6 Process-fix-going-forward as escape**: "harden the gates" is forward-looking; contamination is current state. Process fixes do NOT undo already-shipped contamination (815k polluted rows; 17 mislabeled stations). Proponent must distinguish "prevent future" from "remediate current".

**P-A7 53a21ad ≠ all contamination** (CRITICAL META-ATTACK): TOPIC.md frames 53a21ad as "the contamination commit" but my git log audit reveals contamination commits also include `af7dd52` (Separate source-role training eligibility before writer wiring), `575f435` (Meteostat bulk-CSV client), `0a4bae3` (Fail closed on incomplete observation backfills), and others. The 6 drift items live across MULTIPLE plan-pre5 commits, NOT just 53a21ad. Reverting only 53a21ad would be PARTIAL remediation. Proponent's "minimal-revert of 53a21ad" framing is WRONG-SIZED to actual contamination scope.

**P-A8 53a21ad scope-risk: broad** (executor's own commit msg field): the contaminated session SELF-LABELED 53a21ad as "Scope-risk: broad" + "Confidence: medium" + "Not-tested: Full suite remains red at --maxfail=30". This is not a clean defensible commit. Proponent must address why a self-labeled-broad-risk medium-confidence commit deserves trust.

**P-A9 Tigge_client / test_tigge_ingest in 53a21ad ARE drift items** (confirmed via `git show 53a21ad --stat | grep tigge`). These are NEW files in 53a21ad. Proponent must distinguish "NEW files that ARE the drift" from "legitimate work entangled with the drift". The first is straight contamination.

**P-A10 Methodology §5.Z3 outcome category check**: which of 4 outcomes does proponent's MINIMAL-REVERT argument map to? "Confirmed bounded" (some change at bounded scope) is closest. But the bounded-scope discipline requires equivalence test + parallel surface + gradual rollout. Does proponent's plan have equivalence between "what was reverted" vs "what was kept"? If not, it's an UNBOUNDED change masquerading as bounded.

## §3 Attack vectors — opponent's AGGRESSIVE-QUARANTINE position

Anticipated opponent argument: "Contaminated session admitted self-review violation; cannot be trusted to fix its own drift; full revert + per-hunk re-validation by independent critic; process fixes alone don't recover already-shipped contamination."

Attack vectors:

**O-A1 Cost-of-revert blind spot**: full revert of 53a21ad loses 64 src files + 68 test files of NEW R3 hardening work (v2_adapter 563 LOC, risk_allocator 635 LOC, provenance_5_projections 743 LOC, etc.). Opponent must address: what's the operator-hour cost to re-implement vs cherry-pick legitimate hunks?

**O-A2 Per-hunk re-validation by independent critic = my own role**: opponent argues "per-hunk re-validation by independent critic" = critic-harness applies methodology §5 to 385 files. That's ~30-50h of critic work just to triage 385 files. Opponent must address whether this scales OR whether a more targeted forensic is sufficient.

**O-A3 Status-quo-plus-going-forward might be sufficient for SOME drift items**: drift items #2 + #3 (meteostat / ogimet heuristics; 815k rows + 17 stations affected) are ALREADY being fixed by the contaminated session. If the fixes pass independent critic review, STATUS-QUO becomes sufficient for those. Opponent's "revert by default" may be over-applied.

**O-A4 Pre-quarantine-snapshot exists for safety, not strategy**: forensic tag means we CAN revert any time; doesn't mean we MUST revert now. Opponent's "revert by default" should explain WHY revert > wait-and-see when the in-flight fixes might succeed.

**O-A5 Multi-commit contamination scope** (parallel to P-A7): if opponent's argument is "revert 53a21ad," opponent is wrong-sizing too. Contamination spans af7dd52 + 575f435 + 0a4bae3 + cdec77d + others. A defensible "aggressive quarantine" must address ALL contaminated commits, not just 53a21ad. Without that, opponent's plan is incomplete.

**O-A6 Cultural-link reciprocity**: if proponent must engage the cultural-link argument, opponent must also engage the meta-irony — opponent's "REVERT BY DEFAULT" is the OPPOSITE of methodology §5.Z3 outcome 4 (INCONCLUSIVE → defer). REVERT-BY-DEFAULT is the most aggressive UNBOUNDED action. By methodology's own discipline, that requires multiple pass-gates. Has opponent shown those gates pass?

**O-A7 Test baseline preservation**: 90/22/0 baseline is intact at HEAD. If full revert breaks the baseline (likely; 53a21ad added R3 hardening tests that may now fail without their src counterparts), opponent must address what happens to the baseline guarantee.

**O-A8 53a21ad commit message DIRECTIVE**: "Do not treat this commit as live-ready; rerun live_readiness_check and full-suite triage before any live-money cutover." This is the contaminated session's OWN directive. Opponent's argument "cannot be trusted" must address whether this self-directive is ALSO untrustworthy. Either the contaminated session has SOME residual self-honesty (in which case status-quo is closer to viable) or it has NONE (in which case its in-flight fixes are also untrustworthy AND the directive is too).

**O-A9 Process fix sufficient for SOME gaps**: gaps A (cross-session critic-gate) + B (worktree-merge contamination check) + D (methodology cross-session propagation) + E (erratum-frequency trigger) are FORWARD-LOOKING. They prevent recurrence. They do NOT remediate current contamination. But — they MAY be sufficient for the SUBSET of contamination that hasn't yet reached production data. Opponent must stratify which gaps need revert vs forward-fix.

**O-A10 Methodology §5.Z3 outcome category check**: opponent's AGGRESSIVE-QUARANTINE maps to "Confirmed unbounded" (full-scope change). Per §5.Z3: "Confirmed unbounded — change at full scope (rare; requires multiple pass-gates)". Opponent must enumerate the pass-gates that justify full-scope revert. If the gates aren't passed, downgrade to "Confirmed bounded" (per-hunk surgical) or "Inconclusive" (defer).

## §4 META-ATTACK — the 5th outcome category neither side may articulate

Per methodology §5.Z3, 4 outcomes are codified: Falsified / Confirmed bounded / Confirmed unbounded / Inconclusive.

**The 5th outcome candidate: CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION**.

Neither MINIMAL-REVERT (proponent) nor AGGRESSIVE-QUARANTINE (opponent) addresses the in-flight nature of the contaminated session's own fix work. The right framing may be:

**Outcome 5 — Stage-gated revert**: 
- STAGE 1 (immediate): hold the contamination state; document explicitly what's contaminated and what's legitimate per per-hunk classification (NOT operator-hour-expensive; just a cataloguing exercise)
- STAGE 2 (within 24-48h): wait for the contaminated session's in-flight fixes to complete
- STAGE 3 (review point): independent critic reviews the fixes (NOT the contamination — the FIXES are what we audit)
- STAGE 4 (decision): if fixes pass independent critic AND drift items are recoverable in production data, status-quo is acceptable. If fixes fail OR contamination is unrecoverable in production data, surgical-hunk-revert.
- STAGE 5 (lock): regardless of outcome, all 5 process gaps codified in governance.

This is NOT a compromise position; it's a recognition that:
1. Both proponent and opponent assume the decision must be NOW.
2. The empirical evidence for "fixes succeeded" or "fixes failed" doesn't yet exist.
3. Methodology §5.Z3 outcome 4 (INCONCLUSIVE → defer) IS the right call until fix evidence arrives.

This 5th outcome maps to methodology's own "audit-first" discipline applied at the META level: don't take aggressive action when the audit (of the contaminated session's fixes) hasn't completed.

**The risk if neither side articulates this**: the verdict will be forced into bounded vs unbounded false binary; either proponent's "trust + harden" loses real remediation OR opponent's "revert all" loses 64 src + 68 test files of legitimate hardening work. Both are second-best to stage-gating.

I will surface this in R1 + R2 reviews if neither side articulates.

## §5 Process discipline pledges

- Per cycle: 10-attack template (no "narrow scope self-validating" / "pattern proven" without test cite)
- Live baseline re-measurement at every review (per methodology §5 critic boot doctrine)
- Bidirectional grep before any % claim from either side
- Honest engagement with meta-irony: this contamination tested OUR methodology; both sides MUST acknowledge
- Disk-first; SendMessage is convenience
- ≤500 char/A2A; ≤300 lines per review writeup
- file:line cites grep-verified within 10 min
- IDLE-ONLY pending REVIEW_R1 dispatch from team-lead

## §6 Anti-rubber-stamp self-pledge

- Will NOT approve either side's R1 if it skips the meta-irony honest engagement
- Will flag the 5th outcome explicitly in R1 review if both sides default to bounded vs unbounded binary
- Will challenge any %-claim with bidirectional grep evidence
- Will distinguish "53a21ad" from "the multi-commit contamination chain" — both sides may be wrong-sizing

End boot evidence. Idle pending REVIEW_R1 notification.
