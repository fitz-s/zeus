# verifier proof-of-done for T2H B1
HEAD: 1116d827
Verifier: verifier-sonnet
Date: 2026-05-05

## Claim
Executor cleaned two cargo-cult inline imports from `src/engine/cycle_runner.py`
(`_gtcww` dead-import deleted, `ZEUS_WORLD_DB_PATH` hoisted to module-level) and
wrote `docs/runbooks/T2G_database_locked_degrade_runbook.md` covering the four
required sections per T2H-OPERATOR-RUNBOOK-PRESENT.

## Verdict
VERIFIED

---

## CHECK 1 [STATUS: VERIFIED]
Acceptance criteria per `phase.json test_commands` and `asserted_invariants`:

**T2H-CYCLE-RUNNER-NO-DEAD-IMPORT**: AST walk confirms `_gtcww` is absent.
```
python -c "import ast, pathlib; tree = ast.parse(...); names = [n for n in ast.walk(tree) if isinstance(n, ast.Name)]; found = [n.id for n in names if n.id == '_gtcww']; assert not found"
→ PASS: _gtcww not in AST
```
`grep -rn '_gtcww' src/` exits 1 (no matches). Dead import is gone.

**T2H-CYCLE-RUNNER-WORLD-PATH-MODULE-LEVEL**: Two hits for `ZEUS_WORLD_DB_PATH`:
- Line 58: inside module-level `from src.state.db import (...)` block — the import
- Line 81: inside `get_connection()` body — the usage `(str(ZEUS_WORLD_DB_PATH),)`
No inline `from src.state.db import ZEUS_WORLD_DB_PATH as _world_path` remains.
The function-scope import line is deleted; the name is used directly via the
hoisted module-level binding.

