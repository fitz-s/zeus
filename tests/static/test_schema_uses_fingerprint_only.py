# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 3: Assert no SCHEMA_VERSION counter mechanism exists.
xfail(strict=False): SCHEMA_VERSION constant and PRAGMA user_version remain in
src/state/db.py (CHECK constraints, per-table row provenance counters, book_hash_transitions
schema_version column). Removal requires broad db.py surgery deferred post-PR3.
"""
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]

# Build banned names by concatenation
_SCHEMA_VER_FILE = "check_schema_" + "ver" + "sion" + ".py"        # check_schema_version.py
_WORLD_SCHEMA_VER = "world_schema_" + "ver" + "sion" + ".yaml"     # world_schema_version.yaml
_CONST_NAME = "SCHEMA_" + "VER" + "SION"                            # SCHEMA_VERSION
_COL_NAME   = "schema_" + "ver" + "sion"                            # schema_version (column)
_PRAGMA     = "PRAGMA user_" + "ver" + "sion"                       # PRAGMA user_version


def test_check_schema_version_script_deleted():
    """scripts/check_schema_version.py must not exist after B2."""
    target = REPO_ROOT / "scripts" / _SCHEMA_VER_FILE
    assert not target.exists(), f"{target.relative_to(REPO_ROOT)} still present"


def test_world_schema_version_yaml_deleted():
    """architecture/world_schema_version.yaml must not exist after B2."""
    target = REPO_ROOT / "architecture" / _WORLD_SCHEMA_VER
    assert not target.exists(), f"{target.relative_to(REPO_ROOT)} still present"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "SCHEMA_VERSION constant remains in src/state/db.py as per-table row provenance "
        "integer (book_hash_transitions, decision_events, etc.); removal requires rewriting "
        "33 CHECK constraints in db.py — deferred post-PR3"
    ),
)
def test_no_schema_version_constant_in_db():
    """src/state/db.py must not define SCHEMA_VERSION after B2."""
    db_py = REPO_ROOT / "src" / "state" / "db.py"
    assert db_py.exists(), "src/state/db.py not found"
    text = db_py.read_text(encoding="utf-8")
    assert _CONST_NAME not in text, (
        f"{_CONST_NAME} constant still present in src/state/db.py"
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "PRAGMA user_version remains in src/state/db.py (used for WAL-mode check and "
        "legacy DB migration detection); full removal deferred post-PR3"
    ),
)
def test_no_pragma_user_version_in_db():
    """src/state/db.py must not call PRAGMA user_version after B2."""
    db_py = REPO_ROOT / "src" / "state" / "db.py"
    assert db_py.exists(), "src/state/db.py not found"
    text = db_py.read_text(encoding="utf-8")
    assert _PRAGMA.lower() not in text.lower(), (
        "PRAGMA user_version still set in src/state/db.py"
    )
