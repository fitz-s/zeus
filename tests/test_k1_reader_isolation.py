# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: F40/F41 K1-reader regressions — prevent world-DB direct access
# for forecast_class tables in scripts that cross DB boundaries post-K1-split.
# F43 antibody upgrade 2026-05-17: SELECT/FROM-side check — world_class tables
# referenced under K1-helper MUST be world-qualified (MAIN=forecasts under helper).
# See docs/operations/task_2026-05-17_post_karachi_remediation/FIX_K1_READERS.md §C
# and docs/operations/task_2026-05-17_post_karachi_remediation/F43_F44_DISCOVERY.md
"""Antibody: scripts that were K1-misrouted must not regress to world-DB direct access.

Scope: F40 (bridge_oracle_to_calibration) + F41 (evaluate_calibration_transfer_oos).
These two scripts were confirmed to read forecast_class tables via get_world_connection
before the K1 fix. This test prevents regression.

Broader codebase sweep (37+ scripts) is deferred — those are pre-K1 backfill scripts
whose world-DB access was correct under the legacy single-DB schema and requires
separate per-script migration work.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Tables that are exclusively forecast_class post-K1-split (not ghost-copied to world)
# Per architecture/db_table_ownership.yaml: forecast_class entries with no world ghost.
FORECAST_ONLY_TABLES = {
    "calibration_pairs_v2",   # 91M rows in forecasts.db; ghost on world is legacy_archived
    "source_run",
    "source_run_coverage",
    "readiness_state",
    "market_events_v2",
    "ensemble_snapshots_v2",
    "job_run",
}

# Patterns that indicate direct world-DB access (not cross-DB helper)
BAD_PATTERNS = [
    r'sqlite3\.connect\([^)]*zeus-world\.db',  # direct sqlite3.connect to world
    r'get_world_connection\b',                  # bare world connection helper
]

# Scripts confirmed fixed by F40+F41 — must NOT have BAD_PATTERNS + forecast table
K1_FIXED_SCRIPTS = [
    "bridge_oracle_to_calibration.py",
    "evaluate_calibration_transfer_oos.py",
]


@pytest.mark.parametrize("script_name", K1_FIXED_SCRIPTS)
def test_k1_fixed_script_does_not_use_world_for_forecast_tables(script_name):
    """Regression: F40/F41 scripts must not re-acquire direct world-DB access
    for forecast_class tables after K1 fix."""
    script = SCRIPTS / script_name
    assert script.exists(), f"Script not found: {script}"

    src = script.read_text()

    uses_bad = any(re.search(p, src) for p in BAD_PATTERNS)
    uses_forecast_table = any(t in src for t in FORECAST_ONLY_TABLES)

    assert not (uses_bad and uses_forecast_table), (
        f"{script_name}: regressed — uses world-DB direct access AND references "
        f"a forecast_class table. Use get_forecasts_connection_with_world() instead.\n"
        f"Bad patterns found: {[p for p in BAD_PATTERNS if re.search(p, src)]}\n"
        f"Forecast tables referenced: {[t for t in FORECAST_ONLY_TABLES if t in src]}"
    )


def test_bridge_uses_forecasts_connection_with_world():
    """F40 structural check: bridge must use the cross-DB context manager."""
    src = (SCRIPTS / "bridge_oracle_to_calibration.py").read_text()
    assert "get_forecasts_connection_with_world" in src, (
        "bridge_oracle_to_calibration.py must use get_forecasts_connection_with_world()"
    )
    assert "DB_PATH" not in src or "DB_PATH removed" in src, (
        "DB_PATH constant must be removed from bridge_oracle_to_calibration.py (K1 F40)"
    )


def test_cal_transfer_eval_uses_forecasts_connection_with_world():
    """F41 structural check: evaluate_calibration_transfer_oos must use cross-DB helper."""
    src = (SCRIPTS / "evaluate_calibration_transfer_oos.py").read_text()
    assert "get_forecasts_connection_with_world" in src, (
        "evaluate_calibration_transfer_oos.py must use get_forecasts_connection_with_world()"
    )
    assert "get_world_connection" not in src or src.count("get_world_connection") == 0, (
        "evaluate_calibration_transfer_oos.py must not use get_world_connection() (K1 F41)"
    )


def test_cal_transfer_eval_qualifies_world_table_inserts():
    """F41 correctness: INSERT into world-class table must be qualified as world.*
    so it resolves to the ATTACHed world.db under get_forecasts_connection_with_world."""
    src = (SCRIPTS / "evaluate_calibration_transfer_oos.py").read_text()
    # Must NOT have bare INSERT INTO validated_calibration_transfers
    bare_insert = re.search(
        r'INSERT\s+INTO\s+validated_calibration_transfers\b(?!\s*\(|[^.]*world\.)',
        src,
    )
    assert bare_insert is None, (
        "evaluate_calibration_transfer_oos.py: bare INSERT INTO validated_calibration_transfers "
        "found — must be qualified as world.validated_calibration_transfers since "
        "get_forecasts_connection_with_world uses forecasts.db as MAIN."
    )
    # Must have the qualified form
    assert "world.validated_calibration_transfers" in src, (
        "evaluate_calibration_transfer_oos.py: INSERT must use world.validated_calibration_transfers"
    )


# F43 antibody (2026-05-17): SELECT/FROM-side schema qualification.
#
# Under `get_forecasts_connection_with_world()`, MAIN=forecasts.db, world.db is
# ATTACHed as schema `world`. Bare `FROM <world_class_table>` resolves to the
# empty/shell table in forecasts.db (0 rows), not the live world.db data. PR #137
# F40/F41 introduced this regression on 3 lines (bridge:183/195, eval:222) — all
# silently returned 0 rows.
#
# This test enforces: every world-class table referenced via SELECT/FROM/JOIN
# inside a K1_FIXED_SCRIPT must be prefixed with `world.`.
#
# Scope: world_class tables that have NO forecast_class twin (i.e. live only in
# zeus-world.db, not split during K1). Tables with a forecast-class entry get
# moved to forecasts.db, so bare refs resolve to the correct MAIN.
WORLD_ONLY_TABLES_UNDER_K1_HELPER = {
    "observation_instants_v2",   # 1.8M rows in world.db; no forecast-class entry
    "platt_models_v2",           # 1.4K rows in world.db; no forecast-class entry
    "data_coverage",             # world-class (cross-DB write target post-K1)
    "daily_observation_revisions",  # world-class
}


def _strip_sql_comments(src: str) -> str:
    """Strip Python and SQL comments so we don't false-positive on commented-out SQL."""
    # Remove python # comments (best-effort, line-by-line)
    out = []
    for line in src.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("script_name", K1_FIXED_SCRIPTS)
