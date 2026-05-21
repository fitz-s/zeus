# Zeus Strategy V-Next Mainline Completion — Authority Package

Created 2026-05-21 after orchestrator drift incident in session `4beb2fa4`. Purpose: give a fresh post-compaction orchestrator a structurally complete, third-party-verifiable picture of (a) what is authoritative, (b) what is already on `origin/main`, (c) what remains to ship, (d) what the per-phase workflow looks like, (e) what a verifier should probe.

## Reading order (next orchestrator MUST read in this order)

1. `01_AUTHORITY_CHAIN.md` — what doc is authoritative for which question; provenance + supersession order.
2. `02_MAIN_STATE_INVENTORY.md` — every Phase 0/1/2 deliverable currently on `origin/main` with merge sha + tag. Single source of truth for "what is already shipped".
3. `03_REMAINING_MAINLINE_SCOPE.md` — five remaining structural surfaces (`Phase 3..7` per v4 §M enum), current code state, intent from GPT Round 1 dossier, file-likelihood map.
4. `04_PHASE_3_SHOULDER.md` — `ShoulderStrategyVNext` substantive design. Math + portfolio rules + DB schema delta.
5. `05_PHASE_4_FDR_FAMILY_CANDIDATES.md` — FDR family-ID `spread_bucket` extension + hypothesis-family-id widening per v4 §M line 1101 + dossier §2.5.
6. `06_PHASE_5_WEATHER_REGIME_CORRELATION.md` — `WeatherRegimeTag` + correlation-matrix-via-shrinkage (math spec §15.4) + cluster cap.
7. `07_PHASE_6_EVIDENCE_LADDER.md` — `EvidenceLadder` tiers + `PromotionStatus` + `ShadowExperimentRegistry` per dossier §9 + §13.5.
8. `08_PHASE_7_SETTLEMENT_TYPE_GATE.md` — settlement social→type-gate migration per v4 §M line 1104 + `umaResolutionStatus` typing in current `resolution_era.py`.
9. `09_WORKFLOW.md` — SCAFFOLD → wave-critic → production → silent PR fix-loop → merge → tag. Includes opus/sonnet/haiku tier routing per `~/.claude/skills/orchestrator-delivery/SKILL.md`.
10. `10_VERIFIER_PROBES.md` — third-party verifier probe list per phase. Each phase has 10+ concrete assertions (file:line, query, antibody name) for an external opus verifier to confirm/dispute.

## Hard rules for next orchestrator

These are derived from the May 2026 mainline run lessons. They are **structural**, not procedural reminders:

1. **Authority is session-local + operator-cited + on-disk.** Files in `docs/artifacts/Zeus_*_review_*.md` are reference material; the operator must explicitly cite one before its content becomes plan authority. The mainline plan is `docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md` §M ENUM. Substantive intent for Phase 3+ slots comes from operator-pasted GPT analysis dossiers (see `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/AUTHORITY_GPT_ROUND_1_DOSSIER.md`).

2. **Phase ENUM ≠ phase task breakdown.** v4 §M says `"Phases 2–7 (Day0Nowcast, MarketAnalysisVNext, Shoulder, candidate stubs, EvidenceLadder promotion)"`. That is an enum, not a per-track contract. The per-track contract comes from (operator dossier intent) ∩ (current code state on `origin/main`). Do not import per-track contracts from archived review docs the operator did not cite in the running session.

3. **Code paths in archived dossiers WILL be stale.** GPT Round 1 dossier was written multiple PR cycles ago. Every file:line citation must be re-grepped against `git show origin/main:<path>` within 10 min of plan lock. Two paths in v4 had already rotted by the time Phase 0 dispatched; expect more rot after every merge wave.

4. **Authority over file:line for dossier intent**: dossier defines OBJECTS (`Day0BoundState` 6-class enum per §6.2, `ShoulderStrategyVNext` 21-field record per §7.3 verifier recount 2026-05-21, `EffectiveKellyContext` 12-cell property). Files implementing them can move. Object semantics + invariants are the durable surface.

5. **Substantive shoulder + tail content is bounded by physics + math.** `open_shoulder` payoff is open-ended; Kelly haircut must reflect tail-correlation. Day0 high vs low cannot share router (high resolves afternoon, low resolves morning + can reset). DST handling uses `ZoneInfo`, never `timedelta(hours=…)`. Settlement rounding is WMO half-up; calibrated p_market vs `1-YES` complement are different objects. These are not preferences — they're domain-correctness gates.

## Provenance of this package

- Built from full content of session `4beb2fa4-8b85-4a6b-a7db-41f0423290f7` transcript including: operator-pasted GPT Round 1 dossier (~95K), `PHASE_0_V4_ULTRAPLAN.md` 1119-line read, `~/.claude/skills/orchestrator-delivery/SKILL.md`, and current `origin/main` tag list + file-system enumeration.
- Sonnet drift audit `a828c8506e36f3d65` provided original-requirement reconstruction.
- Written by orchestrator before forced compaction; meant to survive compaction as durable on-disk artifact (per universal methodology #2 "translation loss is a thermodynamic limit" — code/types/tests survive ~100%, prose/intent ~20%; encode as structure not prose).

## Verification status

All claims in this package MUST be probed by a fresh-context opus verifier before the next orchestrator dispatches any track. See `10_VERIFIER_PROBES.md` for the per-phase probe contract.

A file in this package that does not have a matching probe in `10_VERIFIER_PROBES.md` is NOT authoritative. The probe is what makes the claim durable.
