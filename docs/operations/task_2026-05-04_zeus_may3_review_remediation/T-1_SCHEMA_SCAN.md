# T-1_SCHEMA_SCAN.md

**Artifact:** T-1.3 (MASTER_PLAN_v2 para 7)
**Produced:** 2026-05-04T16:49:38Z
**Branch/HEAD:** source-grep-header-only-migration-2026-05-04 / 1116d827

---

## Command 1

Command: git grep -n CREATE TABLE settlement_commands

Output:

    docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:293
    docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:480
    docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:485
    docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:941
    docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:958
    src/execution/settlement_commands.py:28:CREATE TABLE IF NOT EXISTS settlement_commands (
    src/state/db.py:1398:        CREATE TABLE IF NOT EXISTS settlement_commands (

## Command 2

Command: git grep -n SETTLEMENT_COMMAND_SCHEMA

Output:

    MASTER_PLAN_v2.md:294 (grep command reference)
    MASTER_PLAN_v2.md:480 (prose reference)
    src/execution/settlement_commands.py:27:SETTLEMENT_COMMAND_SCHEMA = ...
    src/execution/settlement_commands.py:196:    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)

## Command 3

Command: git grep -n CREATE TABLE IF NOT EXISTS settlement_commands

Output:

    MASTER_PLAN_v2.md:480 (prose)
    MASTER_PLAN_v2.md:958 (prose)
    src/execution/settlement_commands.py:28:CREATE TABLE IF NOT EXISTS settlement_commands (
    src/state/db.py:1398:        CREATE TABLE IF NOT EXISTS settlement_commands (

---

## Schema sources analysis

Total distinct source files with inline DDL: 2

| File | Classification | Notes |
|---|---|---|
| src/execution/settlement_commands.py:27-52 | canonical-DDL | Defines SETTLEMENT_COMMAND_SCHEMA string; consumed by conn.executescript(SETTLEMENT_COMMAND_SCHEMA) at line 196. Intended single source. |
| src/state/db.py:1395-1430 | derived-loader DUPLICATE T1A target | Independent inline executescript with full CREATE TABLE DDL in DB init path. Comment says kept here to avoid circular import src.execution; DDL is fully inlined rather than imported. |

Schema equivalence: both blocks define same 14 columns and 3 indexes.
Indexes: idx_settlement_commands_state, idx_settlement_commands_condition, ux_settlement_commands_active_condition_asset.

T1A acceptance criterion: repo grep finds exactly one inline CREATE TABLE settlement_commands.
Current state: NOT MET (2 definitions exist).
T1A collapses: 2 sources to 1. db.py inline DDL removed; db.py calls canonical init from settlement_commands.py.

Doc-reference matches (not DDL): MASTER_PLAN_v2.md lines 293, 480, 485, 941, 958 are plan prose/reference only.
