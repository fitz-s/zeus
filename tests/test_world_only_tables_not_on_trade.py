# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: 2026-05-19 live-trading daemon crash-loop incident
#                  (zeus-live.err lines ~760635). Legacy init_schema(trade_conn)
#                  created decision_events + db_chunk_boundary_events on trade.db
#                  while architecture/db_table_ownership.yaml declares them
#                  world-only. assert_db_matches_registry fail-closed at boot.
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — declared world-only tables must NOT live on trade DB.
#          Sed-flip target: if someone adds either name to _TRADE_CLASS_TABLES
#          or to _TRADE_CLASS_DDL, this test fails.
"""Antibody: world-only tables stay off the trade DB.

Root cause (2026-05-19 boot crash-loop): legacy ``init_schema()`` in
``src/state/db.py`` issues CREATE TABLE IF NOT EXISTS for
``decision_events`` and ``db_chunk_boundary_events``. When that function
is (or was historically) called against a trade-DB connection, the two
tables end up on trade.db even though ``architecture/db_table_ownership.yaml``
declares them with ``db: world``.

``assert_db_matches_registry(TRADE)`` then fail-closes at every daemon
boot because the disk has tables not declared in the TRADE registry.

The structural fix is two-pronged:
  1. Drop the ghost tables from trade.db (operational, one-shot).
  2. Ensure no current code path can recreate them on trade. The new
     ``init_schema_trade_only`` path uses ``_TRADE_CLASS_TABLES`` /
     ``_TRADE_CLASS_DDL`` which intentionally excludes these names.

Antibody contracts:
  T1: decision_events MUST NOT appear in _TRADE_CLASS_TABLES.
  T2: db_chunk_boundary_events MUST NOT appear in _TRADE_CLASS_TABLES.
  T3: decision_events MUST NOT appear in _TRADE_CLASS_DDL source text
      (catches verbatim CREATE TABLE drift even if the frozenset was
      left untouched).
  T4: db_chunk_boundary_events MUST NOT appear in _TRADE_CLASS_DDL.
  T5: yaml registry MUST keep both tables with ``db: world`` (not trade).
  T6: registry entry's ``schema_class`` MUST be ``world_class`` for both.

Sed-flip verifier: change yaml ``db: world`` → ``db: trade`` for either
name, or add the name to ``_TRADE_CLASS_TABLES`` → tests turn RED.
"""

from __future__ import annotations

from pathlib import Path

import yaml


_REPO = Path(__file__).resolve().parents[1]
_YAML = _REPO / "architecture" / "db_table_ownership.yaml"
_DB_PY = _REPO / "src" / "state" / "db.py"

# Tables that, per registry, must live on world.db only.
_WORLD_ONLY_GHOSTS = (
    "decision_events",
    "db_chunk_boundary_events",
)


def _load_registry() -> dict[str, dict]:
    doc = yaml.safe_load(_YAML.read_text())
    by_name: dict[str, dict] = {}
    for entry in doc.get("tables", []):
        by_name.setdefault(entry["name"], entry)
    return by_name


def _trade_class_frozenset_text() -> str:
    """Return the literal source text of _TRADE_CLASS_TABLES (between braces)."""
    src = _DB_PY.read_text()
    marker = "_TRADE_CLASS_TABLES: frozenset[str] = frozenset({"
    idx = src.find(marker)
    assert idx >= 0, "_TRADE_CLASS_TABLES marker not found in src/state/db.py"
    end = src.find("})", idx)
    return src[idx:end + 2]


def _trade_class_ddl_text() -> str:
    """Return the literal source text of _TRADE_CLASS_DDL block."""
    src = _DB_PY.read_text()
    marker = '_TRADE_CLASS_DDL = """'
    idx = src.find(marker)
    assert idx >= 0, "_TRADE_CLASS_DDL marker not found in src/state/db.py"
    end = src.find('"""', idx + len(marker))
    assert end > 0, "_TRADE_CLASS_DDL closing triple-quote not found"
    return src[idx:end + 3]


def test_t1_decision_events_not_in_trade_class_tables():
    """T1: decision_events must not appear in the TRADE-class frozenset."""
    text = _trade_class_frozenset_text()
    assert '"decision_events"' not in text, (
        "decision_events leaked into _TRADE_CLASS_TABLES — "
        "this re-enables the 2026-05-19 boot crash-loop. "
        "decision_events belongs on world.db per registry "
        "(architecture/db_table_ownership.yaml: db: world)."
    )


def test_t2_db_chunk_boundary_events_not_in_trade_class_tables():
    """T2: db_chunk_boundary_events must not appear in the TRADE-class frozenset."""
    text = _trade_class_frozenset_text()
    assert '"db_chunk_boundary_events"' not in text, (
        "db_chunk_boundary_events leaked into _TRADE_CLASS_TABLES — "
        "F11 BulkChunker observability belongs on world.db only."
    )


def test_t3_decision_events_not_in_trade_class_ddl():
    """T3: decision_events CREATE TABLE must not appear in _TRADE_CLASS_DDL."""
    ddl = _trade_class_ddl_text()
    assert "decision_events" not in ddl, (
        "decision_events CREATE TABLE statement appeared in _TRADE_CLASS_DDL — "
        "init_schema_trade_only would recreate the ghost table on every fresh trade DB."
    )


def test_t4_db_chunk_boundary_events_not_in_trade_class_ddl():
    """T4: db_chunk_boundary_events CREATE TABLE must not appear in _TRADE_CLASS_DDL."""
    ddl = _trade_class_ddl_text()
    assert "db_chunk_boundary_events" not in ddl, (
        "db_chunk_boundary_events CREATE TABLE statement appeared in "
        "_TRADE_CLASS_DDL — observability event table belongs on world.db."
    )


def test_t5_registry_keeps_world_only_tables_on_world():
    """T5: yaml registry must keep both tables with db: world."""
    reg = _load_registry()
    for name in _WORLD_ONLY_GHOSTS:
        assert name in reg, (
            f"{name} missing from db_table_ownership.yaml — "
            f"either add it back as db: world or delete the physical table."
        )
        actual_db = reg[name].get("db")
        assert actual_db == "world", (
            f"{name} registered as db={actual_db!r}; must remain 'world'. "
            f"Moving to 'trade' reintroduces the 2026-05-19 boot crash-loop."
        )


def test_t6_registry_schema_class_is_world_class():
    """T6: schema_class must be world_class for both tables.

    The schema_class field is what assert_db_matches_registry consults to
    decide whether a table is allowed on a given DB. If a future commit
    changes schema_class to trade_class while leaving db: world, the
    classifier and the assertion disagree.
    """
    reg = _load_registry()
    for name in _WORLD_ONLY_GHOSTS:
        actual_class = reg[name].get("schema_class")
        assert actual_class == "world_class", (
            f"{name} schema_class={actual_class!r}; must be 'world_class'. "
            f"world-class tables are forbidden on trade.db by registry semantics."
        )
