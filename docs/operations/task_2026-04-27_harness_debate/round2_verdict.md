# Round-2 Verdict — Zeus Harness Debate (alt-system synthesis)

Judge: team-lead@zeus-harness-debate-2026-04-27
HEAD: 874e00cc0244135f49708682cab434b4d151d25d (main, branch plan-pre5)
Date: 2026-04-28
Round-1 verdict (still authoritative): `verdict.md` (mixed, net-negative tilt on marginal surface)
Round-2 cycle elapsed: ~22 minutes (Phase-1 dispatch → Phase-2 critique → this verdict)

---

## §0 TL;DR

**Both sides arrived at PARTIAL ACCEPT positions and converged on a synthesized middle.** This round did not produce a "winner" — it produced **specification of an actual implementable target** that both proponent and opponent endorse.

**Synthesized target (judge-recommended end-state):**

| Surface | Current (HEAD) | Synthesized target | Reduction |
|---|---|---|---|
| Architecture YAML LOC | 15,234 | **~3,500-4,200** | -73% to -77% |
| AGENTS.md routers | 41 (tracked-non-archive) | **9-11** | -73% to -78% |
| `topology_doctor.py` LOC | 1,630 | **~400-500** | -69% to -75% |
| Anti-drift mechanisms catalogued | 14 | **5 (4 hook-converted + 1 prose SKILL)** | -64% |
| INVs | 30 (10 untested) | **28** (delete INV-16/17, retain rest as YAML pending Python prototype) | -7% (after path-drift fix already shipped) |
| Native `.claude/agents/` | 0 | **3** (critic-opus, verifier, safety-gate) | NEW |
| Native `.claude/skills/` | 0 | **5-7** (phase-discipline, task-boot×N, fatal-misreads post-HK extraction) | NEW |
| Native `.claude/hooks/` | 0 | **2** (pre-edit-architecture, pre-commit-invariant-test) | NEW |
| Type-encoded antibodies | 0 | **1+** (`SettlementRoundingPolicy` ABC + HKO/WMO subclasses) | NEW |
| Migration cost | — | **~110-140h over 9-11 phases** | — |
| 24-month asymptote (both sides) | — | **~1,500-2,000 LOC** | — |

**The headline gap from Phase-1 (5,500 LOC vs 2,800 LOC) was inflated by both sides.** Honest middle, after Phase-2 cross-examination + retraction of over-cuts + concession of under-cuts: **~3,500-4,200 LOC YAML + ~900-1,100 LOC markdown routers + ~1,000 LOC Python types/code = ~5,400-6,300 LOC total harness surface**.

This is approximately **80% reduction from current ~25,000 LOC** harness, agreed by both sides as load-bearing-preserving.

---

## §1 The convergence map (what both sides explicitly endorsed after Phase-2)

These items are **executable** without further debate. They survived adversarial face-value cross-examination from both directions.

### §1.1 Mechanism-level agreements (architecture)

