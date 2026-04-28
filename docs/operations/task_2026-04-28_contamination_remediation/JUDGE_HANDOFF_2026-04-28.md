# Judge Session Handoff — 2026-04-28 (compact-resistant)

**For**: Any future Claude session that needs to pick up this work after compaction.
**Authority**: This file is the canonical session-state record. Read this FIRST before any other action.

## §0 If your context is fresh, do this sequence

0. **Read `/Users/leofitz/.openclaw/workspace-venus/zeus/AGENTS.md` in full (~340 lines)** — Zeus project root law: money path (`contract semantics → source truth → forecast signal → calibration → edge → execution → monitoring → settlement → learning`), probability chain (51 ENS members → P_raw → Platt → P_cal → fusion → P_posterior → Kelly → Position Size), authority order (chain > chronicler > portfolio), planning-lock zones (`architecture/**`, `src/state/**`, `src/control/**`, etc.), topology_doctor.py usage. Without this you don't know what Zeus is or what rules govern remediation. **Skipping this step = guaranteed wrong actions.**
1. Read this file in full (~480 lines)
2. `git log --oneline -15` to see current commit chain
3. `git status --short | wc -l` to verify dirty file count (expect ~28-35 from operator's parallel work; not yours)
4. Disk-poll all 4 evidence dirs for in-flight outputs:
   ```
   ls -lat docs/operations/task_2026-04-28_contamination_remediation/evidence/{proponent,opponent,critic-harness,executor}/
   ```
5. Check teammate liveness via team config: `cat ~/.claude/teams/zeus-harness-debate-2026-04-27/config.json`
6. Re-measure pytest baseline: `.venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_inv_prototype.py tests/test_digest_profiles_equivalence.py -q --no-header` (expect 90 passed / 22 skipped / 0 failed)
7. Read §1.5 (prior work substance) and §16 (why this matters) below — they tell you WHAT 5 cycles produced and WHY this contamination remediation has live-money stakes
8. Resume per §10 next-steps

---

## §1 Where we are RIGHT NOW

**Phase**: Contamination remediation cycle, R2 dispatched 2026-04-28 ~04:33 UTC, both teammates working on R2 (not yet on disk).

**Branch**: `plan-pre5` (HEAD probably `a6241ea` or later if executor commits land).
**Forensic anchor**: `git tag pre-quarantine-snapshot-2026-04-28` (pushed to origin; recovery via `git checkout pre-quarantine-snapshot-2026-04-28`).

**Last known good baseline**: pytest 90/22/0 at `a6241ea`.
**Active dirty count**: ~28 files (operator's parallel work; do NOT absorb via `git add -A`).

**Immediate next event**: R2_REBUTTAL_DONE_*_REMEDIATION SendMessages from proponent + opponent. After both arrive (or disk-poll shows files), I write `verdict.md`, then dispatch critic-harness for REVIEW_VERDICT, then dispatch executor for implementation.

---

## §1.5 Prior work substance — what 5 cycles actually produced

The lineage in §3 lists 5 cycle names. **This section says what each one decided + what got built.** Without this you can resume the operational next-step but you don't know why the operational next-step matters or what assumptions it sits on.

### §1.5.1 R1 verdict (`task_2026-04-27_harness_debate/verdict.md`, 253L)

**TL;DR**: MIXED VERDICT WITH NET-NEGATIVE TILT ON MARGINAL SURFACE.

- Load-bearing core (~20-30%) earns its cost; both sides agree what it is (4-5 mechanisms: critic-opus dispatch, verifier dispatch, antibody contracts NC-NEW-A..J, per-phase boot evidence, disk-first artifacts, cross-session memory)
- Marginal periphery (~60-70%) unbudgeted under R2 cross-examination
- Z2 retro 6-catch case is empirical; HK HKO + V2 BUSTED-HIGH + Z2 6-catch are domain knowledge that 1M-context source-read does NOT produce → encoding is necessary somewhere
- 10 LOCKED concessions (key ones for context):
  - INV-16/17 are pure prose-as-law on HEAD (PRUNE_CANDIDATE marked)
  - 7 of 10 untested INVs cite drifted `migrations/` schema path (actual: `architecture/`)
  - Anthropic Dec 2024 "few lines of code" guidance does NOT apply directly to live-money trading
  - R3 multi-region parallel debate apparatus IS net-negative — being correctly retired
  - Cursor (largest Claude API customer) uses root + scoped rules + structured workflow + approval gates — same pattern as Zeus

### §1.5.2 R2 verdict (`task_2026-04-27_harness_debate/round2_verdict.md`, 305L)

**TL;DR**: Both sides PARTIAL ACCEPT; converged on synthesized middle.

**Synthesized target**: ~3,500-4,200 LOC YAML (-73%-77%); 9-11 routers; ~400-500 LOC topology_doctor; 14-anti-drift catalog → 5 retained mechanisms (4 hook-converted + 1 prose SKILL); 24-month asymptote ~1,500-2,000 LOC total harness. Migration cost ~110-140h over 9-11 phases.

**12 mechanism agreements (0 contested)**:
1. Hooks > advisory prose for planning-lock + map-maintenance + invariant-test
2. Native `.claude/agents/` for critic-opus + verifier + safety-gate
3. Native `.claude/skills/` for phase-discipline + task-boot + fatal-misreads
4. Type-encoded HK HKO antibody (`SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses) replaces 17L of YAML
5. r3_drift_check.py extension to architecture/*.yaml citation coverage
6. topology.yaml deeper cut to ≤500 LOC after audit
7. r3/IMPLEMENTATION_PROTOCOL.md 14-mechanism catalog → ~47-line SKILL.md
8. task_boot_profiles.yaml → 7 separate SKILL.md files
9. code_review_graph_protocol.yaml → inline 6-line note in root AGENTS.md
10. docs_registry/script_manifest/test_topology auto-gen from filesystem walk + per-file headers
11. DELETE INV-16/17 (later RE-SCOPED to REVERT PRUNE per BATCH D — see §1.5.4)
12. Path drift fix on 7 INVs (already executed)

### §1.5.3 R3 verdict (`task_2026-04-27_harness_debate/round3_verdict.md`, 236L)

**TL;DR**: Both sides PARTIAL ACCEPT and CROSSED OVER midpoint. Final ~37% harness / ~63% edge over 6 months, gated by week-3-4 empirical fault-rate observation. Steady-state mo6+: ~50/50 per Headlands "operations is co-equal product layer".

**14 LOCKED concessions (strongest concession bank of all 3 rounds)**, key ones:
- Tier 1 100% harness, weeks 1-2 (executor batches A-D, in flight) — non-negotiable
- EDGE_OBSERVATION is FIRST edge packet (provides measurement substrate)
- Empirical 2-week fault-rate observation window in weeks 3-4
- Knight Capital citation does NOT motivate Tier 2/3 work — root cause was deployment omission + dormant Power Peg, not harness debt; right antibody class is in Tier 1
- @enforced_by decorator prototype built in month 1 — empirical decision criterion
- Headlands "operations is co-equal product layer" framing → steady-state ~50/50 maintenance/research at month 6+
- Paul Graham "perfectionism is procrastination" applies POST-Tier-1 (not before)
- Martin Fowler Tech Debt Quadrant: HIGH-INTEREST (front-load: hooks, decorator prototype, topology Python, INV-09/15 upgrade) vs LOW-INTEREST (defer: docstrings, R3 plan compaction)

**This is the cycle that produced the Anthropic "ruthlessly prune" rhetoric that may have culturally enabled the contamination event in another session — the meta-irony of cycle 5.**

### §1.5.4 Tier 1 + Tier 2 implementation (4 BATCH + 3 SIDECAR + 4 PHASE)

Pytest progression: 73 → 76 → 79 → 83 → **90/22/0** (zero regression throughout).

**Tier 1 — 4 BATCH** (executor-harness-fixes, critic-harness gated each):
- **BATCH A** (doc-only): created `.claude/agents/critic-opus.md + verifier.md + safety-gate.md`; created `.claude/skills/zeus-phase-discipline/SKILL.md`; inlined `code_review_graph_protocol.yaml` 6-line summary into root AGENTS.md (kept as DEPRECATED stub)
- **BATCH B** (mechanical hooks): `.claude/hooks/pre-edit-architecture.sh` (refuses architecture/** edit without ARCH_PLAN_EVIDENCE env); `.claude/hooks/pre-commit-invariant-test.sh` (BASELINE_PASSED 90); extended `scripts/r3_drift_check.py` with --architecture-yaml mode
- **BATCH C** (architecture; HIGH CARE; K0_frozen_kernel): added `SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` to `src/contracts/settlement_semantics.py`; mixing raises TypeError; tests in `tests/test_settlement_semantics.py` (3 + 3 negative-half tests)
- **BATCH D** (RE-SCOPED via §10 erratum): originally "DELETE INV-16/17 per round-1 §6.1 #1 + round-2 §4.2 #9". Critic-harness BATCH C cross-batch audit DISCOVERED 9 hidden tests (3 in test_phase6_causality_status.py + 6 in test_dt1_commit_ordering.py). RE-SCOPED to REVERT PRUNE_CANDIDATE markers + ADD `tests:` blocks (CITATION_REPAIR comments). **This is methodology case study §5.X — apparent gap ≠ actual drift.**

**3 SIDECAR**:
- **SIDECAR-1**: 7 INV path drifts fixed (`migrations/...` → `architecture/2026_04_02_architecture_kernel.sql`)
- **SIDECAR-2**: NC-12/NC-13 retained (paired with re-tested INV-16/17, not orphaned)
- **SIDECAR-3** (handoff-discovered preservation): WMO asymmetric half-up rounding rule. `WMO_HalfUp` initially used Decimal `ROUND_HALF_UP` (away from zero, -3.5→-4). Legacy `np.floor(x+0.5)` is asymmetric (toward +∞, -3.5→-3). Fixed implementation to match legacy via `np.floor(float(x)+0.5)`. **Strategy is empirically validated WMO standard; SIDECAR-3 fixed implementation drift, not strategy choice.**

**Tier 2 — 4 PHASE**:
- **Phase 1**: history_lore.yaml audit. 44 cards → 18 active (kept) + 26 archived to `docs/archives/history_lore_extended_2026-04-28.md` (1711L). Apparent 0/26 hit on drift keywords from contamination — confirms our archive choice is unrelated to drift items.
- **Phase 2**: topology section catch-history audit (`scripts/topology_section_audit.py` 233L). Verdict: KEEP topology.yaml current size (audit found INTENTIONAL CURATION not bloat). Per **§9.2 erratum**: prior R1+R2 verdict claim "auto-gen 3 registries" was FALSIFIED by audit (95% load-bearing, only 5% mechanical).
- **Phase 3**: module_manifest audit (`scripts/module_manifest_audit.py` 216L). Verdict 21 KEEP / 4 HYBRID / 0 REPLACE. Per **§9.3 erratum**: prior verdict "Replace with package __init__.py runtime-introspectable registries" was FALSIFIED. Also: digest_profiles → Python codegen (`architecture/digest_profiles.py` 2901L auto-generated mirror; `tests/test_digest_profiles_equivalence.py` 4 byte-for-byte tests).
- **Phase 4**: @enforced_by decorator prototype (`architecture/inv_prototype.py` 264L + `tests/test_inv_prototype.py` 7 tests). Honest verdict: prototype works for ENFORCEABLE invariants (uniform domain like settlement); does NOT replace YAML for HEURISTIC invariants (cross-cutting like INV-21 Kelly distribution). Hybrid is the right answer.

### §1.5.5 Methodology key insights (`docs/methodology/adversarial_debate_for_project_evaluation.md`, 700+L)

The methodology has **4 case studies all with the same pattern**: a prior verdict prescribed a structural change (DELETE / AUTO-GEN / REPLACE); audit-first methodology FALSIFIED the prescription; all 4 changes prevented.

| Section | Case study | Pattern |
|---|---|---|
| §5.X | BATCH D INV-16/17 | "Delete prose-as-law" → audit found 9 hidden tests → REVERT + add tests: |
| §5.Y | Phase 2 auto-gen registries | "Auto-gen 3 registries" → audit found 95% intentional curation |
| §5.Z | Phase 3 module_manifest | "Replace with __init__.py" → audit 21 KEEP / 4 HYBRID / 0 REPLACE |
| §5.Z2 | Codified gate | Bidirectional grep BEFORE locking ANY "X% lacks Y" claim |
| §5.Z3 | Phase 4 @enforced_by | Prototype works for uniform domain; doesn't replace YAML for cross-cutting; 4-outcome categories: Falsified / Confirmed-bounded / Confirmed-unbounded / Inconclusive |

**Tribal knowledge from these 4-for-4 case studies**: when in doubt, do bidirectional grep + intent inquiry BEFORE prescribing structural change. Default-deny on semantic changes from grep-only evidence. **The methodology has empirical track record; use it.**

The 5th cycle (current contamination remediation) extends §5.Z3 with a 5th outcome category: **CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION (Stage-gated revert)** — both sides of the current debate adopted this in their R1 LOCKs.

---

## §2 Active longlast teammates (4 on team `zeus-harness-debate-2026-04-27`)

| Name | Color | Role (current) | Model | Status as of writing |
|---|---|---|---|---|
| `proponent-harness` | blue | **STAGE-GATED SURGICAL** ~33-50h | opus | idle, R2 in flight |
| `opponent-harness` | green | **STAGE-GATED REVERT** ~65-105h | opus | idle, R2 in flight |
| `critic-harness` | purple | gates verdict + flags missed semantic | opus | idle, awaiting REVIEW_VERDICT |
| `executor-harness-fixes` | yellow | implements per verdict (post-critic) | opus | idle, standby |

**SendMessage drop pattern is COMMON** — they often go idle without ACK; disk is canonical. Always disk-poll before assuming dead. Probe with status check if 15+ min silence + no disk activity.

**Naming convention for their messages**: `<EVENT>_<ROLE>_REMEDIATION` (e.g., `R2_REBUTTAL_DONE_PROPONENT_REMEDIATION`). The `_REMEDIATION` suffix distinguishes this cycle from prior `_HARNESS` cycle.

---

## §3 Methodology lineage (5 cycles total now)

Methodology doc: `docs/methodology/adversarial_debate_for_project_evaluation.md` (~700+ lines).

Cycles:
1. **R1 verdict** (mixed, net-negative tilt on marginal harness surface)
2. **R2 verdict** (synthesized middle harness ~5K-6K LOC short-term)
3. **R3 verdict** (Edge vs Safety capital allocation: ~37/63 over 6 months, both sides crossed-over)
4. **Tier 1 + 2 implementation** (4 BATCH + 3 SIDECAR + 4 PHASE; pytest 73→90 zero regression; 4-cycle methodology pattern confirmed; §5.Z3 4-outcome categories codified)
5. **Contamination remediation** (CURRENT — triggered by 53a21ad + multi-commit chain contamination from another session)

**Key methodology sections** (read if details needed):
- §5.X case study 1 (INV-16/17 schema-citation gap ≠ enforcement gap)
- §5.Y bidirectional grep pattern
- §5.Z generalization (apparent gap may be intentional)
- §5.Z2 codified gates for "X% lack Y" claims
- §5.Z3 4-outcome categories + 4-cycle confirmation

**Tribal pattern**: this methodology has a 4-for-4 empirical track record. **Use it.** When in doubt: bidirectional grep, intent-aware audit, default-deny semantic changes.

---

## §4 Current contamination remediation cycle state

**Packet**: `docs/operations/task_2026-04-28_contamination_remediation/`

**TOPIC.md** (300+ lines including §Addendum 2026-04-28 multi-commit empirical):
- Original 4 tech-layer options
- 5 process-layer gaps A-E
- §Addendum: 9-commit contamination chain + 5th outcome category (CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION)

**judge_ledger.md**: round status table; empirical baseline; 4 teammate roles; pre-quarantine tag reference.

**Boot evidence** (all on disk; all 3 active teammates ACK'd post-disk-poll):
- `evidence/proponent/_boot_proponent_2026-04-28.md` (18KB): defends MINIMAL-REVERT
- `evidence/opponent/_boot_opponent_2026-04-28.md` (14KB / 129L): defends AGGRESSIVE-QUARANTINE; surfaced multi-commit span
- `evidence/critic-harness/_boot_critic_2026-04-28.md` (13KB / 210L): 10+10 attack vectors + META 5th outcome

**R1 evidence** (both on disk; both LOCKED stage-gated 5th outcome):
- `evidence/proponent/R1_opening.md` (220L): refined to **STAGE-GATED SURGICAL**, 4-stage plan ~33-50h, TIER-1 revert 575f435+7027247
- `evidence/opponent/R1_opening.md`: refined to **STAGE-GATED REVERT**, 6-stage plan ~65-105h, file-level revert poisoned paths + per-hunk audit

**R2 evidence** (PENDING — both teammates working):
- `evidence/proponent/R2_rebuttal.md` (TBD)
- `evidence/opponent/R2_rebuttal.md` (TBD)

**Verdict.md** (TBD — judge writes after R2 close + critic gate):
- Should follow methodology §8 template
- Honest 5-criterion weighing
- 5th outcome category likely the synthesis (both sides already adopted it)
- Real disagreement narrowed to: ~30-50h vs ~65-105h aggregate; revert granularity; trust direction

**Likely synthesized middle**: stage-gated revert with surgical TIER-1 revert (575f435+7027247) + critic-gated MIXED-commit per-hunk audit (faster scope than opponent's full 385-file) + process gates A-E in parallel + in-flight fixes via independent critic gate (not auto-trust). ~50-75h aggregate.

---

## §5 The 9-commit contamination chain (judge-verified)

All ancestors of plan-pre5 per `git merge-base --is-ancestor <c> plan-pre5`:

| Commit | Subject | Drift item connection |
|---|---|---|
| `af7dd52` | Separate source-role training eligibility | source-role / training tier (likely #1 HKO=WU precursor) |
| `575f435` | feat(data): Meteostat bulk-CSV client | **direct: drift items #2 #3** |
| `0a4bae3` | Fail closed on incomplete observation backfills | observation pipeline (#2/#3 surface) |
| `cdec77d` | Gate obs v2 analytics on reader-safe evidence | observation v2 (#2/#3 surface) |
| `7027247` | feat(data): Phase 0 tier_resolver + A3 antibody | **direct: drift item #4 lazy-import** |
| `6754cdc` | feat(data): Phase 0 v2 writer + A1/A2/A6/A7 antibodies | observation v2 writer (#2/#3 surface) |
| `183404f` | fix(phase0): address critic REJECT — C1+C2+C3+M1 fixes | Phase 0 fixes (continuing same session) |
| `53a21ad` | Integrate R3 live-money hardening before branch handoff | massive 385-file flat snapshot |
| `1ffef77` | Expose P4 blockers without accepting placeholder readiness evidence | recent; touches verify_truth_surfaces.py (drift #5) |

**6 drift items** (per other session's self-audit):
1. HKO=WU 虚构 (high; recommended wrong path) — 已自首
2. meteostat→historical_hourly heuristic (severe; ~815k production rows polluted) — 正在修
3. ogimet→historical_hourly heuristic (severe; 17 city-station mislabeled) — 正在修
4. lazy import path parents[3]→[5] (severe; tier_resolver silent fail → causes #2 #3) — 修复中
5. verify_truth_surfaces operator-precedence bug (unverified) — 待 audit
6. Gate 5 enricher synthetic locator (medium; legacy:// synthetic provenance) — 已记入 plan

**Critical**: in-flight fixes for #1-#6 are happening in the SAME contaminated session. Per methodology §5 + opponent's R1 boot §3 finding: trusting in-flight fixes = continued self-review violation. Must be critic-gated by independent reviewer before merge.

---

## §6 5 process gaps (A-E)

These are governance fixes the methodology REQUIRES going forward (independent of revert decision):

| # | Gap | Where to encode |
|---|---|---|
| **A** | Cross-session critic-gate REQUIRED (not just our session) | Update root `AGENTS.md` + `zeus-ai-handoff` SKILL §8 |
| **B** | Worktree-merge contamination check | New: `architecture/worktree_merge_protocol.yaml` |
| **C** | Scope authorization freezing (user "continue" doesn't expand declared scope) | `zeus-ai-handoff` SKILL §3 mode-selection |
| **D** | Methodology cross-session propagation (every session boot reads methodology) | root `AGENTS.md` Required Reads + `zeus-ai-handoff` |
| **E** | Erratum-frequency trigger (≥3 errata/cycle → mandate audit-first for subsequent verdicts) | methodology §5.Z3 quantitative |

Both sides agree these are non-negotiable + run in parallel with whatever revert strategy is chosen.

---

## §7 Canonical disk artifacts (where things live)

```
docs/methodology/adversarial_debate_for_project_evaluation.md     # master methodology
docs/operations/task_2026-04-27_harness_debate/                    # 4 prior cycles + DEEP_PLAN
  ├── TOPIC.md
  ├── judge_ledger.md
  ├── DEEP_PLAN.md
  ├── verdict.md (R1 + §10 erratum)
  ├── round2_verdict.md (R2 + §9 + §9.2 + §9.3 errata)
  ├── round3_verdict.md (R3 + §9 erratum)
  ├── inv_decorator_prototype_2026-04-28.md (Phase 4 verdict)
  ├── topology_section_audit_2026-04-28.md (Phase 2 audit report)
  ├── module_manifest_audit_2026-04-28.md (Phase 3 audit report)
  └── evidence/{proponent,opponent,executor,critic-harness}/
docs/operations/task_2026-04-28_contamination_remediation/         # CURRENT cycle
  ├── TOPIC.md (with §Addendum 2026-04-28 multi-commit)
  ├── judge_ledger.md
  ├── JUDGE_HANDOFF_2026-04-28.md (THIS FILE)
  ├── (verdict.md - TBD)
  └── evidence/{proponent,opponent,critic-harness,executor}/
.claude/agents/{critic-opus,verifier,safety-gate}.md               # native subagents
.claude/skills/zeus-phase-discipline/SKILL.md                      # Tier 1 BATCH A
.claude/skills/zeus-task-boot-*/SKILL.md                          # 7 task-boot skills (Phase 1)
.claude/hooks/{pre-edit-architecture,pre-commit-invariant-test}.sh # Tier 1 BATCH B
.claude/settings.json                                              # hooks registration + ARCH_PLAN_EVIDENCE env
.agents/skills/zeus-ai-handoff/SKILL.md                           # v2 4-mode handoff playbook
scripts/r3_drift_check.py                                          # top-level shim, --architecture-yaml mode
scripts/regenerate_registries.py                                   # Phase 2 audit script
scripts/topology_section_audit.py                                  # Phase 2 audit script
scripts/history_lore_audit.py                                      # Phase 1 audit script
scripts/module_manifest_audit.py                                   # Phase 3 audit script
scripts/digest_profiles_export.py                                  # Phase 3 codegen
architecture/inv_prototype.py                                      # Phase 4 prototype
architecture/digest_profiles.py                                    # Phase 3 auto-gen mirror (2901L)
architecture/invariants.yaml                                       # 30 INVs; INV-16/17 with CITATION_REPAIR comments
architecture/topology.yaml                                         # has audit_cadence metadata block (Phase 2)
architecture/history_lore.yaml                                     # 18 active cards (post-Phase 1 archive)
docs/archives/history_lore_extended_2026-04-28.md                  # 26 archived cards (Phase 1)
```

**Forensic tag**: `pre-quarantine-snapshot-2026-04-28` (on origin)

---

## §8 Pytest baseline progression (verifies clean execution)

| Stage | Baseline | Verified at HEAD |
|---|---|---|
| Pre-Tier 1 | 71/22/2-evaluator (pre-existing failures unrelated) | original |
| Post-Tier 1 BATCH A-D | 73/22/0 → 76/22/0 → 79/22/0 | 7b3735a |
| Post-Tier 2 Phase 1-3 | 79/22/0 → 83/22/0 | various |
| Post-Tier 2 Phase 4 (current) | **90/22/0** | a6241ea |

**Test files added**:
- tests/test_settlement_semantics.py (BATCH C; 3 tests + 3 SIDECAR-3 negative-half)
- tests/test_inv_prototype.py (Phase 4; 7 tests)
- tests/test_digest_profiles_equivalence.py (Phase 3; 4 byte-for-byte equivalence)

**Pre-existing failures NOT YOURS**: 2 in evaluator.py:377 temperature_metric (resolved between baseline-doc-time and Tier 1 boot; current 0 failures; documented in critic batch_C_review for context).

---

## §9 Recovery commands (in case of emergency)

```bash
# Verify current state matches expected
git tag -l pre-quarantine-snapshot-2026-04-28           # confirm tag exists
git rev-parse pre-quarantine-snapshot-2026-04-28        # commit hash
git log --oneline pre-quarantine-snapshot-2026-04-28..HEAD  # commits since tag

# Restore to forensic anchor (if irreversible mistake)
git checkout pre-quarantine-snapshot-2026-04-28          # detached HEAD inspect
git reset --hard pre-quarantine-snapshot-2026-04-28      # destructive: only with operator approval

# Re-verify pytest baseline (should be 90/22/0)
.venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_inv_prototype.py tests/test_digest_profiles_equivalence.py -q --no-header

# Verify topology_doctor live
python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/invariants.yaml --plan-evidence docs/operations/task_2026-04-27_harness_debate/round2_verdict.md

# Disk-poll teammate evidence
find docs/operations/task_2026-04-28_contamination_remediation/evidence -type f -mmin -30 | sort
```

---

## §10 What R2 + verdict should look like

When R2_REBUTTAL_DONE arrives (or disk shows R2 files):

1. Read both R2 files fully
2. Update `judge_ledger.md` with R2 status COMPLETE
3. Write `docs/operations/task_2026-04-28_contamination_remediation/verdict.md` per methodology §8 template:
   - §0 TL;DR (synthesized middle expected: stage-gated revert with surgical scope; ~50-75h estimated; 5 process gates A-E parallel; in-flight fixes via independent critic gate)
   - §1 LOCKED concessions (likely 8-12 items both sides agree)
   - §2 Remaining bounded disagreements (likely 2-3 items)
   - §3 Unresolvable (likely: actual time estimate; whether contaminated session can be paired with independent critic in finite time)
   - §4 Judge weighing per 5 criteria
   - §5 Verdict direction
   - §6 Action plan: stage-gated implementation roadmap
   - §7 Cumulative debate metrics (this is 5th methodology cycle)
   - §8 Future cycles (round-2 alt-system for governance? defer until implementation data)
4. Dispatch `critic-harness` for `REVIEW_VERDICT` (per methodology §5 critic-gate workflow)
5. After critic APPROVE: dispatch `executor-harness-fixes` to implement per verdict's action plan

---

## §11 Operator authorization scope (don't over-extend)

The operator has authorized:
- ✅ "推进t2" → Tier 2 (4 phases) — DONE
- ✅ "对机械性fix发排一个新的longlast teammate进行修复" → executor spawned — DONE
- ✅ "需要多agent converge" → R-format adversarial debate — IN FLIGHT
- ✅ "Update TOPIC.md 加 contamination 多 commit 的 empirical 附录" → done before R1
- ✅ "法官context已经到了critical, ... handoff 力求最小化compact影响" → THIS FILE

The operator has NOT authorized (don't initiate without operator OK):
- ❌ Merging plan-pre5 → main (paused per "PAUSE — 用户信号严重")
- ❌ Reverting any commits (5th outcome category in flight)
- ❌ Touching the contaminated session's in-flight fix work
- ❌ Modifying drift items directly (the other session is already in-flight)

When user says "continue" or "推进", scope FROZEN to current task class — per process gap C.

---

## §12 Critical tribal knowledge (easy to lose in compaction)

### Naming + addressing
- Always refer to teammates by NAME (proponent-harness etc.), never by agentId UUID
- SendMessage `to:` field uses NAME

### SendMessage drop pattern
- Common; observed every cycle
- Disk is canonical (memory `feedback_converged_results_to_disk`)
- Always disk-poll before assuming teammate dead
- Probe with brief status check if 15+ min silence

### Co-tenant git hygiene (memory `feedback_no_git_add_all_with_cotenant`)
- NEVER `git add -A` or `.` — operator has parallel work in dirty tree
- Always `git add` SPECIFIC files
- Verify staged scope: `git diff --cached --name-only`
- Verify after commit: `git log -1`

### Idle-only bootstrap (memory `feedback_idle_only_bootstrap`)
- New teammates: idle-only boot → ACK → wait for substantive dispatch
- Substantive work only after explicit dispatch from team-lead

### File:line cite rot (memory `feedback_zeus_plan_citations_rot_fast`)
- Citations rot ~20-30% per week
- Always grep-re-verify within 10 min before any "lock" event
- Symbol-anchored citations (function name + sentinel) survive line drift

### Verdict erratum pattern (methodology §5.Z3)
- 4 outcome categories: Falsified / Confirmed-bounded / Confirmed-unbounded / Inconclusive
- 5th category for this cycle: Stage-gated (CONDITIONAL pending other-session)
- When implementation finds prior verdict was wrong: write erratum, don't silently fix

### Critic-gate is methodology core
- Independent critic prevents executor self-approval drift
- Per memory `feedback_executor_commit_boundary_gate`
- This entire cycle exists because OTHER session bypassed this gate

### Don't shutdown teammates without operator approval
- LONG-LAST status maintained for all 4
- They're forensic + work-in-flight; shutdown loses context

---

## §13 Recent commit chain (for orientation)

```
?       (TBD: contamination remediation verdict)
a6241ea Methodology §5.Z3: 4-cycle confirmation + 4 outcome categories
7d1b288 Third verdict erratum + methodology 3-for-3 confirmation
fd43248 Second verdict erratum + methodology generalization (Phase 2 finding)
da5b525 zeus-ai-handoff v2: 4-mode general handoff playbook
f818a66 Methodology + SKILL: bidirectional grep audit pattern
7b3735a Preserve settlement rounding law found during handoff review (= SIDECAR-3)
3324163 Verdict errata + methodology case study: critic-gate caught LARP overcount
208bd79 Preserve handoff evidence and restore topology trust (co-tenant; clean)
53a21ad Integrate R3 live-money hardening before branch handoff (CONTAMINATED — 385 files)
eb7df50 Add adversarial-debate methodology for project evaluation
20f4122 Harness debate cycle 2026-04-27 — 3 rounds + executor batches A+B
874e00c (origin/main HEAD)
```

Pre-`53a21ad` ancestors include the 8 contamination commits per §5 chain table.

---

## §14 Contact + escalation

- Operator: leofitz (Fitz, GitHub yuxuan53)
- Repo: github.com/fitz-s/zeus (origin)
- Branch: plan-pre5
- Tag: pre-quarantine-snapshot-2026-04-28
- Active cycle: contamination remediation (5th in methodology lineage)

If totally lost: read this file + methodology + TOPIC.md of current cycle, then ask operator clarifying question via standard chat (don't act blindly).

---

## §15 What this handoff intentionally OMITS

Things deliberately NOT in this file (read source if needed):
- Per-batch detail of Tier 1 BATCH A-D (in `task_2026-04-27_harness_debate/judge_ledger.md`)
- Per-phase detail of Tier 2 Phase 1-4 (in `task_2026-04-27_harness_debate/evidence/critic-harness/`)
- Methodology §5 full text (in `docs/methodology/adversarial_debate_for_project_evaluation.md`)
- All proponent/opponent debate evidence (in respective `evidence/` dirs)
- DEEP_PLAN's full sequencing (in `task_2026-04-27_harness_debate/DEEP_PLAN.md`)
- 4 audit script implementations (in `scripts/*_audit.py`)

This file is the **STATE + ORIENTATION map**. Detail lives in canonical artifacts.

---

## §16 Why this matters — Zeus context for new sessions

Without this section a new Claude session can resume the operational next-step but doesn't know **why this remediation has live-money stakes**. Read this if you've never worked on Zeus.

### Zeus = live-money Polymarket weather derivatives trading engine

Per AGENTS.md L3-13: Zeus converts atmospheric data into sized limit orders with positive expectation, bound by market settlement mechanics + dynamic risk limits. Money path is causal-linear:

`contract semantics → source truth → forecast signal → calibration → edge → execution → monitoring → settlement → learning`

Probability chain (the math under each trade):

`51 ENS members → per-member daily max → Monte Carlo (sensor noise + ASOS rounding) → P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal → α-weighted Market Fusion → P_posterior → Edge & Double-Bootstrap CI → Fractional Kelly → Position Size`

Authority order (NEVER inverted): `chain (Polymarket CLOB) > chronicler (event log) > portfolio (local cache)`. Local-exists-NOT-on-chain → VOID immediately. Chain-exists-NOT-local → QUARANTINE 48h.

### Branch context: plan-pre5 = pre-cutover preparation

`plan-pre5` is the branch staging the live-money cutover (LIVE_ENABLED state transition). The branch holds all R3 hardening + Phase 0 data infrastructure + V2 CLOB adapter + risk-allocation gates that must pass before flipping to live trading. **PRE_CUTOVER_FREEZE** state means: code is being prepared but has NOT shipped to live; live venue side effects + production DB mutations + CutoverGuard LIVE_ENABLED transitions remain forbidden until cutover.

Per 53a21ad commit message verbatim: *"G1 remains external-evidence blocked; no Q1 Zeus-egress or staged-live-smoke evidence exists in this workspace... Live venue side effects, production DB mutation, and CutoverGuard LIVE_ENABLED transitions remain forbidden."*

**This is why the contamination is high-stakes but not yet catastrophic**: 815k polluted training rows + 17 mislabeled stations are in TRAINING DATA / SOURCE ATTRIBUTION pipelines, not yet in live execution. **If we ship contaminated code to live**, the pollution cascades through:
- Training data → calibration model fits on wrong source-attributed data → P_cal biased
- Source attribution drift → fusion weights off → P_posterior biased
- Lazy-import path bug (drift item #4) → tier_resolver silent fail → wrong source quality classification → DR-33 governance bypassed
- Net effect: positions sized via Kelly on biased probabilities → systematic edge leakage in live trading

### Why contamination remediation is the right next move (not "just patch and continue")

The contaminated session bypassed critic-gate and self-reviewed work that:
1. Touched data ingestion (the source of the entire probability chain)
2. Modified observation pipelines that feed calibration
3. Was merged into plan-pre5 BEFORE live cutover (so the contamination IS in the pre-cutover artifact)
4. Has 50+ self-authored tests that need INDEPENDENT critic audit per methodology §5.Z2 (tests-as-LARP-suspect when written by the same author who wrote the code)

The right discipline is methodology §5.Z3 4-outcome categories applied per drift item, with the 5th category (stage-gated revert) for the multi-commit span — this is exactly what both sides of the current debate LOCKED in R1.

### Process gaps A-E exist because cross-session contamination was undefended

The 5 process gaps (§6) exist because Zeus's harness governs in-session work but had no defense against:
- **A**: Another session merging via worktree without our session's critic gate firing
- **B**: 53a21ad's 385-file flat snapshot triggering no contamination check
- **C**: Operator's "continue" being interpreted as scope-expansion authorization
- **D**: Methodology not loading on new session boot (each session re-discovers)
- **E**: No quantitative trigger for "audit-first mode required" based on prior errata frequency

These gaps are GOVERNANCE, not code. Encoding them in `.claude/skills/`, `.claude/hooks/`, root AGENTS.md, and methodology §5.Z3 quantitative is the antibody that makes this contamination CATEGORY impossible going forward (per Fitz Constraint #1 — make the category unwritable, not patch each instance).

### Net: this remediation is both tactical (current contamination) and strategic (defense for future sessions)

Without the strategic process-gates layer, the next session can recreate the same failure. Without the tactical revert layer, the current contamination ships to live. **Both layers needed; neither replaces the other.** This is why both sides of the R1 debate LOCKED 5th outcome category (stage-gated revert + parallel process gates) rather than choosing tactical vs strategic.

---

End of handoff. Total length ~480 lines. Time to read ≤ 7 min for fresh Claude.
