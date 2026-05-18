# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: Wave-3 Batch D MIN-1 (PR critic SEV-3 carry-forward)
#   K1 antibody scope extension: scan all of src/ + scripts/ for bare
#   FROM <WORLD_ONLY_TABLE> references — not just K1_FIXED_SCRIPTS.
#
#   Background (MIN-1): tests/test_k1_reader_isolation.py scans only
#   bridge_oracle_to_calibration.py and evaluate_calibration_transfer_oos.py.
#   Bare `FROM validated_calibration_transfers` exists in:
#     src/data/calibration_transfer_policy.py:854
#     src/engine/evaluator.py:555
#   Currently test-only callers, but if either gets wired to a
#   get_forecasts_connection_with_world() MAIN-forecasts connection in a future
#   PR they become silent dead-reads (exactly the F40/F41 pattern).
#   This antibody catches the CATEGORY project-wide, permanently.

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SCRIPTS = REPO / "scripts"

# World-class tables: must be qualified as `world.<table>` when read under a
# forecasts-rooted connection (get_forecasts_connection_with_world).
# Kept in sync with WORLD_ONLY_TABLES_UNDER_K1 in test_k1_reader_isolation.py.
WORLD_ONLY_TABLES = {
    "temp_persistence",                # F102 — world_class, ETL writes to zeus-world.db
    "validated_calibration_transfers", # F41 — world_class
    "observation_instants_v2",         # F43 — 1.8M rows in world.db
    "platt_models_v2",                 # F43 — 1.4K rows in world.db
    "data_coverage",                   # F43 — world-class (cross-DB write target post-K1)
    "daily_observation_revisions",     # F43 — world-class
}

def _strip_comments(src: str) -> str:
    """Strip Python and SQL inline comments to avoid false positives on commented SQL."""
    out = []
    for line in src.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _collect_src_files() -> list[Path]:
    files = []
    for root in (SRC, SCRIPTS):
        if root.exists():
            files.extend(root.rglob("*.py"))
    return files


def test_k1_scripts_qualify_world_class_tables():
    """MIN-1 antibody: any script in scripts/ that imports get_forecasts_connection_with_world
    must qualify all world-class table references as `world.<table>`.

    Scripts are single-purpose; if a script imports the K1 helper, all SQL in that file
    runs under MAIN=forecasts.db, so bare world-class table names silently resolve to
    zero-row MAIN shells — the F40/F41 pattern.

    src/ modules that import the helper may use multiple connection types within the same
    file (e.g. ingest_main.py uses both get_world_connection and get_forecasts_connection_
    with_world in different code paths). Those are checked by separate sentinel tests.
    """
    K1_HELPER_MARKER = "get_forecasts_connection_with_world"
    SCRIPTS_DIR = REPO / "scripts"

    violations: list[tuple[str, int, str, str]] = []

    for path in SCRIPTS_DIR.rglob("*.py"):
        try:
            src_raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if K1_HELPER_MARKER not in src_raw:
            continue

        rel = str(path.relative_to(REPO))
        src_clean = _strip_comments(src_raw)

        for table in WORLD_ONLY_TABLES:
            for lineno, line in enumerate(src_clean.splitlines(), 1):
                m = re.search(
                    r'\b(?:FROM|JOIN)\s+' + re.escape(table) + r'\b',
                    line,
                    re.IGNORECASE,
                )
                if m and not re.search(
                    r'\bworld\.' + re.escape(table) + r'\b',
                    line,
                    re.IGNORECASE,
                ):
                    violations.append((rel, lineno, table, line.strip()))

    if violations:
        detail = "\n".join(
            f"  {rel}:{lineno}: bare '{table}' — line: {line!r}"
            for rel, lineno, table, line in violations
        )
        pytest.fail(
            f"MIN-1 antibody: {len(violations)} bare world-class table reference(s) "
            f"in scripts/ using get_forecasts_connection_with_world.\n"
            f"Bare refs resolve to zero-row MAIN shell under forecasts-rooted connection "
            f"(F40/F41 silent-dead-read pattern).\n\n"
            f"Violations:\n{detail}\n\n"
            f"Fix: qualify as `world.{violations[0][2]}`."
        )


def test_calibration_transfer_policy_does_not_use_forecasts_helper():
    """MIN-1 sentinel: src/data/calibration_transfer_policy.py and
    src/engine/evaluator.py currently do NOT use get_forecasts_connection_with_world.
    If they start importing it, the qualified-ref test above will enforce world. prefix.
    This test documents the current state so that 'adding the import' is a deliberate
    action caught at test time rather than silently changing the risk profile.

    If a future PR legitimately wires either file to the forecasts helper AND
    qualifies all world-class tables, remove the relevant file from this sentinel.
    """
    K1_HELPER_MARKER = "get_forecasts_connection_with_world"
    MONITORED = [
        SRC / "data" / "calibration_transfer_policy.py",
        SRC / "engine" / "evaluator.py",
    ]
    for path in MONITORED:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        assert K1_HELPER_MARKER not in src, (
            f"MIN-1 sentinel: {path.relative_to(REPO)} now imports "
            f"get_forecasts_connection_with_world but has bare world-class "
            f"table references (validated_calibration_transfers at lines 854/555). "
            f"Add `world.` prefix to all world-class table refs before merging."
        )