| # | Item | Both endorse | Source |
|---|---|---|---|
| 1 | Hooks > advisory prose for planning-lock + map-maintenance + invariant-test | YES | proponent A1 / opponent §2 / Anthropic Claude Code best practices |
| 2 | Native `.claude/agents/` for critic-opus + verifier + safety-gate | YES | proponent A2 / opponent §2 / Anthropic verbatim |
| 3 | Native `.claude/skills/` for phase-discipline + task-boot + fatal-misreads (post-HK type extraction) | YES | proponent A3 / opponent §2 / Anthropic verbatim |
| 4 | Type-encoded HK HKO antibody (`SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses, ~30-60 LOC) replaces 17 lines of YAML in `fatal_misreads.yaml` | YES | proponent A4 / opponent §3.1 / Fitz "make category impossible" |
| 5 | Drift-checker (`r3_drift_check.py`) extension to `architecture/*.yaml` citation coverage | YES | proponent A5 / opponent §3.4 / round-1 verdict §6.1 #3 (already locked) |
| 6 | `architecture/topology.yaml` deeper cut to ≤500 LOC after audit | YES | proponent A6 / opponent §2 / Cursor docs Rules |
| 7 | `r3/IMPLEMENTATION_PROTOCOL.md` 14-mechanism catalog → ~47-line SKILL.md | YES | proponent A6 / opponent §3.2 |
| 8 | `architecture/task_boot_profiles.yaml` → 7 separate SKILL.md files (on-demand load) | YES | proponent A6 / opponent §2 |
| 9 | `architecture/code_review_graph_protocol.yaml` → inline 6-line note in root AGENTS.md | YES | proponent M6 / opponent §2 |
| 10 | `architecture/docs_registry.yaml` + `script_manifest.yaml` + `test_topology.yaml` generated from filesystem walk + per-file headers | YES | proponent M4+M8 / opponent §2 |
| 11 | DELETE INV-16 + INV-17 (pure prose-as-law) | YES | round-1 verdict §6.1 #1 (already PRUNE_CANDIDATE marked on disk; awaiting operator delete decision) |
| 12 | Path drift fix on 7 INVs (`migrations/` → `architecture/`) | YES | round-1 verdict §6.1 #2 (ALREADY EXECUTED 2026-04-28) |

**12 items, 0 contested.** Both teammates locked these formally in their Phase-2 critique.

### §1.2 Quantitative agreements

| Dimension | Both sides converged on |
|---|---|
| 24-month asymptote | ~1,500-2,000 LOC total harness surface |
| AGENTS.md routers | 9-11 (proponent moved from 18 → 8-12; opponent moved from 5 → 9-11; midpoint = 9-11) |
| `topology_doctor.py` | ~400 LOC after planning-lock/map-maintenance hook-conversion (proponent moved from 700 → 400; opponent moved from 300 → 400-500) |
| Anti-drift catalog | 5 retained mechanisms (mostly hook-converted) |
| Defense-in-depth principle | RESPECTED in live-money trading mode (verdict §1.7 LOCKED reaffirmed) |
| Migration philosophy | Gradualism > whole-replace for live-money operational safety |

### §1.3 Philosophical agreements (this is the substantive convergence)

Both sides explicitly agreed:

1. **Anthropic Claude Code best practices "Ruthlessly prune" + "If CLAUDE.md too long Claude ignores half"** is binding guidance. The current harness violates it; the synthesized target satisfies it.
2. **Hook-determinism > advisory prose** wherever determinism is achievable.
3. **Skills + on-demand loading > YAML manifests + boot-time loading** for task-class domain knowledge.
4. **Type-encoding > YAML antibody** where type discipline is uniform; defense-in-depth (type + YAML) where type discipline is mixed.
5. **Joel Spolsky 2000 "Things You Should Never Do"** applies in spirit (preserve real-world bug-knowledge) but does NOT veto re-encoding into stronger structure.
6. **Fitz Constraint #2 (translation loss thermodynamic)** applies to BOTH the current and proposed harness; harness shape doesn't escape it; smaller surface = higher per-agent survival rate for rotating agents.
7. **The R3 multi-region parallel debate apparatus is correctly being retired** (operator already started per `RETROSPECTIVE_2026-04-26.md:61-69`).

---

## §2 Remaining bounded disagreements (small)

After Phase-2 mutual concessions, the genuine remaining disagreements are:

### §2.1 Disagreement D1: ~1,200 LOC YAML delta on 3 specific manifests

| Manifest | Proponent retains (final) | Opponent's W1 challenge |
|---|---|---|
| `topology.yaml` | ~500 LOC (after audit) | Replace with `architecture/zones.py` + `architecture/runtime_modes.py` + small `architecture/topology_navigator.py` script |
| `module_manifest.yaml` | ~500 LOC (after merge with source_rationale) | Replace with package `__init__.py` runtime-introspectable registries |
| `history_lore.yaml` | ~600 LOC (75% reduction + archive rest) | Sunset by 90-day-no-catch audit |

**Total contested**: ~1,600 LOC YAML retention vs Python-types-replacement.

**Resolution path**: this is genuinely **operator-decision territory** (not debate-resolvable). The decision depends on:
- Whether operator commits to mypy-strict-everywhere (precondition for type-encoding strength)
- Whether the operator's mental model is more anchored on YAML manifests or Python types
- Whether the trading-domain knowledge in these 3 manifests has produced a catch in the last 90 days (concrete audit, not debate)

**Judge recommendation**: do the 90-day-no-catch audit empirically. Sections that have not produced a catch → opponent's replacement applies. Sections with catch evidence → proponent's retention applies. Decision can be made section-by-section, not file-by-file.

### §2.2 Disagreement D2: INV format (YAML vs Python decorators with `@enforced_by`)

Proponent H1 holds: 28 INVs stay as YAML pending working `@enforced_by` decorator prototype that demonstrates strictly stronger enforcement than current YAML+tests setup.

Opponent §4 over-cut #3 retracted: 12 schema-backed INVs should remain as YAML (schema IS the law); only the 5 semgrep-backed + 13 script/doc/spec-backed could be re-encoded.

**Resolution path**: build the `@enforced_by` decorator prototype as a separate small experiment (estimated 8-12h, not 24h as opponent originally claimed). If it strictly dominates current YAML+tests on the 71-pass baseline, migrate. If not, hold YAML. **This is empirically decidable, not debate-decidable.**

### §2.3 Disagreement D3: Migration phase count + cost (95-105h vs 130-160h)

Proponent H3: 9-10 incremental phases ~95-105h (after A1-A6 additions).
Opponent §6: ~130-160h (proponent's 85-90h gradualism + opponent's P4 type encoding + P6 source_rationale → docstrings + P9 hooks setup).

**The honest read**: opponent's 130-160h includes additions proponent already conceded (A1-A6). The numbers are converging. Real range: **110-140h**.

**Resolution path**: scope per-phase as packets under planning-lock + critic gate (existing harness mechanism); each phase has rollback. Cost emerges from the audit, not from debate.

### §2.4 Disagreement D4: MERGE discipline

Opponent W2 challenge: proponent's MERGE pattern (e.g., topology.yaml + topology_schema + zones + runtime_modes → one 800-LOC file) is file-count cosmetics, NOT attention-surface reduction unless accompanied by sectioned read-order discipline.

Proponent did not formally rebut this in critique §3.

**Judge ruling**: opponent W2 stands. Any MERGE in the implementation must specify per-task-class read order (e.g., "for trading-domain edits, read sections X+Y; for governance edits, read sections Z+W"). Otherwise the merge is cosmetic.

---

## §3 Judge's weighing (per round-1 TOPIC.md "what a win looks like" criteria)

| Criterion | Round-2 outcome |
|---|---|
| 1. Engagement with strongest claim | TIE — both engaged at face value, with multiple itemized concessions |
| 2. External evidence concreteness | TIE — both cited Anthropic Claude Code best practices verbatim with DIFFERENT quotes for cross-validation; opponent added Joel Spolsky; proponent added LangGraph + Aider |
| 3. Repo evidence specificity | Slight proponent edge — §5.2 catch-preservation map is more concretely traceable than opponent's §3 type-encoding claims (some untested) |
| 4. Acknowledgment of trade-offs | OPPONENT EDGE — opponent retracted original 2,800 LOC target with 3 honest over-cut admissions; proponent updated 5,500 → 4,000 with explicit A1-A6 acceptances. Both admitted error; opponent admitted larger error magnitude (built more credibility) |
| 5. Survival under cross-examination | TIE — both sides' final positions are weaker than their opening positions, and both narrowed honestly |

**Aggregate**: this round produced **synthesis, not victory**. Neither "in-place reform" nor "whole-system replace" stands as proposed. The synthesized middle is the correct outcome.

---

## §4 Action plan (graded by readiness)

### §4.1 READY-TO-EXECUTE today (no further debate, no operator decision needed beyond resource allocation)

These are §1.1 items where both sides explicitly agree, and the change is mechanical or pre-bounded.

| # | Action | Estimated effort | Status |
|---|---|---|---|
| 1 | Execute INV-16/17 deletion (currently PRUNE_CANDIDATE marker added) | 0.5h | PENDING operator decide: delete vs rewrite vs upgrade with NC verification test |
| 2 | Path drift fix on 7 INVs | DONE 2026-04-28 | COMPLETE |
| 3 | Extend `r3_drift_check.py` to `architecture/*.yaml` citation coverage | 2-3h | READY |
| 4 | Encode HK HKO as `SettlementRoundingPolicy` ABC + subclasses | 2-4h | READY (specs in opponent's §3.1) |
| 5 | Create 2 hooks: `pre-edit-architecture.sh`, `pre-commit-invariant-test.sh` | 3-4h | READY |
| 6 | Create 3 native subagent files in `.claude/agents/`: critic-opus.md, verifier.md, safety-gate.md | 2-3h | READY (extract from existing prompt patterns) |
| 7 | Convert `r3/IMPLEMENTATION_PROTOCOL.md` 14-mechanism catalog → 47-line SKILL.md | 3-4h | READY |
| 8 | Inline `architecture/code_review_graph_protocol.yaml` 6-line note into root AGENTS.md → DELETE the 62-line YAML | 1h | READY |

**Subtotal "ready today"**: ~14-20h, all mechanical, both sides endorsed.

### §4.2 NEEDS OPERATOR DECISION (small disagreement remaining; judge recommendation provided)

| # | Decision | Recommendation |
|---|---|---|
| 9 | INV-16/17 fate | DELETE (per round-1 verdict §6.1 #1; both sides agree the negative_constraints citation alone is insufficient enforcement) |
| 10 | `topology.yaml` 90-day audit + Python-replace decision per section | Run audit empirically; section without 90-day catch → replace; section with catch → retain (≤500 LOC after) |
| 11 | `module_manifest.yaml` retain vs `__init__.py` registries | Recommend Python registries (runtime-introspectable) for active packages; retain ~150 LOC YAML for cross-package metadata |
| 12 | `history_lore.yaml` archive vs delete-by-90-day-audit | RECOMMEND archive (proponent H4 + Fitz Constraint #3); the cost of archive vs delete is small (don't load at boot vs gone), the cost of premature delete is unbounded |
| 13 | INV format experiment: build `@enforced_by` decorator prototype 8-12h | RECOMMEND: do it. Empirical answer settles D2. |

**Subtotal "needs decision"**: ~5 small operator decisions; judge provided a recommendation for each.

### §4.3 LARGER WORK (in-place gradualism, recommended over whole-replace)

| # | Phase | Effort | Notes |
|---|---|---|---|
| P1 | Hooks + native agents + native skills setup | ~10-15h | §4.1 items 5+6+7 |
| P2 | `topology.yaml` audit + Python replacement (per-section) | ~15-25h | Per §4.2 #10 audit |
| P3 | `module_manifest.yaml` → package registries | ~10-15h | Per §4.2 #11 |
| P4 | `source_rationale.yaml` → inline docstrings (irreversible; staged) | ~16-20h | Opponent's P6 with proponent's H3 staging |
| P5 | `task_boot_profiles.yaml` → 7 SKILL.md | ~6-10h | Per §1.1 #8 |
| P6 | `r3_drift_check.py` extension + `code_review_graph_protocol.yaml` inline + `docs_registry.yaml` generation + `script_manifest.yaml` generation + `test_topology.yaml` generation | ~10-15h | Per §1.1 #5+9+10 |
| P7 | Scoped AGENTS.md cull from 41 → 9-11 | ~10-15h | Per §1.2 routers convergence |
| P8 | Type-encoded antibody migration: HK HKO + (optional) 1-2 more | ~6-12h | Per §4.1 #4 |
| P9 | INV format experiment (`@enforced_by` decorator prototype) | ~8-12h | Per §4.2 #13 |
| P10 | Validation: simulated regression replay + 71-pass baseline preservation | ~10-15h | Both sides' acceptance criteria converge here |

**Total**: ~110-150h across 10 phases. Distributed over 4-8 weeks at part-time engineer + operator review cadence.

This is the synthesized migration plan. Both sides converged here; gradualism (proponent) provides operational safety; deeper cuts (opponent) provide the asymptote-approach.

---

## §5 What round-2 did NOT resolve (deferred to operator-execution or round-3 if pursued)

1. **The INV format question (D2)** is empirically decidable; no further debate would help. The decorator prototype IS the test.

2. **The `topology.yaml` per-section retention question (D1)** is empirically decidable via 90-day catch audit; no further debate would help.

3. **The MERGE read-order discipline (D4)** is an implementation detail; the principle is locked (opponent W2 stands), the per-merge decisions are operator-implementer territory.

4. **Forward-looking model capability question (round-1 verdict §6.4 #2)**: at what model capability point does even the synthesized harness lose marginal value? Both proposals' §6 converged on ~1,500-2,000 LOC asymptote at GPT-6/Opus-5 generation; the harness floor is the type system, not the model. **No further debate likely productive**; this is a wait-and-see empirical question.

5. **Whether the operator would actually execute this plan** is operator-decision, not debate territory.

**My recommendation**: round-2 verdict is FINAL. No round-3 unless the operator wants a specific narrow question litigated (e.g., "should we mypy-strict-everywhere as precondition for type-encoding?" — that would be a focused round-3).

---

## §6 The one most important finding of round-2

**The headline gap collapsed under cross-examination.** Phase-1 had proponent at 5,500 LOC and opponent at 2,800 LOC — a ~2,700 LOC gap that looked like a fundamental architectural disagreement.

After Phase-2 mutual concessions:
- Proponent moved to ~4,000 LOC (accepted A1-A6: hooks, native agents, native skills, type-encoded antibodies, drift-checker, deeper cuts)
- Opponent moved to ~3,500-3,800 LOC (retracted §2.1: 5 routers too aggressive, fatal_misreads delete too aggressive, all-INVs-to-Python too aggressive)

**Real remaining gap: ~200-500 LOC + INV format choice + topology.yaml replacement strategy + history_lore archive policy.**

This is operator-implementer territory, not debate territory. The "is the harness net-positive vs net-negative" round-1 question and the "in-place vs whole-replace" round-2 question both **collapse to the same operationally-clear answer**: **prune to ~5,000-6,000 LOC short-term harness via gradualist 110-140h migration; converge to ~1,500-2,000 LOC at 24-month asymptote**.

The user's original hypothesis ("modern Opus 4.7 / GPT 5.5 eat scaffolding for breakfast") was correct in direction (the current harness IS over-built for current model capability) but wrong in degree (the load-bearing core is real, ~5K-6K LOC not zero). Both teammates independently arrived at this synthesis.

---

## §7 Process notes (judge-side, not part of substantive verdict)

- **Round-2 elapsed**: ~22 minutes (Phase-1 dispatch ~1 min → Phase-1 done ~10 min → Phase-2 dispatch <1 min → Phase-2 done ~10 min → this verdict).
- **Total debate elapsed**: ~36 minutes (round-1 ~14 min + round-2 ~22 min).
- **Token discipline maintained**: A2A turns ≤500 char; converged statements ≤200 char; disk-first; ≥2 NEW WebFetch per phase.
- **Cumulative external sources**: 8 (Anthropic Jun13/Sep29 2025 + Cognition Jun12 2025 + Contrary Cursor Dec11 + Anthropic Sonnet 4.5 + Cursor docs Rules + Anthropic Claude Code best practices + Aider repo-map + LangChain LangGraph + Joel Spolsky 2000). All cited verbatim with URLs and timestamps.
- **Cumulative repo evidence**: 30+ INV citations grep-verified at HEAD `874e00c`; Z2 retro mechanism attribution check; AGENTS.md count methodology cross-verified between both sides + judge.
- **Anti-rubber-stamp discipline**: both teammates retracted significant portions of their opening positions under Phase-2 cross-examination. This is the signature of high-quality adversarial debate (per round-1 TOPIC.md L72-75).
- **Both teammates LONG-LAST**; persist for any further dispatch.

---

## §8 Status

**Round-2 verdict: FINAL.**

The synthesized middle (~5,000-6,000 LOC short-term, ~1,500-2,000 LOC at 24-month asymptote, gradualist migration over 110-140h, hook + native subagent + native skill + type-encoded antibody pattern) is endorsed by both proponent and opponent and the judge.

The ~14-20h of "ready today" mechanical work in §4.1 can begin without further debate.

The ~5 operator decisions in §4.2 have judge recommendations provided.

The ~110-150h of gradualist migration in §4.3 is the synthesized plan.

Round-3 is not recommended unless operator wants a narrow question litigated. The substantive disagreement has been narrowed to the operator-decision residue.

End of round-2 verdict.
