# Zeus Harness Evolution — Deep Plan
## Synthesized from R1+R2 debate; canonical execution roadmap

Created: 2026-04-28
HEAD anchor: `874e00cc0244135f49708682cab434b4d151d25d` (main, branch plan-pre5)
Author: team-lead@zeus-harness-debate-2026-04-27 (judge)
Status: **AUTHORITATIVE** for harness pruning work; supersedes ad-hoc decisions on §4.1-§4.3 items.

---

## §0 TL;DR

**Goal**: Reduce Zeus harness from current ~25K LOC to ~5K-6K LOC short-term and ~1.5-2K LOC at 24-month asymptote, **WITHOUT** losing the empirical catch-rate validated in round-1 evidence (Z2 retro 6/6, V2 BUSTED-HIGH 5+8, HK HKO antibody, all 5 verified semgrep rules, 12 schema-backed INVs).

**Method**: 3-tier gradualist migration — endorsed by both proponent-harness and opponent-harness after 2 rounds of adversarial debate (~36 min, 8 external sources, 30+ repo evidence cites, mutual face-value cross-examination).

**Critical path (this plan)**:
```
Tier 1 (executor, in flight, ~14-20h)
  → operator decides 5 §4.2 items (~1-2 days elapsed)
    → Tier 2 quick wins (~30-40h)
      → Tier 3 gradualist migration (~80-110h over 4-8 weeks)
        → 6-month re-audit (90-day catch evidence)
          → 24-month asymptote check
```

