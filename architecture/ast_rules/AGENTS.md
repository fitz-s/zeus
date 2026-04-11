# architecture/ast_rules AGENTS

AST-level enforcement rules. These are machine-checkable patterns that prevent known-bad code from entering the repo.

## File registry

| File | Purpose |
|------|---------|
| `semgrep_zeus.yml` | Semgrep rules for Zeus-specific code enforcement |
| `forbidden_patterns.md` | Human-readable forbidden pattern catalog (FM-01 through FM-10), aligned with `negative_constraints.yaml` |

## Rules

- Forbidden patterns must have corresponding IDs in `../negative_constraints.yaml`
- Semgrep rules must be testable — include test cases or reference test files
- Changes here are governance changes (this is inside `architecture/`)
