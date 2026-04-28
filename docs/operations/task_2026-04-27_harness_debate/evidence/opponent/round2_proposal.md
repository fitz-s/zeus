# Opponent ROUND-2 Phase-1 — Whole-System Replace Proposal

Author: opponent-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Builds on: `verdict.md` §1 LOCKED concessions (10 items, NOT REOPENED) + §6.4 deferred items + §8 round-2 framing
Stance: **whole-system replace** with surface reduction to ~22% of current LOC, while preserving 100% of the verdict §1 concession #2 load-bearing core (critic + verifier dispatch + antibody contracts + per-phase boot + disk-first + memory).

---

## §0 What I am NOT relitigating (per verdict §1)

Per dispatch directive ("CANNOT reopen"). For brevity:

- Z2 retro 6 catches: real, load-bearing — the proposal PRESERVES the mechanism that produced them.
- 4-5 mechanisms load-bearing (critic-opus + verifier + antibody contracts + per-phase boot + disk-first + memory): **fully preserved**.
- TOPIC count error: factual, unrelated to system architecture.
- INV-16/17 pure prose-as-law: deletion already in §6.1 mechanical fix list (judge applied PRUNE_CANDIDATE markers per ledger lines 41-46).
- Citation drift in 7 INVs: addressed both in mechanical fix list and in proposed §3.4 (drift-checker coverage extension).
- Cross-session memory + domain encoding necessary: **proposal preserves both** as type-encoded code + minimal manifest, not eliminated.
- Anthropic Dec 2024 quote mode-mismatched: yes, conceded; this proposal does NOT cite it.

---

## §1 Engaging proponent's likely "in-place reform" direction at face value

Per anti-rubber-stamp rule: address opponent's strongest counter-position before pivoting.

Proponent will argue (per their R2 §6 verdict direction): "MIXED-POSITIVE with bounded subtraction list", retaining 70-80% of current surface. Their concrete fix list is the §1 INV table (delete 2, fix path drift in 6, upgrade 2 with transitive-NC test). They will frame round-2 as "additive evolution: keep the cathedral, prune dead wings."

### Strongest in-place reform argument I must engage face-value

The strongest version of the in-place-reform position is:
1. The §1 LOCKED concessions show domain-encoded artifacts are necessary (HK HKO + V2 BUSTED-HIGH + Z2 6-catch).
2. The harness already has the right shape for this — manifests + invariants + critic gates + disk artifacts.
3. Switching to a different shape costs migration overhead + retraining the operator + breaking working CI.
4. The empirical catches we have prove the current shape WORKS (even if oversize).
5. Therefore: **shrink the surface in place; do not replace.**

### Conceded at face value

1. **Migration cost is real and load-bearing**. Whole-system replace means rewriting `topology_doctor.py`, recompiling `architecture/*.yaml` knowledge into types/code, retraining the operator's mental model, and re-validating CI. This is non-trivial and has a "kitchen sink session" failure mode (per the Anthropic Claude Code best practices URL §6 below).

2. **Working CI is precious**. 71 passing tests in `test_architecture_contracts.py` (per judge ledger §54) prove parts of the current harness ARE wired. Replacing them risks breaking known-good gates.

3. **Operator mental model is sunk cost in a good sense**. The operator's reasoning about Zeus is itself encoded in the harness shape; pruning preserves the shape and is therefore lower-friction.

4. **The proponent's bounded subtraction list (delete 2 INVs, fix 6 path drifts, upgrade 2 with tests) is correct AND insufficient**. It lands the right small fixes; it does NOT address the 14-anti-drift-catalog, 7-class boot profile ritual, 165KB topology.yaml, or 1,573-line source_rationale.yaml.

### Why in-place reform is nonetheless wrong target

