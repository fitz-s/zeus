---
name: zeus-phase-discipline
description: Heuristics for executing a multi-phase Zeus implementation slice without drift. Auto-loads when the user is working on r3 phases, slice cards, or any multi-session implementation pulled from docs/operations/task_*/r3/slice_cards/*.yaml. Replaces the 14-mechanism IMPLEMENTATION_PROTOCOL.md catalog with a screen-sized operating heuristic per Anthropic Claude Code best practices "Bloated CLAUDE.md files cause Claude to ignore your actual instructions" + Cursor "<500 lines" + Anthropic Jun 2025 "good heuristics rather than rigid rules".
model: inherit
---

# Zeus phase-execution heuristics

Source: round2_verdict.md §1.1 #7 + opponent §3.2. Replaces 14-mechanism catalog at `docs/operations/task_2026-04-26_ultimate_plan/r3/IMPLEMENTATION_PROTOCOL.md`.

When executing a phase from `docs/operations/task_*/r3/slice_cards/<phase>.yaml`:

## Boot (3 steps, not 17)

1. Read the slice card + the immediate predecessor's `learnings/<phase>_*_retro.md` if it exists.
2. Run `python3 scripts/topology_doctor.py --task-boot-profiles --task <phase> --files <files>` (returns: changed-files, gates, semgrep status, drifted citations).
3. If any cited file:line returns SEMANTIC_MISMATCH or FILE_MISSING, write `r3/_blocked_<phase>.md` and STOP. Do NOT implement.

## During implementation (rules of thumb, not rigid checklist)

- Antibody contracts (NC-NEW-A..J) are SQL/semgrep, not prose; if a new behavior would violate, the test fails BEFORE merge.
- Citations rot. When you cite a file:line, also cite a SYMBOL (function/class). The drift-checker re-verifies on the symbol.
- **Bidirectional grep before any "X% of Y lack Z" claim**: forward (manifest cites field?) AND reverse (target system back-cites identity?). Schema-citation gap (forward only) ≠ enforcement gap (both). A grep on `architecture/invariants.yaml:tests:` plus a separate `grep -rn "INV-[0-9]\+" tests/` together — diff is the real gap. See methodology §5.X case study (round-1 verdict §10 erratum: 33% LARP claim was 5x overcount because reverse grep was skipped).
- Frozen interfaces are downstream-stable. If you need to break one, write `r3/_protocol_evolution/<topic>.md` first.
- Every public API a downstream phase consumes must have at least one cross-phase relationship test.
- DB-canonical-truth direction (INV-17 spirit) is one-way: DB > derived JSON > reports. Never write the reverse direction.

## Closeout (3 steps)

1. Dispatch critic subagent (`~/.claude/agents/critic.md` — universal; no repo-local override needed) with the diff. If critic flags spirit-mismatch, fix and re-dispatch.
2. Dispatch verifier subagent (`.claude/agents/verifier.md`) with the test results. If verifier flags coverage gap, address.
3. Write `learnings/<phase>_<author>_<date>_retro.md`: what changed, what critic/verifier caught, what RULES_TO_CARRY_FORWARD this phase produced.

## Forbidden shortcuts

- "tests pass" alone ≠ shipped. Critic + verifier dispatch is mandatory.
- Bypassing the antibody contract via mock is itself a Z2-class regression.
- Slice card YAML must parse before claiming phase is reusable.
- Co-tenant `git add -A`: never. Always stage specific files (memory `feedback_no_git_add_all_with_cotenant`).

## When to stop and ask the operator

- Cited gate is in the operator-decisions register: STOP. Do not implement default.
- Cited NC or INV is marked PRUNE_CANDIDATE: STOP. Pruning is operator decision.
- More than 4 files changed in a single phase that does not declare cross-zone scope: STOP. Plan first (planning-lock applies, see safety-gate agent).

That's it. The 14-mechanism catalog rotted into prose. This SKILL.md is what survives translation across sessions per Fitz Constraint #2 (translation loss is thermodynamic; ~20% design-intent survival; encode insight into structure).
