---
name: zeus-task-boot-graph-review
description: Boot profile for graph/Code-Review-Graph/CRG/impact/blast-radius/review tasks. Auto-loads when using Code Review Graph for review/debug routing in Zeus. Keeps semantic boot ordered before graph use. Replaces the graph_review entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12). Cross-link: code_review_graph_protocol.yaml is DEPRECATED per BATCH A; root AGENTS.md §Code Review Graph is authoritative.
model: inherit
---

# Zeus task boot — graph_review

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `graph_review` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: graph, Code Review Graph, CRG, impact, blast radius, review.

## Required reads (in order)

1. AGENTS.md (root) — §Code Review Graph (the authoritative summary post-BATCH A)
2. workspace_map.md
3. architecture/task_boot_profiles.yaml (DEPRECATED stub but task-class catalog still useful) OR this skill family
4. architecture/fatal_misreads.yaml
5. architecture/source_rationale.yaml

## Current-fact surfaces

- docs/operations/current_state.md

## Required proofs (answer BEFORE editing)

1. **semantic_boot_before_graph**: Has the task class been identified before graph impact analysis?
   - Evidence: architecture/task_boot_profiles.yaml (or the matching .claude/skills/zeus-task-boot-* skill), architecture/fatal_misreads.yaml
2. **graph_is_derived_context**: Is graph output used only for where/impact/review order?
   - Evidence: AGENTS.md, workspace_map.md

## Fatal misreads (load these antibodies)

- code_review_graph_answers_where_not_what_settles

## Forbidden shortcuts

- Do NOT let graph results waive required reads, proofs, planning lock, or tests.
- Do NOT ask graph to decide settlement / source / current truth.
- Prefer explicit `--changed-files` / `--repo-root` when graph tooling needs impact scope.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot
- Use for: impacted files, callers, tests, review order
- NOT for: semantic truth, source validity, packet authority

## Verification gates

```
python3 scripts/topology_doctor.py --code-review-graph-status --json
python3 scripts/topology_doctor.py --task-boot-profiles --json
```
