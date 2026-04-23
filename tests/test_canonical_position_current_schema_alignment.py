# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: INV-14 temperature_metric identity + CANONICAL_POSITION_CURRENT_COLUMNS
# ↔ kernel SQL alignment; T3.2b of the midstream remediation packet
# (docs/operations/task_2026-04-23_midstream_remediation/plan.md); plan
# T3.2b premise "AST-walk src/state/projection.py builders" corrected
# to structural constant/schema alignment because projection.py has no
# dict-returning builder functions.

"""Structural guards for `position_current` canonical schema alignment.

Three categories of drift are prevented by this file:

1. **INV-14 constant drift** — `temperature_metric` must stay in
   `CANONICAL_POSITION_CURRENT_COLUMNS`.
2. **INV-14 schema drift** — `architecture/2026_04_02_architecture_kernel.sql`
   `CREATE TABLE position_current (...)` must declare `temperature_metric`.
3. **Constant ↔ schema alignment** — every column listed in
   `CANONICAL_POSITION_CURRENT_COLUMNS` must appear in the kernel-SQL
   CREATE TABLE block, catching drift between the Python tuple and the
   migration SQL that was responsible for several downstream test
   failures during the midstream remediation packet.

The fix-plan v2 (T3.2b row) originally specified "AST-walk
`src/state/projection.py` builders", but fresh grep of projection.py
at commit 36f0189 showed no dict-returning builders. This file pivots
to the structural-alignment antibody that actually catches the drift
category (constant/schema mismatch) that caused T3.2 fixture breakage.
"""

from __future__ import annotations

import re
from pathlib import Path

ZEUS_ROOT = Path(__file__).parent.parent
KERNEL_SQL = ZEUS_ROOT / "architecture" / "2026_04_02_architecture_kernel.sql"


def _extract_create_table_position_current(kernel_sql_text: str) -> str:
    """Return the body between `CREATE TABLE IF NOT EXISTS position_current (` and its matching `);`.

    The regex is robust against leading/trailing whitespace and `\r\n`
    line endings between the final column and the closing `);`.
    Surrogate-critic note 2026-04-23: the earlier `\n\)` anchor was
    brittle against future migration reformatting; `\)\s*;` tolerates
    any whitespace (including none) before the semicolon.
    """
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS position_current\s*\((.*?)\)\s*;",
        kernel_sql_text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            "CREATE TABLE IF NOT EXISTS position_current not found in "
            f"{KERNEL_SQL}; check that the kernel migration file still "
            "defines the canonical table."
        )
    return match.group(1)


def test_canonical_position_current_columns_includes_temperature_metric() -> None:
    """INV-14: `temperature_metric` must be a canonical column of position_current.

    This catches the class of drift where a refactor accidentally
    removes the metric-identity column from the canonical tuple.
    """
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    assert "temperature_metric" in CANONICAL_POSITION_CURRENT_COLUMNS, (
        "temperature_metric missing from CANONICAL_POSITION_CURRENT_COLUMNS. "
        "INV-14 (see architecture/invariants.yaml) requires every "
        "temperature-market row to carry temperature_metric as part of "
        "its identity."
    )


def test_kernel_sql_position_current_declares_temperature_metric() -> None:
    """INV-14: `architecture/2026_04_02_architecture_kernel.sql` CREATE TABLE must declare temperature_metric.

    This catches the class of drift where the migration SQL falls out
    of sync with the canonical constant.
    """
    kernel_sql_text = KERNEL_SQL.read_text()
    create_block = _extract_create_table_position_current(kernel_sql_text)

    assert re.search(r"\btemperature_metric\b", create_block), (
        "temperature_metric missing from architecture/2026_04_02_architecture_kernel.sql "
        "CREATE TABLE position_current block. INV-14 requires the column "
        "to be declared in the canonical schema."
    )


def test_canonical_constants_match_kernel_sql_position_current_columns() -> None:
    """Constant ↔ schema alignment: every CANONICAL_POSITION_CURRENT_COLUMN must appear in the kernel SQL CREATE TABLE.

    This was the drift category responsible for several midstream test
    failures during the 2026-04-23 remediation packet: the Python
    tuple and the migration SQL diverged on which columns defined the
    canonical row, causing `require_payload_fields` to reject payloads
    that the schema would otherwise accept (and vice versa).
    """
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    kernel_sql_text = KERNEL_SQL.read_text()
    create_block = _extract_create_table_position_current(kernel_sql_text)

    missing = [
        column
        for column in CANONICAL_POSITION_CURRENT_COLUMNS
        if not re.search(rf"\b{re.escape(column)}\b", create_block)
    ]
    assert not missing, (
        "Columns listed in CANONICAL_POSITION_CURRENT_COLUMNS but missing "
        f"from the kernel SQL CREATE TABLE position_current block: {missing}. "
        "Either add them to the migration or remove them from the canonical "
        "constant — the two must stay aligned for the canonical write path "
        "to function."
    )