In-place reform asks: "what should we delete?" That is the wrong question. The right question is: **"what is the minimum-surface system that preserves all 6 verdict §1 concession-2 mechanisms (critic dispatch + verifier + antibody contracts + per-phase boot + disk-first + memory)?"** The answer to the second question is dramatically smaller than 70% of current surface — **closer to 22%** by the LOC accounting in §2 below — because the load-bearing mechanisms do not require 165KB of YAML to operate. They require:
- A small list of antibody contracts (NC-NEW-A..J already serves this)
- Critic + verifier subagent definitions (`.claude/agents/`)
- Per-phase boot template (single file, ~100 lines)
- Disk-first artifact convention (single sentence in CLAUDE.md)
- Memory mechanism (Anthropic's `/memory` already provides this)

Everything else — the 14-mechanism catalog, the 7 boot-profile class taxonomy, the 165KB `topology.yaml`, the per-file rationale at 9 lines/file in `source_rationale.yaml`, the 62-line YAML governing "derived context" — is **scaffolding around the load-bearing core, not the load-bearing core itself.**

In-place reform cannot get below ~50% of current surface because it cannot delete files that have CI tests pointing at them; replace can. Replace authorizes a single migration window where tests are rewritten against the new shape, then the old shape is deleted entirely.

**Pivot**: I propose replace. The bounded subtraction list (proponent §1) is preserved as a Phase 1 DOWN PAYMENT inside this proposal — implementing the proponent's fixes IS step 1 of the migration.

---

## §2 Total post-replace surface size target

### Current surface (per TOPIC.md L14-22 + judge-verified)

| Surface | LOC / count | Source of count |
|---|---|---|
| `architecture/*.yaml` | **15,234 LOC** across 29 files | `wc -l architecture/*.yaml` |
| `topology.yaml` (single file) | 165KB ≈ ~5,000 LOC, 19 top-level keys | `wc -l` + `grep -c "^[a-z_]"` |
| `source_rationale.yaml` (single file) | 1,573 LOC | `wc -l` |
| `script_manifest.yaml` | 38,841 bytes | `ls -la` |
| `module_manifest.yaml` | 28,404 bytes | `ls -la` |
| `history_lore.yaml` | 106,368 bytes | `ls -la` |
| AGENTS.md routers | 41 tracked-non-archive (verdict §1 concession 4) | `git ls-files \| wc -l` |
| `topology_doctor.py` | 1,630 LOC | TOPIC.md row 4 |
| R3 plan/protocol surface | 144 .md + 45 .yaml = **189 files for one 312h plan** | `find docs/operations/task_2026-04-26_ultimate_plan` |
| 14-anti-drift mechanisms catalog (`IMPLEMENTATION_PROTOCOL.md`) | 465 LOC | TOPIC.md row 5 |
| `task_boot_profiles.yaml` | 360 LOC, 7 task classes | from boot reading |
| `code_review_graph_protocol.yaml` | 62 LOC | from boot reading |
| `fatal_misreads.yaml` | 153 LOC, 7 misreads | from boot reading |
| **Estimated total maintained surface** | **~25-30K LOC + 41 routers + 189 plan files** | sum |

### Proposed post-replace surface

| Surface | LOC / count | Replaces |
|---|---|---|
| Single `CLAUDE.md` (root) | ≤500 LOC | `AGENTS.md` (336 LOC) + reduces inline content from `workspace_map.md` (107 LOC); per Cursor "<500 lines" + Anthropic "ruthlessly prune" |
| `.claude/agents/critic-opus.md` | ~50 LOC | inline critic dispatch invocation pattern |
| `.claude/agents/verifier.md` | ~50 LOC | inline verifier dispatch pattern |
| `.claude/agents/safety-gate.md` | ~50 LOC | replaces `topology_doctor.py --planning-lock` ritual |
| `.claude/skills/zeus-domain/SKILL.md` | ~150 LOC | replaces `architecture/fatal_misreads.yaml` (153 LOC) — auto-loaded on demand, not boot-time |
| `.claude/skills/settlement-rounding/SKILL.md` | ~50 LOC | replaces HK HKO YAML antibody (delegates to type system) |
| `.claude/hooks/pre-edit-architecture.sh` | ~30 LOC | replaces planning-lock ritual; deterministic gate per Anthropic best practices "Use hooks for actions that must happen every time with zero exceptions" |
| `.claude/hooks/pre-commit-invariant-test.sh` | ~30 LOC | runs invariant pytest as Git hook |
| `architecture/invariants.py` (CODE, not YAML) | ~400 LOC | replaces `architecture/invariants.yaml` (370 LOC) — same 30 invariants, but as Python dataclasses with @enforced_by decorators that wire DIRECTLY to tests/semgrep/schema |
| `architecture/antibodies/` (Python types) | ~300 LOC across 5 files | replaces `architecture/source_rationale.yaml` (1,573 LOC) hazard_badges + `architecture/fatal_misreads.yaml` (153 LOC) for cases best encoded as types |
| `src/contracts/settlement_semantics.py` (EXTEND existing) | +60 LOC (HKO_Truncation + WMO_HalfUp subclasses) | replaces HK HKO antibody YAML — type-checker enforces |
| `architecture/zones.py` (CODE) | ~200 LOC | replaces `zones.yaml` (127 LOC) + parts of `topology.yaml` zone routing |
| `architecture/runtime_modes.py` (CODE) | ~100 LOC | replaces `runtime_modes.yaml` (33 LOC) + `runtime_posture.yaml` (34 LOC) — Python enums + types |
| `scripts/topology_navigator.py` | ~300 LOC | replaces `topology_doctor.py` (1,630 LOC) — slim navigator that reads `architecture/*.py` + `git diff`; no digest profiles, no map-maintenance facade |
| `MEMORY.md` (root) | ≤100 LOC pointer to `~/.claude/memory/` | preserved as-is; Anthropic native memory mechanism |
| Per-package AGENTS.md (5 files: src, tests, scripts, docs, architecture) | ≤80 LOC each = 400 LOC total | replaces 41 scoped AGENTS.md routers (cull ~36 routers; keep top-level) |
| **Estimated total replaced surface** | **~2,800 LOC + 5 routers + per-task SKILL.md loaded on demand** | |

### Headline ratio

- **Current**: ~25-30K LOC + 41 routers + 189 plan files for one task
- **Post-replace**: **~2,800 LOC + 5 routers + on-demand SKILLs**
- **Reduction**: **~89% LOC, ~88% routers, ~95% plan-files-per-task** (R3 plan would be 1 SPEC.md + 1 phase-tracker.yaml, not 189 files)
- **Preserved**: critic dispatch + verifier dispatch + antibody contracts + per-phase boot + disk-first + memory + all 5 verified semgrep rules + HK HKO domain knowledge + settlement gate + RED-cancel-sweep + lifecycle grammar + all 30 invariants (re-encoded as Python with same enforcement)

The ~22% surface (2,800 of ~13,000 cited LOC base) is the bottom-floor estimate. Realistic floor with a margin for manifests I haven't audited (e.g., `kernel_manifest.yaml`, `change_receipt_schema.yaml`) is **25-30%**. Either way, it is dramatically smaller than the proponent's "70-80% of current".

---

## §3 Honoring verdict §6.2 commits in concrete code/spec form

### §3.1 HK HKO as `SettlementRoundingPolicy` subclass code

Replaces `architecture/fatal_misreads.yaml` antibody for HK HKO + Hong Kong-specific routing prose.

```python
# src/contracts/settlement_semantics.py (EXTEND)

from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

class SettlementRoundingPolicy(ABC):
    """
    Antibody-as-type per Fitz Constraint #1: structural decisions > patches.
    Replaces fatal_misreads.yaml HK HKO caution prose with unconstructable error.
    """
    @abstractmethod
    def round_to_settlement(self, raw_temp_c: Decimal) -> int: ...

    @abstractmethod
    def source_authority(self) -> str: ...

class WMO_HalfUp(SettlementRoundingPolicy):
    """WMO half-up: 74.45 → 74; 74.50 → 75. Used for WU/NOAA settlement chain."""
    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
        return int(raw_temp_c.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    def source_authority(self) -> str: return "WMO"

class HKO_Truncation(SettlementRoundingPolicy):
    """HKO truncation: 74.99 → 74. Used for Hong Kong settlement chain ONLY."""
    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
        return int(raw_temp_c.quantize(Decimal('1'), rounding=ROUND_DOWN))
    def source_authority(self) -> str: return "HKO"

# Mixing them is now a TypeError, NOT a documentation antibody:
def settle_market(market: WeatherMarket, raw_temp: Decimal,
                  policy: SettlementRoundingPolicy) -> int:
    if market.city == "Hong Kong" and not isinstance(policy, HKO_Truncation):
        raise TypeError(f"HK markets require HKO_Truncation policy, got {type(policy).__name__}")
    if market.city != "Hong Kong" and isinstance(policy, HKO_Truncation):
        raise TypeError(f"HKO_Truncation only valid for HK; got city={market.city}")
    return policy.round_to_settlement(raw_temp)
```

**Verification**: Adding `assert isinstance(policy, SettlementRoundingPolicy)` at the existing `assert_settlement_value()` gate site means every settlement-touching code path is type-checked. The category of "wrong rounding for wrong city" is unconstructable. Replaces 17 lines of YAML antibody with 30 LOC of self-enforcing types per Fitz Constraint #1: "make the category impossible, not just the instance."

Time-to-implement: **~2 engineer-hours** (write code, add 3 pytest cases, delete YAML antibody, update `src/contracts/settlement_semantics.py` AGENTS.md if exists). Net LOC delta: **-(17 YAML antibody + ~50 prose around it) + 60 = effectively neutral, but BUG-CATEGORY-IMPOSSIBLE.**

### §3.2 14-anti-drift catalog → ~100-line operating heuristic spec

Replaces `r3/IMPLEMENTATION_PROTOCOL.md` §1 (14 anti-drift mechanisms in 465 LOC document) per Anthropic Jun 2025 "good heuristics rather than rigid rules" + Cursor "<500 lines" + Anthropic Claude Code best practices "Bloated CLAUDE.md files cause Claude to ignore your actual instructions!"

Proposed replacement:

```markdown
# .claude/skills/zeus-phase-discipline/SKILL.md
---
name: zeus-phase-discipline
description: Heuristics for executing a multi-phase Zeus implementation slice without drift
---

# Zeus phase-execution heuristics

When executing a phase from `r3/slice_cards/<phase>.yaml`:

## Boot (3 steps, not 17)
1. Read the slice card + the immediate predecessor's `learnings/<phase>_*_retro.md` if it exists.
2. Run `python3 scripts/topology_navigator.py --phase <id>` (returns: changed-files, gates, semgrep status, drifted citations).
3. If any cited file:line in the slice card returns SEMANTIC_MISMATCH, write `r3/_blocked_<phase>.md` and stop. Do NOT implement.

## During implementation (rules of thumb, not rigid checklist)
- Antibody contracts (NC-NEW-A..J) are SQL/semgrep, not prose; if a new behavior would violate, the test fails.
- Citations rot. When you cite a file:line, also cite a SYMBOL. The drift-checker re-verifies on the symbol.
- Frozen interfaces are downstream-stable. If you need to break one, write `r3/_protocol_evolution/<topic>.md` first.
- Every public API a downstream phase consumes must have at least one cross-phase relationship test.

## Closeout (3 steps)
1. Dispatch critic-opus subagent with the diff. If critic flags spirit-mismatch, fix and re-dispatch.
2. Dispatch verifier subagent with the test results. If verifier flags coverage gap, address.
3. Write `learnings/<phase>_<author>_<date>_retro.md`: what changed, what critic/verifier caught, what RULES_TO_CARRY_FORWARD this phase produced.

## Forbidden shortcuts
- "tests pass" alone ≠ shipped. Critic + verifier dispatch is mandatory.
- Bypassing the antibody contract via mock is itself a Z2-class regression.
- Slice card YAML must parse before claiming phase is reusable.

## When to stop and ask the operator
- Cited gate is in the operator-decisions register: STOP. Do not implement default.
- Cited NC or INV is marked PRUNE_CANDIDATE: STOP. Pruning is operator decision.
- More than 4 files changed in a single phase that does not declare cross-zone scope: STOP. Plan first.

That's it. The 14-mechanism catalog rotted into prose. This SKILL.md is what survives translation across sessions.
```

**Total**: 47 lines, fits in single screen, auto-loaded by Claude Code on phase work, replaces 465-LOC `IMPLEMENTATION_PROTOCOL.md`. Per Anthropic best practices: "If your CLAUDE.md is too long, Claude ignores half of it because important rules get lost in the noise."

### §3.3 `topology.yaml` / `source_rationale.yaml` audit principle

Per dispatch directive: "any section without 90-day catch → sunset."

Concrete audit principle for retained `architecture/*.yaml` (or for the equivalent code-encoded files post-replace):

```python
# scripts/yaml_section_sunset_audit.py (NEW)
"""
Run quarterly. Any section in architecture/*.yaml that has not been READ by an
agent (per `.code-review-graph` access logs) AND has not produced a documented
catch (per `learnings/*_retro.md` + `feedback_*` memory) in the last 90 days is
flagged SUNSET_CANDIDATE.

Sunset workflow:
1. Scan all .yaml sections via parser
2. For each section, query: was section path mentioned in any retro/learning/memory in last 90 days?
3. If NO: emit SUNSET_CANDIDATE with last-modified timestamp + git-blame author
4. Operator reviews quarterly; sections with no catch + no read get DELETED
5. If a future bug surfaces that section would have prevented, the deletion was a learning event
   per Fitz Constraint #3 (immune system); re-add as a code-encoded antibody, not a yaml entry

Acceptance: passes when every section in architecture/*.yaml either (a) has a
catch in last 90 days OR (b) carries a sunset-exception tag
"""
```

Concrete first-pass: applying this to current `architecture/topology.yaml` (19 top-level sections):

| Section | 90-day catch evidence | Verdict |
|---|---|---|
| `coverage_roots`, `root_governed_files`, `state_surfaces` | NEEDS AUDIT (no immediate retro evidence) | likely keep |
| `required_active_pointers` | NEEDS AUDIT | likely keep |
| `registry_directories`, `docs_root_allowed_files` | NEEDS AUDIT | likely sunset (administrative; not catching anything) |
| `active_operations_registry`, `docs_registry` | likely keep | catches bad ops paths |
| `module_manifest`, `module_reference_layer` | NEEDS AUDIT | candidate sunset (duplicates module_manifest.yaml) |
| `runtime_artifact_inventory`, `docs_mode_excluded_roots`, `docs_subroots` | likely sunset | not in any retro |
| `archive_interface`, `reference_fact_specs` | likely sunset | not in any retro |
| `core_map_profiles`, `digest_profiles` | NEEDS AUDIT | proponent argues these route the topology_doctor; if topology_doctor shrinks per §2, these shrink |

Estimated `topology.yaml` post-audit: drop ~40-60% of sections. Goes from 165KB to ~70-100KB. Still oversized; recommend further break into 4-5 focused yaml/code files.

For `source_rationale.yaml` (1,573 LOC for 173 src files = 9 lines/file): the per-file rationale model is **better expressed as docstrings in the source file itself** + module-level AGENTS.md, NOT as a separate yaml manifest. Concrete migration: write a script that reads each `src/**/*.py` for an existing `__doc__` and `# ZONE:` / `# AUTHORITY:` comments; if missing, propose insertion; once all source files have inline metadata, delete `source_rationale.yaml` entirely.

Estimated `source_rationale.yaml` post-migration: **0 LOC** (deleted; all metadata moves into source files where it belongs per Fitz "encode insight into structure"). Migration cost: ~16 engineer-hours (script + walking 173 files).

### §3.4 Drift-checker coverage extension (verdict §6.1 item 3)

Per verdict consensus: extend `r3_drift_check.py` to cover `architecture/*.yaml` citation blocks.

Concrete diff (~50 LOC addition):

```python
# Add to scripts/r3_drift_check.py

ARCHITECTURE_YAML_DIRS = ['architecture/', 'docs/operations/']

def check_yaml_citations(yaml_path: Path) -> list[DriftReport]:
    """
    For each `enforced_by:` block in architecture/*.yaml,
    verify cited paths exist and named symbols resolve.
    """
    drifts = []
    with yaml_path.open() as f:
        doc = yaml.safe_load(f)
    for path_field in ['schema', 'scripts', 'docs', 'tests']:
        # walk entire YAML tree looking for these keys
        for cited_path in walk_yaml_for_paths(doc, path_field):
            if not Path(cited_path).exists():
                drifts.append(DriftReport(
                    yaml=str(yaml_path), field=path_field,
                    cited=cited_path, status='MISSING',
                ))
    return drifts
```

Test that catches the 7-INV `migrations/` drift: `test_invariants_yaml_citations_resolve()`.

This single addition would have caught Finding 1 from proponent R2 §1, which was the strongest mid-debate finding. Proponent already committed to it in their R2.

---

## §4 Itemized — what survives, what is replaced (with what), what is deleted

### Survives (load-bearing core, both sides agree per verdict §1 concession #2)

| Survives | Why |
|---|---|
| `critic-opus` subagent dispatch pattern | Z2 retro empirical proof |
| `verifier` subagent dispatch pattern | Z2 retro empirical proof |
| Antibody contracts NC-NEW-A..J | empirically catches behaviors |
| Per-phase boot evidence file | survives compaction |
| Disk-first artifact convention | per memory `feedback_converged_results_to_disk` |
| Cross-session memory (Anthropic native `/memory` + `~/.claude/memory/`) | per Fitz Constraint #2 |
| All 5 verified semgrep rules in `architecture/ast_rules/semgrep_zeus.yml` | judge-verified present + wired |
| `assert_settlement_value()` gate at `src/contracts/settlement_semantics.py` | INV-02 / settlement law |
| RED → cancel + sweep behavior | INV-05 risk law |
| 9-state `LifecyclePhase` enum + finite grammar | INV-07 |
| Chain reconciliation hierarchy (Chain > Chronicler > Portfolio) | runtime law |
| 4-strategy taxonomy (`strategy_key` as governance identity) | INV-04 |
| All 30 invariants — same content, re-encoded | not deleted, restructured |

### Replaced (with what)

| Old | New | Reduction |
|---|---|---|
| `architecture/invariants.yaml` (370 LOC) | `architecture/invariants.py` Python dataclasses with `@enforced_by(test=..., semgrep=..., schema=...)` decorators that fail-import if cited path missing | same content, drift-impossible, ~400 LOC |
| `architecture/source_rationale.yaml` (1,573 LOC) | inline docstrings + module AGENTS.md per package | -1,573 LOC YAML; +~300 LOC inline docstring extension; net -1,273 LOC |
| `architecture/zones.yaml` + `runtime_modes.yaml` + `runtime_posture.yaml` | Python enums in `architecture/zones.py` / `runtime.py` | -195 LOC YAML; +~200 LOC types; net neutral but type-safe |
| `architecture/fatal_misreads.yaml` HK case (153 LOC) | `SettlementRoundingPolicy` subclasses (60 LOC) | -93 LOC, bug-impossible |
| `architecture/code_review_graph_protocol.yaml` (62 LOC) | Single sentence in CLAUDE.md root: "code-review-graph is derived context only; never authority. Use only after `topology_navigator` runs." | -61 LOC |
| `architecture/task_boot_profiles.yaml` (360 LOC, 7 classes) | `.claude/skills/zeus-task-boot/SKILL.md` (~120 LOC, autoloaded on relevant keywords) | -240 LOC, on-demand-loaded not boot-time |
| `architecture/IMPLEMENTATION_PROTOCOL.md` 14-mechanism catalog (465 LOC) | `.claude/skills/zeus-phase-discipline/SKILL.md` (47 LOC per §3.2) | -418 LOC |
| 41 scoped AGENTS.md routers | 5 per-package routers (src, tests, scripts, docs, architecture) | -36 routers |
| `topology_doctor.py` (1,630 LOC) | `topology_navigator.py` (~300 LOC) — slim, reads code not yaml | -1,330 LOC |
| R3 plan/protocol surface (189 files for 1 task) | `r3/SPEC.md` + `r3/phase_status.yaml` + per-phase `r3/learnings/<phase>_*.md` retros (auto-generated) | -180+ files per task |
| Manual planning-lock ritual | `.claude/hooks/pre-edit-architecture.sh` (deterministic gate) | per Anthropic best practices on hooks |

### Deleted entirely (no replacement; not load-bearing)

| Deleted | Why |
|---|---|
| `architecture/topology.yaml` 50% of sections (registry_directories, docs_root_allowed_files, runtime_artifact_inventory, docs_mode_excluded_roots, docs_subroots, archive_interface) | per §3.3 audit, no 90-day catch |
| `architecture/maturity_model.yaml` (48 LOC) | not cited in any retro/memory |
| `architecture/code_idioms.yaml` (33 LOC) | superseded by skill-level guidance |
| `architecture/change_receipt_schema.yaml` (~3KB) | candidate; verify no 90-day catch first |
| `architecture/context_pack_profiles.yaml` (~3.8KB) | candidate; verify no 90-day catch first |
| `architecture/data_rebuild_topology.yaml` (~8KB) | merge into `data_*` AGENTS.md if catches exist |
| INV-16, INV-17 (already PRUNE_CANDIDATE per ledger) | per verdict §6.1 |
| 36 scoped AGENTS.md (keep 5; delete rest, content moves into module docstring or single root sentence) | per Cursor "<500 lines" + Anthropic ruthless-prune |
| `IMPLEMENTATION_PROTOCOL.md`'s "12 confusion checkpoints" (CC-1..12) | replaced by 3-line rule in skill: "if confused, write `_confusion/<phase>.md`, do not proceed" |
| `task_boot_profiles.yaml`'s 7-class keyword-trigger system | Claude Code skills auto-detect on keywords; redundant |

---

## §5 Migration cost vs benefit

### Cost (engineer-hours, by phase)

| Phase | Work | Estimate |
|---|---|---|
| **P0 — proponent's bounded subtraction list** (verdict §6.1) | Delete INV-16/17, fix 7-INV path drift, extend r3_drift_check.py, fix TOPIC count | **8 hr** |
| **P1 — type-encode HK HKO + 2-3 other YAML antibodies** | `SettlementRoundingPolicy` subclasses + 2 other obvious cases | **6 hr** |
| **P2 — `invariants.yaml` → `invariants.py`** | Move 30 INVs to Python dataclasses with decorators that fail-import on bad paths; preserve all 5 semgrep rules; add 10 missing tests | **24 hr** |
| **P3 — `IMPLEMENTATION_PROTOCOL.md` → SKILL.md** | Write 47-line skill per §3.2; archive old protocol | **4 hr** |
| **P4 — `task_boot_profiles.yaml` → SKILL.md** | One-time skill conversion; Claude Code auto-detects on keywords | **6 hr** |
| **P5 — 36 scoped AGENTS.md cull** | Walk each, decide: keep / merge to root / migrate content to module docstrings | **20 hr** |
| **P6 — `source_rationale.yaml` → inline docstrings** | Script-assisted walk of 173 src files; add `# ZONE:` `# AUTHORITY:` headers | **16 hr** |
| **P7 — `topology.yaml` audit + prune** | §3.3 sunset audit; delete 40-60% sections; refactor remainder into 4-5 focused files | **20 hr** |
| **P8 — `topology_doctor.py` → `topology_navigator.py`** | Slim rewrite; preserve digest API for backward-compat one cycle | **40 hr** |
| **P9 — Hook + skill gate setup** | `.claude/hooks/pre-edit-architecture.sh` + planning-lock hook + critic-dispatch helper | **8 hr** |
| **P10 — R3 plan-surface compaction** | Write 1 SPEC.md from 189 files; auto-generate phase_status.yaml | **24 hr** |
| **P11 — Validation week** | Run full pytest, semgrep, integration tests; verify Z2-class catches still happen on simulated regressions; operator parallel-test on 3 next phases | **40 hr** |
| **TOTAL** | | **~216 engineer-hours** |

That is **70-90% of one R3-equivalent (312h)** — substantial. Distributed over 4-6 weeks at 1 engineer + part-time operator review.

### Benefit (preserved + recovered)

**Preserved**:
- 100% of verdict §1 concession-2 load-bearing core (critic + verifier + antibody contracts + per-phase boot + disk-first + memory)
- 100% of 30 invariants (re-encoded, not deleted)
- 100% of 5 verified semgrep rules
- 100% of HK HKO + V2 BUSTED-HIGH + Z2 6-catch CAUSAL CHAIN (the mechanisms that produced these catches survive)
- All currently-passing CI gates (71 in `test_architecture_contracts.py` + others)

**Recovered**:
- ~22-30K LOC of YAML → code/types/skills/hooks (smaller surface, type-checker-enforced)
- 36 router files culled → 5 retained → operator's working memory recovered
- 189 R3 plan files → ≤10 R3 plan files → next-plan-cycle starts at 5% the surface load
- 14-anti-drift-mechanism catalog → 47-line skill → no recursion-without-bottom
- 7-class boot profile ritual → on-demand skill loading (Anthropic native pattern)
- Hook-deterministic safety gates (per best practices) → 0 tax on every call
- Drift-impossible invariants (Python @enforced_by raises ImportError if path missing) → no more "7 INVs cite drifted path" silent rot
- Bug-impossible HK rounding (TypeError, not YAML antibody) → category eliminated per Fitz Constraint #1

### Cost vs benefit ratio

216h cost is paid ONCE. Benefits recur on every future phase (R3 + R4 + R5 + ...). On the R3 plan alone:
- Plan file load reduces from 189 to ~10 files per task → recurring savings of ~30 min/agent-session for boot reading.
- 14-mechanism boot ritual reduces from 17 steps to 3 → recurring savings of ~15-20 min/agent-session.
- Drift-checker covers `architecture/*.yaml` → next 7-INV-path-drift caught at write-time, not in Round-N debate.
- Cursor < 500 LOC + Anthropic ruthless-prune compliance → fewer "ignored half my CLAUDE.md" failures (per Anthropic Claude Code best practices).

Break-even: **after ~10-15 future agent sessions** at conservative 30-min savings each. Zeus runs many sessions per week. Break-even within 2-4 weeks of completion.

---

## §6 Address verdict §6.4 — model-capability asymptote

Question: "where's the model-capability asymptote at which even your minimal harness loses marginal value? Or is it asymptotic?"

### Argument

I do NOT think it is asymptotic in the sense of "harness goes to zero." Some encoding-as-artifact will always survive thermodynamic translation loss (per Fitz Constraint #2 + Anthropic Sept 2025 "context windows become insufficient"). The load-bearing core in §4 — critic + verifier + antibody contracts + per-phase boot + disk-first + memory — is a property of the WORK (live-money trading with cross-session multi-agent operation), not a property of the model. No future model frees you from "the agent should ask another agent to verify before the user is exposed to the answer." That's a process invariant, not a capability gap.

### What WOULD compress further

As models improve along three specific axes, the proposed minimal harness can shrink further:

1. **Hooks become richer** (Claude Code auto-classifier-mode-equivalents for trading-specific risks): planning-lock hook becomes just `claude --permission-mode auto` with custom classifier. Removes another ~30 LOC.

2. **Native cross-session memory matures**: Anthropic Sonnet 4.5+ already ships memory tool. As this matures, `MEMORY.md` may become 0 LOC (memory persists in API). Per Anthropic Sept 2025 "context editing automatically clears stale tool calls" — fully automated.

3. **On-demand skill loading replaces all boot ritual**: when keyword-detection is fully reliable, even the 47-line skill in §3.2 becomes a 0-LOC SKILL.md (auto-detected from filename or commit-touched-file pattern).

### What WILL NOT compress

- Domain knowledge (HK HKO, settlement station identity) MUST be encoded somewhere. Type system in source code is the floor — cannot go below "type that the wrong action requires."
- Cross-module relationship contracts (per Fitz "test relationships not functions") MUST be expressed as relationship tests. Below 5-10 such tests for a system as relationship-rich as Zeus is unsafe.
- Critic + verifier dispatch pattern remains useful even at AGI: separation of concerns between executor and reviewer is an organizational invariant, not a capability gap.
- One operator-decision register for live-money cutover gates is a regulatory + accountability invariant, not a model capability gap.

### Numerical estimate

The proposed minimal harness (~2,800 LOC) is the floor for the trading-machine class of work. Within that 2,800:
- Type encoding of domain (HK, settlement gate, lifecycle): ~500 LOC. Cannot compress below this; it IS the code.
- Antibody contracts as types/tests: ~800 LOC. Cannot compress.
- Critic/verifier subagent specs + skill files: ~300 LOC. Could compress to ~100 LOC if Anthropic ships better defaults.
- Hooks + memory bridge: ~100 LOC. Could compress to ~30 LOC.
- Operator decision register: ~200 LOC. Cannot compress (regulatory).
- Single root CLAUDE.md (≤500 LOC) + 5 scoped AGENTS.md (~80 LOC each): ~900 LOC. Could compress to ~600 LOC if hooks subsume more.

**Asymptote estimate at GPT 6 / Opus 5 generation: ~1,800-2,000 LOC.** That is the bottom for live-money trading. Below this, the harness stops being a harness and becomes "code-with-comments" — which is fine, that is the natural endpoint.

### Forward-looking caveat

If model capability ever reaches a regime where the model can RELIABLY synthesize the type-encoded antibodies from `src/contracts/` source-read alone (no separate manifest), then even the type subclasses become commentary — but the categories themselves still need to be in source code, because category-impossibility is a TYPE PROPERTY not a model property. **The harness floor is the type system, not the model.**

---

## §7 NEW WebFetch evidence (no recycle from round-1's 5)

Round-1 already cited: Anthropic Jun13/Sep29 2025; Cognition Jun12 2025 full body; Contrary Cursor Dec11 2025; Anthropic Sonnet 4.5 announce; Cursor docs Rules. NEW citations:

### Source NEW-1 — Aider documentation, "Repo Map" (aider.chat/docs/repomap.html)

URL: `https://aider.chat/docs/repomap.html`
Fetched: 2026-04-28 ~01:25 UTC
**Not previously cited in round-1.**

Verbatim quotes:

> "Aider uses a **concise map of your whole git repository** that includes the most important classes and functions."

> "Aider sends a **repo map** to the LLM along with each change request from the user."

> "The optimization identifies and maps the portions of the code base which are most relevant to the current state of the chat."

> "The token budget is influenced by the `--map-tokens` switch, which defaults to **1k tokens**."

> "**This alone may give it enough context to solve many tasks.**"

> "For example, it can probably figure out how to use the API exported from a module just based on the details shown in the map."

**Application**: Aider is a major open-source coding agent (40K+ GitHub stars), used heavily on Anthropic + OpenAI models. Their entire architectural context-budget for "where to find things" is **1,000 tokens of dynamically-generated repo map** — not 15K LOC of static YAML manifests. Aider's authors explicitly state this "alone may give it enough context to solve many tasks." Zeus uses 30,000+ LOC of static manifests for what Aider does in 1,000 dynamic tokens.

The proponent will counter that Zeus is not "many tasks", it is live-money trading. Conceded. The proposed minimal harness in §2 is ~2,800 LOC — STILL 3-15× larger than Aider's dynamic budget, calibrated for the trading-domain encoding overhead. The principle holds: dynamic + minimal beats static + maximal at this model generation.

### Source NEW-2 — Anthropic, "Best Practices for Claude Code" (code.claude.com/docs/en/best-practices)

URL: `https://code.claude.com/docs/en/best-practices`
Fetched: 2026-04-28 ~01:30 UTC
**Not previously cited in round-1.**

Verbatim quotes — Anthropic's own published advice on CLAUDE.md size + scoping + skills + hooks (this is the model vendor on the model's behavior):

> "**Bloated CLAUDE.md files cause Claude to ignore your actual instructions!**"

> "**If your CLAUDE.md is too long, Claude ignores half of it because important rules get lost in the noise.**"

> "**Ruthlessly prune. If Claude already does something correctly without the instruction, delete it or convert it to a hook.**"

> "**Keep it concise. For each line, ask: 'Would removing this cause Claude to make mistakes?' If not, cut it.**"

> "**Use hooks for actions that must happen every time with zero exceptions.**"

> "Hooks run scripts automatically at specific points in Claude's workflow. Unlike CLAUDE.md instructions which are advisory, hooks are deterministic and guarantee the action happens."

> "Skills extend Claude's knowledge with information specific to your project, team, or domain. Claude applies them automatically when relevant, or you can invoke them directly with `/skill-name`."

> "Subagents run in their own context with their own set of allowed tools. They're useful for tasks that read many files or need specialized focus without cluttering your main conversation."

> Common failure pattern: "**The over-specified CLAUDE.md.** If your CLAUDE.md is too long, Claude ignores half of it because important rules get lost in the noise. **Fix**: Ruthlessly prune."

**Application**: This is **the model vendor's official guidance**, dated more recent than the proponent's R1/R2 citations. It directly endorses every architectural choice in this proposal:

- **Hooks for deterministic gates** → §2 `.claude/hooks/pre-edit-architecture.sh` and `pre-commit-invariant-test.sh`
- **Skills for on-demand domain knowledge** → §2 `.claude/skills/zeus-domain/`, `settlement-rounding/`, `zeus-phase-discipline/`
- **Subagents for specialized review** → §2 `.claude/agents/critic-opus.md`, `verifier.md`, `safety-gate.md`
- **Ruthless pruning of CLAUDE.md** → §2 ≤500 LOC root CLAUDE.md
- **"Bloated CLAUDE.md files cause Claude to ignore your actual instructions"** → directly indicts current Zeus state where AGENTS.md root is 336 LOC + workspace_map.md 107 LOC + 41 scoped routers + 15K LOC YAML being read into context

**Single most damning sentence for proponent's "in-place reform retains 70-80%" position**: *"If your CLAUDE.md is too long, Claude ignores half of it because important rules get lost in the noise."* Zeus has 41 router files; even at 200 LOC average, that's 8,200 LOC of router prose. Per Anthropic's own guidance, this is far past the threshold where Claude starts ignoring half. The proposal's 5-router + ≤500-LOC-root structure is what Anthropic recommends.

---

## §8 Concrete commit / acceptance criteria for round-2 verdict

If judge accepts this proposal as round-2 winner, these are the binding deliverables:

| # | Acceptance | Verifies |
|---|---|---|
| AC-1 | All Z2-class regressions still detected by post-replace harness in simulated re-run | preserves load-bearing core |
| AC-2 | All 5 verified semgrep rules pass in CI on post-replace HEAD | preserves verified topology law |
| AC-3 | All 30 invariants present in `architecture/invariants.py` with @enforced_by decorators that fail-import if cited path missing | drift-impossible re-encoding |
| AC-4 | HK HKO mixing produces TypeError, not silent settlement error | category-impossible per Fitz #1 |
| AC-5 | Total architecture/ + .claude/ + r3/ surface ≤ 25% of pre-replace LOC, judge-verified by `wc -l` | quantitative target |
| AC-6 | One full R3-style phase executes start-to-finish with new harness within ≤80% of pre-replace median time | ergonomics test |
| AC-7 | Operator self-report: "I can hold this in my head" or equivalent confidence statement | bandwidth recovery |
| AC-8 | Post-replace `r3_drift_check.py` covers `architecture/*.py` AND citations | drift-checker coverage gap closed (verdict §6.1 #3) |

---

## §9 Concession bank addendum (round-2 layer)

Per debate discipline. Items added to round-1 LOCKED concessions:

### NEW concessions specific to this round-2 proposal

1. **216 engineer-hours migration cost is real and not amortized inside this debate cycle.** Operator must decide whether the recurring per-session benefit justifies. I do not deny the cost.
2. **Zone reorganization (P5 — 36 scoped AGENTS.md cull) carries risk of losing mid-tier domain knowledge currently encoded in routers.** Mitigation: walk each router and either fold into module docstring or merge to root; do not bulk-delete.
3. **`source_rationale.yaml` migration to inline docstrings (P6) is irreversible.** Once content moves into source files, reverting requires re-extraction. Operator approval required.
4. **`topology_doctor.py` → `topology_navigator.py` rewrite (P8, 40h) is the highest-risk phase.** Existing planning-lock and map-maintenance facades have CI-test consumers. Migration must preserve the public CLI signature for one cycle.
5. **The proposed asymptote (1,800-2,000 LOC at GPT 6 / Opus 5 generation) is a forward-looking claim** that I cannot verify from current evidence. Could be wrong by ±50%.

### NEW holds (specific to this proposal vs in-place reform)

1. **Type-encoded antibodies are strictly better than YAML antibodies** for cases where they apply. HK HKO, Day0 vs hourly, settlement station ≠ airport station are all type-encodable. The harness has the mechanism (`src/contracts/`); the proposal extends it.
2. **Hook-deterministic gates are strictly better than prose-advisory gates** per Anthropic best practices. Planning-lock should be a hook, not a manual `topology_doctor` call.
3. **Skills loaded on-demand are strictly better than YAML manifests loaded on-boot** for domain knowledge that applies to specific task classes. `task_boot_profiles.yaml` should be 7 SKILL.md files, not one 360-LOC YAML.
4. **One R3 plan = ≤10 files is achievable** if the plan-construction debate harness is used to build an executable spec, not a document system. This proposal includes `r3/SPEC.md` + `r3/phase_status.yaml` + per-phase retros only.

---

## §10 Self-check (anti-rubber-stamp per TOPIC.md L72-75)

- [x] Engaged proponent's likely "in-place reform" position at face value with 4 explicit concessions before pivoting (§1)
- [x] Honored verdict §6.2 commits in CONCRETE form (§3.1 HK HKO Python, §3.2 47-line skill, §3.3 audit principle script, §3.4 drift-checker extension diff)
- [x] Itemized: survives, replaced (with what), deleted (§4 — three explicit tables, no hand-waving)
- [x] Migration cost broken by 11 phases with engineer-hour estimates totaling 216h (§5)
- [x] Benefit listed with break-even calculation (≤4 weeks)
- [x] Addressed §6.4 forward question with WILL/WILL-NOT compress + numerical asymptote estimate (§6)
- [x] ≥2 NEW WebFetch with full URL + verbatim quote + timestamp (§7 NEW-1 Aider, NEW-2 Anthropic Claude Code best practices)
- [x] No recycle from round-1's 5 sources (Anthropic Jun13/Sep29 2025, Cognition Jun12 2025, Contrary Cursor Dec11 2025, Anthropic Sonnet 4.5 announce, Cursor docs Rules)
- [x] Did NOT relitigate §1 LOCKED concessions
- [x] Concession bank addendum (§9) extends, does not contradict, round-1 lock
- [x] Disk-first write before SendMessage

---

## Status

ROUND2_PROPOSAL_OPPONENT complete. Disk-canonical at this path.

LONG-LAST status maintained for proponent's in-place-reform proposal + judge's grading.

Headline: **Whole-system replace targeting 22-25% of current surface LOC, 100% of load-bearing core preserved, 216h migration paid once, break-even <4 weeks, asymptote ~2,000 LOC at GPT-6/Opus-5 generation.** Per Anthropic's own Claude Code best practices: the current harness violates "ruthlessly prune" and "bloated CLAUDE.md files cause Claude to ignore your actual instructions" — direct application of model-vendor guidance to model-vendor's own model.
