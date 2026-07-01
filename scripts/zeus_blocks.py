#!/usr/bin/env python3
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""zeus_blocks.py — CLI: enumerate current runtime entry blockers.

Usage:
    .venv/bin/python scripts/zeus_blocks.py
    .venv/bin/python scripts/zeus_blocks.py --blocking-only
    .venv/bin/python scripts/zeus_blocks.py --json

Exit codes:
    0   All DISCOVERY-stage blocks are CLEAR (safe to place entries)
    1   One or more DISCOVERY-stage blocks are BLOCKING or UNKNOWN
    2   Registry construction failed (deps build error — check stderr)

RegistryDeps construction mirrors the cycle_runner block-registry snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path so `python scripts/zeus_blocks.py` works
# without installing the package.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_ZEUS_ROOT = _SCRIPT_DIR.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

# ---------------------------------------------------------------------------
# Colour helpers — only when stdout is a tty
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty()

_COLOUR = {
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "reset":  "\033[0m",
}


def _colour(text: str, name: str) -> str:
    if not _USE_COLOUR:
        return text
    return f"{_COLOUR[name]}{text}{_COLOUR['reset']}"


def _state_coloured(state: str) -> str:
    if state == "blocking":
        return _colour("BLOCKING", "red")
    if state == "clear":
        return _colour("CLEAR", "green")
    return _colour("UNKNOWN", "yellow")


# ---------------------------------------------------------------------------
# RegistryDeps construction — mirrors cycle_runner.py block-registry snapshot.
# One helper so it can be reused if needed.
# ---------------------------------------------------------------------------

def _build_runtime_deps():
    """Build a live RegistryDeps using the same recipe as cycle_runner.py."""
    from src.state.db import (
        get_world_connection as _get_world_conn,
        get_connection as _get_db_conn,
        RISK_DB_PATH as _RISK_DB_PATH,
    )
    from src.control import heartbeat_supervisor as _heartbeat_mod
    from src.control import ws_gap_guard as _ws_gap_mod
    from src.control.block_adapters._base import RegistryDeps

    return RegistryDeps(
        db_connection_factory=_get_world_conn,
        risk_state_db_connection_factory=lambda: _get_db_conn(_RISK_DB_PATH),
        heartbeat_module=_heartbeat_mod,
        ws_gap_guard_module=_ws_gap_mod,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zeus_blocks",
        description="Enumerate current runtime entry blockers.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump raw JSON array instead of table.",
    )
    parser.add_argument(
        "--blocking-only",
        action="store_true",
        help="Show only BLOCKING/UNKNOWN gates.",
    )
    args = parser.parse_args(argv)

    from src.control.entries_block_registry import (
        BlockStage,
        BlockState,
        EntriesBlockRegistry,
    )

    try:
        deps = _build_runtime_deps()
        registry = EntriesBlockRegistry.from_runtime(deps)
        blocks = registry.enumerate_blocks(stage="all")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to build registry: {exc!r}", file=sys.stderr)
        return 2

    if args.blocking_only:
        blocks = [b for b in blocks if b.state in (BlockState.BLOCKING, BlockState.UNKNOWN)]

    # ── JSON output ────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps([b.to_dict() for b in blocks], indent=2, default=str))
    else:
        # ── Table output ──────────────────────────────────────────────────
        # Column widths
        _W_ID    = 3
        _W_NAME  = 44
        _W_CAT   = 20
        _W_STG   = 10
        _W_STATE = 10  # raw, colour added separately
        _W_SFL   = 48
        _W_REASON = 50

        def _row(id_: str, name: str, cat: str, stg: str, state: str,
                 reason: str, sfl: str, *, header: bool = False) -> str:
            state_field = state if header else _state_coloured(state).ljust(
                _W_STATE + (len(_state_coloured(state)) - len(state))
            )
            return (
                f"{id_:<{_W_ID}}  "
                f"{name:<{_W_NAME}}  "
                f"{cat:<{_W_CAT}}  "
                f"{stg:<{_W_STG}}  "
                f"{state_field:<{_W_STATE}}  "
                f"{reason:<{_W_REASON}}  "
                f"{sfl}"
            )

        header = _row("id", "name", "category", "stage", "state",
                      "reason", "source_file_line", header=True)
        sep = "-" * len(header)
        print(header)
        print(sep)
        for b in blocks:
            reason = b.blocking_reason or ""
            # Truncate long strings so table stays readable
            name_trunc = b.name[:_W_NAME]
            reason_trunc = reason[:_W_REASON]
            sfl_trunc = b.source_file_line[:_W_SFL]
            print(_row(
                str(b.id),
                name_trunc,
                b.category.value,
                b.stage.value,
                b.state.value,
                reason_trunc,
                sfl_trunc,
            ))

        total = len(blocks)
        blocking = sum(1 for b in blocks if b.state in (BlockState.BLOCKING, BlockState.UNKNOWN))
        print(sep)
        print(f"Total: {total}  Blocking: {blocking}")

    # ── Exit code: 0 iff all DISCOVERY gates are CLEAR ────────────────────
    discovery_clear = all(
        b.state == BlockState.CLEAR
        for b in registry.enumerate_blocks(stage=BlockStage.DISCOVERY)
    )
    return 0 if discovery_clear else 1


if __name__ == "__main__":
    sys.exit(main())
