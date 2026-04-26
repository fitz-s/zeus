# P1 Work Log

## 2026-04-26 — P1.S1: Schema + Repo

### Scope landed

Created the durable command journal infrastructure: two new DB tables, an
append-only repo API, manifest law additions, a semgrep guard, and a test
suite. No live writers introduced (those land in P1.S3+).

### Sequence executed

1. Read `implementation_plan.md` §P1.S1 and `decisions.md` to fix exact schema
   and transition table before touching any file.
2. Extended `src/state/db.py::init_schema()` — appended `venue_commands` and
   `venue_command_events` `CREATE TABLE IF NOT EXISTS` blocks at the end of the
   existing `executescript("""...""")` body, plus 5 `CREATE INDEX IF NOT EXISTS`
   statements. Idempotent by design.
3. Created `src/state/venue_command_repo.py` — 6 public functions, 33 legal
   transitions encoded in `_TRANSITIONS` dict, atomicity via `with conn:`,
   positional row access inside `append_event` to be row_factory-agnostic.
4. Added INV-28 to `architecture/invariants.yaml` (exact plan wording).
5. Added NC-18 to `architecture/negative_constraints.yaml` (exact plan wording).
6. Added `zeus-no-direct-venue-command-update` to
   `architecture/ast_rules/semgrep_zeus.yml` (exact plan rule body).
7. Added FM-NC-18 entry to `architecture/ast_rules/forbidden_patterns.md`
   following FM-NC-16 pattern.
8. Updated `tests/test_architecture_contracts.py`:
   - `test_semgrep_rules_cover_core_forbidden_moves` now checks 5 rule IDs
     (added `zeus-no-direct-venue-command-update`).
   - `test_init_schema_creates_venue_command_tables` (new) verifies both tables
     and all 5 indexes on fresh in-memory DB.
9. Created `tests/test_venue_command_repo.py` — 44 tests across 8 test classes
   covering atomicity, grammar, idempotency, find_unresolved, list_events,
   payload round-trip, and AST-walk NC-18 enforcement.

### Verification commands

```
pytest tests/test_venue_command_repo.py -v
  → 44 passed

pytest tests/test_architecture_contracts.py::test_init_schema_creates_venue_command_tables \
       tests/test_architecture_contracts.py::test_semgrep_rules_cover_core_forbidden_moves -v
  → 2 passed

pytest tests/test_p0_hardening.py --tb=no -q
  → 25 passed, 1 skipped  (baseline parity)

pytest tests/test_phase5a_truth_authority.py tests/test_phase8_shadow_code.py \
       tests/test_executor_typed_boundary.py tests/test_pre_live_integration.py \
       tests/test_architecture_contracts.py tests/test_runtime_guards.py \
       tests/test_live_execution.py tests/test_dual_track_law_stubs.py --tb=no -q
  → 18 failed, 234 passed, 25 skipped  (≤18 baseline; same pre-existing failures)
```

### Touched files

- `src/state/db.py` — schema extension (venue_commands, venue_command_events + indexes)
- `src/state/venue_command_repo.py` — new module
- `tests/test_venue_command_repo.py` — new test file (44 tests)
- `architecture/invariants.yaml` — INV-28
- `architecture/negative_constraints.yaml` — NC-18
- `architecture/ast_rules/semgrep_zeus.yml` — zeus-no-direct-venue-command-update
- `architecture/ast_rules/forbidden_patterns.md` — FM-NC-18
- `tests/test_architecture_contracts.py` — 2 updates

### Commit

`0a7845f` — Land P1.S1: venue_commands schema + repo + INV-28 / NC-18

### State transition table

Implemented exactly as specified in `implementation_plan.md` §P1.S1.
No additions or removals. Table verified by parametrized illegal-transition
tests (23 parametrize cases) + 8 legal-transition positive tests.