def test_k1_helper_world_tables_qualified(script_name):
    """F43 regression-block: SELECT/FROM refs to world_class-only tables under
    a K1-helper-using script must be prefixed with `world.`."""
    src = (SCRIPTS / script_name).read_text()
    if "get_forecasts_connection_with_world" not in src:
        pytest.skip(f"{script_name} no longer uses K1 helper; antibody not applicable")
    src_clean = _strip_sql_comments(src)

    violations = []
    for table in WORLD_ONLY_TABLES_UNDER_K1_HELPER:
        # Find any FROM <table> or JOIN <table> that is NOT preceded by 'world.'
        # Negative lookbehind: not preceded by 'world.' or another qualifier
        pattern = (
            r'\b(?:FROM|JOIN)\s+'        # SQL keyword
            r'(?<!\bworld\.)'             # not already qualified as world.
            r'\b' + re.escape(table) + r'\b'
        )
        for m in re.finditer(pattern, src_clean, re.IGNORECASE):
            # Verify the literal char before the table is NOT a dot (qualifier)
            preceding = src_clean[max(0, m.start()):m.end()]
            if not re.search(r'\bworld\.\s*' + re.escape(table), preceding, re.IGNORECASE):
                line = src_clean[:m.start()].count("\n") + 1
                violations.append((line, table, preceding.strip()))

    assert not violations, (
        f"{script_name}: F43 regression — bare references to world_class tables "
        f"under K1-helper context.\n"
        + "\n".join(
            f"  line {ln}: {tbl} (raw: {raw!r})" for ln, tbl, raw in violations
        )
        + "\nFix: prefix with `world.` (e.g. `FROM world." + violations[0][1] + "`) "
        + "since get_forecasts_connection_with_world() makes forecasts.db the MAIN."
    )


def test_world_only_tables_set_matches_registry():
    """Sanity: every table in WORLD_ONLY_TABLES_UNDER_K1_HELPER must be classified
    as world_class in architecture/db_table_ownership.yaml and must NOT have a
    forecast_class twin entry. Catches drift if a table gets reclassified during
    future K-splits."""
    import yaml
    registry_path = REPO / "architecture" / "db_table_ownership.yaml"
    if not registry_path.exists():
        pytest.skip("db_table_ownership.yaml not present")
    data = yaml.safe_load(registry_path.read_text())
    entries_by_name = {}
    for t in data.get("tables", []):
        entries_by_name.setdefault(t["name"], []).append(t)

    for table in WORLD_ONLY_TABLES_UNDER_K1_HELPER:
        entries = entries_by_name.get(table, [])
        assert entries, f"{table}: not registered in db_table_ownership.yaml"
        classes = {e.get("schema_class") for e in entries}
        # Must include world_class
        assert "world_class" in classes, (
            f"{table}: schema_class set {classes!r} does not include world_class; "
            f"remove from WORLD_ONLY_TABLES_UNDER_K1_HELPER or fix registry."
        )
        # Must NOT include forecast_class (would make bare ref correct under K1 helper)
        assert "forecast_class" not in classes, (
            f"{table}: registry shows BOTH world_class and forecast_class. "
            f"Bare ref under K1 helper could be correct — remove from this antibody set."
        )
