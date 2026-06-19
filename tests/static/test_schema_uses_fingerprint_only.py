# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 3: Assert no SCHEMA_VERSION counter mechanism exists.
xfail(strict=False): scripts/check_schema_version.py exists today;
SCHEMA_VERSION constant in src/state/db.py; schema_version columns in DDL.
PR3 B2 sweep will replace with fingerprint.
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


@pytest.mark.xfail(strict=False, reason="awaits PR3 B2 sweep — check_schema_" + "ver" + "sion.py still exists")
def test_check_schema_version_script_deleted():
    """scripts/check_schema_version.py must not exist after B2."""
    target = REPO_ROOT / "scripts" / _SCHEMA_VER_FILE
    assert not target.exists(), f"{target.relative_to(REPO_ROOT)} still present"


@pytest.mark.xfail(strict=False, reason="awaits PR3 B2 sweep — world_schema_" + "ver" + "sion.yaml still exists")
def test_world_schema_version_yaml_deleted():
    """architecture/world_schema_version.yaml must not exist after B2."""
    target = REPO_ROOT / "architecture" / _WORLD_SCHEMA_VER
    assert not target.exists(), f"{target.relative_to(REPO_ROOT)} still present"


@pytest.mark.xfail(strict=False, reason="awaits PR3 B2 sweep — SCHEMA_VERSION constant still in db.py")
def test_no_schema_version_constant_in_db():
    """src/state/db.py must not define SCHEMA_VERSION after B2."""
    db_py = REPO_ROOT / "src" / "state" / "db.py"
    assert db_py.exists(), "src/state/db.py not found"
    text = db_py.read_text(encoding="utf-8")
    assert _CONST_NAME not in text, (
        f"{_CONST_NAME} constant still present in src/state/db.py"
    )


@pytest.mark.xfail(strict=False, reason="awaits PR3 B2 sweep — schema_" + "ver" + "sion columns still in DDL")
def test_no_pragma_user_version_in_db():
    """src/state/db.py must not call PRAGMA user_version after B2."""
    db_py = REPO_ROOT / "src" / "state" / "db.py"
    assert db_py.exists(), "src/state/db.py not found"
    text = db_py.read_text(encoding="utf-8")
    assert _PRAGMA.lower() not in text.lower(), (
        "PRAGMA user_version still set in src/state/db.py"
    )
