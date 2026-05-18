# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: F40/F41 K1-reader regressions — prevent world-DB direct access
# for forecast_class tables in scripts that cross DB boundaries post-K1-split.
# See docs/operations/task_2026-05-17_post_karachi_remediation/FIX_K1_READERS.md §C
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


# ---------------------------------------------------------------------------
# K-A antibody (F19/F46/F81/F82): log_forward_market_substrate writer path pin
# Decision A2 (2026-05-17): function opens its own forecasts conn; must not
# accept a positional conn argument that could route writes to trades.db MAIN.
# ---------------------------------------------------------------------------

def test_log_forward_market_substrate_does_not_accept_positional_conn():
    """K-A regression: log_forward_market_substrate must be keyword-only (no positional conn).

    Callers that pass the cycle trades-rooted conn as a positional argument would
    silently route INSERT INTO market_events_v2 to trades.db MAIN (0-row shell).
    Decision A2 fix: function opens its own forecasts conn; conn param removed.
    """
    import ast
    src_file = REPO / "src" / "state" / "db.py"
    tree = ast.parse(src_file.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "log_forward_market_substrate":
            # All parameters must be keyword-only (after * in signature)
            # posonlyargs and args (positional) must be empty
            positional_params = [a.arg for a in (node.args.posonlyargs + node.args.args)]
            assert positional_params == [], (
                f"log_forward_market_substrate has positional params: {positional_params}. "
                "K-A fix (Decision A2): function must be fully keyword-only so callers "
                "cannot pass a trades-rooted conn and silently misroute writes."
            )
            return
    raise AssertionError(
        "log_forward_market_substrate not found in src/state/db.py — "
        "function was removed or renamed without updating this antibody."
    )


def test_log_forward_market_substrate_opens_forecasts_path():
    """K-A regression: log_forward_market_substrate must reference ZEUS_FORECASTS_DB_PATH.

    The function must open its own connection to forecasts.db (not accept an opaque conn).
    """
    src = (REPO / "src" / "state" / "db.py").read_text()
    # Find the function body (from def to next top-level def)
    in_fn = False
    fn_lines = []
    for line in src.splitlines():
        if line.startswith("def log_forward_market_substrate("):
            in_fn = True
        elif in_fn and line.startswith("def ") and "log_forward_market_substrate" not in line:
            break
        if in_fn:
            fn_lines.append(line)
    fn_src = "\n".join(fn_lines)

    assert "ZEUS_FORECASTS_DB_PATH" in fn_src, (
        "log_forward_market_substrate must reference ZEUS_FORECASTS_DB_PATH internally "
        "(K-A fix Decision A2: opens own forecasts conn, not caller-supplied conn)."
    )


# ---------------------------------------------------------------------------
# K-B antibody (F48/F103/F102): monitor_refresh.py reader schema-qualifier
# Reader under get_forecasts_connection_with_world: MAIN=forecasts.db.
# Bare table names resolve to MAIN, not to world ATTACH.
# ---------------------------------------------------------------------------

def test_monitor_refresh_settlements_query_uses_forecasts_qualifier():
    """K-B regression (F48/F103): settlements query in _check_persistence_anomaly
    must use forecasts.settlements_v2, not bare settlements or settlements_v2.

    Bare name under cycle conn resolves to trades.db MAIN (0 rows) — silent dead-read.
    """
    src = (REPO / "src" / "engine" / "monitor_refresh.py").read_text()

    # Must NOT have bare FROM settlements (old name)
    assert "FROM settlements " not in src and "FROM settlements\n" not in src, (
        "monitor_refresh.py: bare 'FROM settlements' found — must use "
        "'FROM forecasts.settlements_v2' (K-B fix F48/F103)."
    )
    # Must NOT have bare FROM settlements_v2 in SQL (comment/docstring mentions are OK)
    import re as _re
    # SQL context: FROM immediately before settlements_v2 (not qualified by schema prefix)
    bare_sql = _re.search(r'FROM\s+settlements_v2\b', src)
    assert bare_sql is None, (
        "monitor_refresh.py: bare 'FROM settlements_v2' found — must use "
        "'FROM forecasts.settlements_v2' (K-B fix F48/F103)."
    )

    assert "forecasts.settlements_v2" in src, (
        "monitor_refresh.py: must contain 'FROM forecasts.settlements_v2' "
        "in _check_persistence_anomaly (K-B fix F48/F103)."
    )


def test_monitor_refresh_temp_persistence_query_uses_world_qualifier():
    """K-B regression (F102): temp_persistence query must use world.temp_persistence.

    temp_persistence is world_class. Under get_forecasts_connection_with_world,
    MAIN=forecasts.db; bare 'FROM temp_persistence' resolves to a zero-row
    forecasts.db shell. world. qualifier routes to the writer's target.
    """
    src = (REPO / "src" / "engine" / "monitor_refresh.py").read_text()

    # Must NOT have bare FROM temp_persistence
    import re as _re
    bare = _re.search(r'FROM\s+temp_persistence\b(?!\s*--)', src)
    # Allow world.temp_persistence — verify only unqualified occurrences
    all_occ = _re.findall(r'temp_persistence', src)
    qualified_occ = _re.findall(r'world\.temp_persistence', src)
    assert len(all_occ) == len(qualified_occ), (
        f"monitor_refresh.py: {len(all_occ) - len(qualified_occ)} unqualified "
        "temp_persistence reference(s) found — must all be 'world.temp_persistence' "
        "(K-B fix F102)."
    )
    assert "world.temp_persistence" in src, (
        "monitor_refresh.py: must contain 'FROM world.temp_persistence' "
        "in _check_persistence_anomaly (K-B fix F102)."
    )


# ---------------------------------------------------------------------------
# WORLD_ONLY_TABLES helper — extends the existing pattern from F41 to cover
# world-class tables read under get_forecasts_connection_with_world.
# Coordinator addition (2026-05-17): temp_persistence is world_class.
# Restored 2026-05-17 (phase critic CRIT-1): F43 antibody scope merged back in
# after K1-sweep cherry-pick `git checkout --theirs` accidentally wiped it.
# ---------------------------------------------------------------------------

# Tables that are WORLD_CLASS and must be qualified as world.<table> when read
# under a forecasts-rooted connection (MAIN=forecasts.db).
# Union of: K-B sweep additions + F43 SELECT/FROM-side antibody set.
WORLD_ONLY_TABLES_UNDER_K1 = {
    "temp_persistence",                  # F102 — world_class, ETL writes to zeus-world.db
    "validated_calibration_transfers",   # F41 — world_class
    "observation_instants_v2",           # F43 — 1.8M rows in world.db
    "platt_models_v2",                   # F43 — 1.4K rows in world.db
    "data_coverage",                     # F43 — world-class (cross-DB write target post-K1)
    "daily_observation_revisions",       # F43 — world-class
}


def test_monitor_refresh_world_tables_are_qualified():
    """K-B regression: all world_class table reads in monitor_refresh.py must use world. prefix.

    Under get_forecasts_connection_with_world (MAIN=forecasts.db), bare world-class
    table references resolve to a zero-row MAIN shell instead of the actual world.db.
    """
    import re as _re
    src = (REPO / "src" / "engine" / "monitor_refresh.py").read_text()
    for table in WORLD_ONLY_TABLES_UNDER_K1:
        all_occ = _re.findall(rf'\b{table}\b', src)
        qualified_occ = _re.findall(rf'world\.{table}\b', src)
        if not all_occ:
            continue  # table not referenced in this file — not an error
        assert len(all_occ) == len(qualified_occ), (
            f"monitor_refresh.py: {len(all_occ) - len(qualified_occ)} unqualified "
            f"'{table}' reference(s) — must be 'world.{table}' (K-B fix, world_class)."
        )


# ---------------------------------------------------------------------------
# F43 antibody (RESTORED 2026-05-17 per phase critic CRIT-1) — broader scope:
# scan ALL K1-helper-using scripts (not just monitor_refresh.py) for bare
# world_class FROM/JOIN refs. K1-sweep's antibody was monitor_refresh-only;
# F43's was bridge_oracle_to_calibration + evaluate_calibration_transfer_oos.
# Both are needed; this is the union (broader file scope, broader table scope).
# ---------------------------------------------------------------------------


def _strip_sql_comments(src: str) -> str:
    """Strip Python and SQL comments so we don't false-positive on commented-out SQL."""
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
    a K1-helper-using script must be prefixed with `world.`.

    PR #137 F40/F41 fix introduced 3 silent dead-reads (bridge:183/195, eval:222)
    by changing connection to forecasts.db MAIN without world-qualifying the SQL.
    This antibody catches that category permanently.
    """
    import re as _re
    src = (SCRIPTS / script_name).read_text()
    if "get_forecasts_connection_with_world" not in src:
        pytest.skip(f"{script_name} no longer uses K1 helper; antibody not applicable")
    src_clean = _strip_sql_comments(src)

    violations = []
    for table in WORLD_ONLY_TABLES_UNDER_K1:
        # Find any FROM <table> or JOIN <table> that is NOT preceded by 'world.'
        pattern = (
            r'\b(?:FROM|JOIN)\s+'
            r'(?<!\bworld\.)'
            r'\b' + _re.escape(table) + r'\b'
        )
        for m in _re.finditer(pattern, src_clean, _re.IGNORECASE):
            preceding = src_clean[max(0, m.start()):m.end()]
            if not _re.search(r'\bworld\.\s*' + _re.escape(table), preceding, _re.IGNORECASE):
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
    """Sanity: every table in WORLD_ONLY_TABLES_UNDER_K1 must be classified
    as world_class in architecture/db_table_ownership.yaml (canonical entry,
    not legacy_archived) and must NOT have a forecast_class twin (which would
    make bare ref correct under K1 helper). Catches drift if a table gets
    re-classified during future K-splits."""
    import yaml
    registry_path = REPO / "architecture" / "db_table_ownership.yaml"
    if not registry_path.exists():
        pytest.skip("db_table_ownership.yaml not present")
    data = yaml.safe_load(registry_path.read_text())
    entries_by_name = {}
    for t in data.get("tables", []):
        entries_by_name.setdefault(t["name"], []).append(t)

    for table in WORLD_ONLY_TABLES_UNDER_K1:
        entries = entries_by_name.get(table, [])
        assert entries, f"{table}: not registered in db_table_ownership.yaml"
        # Must have a world_class canonical entry (not legacy_archived only)
        canonical_classes = {
            e.get("schema_class") for e in entries
            if e.get("schema_class") != "legacy_archived"
        }
        assert "world_class" in canonical_classes, (
            f"{table}: canonical schema_class set {canonical_classes!r} does not "
            f"include world_class; remove from WORLD_ONLY_TABLES_UNDER_K1 or fix registry."
        )
        # Must NOT include forecast_class (would make bare ref correct under K1 helper)
        assert "forecast_class" not in canonical_classes, (
            f"{table}: registry shows BOTH world_class and forecast_class. "
            f"Bare ref under K1 helper could be correct — remove from this antibody set."
        )
