#!/usr/bin/env python3
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Authority basis: live_entry_health_repair PLAN Slice B71.
"""Admit B71's POSITION_TOKEN_SPLIT_RECONSTRUCTED canonical event literal.

This wrapper deliberately reuses the already-audited single-table CHECK
migration protocol: writer-plane fence, optional operator backup, one
BEGIN IMMEDIATE transaction, index/trigger preservation, and idempotent
re-run.  It introduces no data rewrite and never repairs positions itself.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

TARGET_LITERAL = "POSITION_TOKEN_SPLIT_RECONSTRUCTED"
_BASE = Path(__file__).with_name("2026_07_position_identity_supersession_check.py")
_SPEC = importlib.util.spec_from_file_location("_b71_event_check_base", _BASE)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - broken checkout
    raise RuntimeError(f"cannot load B71 CHECK migration base: {_BASE}")
_BASE_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE_MODULE
_SPEC.loader.exec_module(_BASE_MODULE)
_BASE_MODULE.TARGET_LITERAL = TARGET_LITERAL

TARGET_DB = "trade"
RebuildResult = _BASE_MODULE.RebuildResult
run_migration = _BASE_MODULE.run_migration


def up(conn) -> None:
    _BASE_MODULE.up(conn)


def down(conn) -> None:
    _BASE_MODULE.down(conn)


def main(argv: list[str] | None = None) -> int:
    return _BASE_MODULE.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
