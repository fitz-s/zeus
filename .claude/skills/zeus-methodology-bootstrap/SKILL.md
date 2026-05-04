---
name: zeus-methodology-bootstrap
description: Auto-loads the Zeus adversarial-debate methodology on session boot when the task involves multi-agent coordination, adversarial debate, contamination remediation, or any cycle ≥ 2 of structured pro/con dialogue. Triggers on compound-phrase keywords (per critic MED-CAVEAT-S4-1 fix to avoid over-firing on routine Zeus drift/verdict work): "adversarial debate", "critic-gate", "debate verdict", "verdict drift", "round verdict", "concession bank", "contamination remediation", "anti-drift methodology", "phase discipline", "longlast teammate", "cross-session merge", "stage-gated revert", "process gate", "5th outcome", "audit-first mode", "verdict erratum". Note: bare keywords "drift" and "verdict" intentionally REMOVED — empirical 138 commits in 30 days mention "drift" (data drift, signal drift, calibration drift) which would over-fire the auto-load. Returns the methodology doc as required reading + the 5-for-5 case study summary + the 5th outcome category from contamination remediation 2026-04-28.
---

# Zeus Methodology Bootstrap

## Purpose

Ensure the adversarial-debate methodology (`docs/methodology/adversarial_debate_for_project_evaluation.md`, ~785 lines) loads into the active session's context BEFORE any debate-class work begins. Prevents the cross-session knowledge loss pattern that contributed to the 2026-04-28 contamination event (other session did not know about §5.Z2 codified gates → bypassed bidirectional grep → produced 6 drift items including 815k mislabeled production rows).

## When this SKILL fires

Auto-triggers on session boot when ANY of these hold:
- Task description contains keywords listed in the `description` frontmatter
- Active task is a Mode C (longlast multi-batch) or Mode D (adversarial debate) per `.agents/skills/zeus-ai-handoff/SKILL.md` §3
- Operator references "another cycle", "next round", "the methodology", "critic gate", "stage-gated revert", "5th outcome"
- Recent commits in active branch reference `task_*_harness_debate/` or `task_*_contamination_remediation/`
- New session resuming work after compaction in a debate-class context

## What this SKILL provides

When fired, this SKILL ensures the agent reads:

1. **Methodology doc**: `docs/methodology/adversarial_debate_for_project_evaluation.md` (master text)

2. **5-for-5 case study summary** (the empirical track record — load these into immediate context):
   - **§5.X BATCH D INV-16/17**: prescribed DELETE → audit found 9 hidden tests → REVERT + add tests:; first case study of "apparent gap may be intentional"
   - **§5.Y Phase 2 auto-gen registries**: prescribed AUTO-GEN → audit found 95% intentional curation
   - **§5.Z Phase 3 module_manifest**: prescribed REPLACE → audit 21 KEEP / 4 HYBRID / 0 REPLACE
   - **§5.Z2 Phase 4 @enforced_by**: prototype works for uniform domain; HYBRID for cross-cutting; bounded confirmation
   - **§5.Z3.1 (NEW from cycle 5) Cycle 5 contamination remediation**: 5th outcome category formalized; quantitative ≥3 errata/cycle audit-first trigger added

3. **§5.Z3 4-outcome categories + 5th meta-level outcome**:
   - 1. **Falsified** — don't change; erratum upstream
   - 2. **Confirmed bounded** — change at bounded scope with discipline
   - 3. **Confirmed unbounded** — change at full scope (rare; multiple pass-gates required)
   - 4. **Inconclusive** — defer; iterate on the audit
   - 5. **CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION (Stage-gated revert)** — for already-contaminated state across session boundaries; revert+quarantine NOW + condition future restoration on independent critic gate + staged restoration as evidence accumulates

4. **§5.Z2 codified gates** (mandatory before locking ANY % claim):
   - Bidirectional grep (forward: manifest cites field? + reverse: target system back-cites identity?)
   - Intent-aware audit (does the code's stated intent match its observed behavior?)
   - Default-deny on grep-only evidence

5. **§5.Z3.1 quantitative erratum-frequency trigger**: ≥3 errata/cycle → next cycle MUST start audit-first mode

## Cross-references

- **`.claude/skills/multi-agent-debate-and-execution/SKILL.md`** (NEW v1 2026-05-03) — generic reusable EXECUTION + CLOSURE phase skill. Covers per-packet boot template, GO/DONE/REVIEW cycle, 3-batch decomposition, PATH A precision-favored framing, K3-adjacent surface pre-flag, HONEST DISCLOSURE pattern, 10-ATTACK template, co-tenant safe staging, cross-module orchestration seam. Embeds 5-packet 32-cycle Zeus R3 §1 #2 case study. Mirror at `~/.claude/skills/multi-agent-debate-and-execution/SKILL.md` for cross-project reuse. Methodology doc covers DEBATE phase; this new skill covers EXECUTION + CLOSURE.
- `.agents/skills/zeus-ai-handoff/SKILL.md` (Mode C/D selection)
- `.claude/skills/zeus-phase-discipline/SKILL.md` (Mode C per-batch; 14 anti-drift mechanisms compressed)
- Root `AGENTS.md` (Required Reads for adversarial work; §3 "What to read by task" → Adversarial debate / multi-agent / contamination remediation bullet)
- `docs/operations/task_2026-04-27_harness_debate/` (4-cycle case studies: R1+R2+R3+Tier 2)
- `docs/operations/task_2026-04-28_contamination_remediation/` (5th cycle + 5th outcome category instantiation)

## Memory references (load if relevant to current task)

- `feedback_critic_prompt_adversarial_template` — 10-attack template; never write "narrow scope self-validating" or "pattern proven"
- `feedback_executor_commit_boundary_gate` — executor cannot self-approve over multi-batch work; independent critic mandatory
- `feedback_zeus_plan_citations_rot_fast` — file:line citations rot ~20-30%/week; grep-verify within 10 min before lock
- `feedback_converged_results_to_disk` — SendMessage drop pattern is empirical; disk is canonical
- `feedback_idle_only_bootstrap` — spawn longlast teammates with idle-only boot prompt
- `feedback_no_git_add_all_with_cotenant` — `git add` SPECIFIC files; never `-A` with co-tenant active
- `feedback_multi_angle_review_at_packet_close` — 5 parallel sub-agents (architect/critic/explore/scientist/verifier) before declaring DEBATE_CLOSED

## Maintenance

This SKILL is **living**. Update after each methodology cycle that surfaces new patterns or new outcome categories. Cite this SKILL in new methodology cycles' TOPIC.md as the boot context loader.

When promoting to OMC/global skill: copy to `~/.claude/skills/zeus-methodology-bootstrap/SKILL.md` and adjust paths.

## Lineage

v1 (2026-04-28): created during contamination remediation Stage 4 Gate D. Replaces the implicit "methodology will be loaded if agent thinks to" pattern with explicit auto-load on debate-class tasks. Born from the 2026-04-28 contamination event where another session did not know §5.Z2 codified gates and bypassed bidirectional-grep audit, producing 6 drift items.