**Total engineer effort**: ~110-150h. Distributed at part-time cadence + operator review = 6-10 calendar weeks. Break-even: ~6 months of normal Zeus session activity (per opponent's ROI math, accepted by proponent).

**This plan is NOT**: a clean-slate rewrite (Spolsky-rejected); a hypothetical greenfield design; a forward-projection beyond 24 months.

---

## §1 Source authority

| Document | Role |
|---|---|
| `verdict.md` (round-1) | LOCKED concessions §1; subtraction agreements §6.1; deferred §6.4 |
| `round2_verdict.md` (round-2) | Synthesized end-state §0; convergence map §1; remaining bounded disagreements §2 |
| `evidence/proponent/round2_proposal.md` + `round2_critique.md` | In-place reform spec; A1-A6 acceptances; H1-H4 holds |
| `evidence/opponent/round2_proposal.md` + `round2_critique.md` | Whole-replace spec (now retracted to ~3,500 LOC); §3.1 HKO type code; §3.2 47-line SKILL.md template; §3.4 drift-checker diff |
| `judge_ledger.md` | Empirical scoresheet of all numerical claims (judge-verified) |

---

## §2 Three execution tiers

### §2.1 Tier 1 — Mechanical fixes (12 items, ~14-20h)

**Status**: Executor `executor-harness-fixes@zeus-harness-debate-2026-04-27` (opus, longlast) currently booting / executing in 4 batches. See judge_ledger §"Executor phase" for batch composition + status.

| # | Item | Batch | Risk | Target file(s) | Acceptance | Rollback |
|---|---|---|---|---|---|---|
| 1 | DELETE INV-16, INV-17 | D | LOW | architecture/invariants.yaml | 28 INVs remain; pytest test_architecture_contracts 71-pass preserved | git revert one commit |
| 2 | Path drift fix on 7 INVs | DONE 2026-04-28 | LOW | architecture/invariants.yaml | grep `migrations/...` returns 0 | DONE |
| 3 | TOPIC.md count correction | DONE 2026-04-28 | LOW | TOPIC.md | factual fix | DONE |
| 4 | Extend r3_drift_check.py to architecture/*.yaml citations | B | LOW | scripts/r3_drift_check.py (+~50 LOC) | drift-checker now flags non-existent paths in architecture/ | git revert |
| 5 | Encode HK HKO as type subclasses | C | HIGH (K0_frozen) | src/contracts/settlement_semantics.py (+~60 LOC); fatal_misreads.yaml (HK row reference); tests/test_settlement_semantics.py (+1 relationship test) | TypeError on HKO+WMO mixing; existing tests preserved | git revert; YAML antibody still in place pending delete |
| 6 | 2 hooks (pre-edit-architecture, pre-commit-invariant-test) | B | MED | .claude/hooks/ (NEW dir) | architecture/** edits without plan_evidence are blocked; commits without invariant-test passing are blocked | unset hooks via .git/hooks symlink |
| 7 | 3 native subagent files (.claude/agents/) | A | LOW | .claude/agents/critic-opus.md, verifier.md, safety-gate.md (NEW) | files exist; can be invoked via Agent tool | delete files |
| 8 | 5-7 native skills (.claude/skills/) | partial in A,B | LOW | .claude/skills/zeus-phase-discipline/SKILL.md (47 LOC); .claude/skills/zeus-task-boot/* (7 SKILL.md); .claude/skills/zeus-fatal-misreads/SKILL.md (post-HK extraction, ~120 LOC) | skills loadable on keyword + via /skill-name | delete files |
| 9 | Inline code_review_graph_protocol.yaml (62 lines) → root AGENTS.md (6 lines) + DELETE the YAML | A | LOW | AGENTS.md (+6 lines); architecture/code_review_graph_protocol.yaml (DELETE) | root AGENTS.md self-contained on graph protocol; topology_doctor reference if any updated | git revert |
| 10 | r3/IMPLEMENTATION_PROTOCOL.md (465 lines, 14 mechanisms) → 47-line SKILL.md | A | LOW-MED | .claude/skills/zeus-phase-discipline/SKILL.md (NEW); IMPLEMENTATION_PROTOCOL.md retained (operator delete decision later) | SKILL has 5 essential mechanisms; preserves 4-of-14 named in Z2 retro | git revert |
| 11 | docs_registry.yaml + script_manifest.yaml + test_topology.yaml → filesystem-walk auto-generated | (Tier 2) | MED | architecture/* (DELETE 3 files); scripts/regenerate_registries.py (NEW ~200 LOC) | regenerated registries diff-equivalent to current static yaml on the relevant fields | git revert script + restore yaml from git history |
| 12 | task_boot_profiles.yaml (360 LOC, 7 profiles) → 7 SKILL.md files | (Tier 2) | MED | .claude/skills/zeus-task-boot/{profile_name}/SKILL.md ×7; architecture/task_boot_profiles.yaml (DELETE) | each SKILL preserves required_proofs + forbidden_shortcuts of its source profile | git revert |

**Tier 1 items 1, 4-10**: in scope for executor (Batches A-D).
**Tier 1 items 11, 12**: deferred to Tier 2 (require small audit + script-write that exceeds "mechanical fix" scope).

---

### §2.2 Tier 2 — Operator decisions + medium work (5 §4.2 items + 2 promoted from Tier 1, ~30-50h)

| # | Item | Judge recommendation | Decision criteria | Default if no decision |
|---|---|---|---|---|
| 13 | INV-16/17 fate (executor will DELETE per recommend) | DELETE | Both round-1 §6.1 #1 + round-2 §4.2 #9 agree negative_constraints alone insufficient | DELETE (executor proceeds unless operator says otherwise within 24h) |
| 14 | topology.yaml retention strategy | Per-section 90-day-no-catch audit | Section without catch in 90 days → replace with Python types (architecture/zones.py + architecture/runtime_modes.py + topology_navigator.py); section with catch → retain ≤500 LOC after audit | Run audit script (TODO: write a section-level catch-history scanner ~6h); decide section-by-section |
| 15 | module_manifest.yaml replacement | Python registries for active packages; ~150 LOC YAML for cross-package metadata | Active = touched in last 90 days | Migrate 9-12 packages to `__init__.py` registry; retain residual YAML |
| 16 | history_lore.yaml policy | Archive (proponent H4 + Fitz #3) | Cost of archive << cost of premature delete | Archive `docs/archives/history_lore_extended_2026-04.md`; retain ~600 LOC active in architecture/ |
| 17 | @enforced_by decorator prototype | BUILD IT (8-12h experiment) | If prototype strictly dominates current YAML+tests on 71-pass baseline → migrate; else hold YAML | Build prototype; empirical decide |
| 11* | Generate registries from filesystem walk | ~~DO IT~~ **AUDIT-AND-KEEP per Phase 2 erratum** | ~~Auto-generation removes a maintenance surface~~ Phase 2 audit found 3 manifests are 99% complete + INTENTIONAL CURATION (95% load-bearing hand-curated metadata). **Auto-gen would discard intent.** | Audit script `scripts/regenerate_registries.py` 228L written but used as PR-gate (--diff mode) NOT --apply. See round2_verdict §9.2 erratum + methodology §5.Z case study |
| 12* | task_boot_profiles → 7 SKILL.md | DO IT | On-demand load saves ~3-5K boot tokens per session | Migrate 7 profiles |

**Tier 2 sequencing**: items 13-17 can run in any order; 11* and 12* are Tier 1 → Tier 2 promotions.

**Tier 2 estimated effort**: 30-50h depending on decision speeds + audit complexity.

---

### §2.3 Tier 3 — Gradualist migration (10 phases, ~80-110h, 4-8 weeks calendar)

**Per round-2 verdict §4.3**, structured as packet-per-phase under planning-lock + critic gate. Each phase has rollback at packet boundary.

| Phase | Item | Effort | Depends on | Rollback boundary |
|---|---|---|---|---|
| P1 | Hooks + native agents + native skills setup (Tier 1 #6+7+8 if not yet) | 10-15h | Tier 1 done | per-file revert |
| P2 | topology.yaml audit + Python replacement (per-section, decisions from Tier 2 #14) | 15-25h | Tier 2 #14 decision | per-section retain or restore |
| P3 | module_manifest.yaml → package registries (per Tier 2 #15) | 10-15h | Tier 2 #15 decision | per-package revert; YAML retained until last package done |
| P4 | source_rationale.yaml → inline docstrings (irreversible; staged) | 16-20h | none | per-file YAML→docstring; retain YAML stub during transition |
| P5 | task_boot_profiles → 7 SKILL.md (Tier 2 #12*) | 6-10h | Tier 1 done | per-profile revert |
| P6 | Auto-gen registries (Tier 2 #11*) + drift-checker extension polished (Tier 1 #4) + code_review_graph inline (Tier 1 #9) + docs_registry generation | 10-15h | Tier 1 #4+9 done | per-file revert |
| P7 | Scoped AGENTS.md cull from 41 → 9-11 (per round-2 §1.2 convergence) | 10-15h | Tier 2 #14+#15 done (some routers obsoleted by Python types) | per-router restore from git history |
| P8 | Type-encoded antibody migration: HK HKO done in Tier 1 #5; consider 1-2 more candidates (per round-2 verdict W3 T1: only where type discipline is uniform) | 6-12h | Tier 1 #5 + Tier 2 #17 (mypy-strict policy) | per-antibody revert; YAML antibody preserved in defense-in-depth pattern |
| P9 | INV format migration (per Tier 2 #17 result) | 8-12h (only if prototype dominates) | Tier 2 #17 prototype + 71-pass preserved | per-INV revert; YAML retained until prototype absorbs each |
| P10 | Validation: simulated regression replay + Z2-class pattern reproduction + 71-pass baseline + planning-lock receipts ledger | 10-15h | All P1-P9 done | NO ROLLBACK — this is verification only; failures roll back specific phases |

**Tier 3 critical path**: P1 → P5 → P6 (in parallel with P2/P3/P4 if multiple engineers); P7 depends on P2+P3 partial; P8 depends on P1+Tier 2 #17; P9 conditional on Tier 2 #17 outcome; P10 last.

**Tier 3 estimated total**: 80-110h pure engineering + ~20-30h operator review + ~10h validation = ~110-150h elapsed effort over 4-8 weeks at part-time cadence.

---

## §3 Sequencing & calendar

```
Week 0 (now): Tier 1 in flight (executor, 4 batches sequential)
              → Operator decides 5 §4.2 items in parallel (1-2 days)
              → COMMIT Tier 1 + Tier 2 decisions as packet milestone

Week 1-2:    Tier 2 execution
              → P1 setup (hooks, agents, skills if not yet)
              → @enforced_by prototype (Tier 2 #17) starts
              → topology.yaml audit script written + run

Week 2-4:    Tier 3 P2 + P3 + P4 (parallelizable; touching different YAMLs)
              → Each phase as its own packet under planning-lock + critic gate

Week 4-6:    Tier 3 P5 + P6 + P7 (depends on earlier)
              → Scoped AGENTS.md cull as routers become obsolete

Week 6-8:    Tier 3 P8 + P9 + P10 (validation week)
              → Simulated regression replay against pre-migration HEAD
              → 71-pass baseline preservation gate
              → 90-day forward-monitoring period begins

Month 6:      Re-audit: did the synthesized harness catch what current would?
              Section-level catch-history audit against 24-month asymptote target

Month 12-24: Asymptote convergence: ~1,500-2,000 LOC total harness
              Capability-trigger reviews when GPT-6/Opus-5 ships
```

**Parallelism**: P2/P3/P4 are independent (different YAMLs); P5/P6 are independent. Single engineer + part-time operator can do these serially in ~6 weeks; two engineers can compress to ~4 weeks.

**Critical-path dependencies**: Tier 1 executor batches must close before Tier 2 begins. Tier 2 #14 (topology.yaml audit) blocks P2; Tier 2 #15 (module_manifest decision) blocks P3.

---

## §4 Acceptance criteria

### §4.1 Tier 1 acceptance

- [ ] All 12 mechanical items either completed or properly tracked in judge_ledger as deferred
- [ ] pytest tests/test_architecture_contracts.py passes 71+ tests (baseline preservation)
- [ ] topology_doctor planning-lock check passes for all architecture/** edits with `--plan-evidence round2_verdict.md`
- [ ] No git commits made without explicit operator authorization
- [ ] Executor sends ALL_BATCHES_DONE to team-lead with delta summary

### §4.2 Tier 2 acceptance

- [ ] All 5 operator decisions made (deletion, audit policy, replacement, archive, prototype)
- [ ] @enforced_by decorator prototype either dominates baseline (then proceed Tier 3 P9) or fails (then INVs stay YAML)
- [ ] topology.yaml audit script written; section-level catch-history report produced
- [ ] All Tier 2 changes committed as discrete packets with planning-lock receipts

### §4.3 Tier 3 acceptance (per phase)

- [ ] Phase boot evidence written + critic-opus + verifier dispatched per IMPLEMENTATION_PROTOCOL pattern
- [ ] pytest baseline preserved or new failures explicitly attributed and accepted
- [ ] planning-lock receipt cited in commit message
- [ ] Per-phase rollback path documented and tested in dry-run

### §4.4 Overall acceptance (after Tier 3 P10)

- [ ] Total harness surface ≤ 6,500 LOC (target ~5,000-6,000 LOC; 6,500 is upper acceptance)
- [ ] AGENTS.md routers ≤ 12 (target 9-11)
- [ ] topology_doctor.py ≤ 500 LOC (target 400)
- [ ] Anti-drift mechanisms ≤ 6 (target 5)
- [ ] All 12 round-2 verdict §1.1 agreements implemented
- [ ] Z2-class regression replay in fake-venue simulation: 6/6 catches reproduced by post-migration harness
- [ ] V2-class WebFetch + critic dispatch pattern preserved
- [ ] HK HKO mixing raises TypeError (relationship test)
- [ ] No new pre-existing test failures introduced
- [ ] Operator self-report: "I can hold the post-migration harness in my head"
- [ ] Forward-monitoring 90-day catch log started

---

## §5 Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Tier 1 executor batches break pre-existing test (the 2 evaluator.py failures) | LOW | LOW | Pre-existing failures are NOT executor's; baseline is 71 pass; failures must be attributed |
| Tier 1 #5 HK HKO type encoding has gap (round-2 W3 T1 critique) | MED | HIGH (live-money) | Defense-in-depth: keep YAML antibody marked TYPE_ENCODED; relationship test asserts mixing raises; mypy-strict policy decision (Tier 2 #17) |
| Tier 2 #14 topology.yaml audit reveals catches the type-replacement would lose | MED | MED | Section-level decision; catch-positive sections retain as ≤500 LOC YAML |
| Tier 3 P4 source_rationale.yaml → docstrings is irreversible (opponent §9 admission) | HIGH | MED | Stage migration; retain YAML stub during transition; per-file rollback for ~30 days |
| Tier 3 P9 INV format migration breaks 71-pass baseline | LOW | HIGH | Conditional on Tier 2 #17 prototype passing; if prototype fails, P9 doesn't happen |
| Operator-bandwidth saturation during 4-8 week migration | MED | MED | Per-phase packets with operator review only at packet boundaries; not per-edit; total operator review ~20-30h spread across weeks |
| New Z2-class implementation defects emerge during migration window | MED | HIGH (the migration touches the catch mechanisms) | Critic-opus + verifier dispatched per phase; planning-lock + invariant-test hooks deterministic |
| Mid-migration model upgrade (Opus 5 / GPT 6 ships during weeks 4-8) | LOW | LOW (the plan converges to capability anyway) | Asymptote target 1,500-2,000 LOC is capability-aligned; mid-migration upgrade just accelerates convergence |
| Forward-asymptote bet wrong (capability grows slower than expected) | LOW | LOW | Plan delivers ~5K-6K short-term; asymptote convergence is forward, not blocking |
| Operator interrupts migration partway through | MED | LOW | Each phase is its own packet with rollback boundary; partial migration is stable state, not broken state |

---

## §6 Forward asymptote roadmap

Both proponent §6.3 and opponent §6 converged on **~1,500-2,000 LOC harness floor at GPT-6/Opus-5 generation (12-24 months)**. The harness floor is the type system, not the model.

### §6.1 Asymptote-target end-state (24-month)

| Component | LOC |
|---|---|
| 1 root AGENTS.md (small, clean) | ~200 |
| 5-7 scoped AGENTS.md (irreducible trading-domain knowledge) | ~500-700 |
| Type-encoded antibodies (`SettlementRoundingPolicy`, `LifecyclePhase`, `RiskLevel`, etc., expanded from 1 → ~5) | ~300-500 |
| 5 `.claude/agents/` files (critic-opus, verifier, safety-gate, executor, document-specialist) | ~250 |
| 7-10 `.claude/skills/` files (phase-discipline, task-boot×N, fatal-misreads, calibration-domain, settlement-domain) | ~700-1,000 |
| 3-5 `.claude/hooks/` (deterministic gates) | ~150-250 |
| Residual YAML (12 schema-backed INVs as documentation; 9-11 negative_constraints; small antibody set for non-type-uniform cases) | ~300-500 |
| `topology_doctor.py` minimal navigator | ~300 |
| **TOTAL** | **~2,700-3,650 LOC** |

This brackets the "1,500-2,000 LOC asymptote" claim from both teammates. Honest reading: 1,500 LOC is aspirational lower bound; 2,000 LOC is more realistic; 3,000 LOC is achievable.

### §6.2 Capability monitoring triggers

Re-evaluate harness sizing when ANY of the following:

1. New model generation ships (Opus 5 / GPT 6) — re-test the asymptote claim
2. 90-day catch log shows ZERO catches attributed to a specific manifest section → trigger sunset of that section
3. New domain added to Zeus (e.g., new venue, new strategy family) — assess whether new harness mechanisms are needed
4. Operator reports "I no longer hold this in my head" — trigger pruning round
5. Cumulative agent process-fault rate > 1 per 12h sustained — review whether harness is producing failures it then catches

---

## §7 Open questions for round-3 (or other adversarial review)

Three candidate topics for genuine adversarial debate (selected for: actionable output + multi-valid-approach + non-trivial empirical content):

### §7.1 Candidate A — Edge vs Safety capital allocation

**Question**: Given finite operator + engineer hours, what's the right allocation between (a) remaining harness pruning (~110-150h, this plan) vs (b) Dominance Roadmap edge work (round-1 verdict §4: EDGE_OBSERVATION + CALIBRATION_HARDENING + ATTRIBUTION_DRIFT + LEARNING_LOOP + WS_OR_POLL_TIGHTENING)?

**Why debate-valuable**: directly answers the "now that safety is sized, what's next?" question; both sides will produce concrete sequencing proposals; the answer materially affects Zeus's live-trading P&L.

### §7.2 Candidate B — Dominance Roadmap packet sequencing

**Question**: Among the 5 packets deferred in round-1 verdict §4, which is highest-leverage for Polymarket weather trading specifically? Sequencing options: edge-observation-first (measurement before optimization) vs calibration-hardening-first (the Platt model is the bottleneck) vs ws-or-poll-tightening-first (operational latency wins).

**Why debate-valuable**: each packet has 30-60h of work; choosing wrong packet first is significant misallocation; the answer requires market-microstructure reasoning + alpha-decay analysis + operational tradeoffs.

### §7.3 Candidate C — Forward-asymptote bet (model capability assumption)

**Question**: Should Zeus's harness be designed for current Opus 4.7 / GPT 5.5 capability, 6-month projection, or 12-24 month asymptote? Each timeframe implies different architecture (more vs less curation; type-system depth; operator-loop intensity).

**Why debate-valuable**: forward-looking; engages with empirical capability benchmarks (SWE-bench, long-context retrieval, multi-step agent benchmarks); answer affects every component sizing decision; both teammates will pull on different external evidence; honest reading of model capability trajectory.

### §7.4 Candidate D — Harness governance evolution

**Question**: Now that the harness is being right-sized, what governance mechanism prevents re-bloat over the next 24 months? Decision criteria for adding NEW antibodies / mechanisms / routers; pruning cadence; metrics for harness health.

**Why debate-valuable**: the "harness producing the failures it then catches" round-1 finding implies governance discipline matters; without it, the synthesized middle drifts back to the cathedral; both sides will propose specific governance mechanisms (immutable-by-default? ratchet? scheduled audits?); answer is durable cross-session.

---

## §8 Maintenance & governance

Recommended (not yet adopted; see §7.4 for debate territory):

1. **Quarterly section-level catch-history audit** (script: `scripts/section_catch_audit.py` ~50 LOC) — for every architecture/* manifest section, attribute or null-attribute catches in the last 90 days. Sections with zero catches → sunset candidate.

2. **New-mechanism gate**: any proposed new architecture/*.yaml or new AGENTS.md router must cite (a) the specific catch-class it prevents, (b) why a hook / type / SKILL cannot do the same, (c) projected maintenance cost.

3. **Drift-checker as CI gate** (post Tier 1 #4): r3_drift_check.py runs on every PR touching architecture/*.yaml; PR-level fail on broken citations.

4. **Antibody type-encoding ratchet**: when a YAML antibody can be type-encoded, do it; don't add new YAML antibodies for cases that admit type encoding.

5. **Operator self-report cadence**: monthly "I can hold this in my head" check; if NO, trigger pruning round.

6. **Capability re-evaluation**: every 6 months OR on new model generation, re-run the round-1+round-2 debate (now ~36 min total) against current model + harness state.

---

## §9 What this plan does NOT cover

- **Tier 4 + Dominance Roadmap**: round-1 verdict §4 deferred 5 packets (EDGE_OBSERVATION, CALIBRATION_HARDENING, ATTRIBUTION_DRIFT, LEARNING_LOOP, WS_OR_POLL_TIGHTENING). Each warrants its own debate cycle. **§7.1 candidate A would address sequencing if pursued**.

- **Trading engine refactor**: the harness debate is about PROCESS infrastructure, not the trading engine itself. The 173 src/ + 241 tests + 135 scripts are mostly out of scope (except where Tier 1 #5 / Tier 3 P4 / P8 touch them additively).

- **Multi-venue expansion**: if Zeus expands beyond Polymarket, new venue adapters carry harness needs not addressed here.

- **Operator workflow changes**: this plan assumes single-operator + rotating Claude/Codex agents. If team expands, governance needs revisit (see §7.4).

- **Daemon / runtime changes**: live trading daemon `src/main.py` and cycle_runner are not touched; only the documentation/manifest layer governing them.

---

## §10 Status & next steps

**Today (2026-04-28)**:
- ✅ R1 + R2 verdicts FINAL on disk
- ✅ 4 mechanical fixes already executed (TOPIC.md, 7-INV path drift, INV-16/17 PRUNE_CANDIDATE markers — judge ledger §"Post-verdict mechanical fixes")
- 🔄 Executor `executor-harness-fixes` BOOTING for Tier 1 Batches A-D
- ⏳ Operator decides: §7 round-3 topic OR proceed with Tier 2 directly OR pause

**Within 24h**:
- Operator answers 5 §4.2 small decisions (or accepts judge defaults)
- Executor completes Batches A-D; reports ALL_BATCHES_DONE
- Operator commits Tier 1 + Tier 2 decisions as packet milestone

**Week 1-2**:
- Tier 2 execution (decisions + scripts)
- @enforced_by prototype experiment

**Week 2-8**:
- Tier 3 gradualist migration (10 phases, packet-per-phase)

**Month 6 + Month 12 + Month 24**:
- Re-audits per §6.2 capability monitoring triggers

**Round-3 (if pursued)**:
- Operator picks topic from §7
- Same 2 longlast teammates (proponent-harness, opponent-harness) re-dispatched
- Estimated cycle: ~20-30 min based on round-2 elapsed
