---
gate_id: gate3_commit_time
name: Gate 3 — Commit-time diff verifier
phase: 4.B
mandatory: false
sunset_date: 2026-08-04
evidence:
  - phase3_h_decision.md F-7 (non-py path-match enforcement)
  - phase2_h_decision.md F-7 (authority_doc_rewrite + archive_promotion non-py)
  - ULTIMATE_DESIGN §5 Gate 3
  - IMPLEMENTATION_PLAN §6 days 61-64
feature_flag: ZEUS_ROUTE_GATE_COMMIT=off
implementation: src/architecture/gate_commit_time.py
hook_wiring: scripts/pre-commit-capability-gate.sh (git pre-commit or PreToolUse Bash)
ritual_signal: logs/ritual_signal/YYYY-MM.jsonl
non_py_enforcement: path-match-only (F-7 mandatory condition — no AST walk)
schema_version: 1
---

# Gate 3: Commit-time diff verifier

Reads `git diff --cached --name-only HEAD` for staged changes. For each path:

- **.py paths**: AST-walks to confirm `@capability` decorator is present on at
  least one function in the changed file.
- **non-.py paths** (F-7 mandatory condition from phase3_h_decision.md):
  path-match-only against `capabilities.yaml::hard_kernel_paths` — no AST walk.
  This is the sole enforcement mechanism for `authority_doc_rewrite` and
  `archive_promotion` capabilities whose `hard_kernel_paths` contain only
  non-.py files (AGENTS.md, LIVE_LAUNCH_HANDOFF.md, etc.).

Reads pending commit message from `.git/COMMIT_EDITMSG`. If
`original_intent.out_of_scope_keywords` match the commit message (case-insensitive
word boundary), rejects the commit with a structured error.

Emits one `ritual_signal` JSON line per evaluation.
Feature flag `ZEUS_ROUTE_GATE_COMMIT=off` short-circuits all checks.
Sunset: 2026-08-04 (90 days from authoring per CHARTER §5).
