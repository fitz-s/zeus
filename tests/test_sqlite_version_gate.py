# Created: 2026-07-22
# Authority basis: PR review (GPT-5.6 consult REQ-20260722-005247) HIGH finding —
#   the daemon runs recurring WAL checkpoints on the money DBs, so a linked
#   SQLite carrying the <=3.51.2 WAL-reset corruption bug must be machine-blocked
#   at boot, not left to a prose deployment note.
"""assert_sqlite_version_safe boot-gate antibodies.

The gate compares the interpreter's linked sqlite against the 3.51.3 floor. The
test interpreter may itself be old, so both branches are exercised by patching
``sqlite3.sqlite_version_info`` rather than depending on the runner's build.
"""

from __future__ import annotations

import pytest

from src.state import db as db_module


def test_gate_passes_on_fixed_version(monkeypatch):
    """>= 3.51.3 boots without raising."""
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 51, 3), raising=True)
    db_module.assert_sqlite_version_safe()  # must not raise


def test_gate_passes_on_newer_version(monkeypatch):
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 53, 2), raising=True)
    db_module.assert_sqlite_version_safe()  # must not raise


def test_gate_blocks_vulnerable_version(monkeypatch):
    """<= 3.51.2 fails closed with an actionable message (no override set)."""
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 51, 2), raising=True)
    monkeypatch.delenv("ZEUS_ACCEPT_UNSAFE_SQLITE", raising=False)
    with pytest.raises(RuntimeError, match="SQLITE_VERSION_UNSAFE"):
        db_module.assert_sqlite_version_safe()


def test_gate_blocks_older_minor(monkeypatch):
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 44, 0), raising=True)
    monkeypatch.delenv("ZEUS_ACCEPT_UNSAFE_SQLITE", raising=False)
    with pytest.raises(RuntimeError, match="SQLITE_VERSION_UNSAFE"):
        db_module.assert_sqlite_version_safe()


def test_emergency_override_boots_on_vulnerable(monkeypatch):
    """ZEUS_ACCEPT_UNSAFE_SQLITE=1 boots on a known-old SQLite (emergency escape
    hatch so a mandatory restart is not hard-blocked before the upgrade lands)."""
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 51, 2), raising=True)
    monkeypatch.setenv("ZEUS_ACCEPT_UNSAFE_SQLITE", "1")
    db_module.assert_sqlite_version_safe()  # must not raise (override)


def test_override_only_honored_for_exact_value(monkeypatch):
    """A stray/typo value does not silently disable the gate."""
    monkeypatch.setattr(db_module.sqlite3, "sqlite_version_info", (3, 51, 2), raising=True)
    monkeypatch.setenv("ZEUS_ACCEPT_UNSAFE_SQLITE", "true")
    with pytest.raises(RuntimeError, match="SQLITE_VERSION_UNSAFE"):
        db_module.assert_sqlite_version_safe()