**T2H-CYCLE-RUNNER-IMPORT-NO-REGRESSION**:
```
zeus-venv python -c 'import src.engine.cycle_runner; print("OK")'
→ OK
```
Import succeeds. No circular import emerged from the hoist (confirming the
executor's circular-import defense was cargo-cult, not load-bearing).

**AMD-T2H-2 four-name preservation**: Diff shows only one added line
(`ZEUS_WORLD_DB_PATH`) in the import block; the four pre-existing names
`_zeus_trade_db_path`, `connect_or_degrade`, `get_trade_connection_with_world`,
`record_token_suppression` are present byte-identical, same relative order.

```
get_connection.__doc__: 'T2G: Acquire trade+world DB connection via connect_or_degrad...'
→ PASS (docstring asserted present)
```

**T2H-OPERATOR-RUNBOOK-PRESENT** (exact invariant checks reproduced):
```
wc -l docs/runbooks/T2G_database_locked_degrade_runbook.md          → 191  (≥50 PASS)
grep -c -E 'Pre-T2G|Post-T2G|alert-rule|db_write_lock_timeout_total|launchd' → 25  (≥5 PASS)
grep -c 'PLACEHOLDER pending first-7-day production calibration'             →  3  (≥3 PASS)
```

**T2H-RUNBOOK-CITES-AUTHORITY** (four distinct citation terms):
```
ee94539f              → 3 lines
AMD-T2-2              → 4 lines
T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH → 3 lines
operator_awareness    → 3 lines
Total matching lines  → 12  (≥4 PASS)
```

---

## CHECK 2 [STATUS: VERIFIED]
Regression baseline: `test_cycle_runner_db_lock_degrade.py` is the stated
regression contract per scope.yaml and phase.json notes.

```
zeus-venv python -m pytest tests/test_cycle_runner_db_lock_degrade.py -q
→ 4 passed
```

Note: `tests/test_cycle_runner_smoke.py` cited in `phase.json test_commands` does
not exist — pre-existing absence (not present in unmodified repo). The
scope.yaml notes acknowledge this: "existing T2G test suite is the regression
contract" and the only extant file is `test_cycle_runner_db_lock_degrade.py`.
This is not a T2H regression.

T2H explicitly adds no new tests (scope.yaml `out_of_scope: tests/**`). The 4
pre-existing tests continue to pass — no regression introduced.

---

## CHECK 3 [STATUS: VERIFIED]
Artifact existence and shape:

**src/engine/cycle_runner.py** (modified):
```
git diff src/engine/cycle_runner.py
```
Shows exactly:
- `+    ZEUS_WORLD_DB_PATH,` (1 line added in module-level import block)
- `-    from src.state.db import get_trade_connection_with_world as _gtcww` (dead import deleted)
- `-        from src.state.db import ZEUS_WORLD_DB_PATH as _world_path` (inline import deleted)
- `-            conn.execute("ATTACH DATABASE ? AS world", (str(_world_path),))` (reference deleted)
- `+            conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))` (updated)

Net: -3 lines, +2 lines (hoist line + usage reference). Structural purity confirmed —
no behavior change, only import shape change.

**docs/runbooks/T2G_database_locked_degrade_runbook.md** (new):
```
ls -la → -rw-r--r--  8228 bytes  May  5 04:23
wc -l  → 191 lines
head   → # T2G: Database-Locked Degrade Runbook
          <!--
          # Created: 2026-05-05
          # Last reused or audited: 2026-05-05
          # Authority basis: T2G phase.json + AMD-T2-2 + invariants.jsonl T2G close + SEC-MEDIUM-1+2
          -->
          > **Runbook class**: Durable Operator Runbook
          > **Behavior-change commit**: ee94539f (T2G)
```
191 lines, 8228 bytes — well within expected 150-180 LOC range (runbook is
slightly larger; all four sections present).

---

## CHECK 4 [STATUS: VERIFIED]
Cross-module side effects:

The two deleted inline import lines were inside `get_connection()` body. The
hoisted `ZEUS_WORLD_DB_PATH` now binds at module level. No callers of
`get_connection` are affected — the function's signature and return type are
unchanged. Usage of `_world_path` (the deleted alias) was confined to a single
`conn.execute()` call on the same line; that call now references `ZEUS_WORLD_DB_PATH`
directly with identical runtime value.

`_gtcww` was imported but never referenced after the import — confirmed by:
1. AST walk finding zero `ast.Name` nodes with `id == '_gtcww'`
2. `git diff` showing the import line removed with no compensating use-site changes

Scope compliance: only two files changed — `src/engine/cycle_runner.py`
(modified) and `docs/runbooks/T2G_database_locked_degrade_runbook.md` (new).
Both are exactly the `in_scope` files declared in scope.yaml. No out-of-scope
files were touched.

---

## CHECK 5 [STATUS: VERIFIED]
Cold-start reproducibility:

The diff is self-explanatory:
- One import added to the module-level block (`ZEUS_WORLD_DB_PATH`)
- Two function-scope import lines deleted
- One `_world_path` reference updated to `ZEUS_WORLD_DB_PATH`

The git diff alone is sufficient to reconstruct the rationale: the module-level
block already imported other names from `src.state.db`; the function-scope
imports were redundant; `_world_path` and `_gtcww` were local aliases
eliminated by the hoist. No tribal knowledge required — the diff is complete.

The runbook's provenance header (`# Created: 2026-05-05`, `# Authority basis:
T2G phase.json + AMD-T2-2 + invariants.jsonl T2G close + SEC-MEDIUM-1+2`) and
the explicit citations to `ee94539f`, `AMD-T2-2`, `T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH`,
and `operator_awareness` make the runbook traceable to the packet surface without
packet-doc archaeology.

---

## Deviations
**test_cycle_runner_smoke.py absent**: `phase.json test_commands` lists
`tests/test_cycle_runner_smoke.py` but this file does not exist in the
repository. This is a pre-existing absence — T2H did not create or delete it.
The scope.yaml is explicit that no new tests are permitted and the existing
`test_cycle_runner_db_lock_degrade.py` (4 tests, all passing) is the full
regression contract. Not a T2H failure.

---

VERIFIER_DONE_T2H: PASS
tests_cycle_runner=4/4
_gtcww_grep_count=0
ZEUS_WORLD_DB_PATH_hits=2 (line_58_module_level + line_81_usage)
runbook_lines=191
runbook_keyword_hits=25
placeholder_count=3
authority_citation_lines=12
import_clean=OK
