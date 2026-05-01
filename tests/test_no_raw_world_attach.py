# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2, §6 antibody #13
"""Antibody #13: No raw ATTACH DATABASE or get_trade_connection_with_world in
trading-lane source modules (src/engine, src/strategy, src/signal, src/execution).

The only allowed ATTACH site in src/ is db.py:66-73 (for backward compat during
Phase 2 overlap). The world_view accessor layer is the approved read path.
Harvester is allowlisted during Phase 1.5 transition.

This test uses grep-based AST-style scanning to detect forbidden patterns.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "src"

# Modules under scan (trading lane)
SCAN_DIRS = [
    SRC / "engine",
    SRC / "strategy",
    SRC / "signal",
    SRC / "execution",
]

# Allowlisted files that may use get_trade_connection_with_world or ATTACH
# (during Phase 2 overlap; Phase 3 antibody #8 will tighten further by
# deleting get_trade_connection_with_world from db.py and removing these entries)
ALLOWLIST = {
    # cycle_runner.py imports the seam for backward compat (Phase 2 migration)
    str(SRC / "engine" / "cycle_runner.py"),
    # replay.py uses get_trade_connection_with_world for backtest reads
    str(SRC / "engine" / "replay.py"),
    # execution/ files below use the legacy ATTACH seam — Phase 3 targets for migration
    str(SRC / "execution" / "command_recovery.py"),
    str(SRC / "execution" / "executor.py"),
    str(SRC / "execution" / "settlement_commands.py"),
    str(SRC / "execution" / "wrap_unwrap_commands.py"),
}


def _collect_py_files(dirs: list[Path]) -> list[Path]:
    files = []
    for d in dirs:
        if d.exists():
            files.extend(d.rglob("*.py"))
    return sorted(files)


def _grep_file(path: Path, pattern: str) -> list[int]:
    """Return list of line numbers matching pattern."""
    results = []
    try:
        content = path.read_text(errors="replace")
        for i, line in enumerate(content.splitlines(), 1):
            if pattern in line:
                results.append(i)
    except OSError:
        pass
    return results


class TestNoRawWorldAttach:
    """Assert trading-lane modules do not use raw ATTACH or legacy seam outside allowlist."""

    def _violations_for_pattern(self, pattern: str) -> list[str]:
        files = _collect_py_files(SCAN_DIRS)
        violations = []
        for f in files:
            if str(f) in ALLOWLIST:
                continue
            lines = _grep_file(f, pattern)
            if lines:
                violations.append(f"{f.relative_to(PROJECT_ROOT)}:{lines} — found '{pattern}'")
        return violations

    def test_no_attach_database_in_trading_lane(self):
        """src/engine|strategy|signal|execution must not contain 'ATTACH DATABASE'."""
        violations = self._violations_for_pattern("ATTACH DATABASE")
        assert not violations, (
            "ATTACH DATABASE found in trading-lane modules (forbidden — use world_view accessors):\n"
            + "\n".join(violations)
        )

    def test_no_get_trade_connection_with_world_in_trading_lane(self):
        """Trading lane must not call get_trade_connection_with_world outside allowlist.

        cycle_runner.py is allowlisted (Phase 2 backward compat seam).
        Phase 3 will remove this seam and tighten the antibody.
        """
        violations = self._violations_for_pattern("get_trade_connection_with_world")
        assert not violations, (
            "get_trade_connection_with_world found outside allowlist in trading-lane modules:\n"
            + "\n".join(violations)
            + "\n\nMigrate callers to src.state.connection_pair.get_connection_pair() "
            "and world_view accessors. Phase 3 will delete the legacy seam."
        )

    def test_allowlisted_files_exist(self):
        """Allowlisted files must still exist (detect stale allowlist entries)."""
        for path_str in ALLOWLIST:
            path = Path(path_str)
            assert path.exists(), (
                f"Allowlisted file {path_str} no longer exists — "
                "update ALLOWLIST in test_no_raw_world_attach.py"
            )

    def test_world_view_module_exists(self):
        """src/contracts/world_view/ must exist as the approved read path."""
        world_view = SRC / "contracts" / "world_view"
        assert world_view.is_dir(), "src/contracts/world_view/ must exist"
        init = world_view / "__init__.py"
        assert init.exists(), "src/contracts/world_view/__init__.py must exist"

    def test_connection_pair_module_exists(self):
        """src/state/connection_pair.py must exist (Phase 2 seam replacement)."""
        cp = SRC / "state" / "connection_pair.py"
        assert cp.exists(), "src/state/connection_pair.py must exist"

    def test_freshness_gate_module_exists(self):
        """src/control/freshness_gate.py must exist (Phase 2 §3.1)."""
        fg = SRC / "control" / "freshness_gate.py"
        assert fg.exists(), "src/control/freshness_gate.py must exist"
