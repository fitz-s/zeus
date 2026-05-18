# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: F39 self-policing structural fix (evaluate_calibration_transfer_oos.py)
"""Antibody: F39 plist self-policing guard in evaluate_calibration_transfer_oos.py.

When ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED is unset (or != "1"),
main() must return 0 immediately without opening any DB connection.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest.mock import patch, MagicMock

import pytest

# Load the script directly (avoids side-effects from top-level imports at module scope)
_script_path = pathlib.Path(__file__).parents[2] / "scripts" / "evaluate_calibration_transfer_oos.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("evaluate_calibration_transfer_oos", _script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# F39 self-policing: no DB when flag off
# ---------------------------------------------------------------------------

class TestF39SelfPolicing:
    """main() exits 0 and never opens a DB connection when flag is absent."""

    def _run_main_with_flag(self, monkeypatch, flag_value=None):
        """
        Run main() with env flag set to flag_value (None = unset).
        Patches get_forecasts_connection_with_world to detect unexpected calls.
        Returns (exit_code, db_called).
        """
        mod = _load_module()

        db_called = []

        def _fake_db(*args, **kwargs):
            db_called.append(True)
            raise AssertionError("DB should not be opened when flag is off")

        # Patch the DB connection inside the module's namespace
        with patch.dict("sys.modules", {}):
            import os as real_os
            env_backup = real_os.environ.copy()
            try:
                if flag_value is None:
                    real_os.environ.pop("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", None)
                else:
                    real_os.environ["ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED"] = flag_value

                # Patch inside the already-loaded module
                with patch(
                    "src.state.db.get_forecasts_connection_with_world",
                    side_effect=_fake_db,
                ):
                    # Patch sys.argv so argparse doesn't consume test runner args
                    with patch.object(sys, "argv", ["evaluate_calibration_transfer_oos.py"]):
                        exit_code = mod.main()
            finally:
                real_os.environ.clear()
                real_os.environ.update(env_backup)

        return exit_code, db_called

    def test_flag_unset_returns_zero(self, monkeypatch):
        """main() returns 0 when ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED is unset."""
        exit_code, db_called = self._run_main_with_flag(monkeypatch, flag_value=None)
        assert exit_code == 0, f"Expected exit 0, got {exit_code}"

    def test_flag_unset_no_db_connection(self, monkeypatch):
        """main() does NOT open a DB connection when the flag is absent."""
        # The _fake_db raises AssertionError if called; if we reach here, it wasn't.
        exit_code, db_called = self._run_main_with_flag(monkeypatch, flag_value=None)
        assert db_called == [], "get_forecasts_connection_with_world was called; guard failed"

    def test_flag_zero_string_returns_zero(self, monkeypatch):
        """main() returns 0 when flag is explicitly '0'."""
        exit_code, db_called = self._run_main_with_flag(monkeypatch, flag_value="0")
        assert exit_code == 0
        assert db_called == []

    def test_flag_wrong_value_no_db(self, monkeypatch):
        """main() no-ops on arbitrary non-'1' values like 'false' or 'yes'."""
        for bad_val in ("false", "yes", "TRUE", "enabled"):
            exit_code, db_called = self._run_main_with_flag(monkeypatch, flag_value=bad_val)
            assert exit_code == 0, f"flag={bad_val!r}: expected 0, got {exit_code}"
            assert db_called == [], f"flag={bad_val!r}: DB opened unexpectedly"
