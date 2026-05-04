# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 2: auto_pause_streak_escalation adapter.

Probes: state/auto_pause_streak.json
Blocks when: count >= STREAK_THRESHOLD (3) within STREAK_WINDOW_SECONDS (300s).

Note: the streak does NOT directly set entries_blocked_reason. It calls
pause_entries() → entries_paused=True. We probe the streak JSON directly
here to give the registry visibility into the streak state independent of
whether pause_entries has been called yet (i.e., the streak might be at 2/3).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)

_STREAK_THRESHOLD = 3
_STREAK_WINDOW_SECONDS = 300


def _is_in_window(last_seen_at: str | None) -> bool:
    if not last_seen_at:
        return False
    try:
        last = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last) <= timedelta(seconds=_STREAK_WINDOW_SECONDS)
    except ValueError:
        return False


class AutoPauseStreakAdapter:
    id = 2
    name = "auto_pause_streak_escalation"
    category = BlockCategory.FILE_FAIL_CLOSED
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:767"

    def probe(self, deps: RegistryDeps) -> Block:
        streak_path = deps.state_dir / "auto_pause_streak.json"
        try:
            try:
                raw: dict = json.loads(streak_path.read_text())
            except FileNotFoundError:
                raw = {}

            count = int(raw.get("count") or 0)
            last_seen_at: str | None = raw.get("last_seen_at")
            reason_code: str = str(raw.get("reason_code") or "unknown")
            in_window = _is_in_window(last_seen_at)
            blocking = count >= _STREAK_THRESHOLD and in_window

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if blocking else BlockState.CLEAR,
                blocking_reason=f"auto_pause:{reason_code}" if blocking else None,
                state_source="file:state/auto_pause_streak.json",
                source_file_line=self.source_file_line,
                owner_module="src.control.auto_pause_streak",
                owner_function="record_failure",
                raw_probe={
                    "count": count,
                    "threshold": _STREAK_THRESHOLD,
                    "window_seconds": _STREAK_WINDOW_SECONDS,
                    "in_window": in_window,
                    "reason_code": reason_code,
                    "last_seen_at": last_seen_at,
                },
                notes=(
                    "Streak calls pause_entries() at cycle_runner.py:770 when count>=3. "
                    "BLOCKING here means threshold met and in-window."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.UNKNOWN,
                blocking_reason=f"adapter_error:{exc.__class__.__name__}: {exc}",
                state_source="file:state/auto_pause_streak.json",
                source_file_line=self.source_file_line,
                owner_module="src.control.auto_pause_streak",
                owner_function="record_failure",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
