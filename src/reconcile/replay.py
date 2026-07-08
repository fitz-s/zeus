# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R2-c (acceptance tool,
#                  built in R2-core per brief item 6) + §G (per-packet replay evidence).
"""Certificate/event-native replay harness -- the R2-c acceptance tool.

Replays a historical window PURELY from persisted, already-durable facts
(position_events, venue_commands/venue_order_facts/venue_trade_facts,
collateral_reservations, decision_certificates) through diff_engine.classify,
and compares the emitted findings against what the legacy reconcile passes
ACTUALLY appended in that same window (identified by position_events.
source_module matching a known legacy reconciler module).

READ ONLY. No network I/O, no venue calls, no writes (reconcile() is always
invoked with apply=False here) -- this harness may be pointed at a live DB
opened ``mode=ro`` per §C4 K0 discipline. A mismatch is a FINDING to report,
not necessarily a failure: the legacy pass may itself be wrong (that is
precisely the class of bug this packet's diff engine exists to replace) --
see replay_window's docstring.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.reconcile.diff_engine import DiffFinding, DiffReport, reconcile

# Legacy reconcile passes whose position_events.source_module marks an event
# as a "corrective event a 31-pass legacy reconciler actually appended" --
# see docs/rebuild/whole_system_first_principles_2026-07-07.md §2.4.
LEGACY_RECONCILER_SOURCE_MODULES = frozenset(
    {
        "src.state.chain_mirror_reconciler",
        "src.execution.command_recovery",
        "src.execution.exchange_reconcile",
        "src.state.chain_reconciliation",
    }
)


@dataclass(frozen=True)
class ReplayComparisonRow:
    position_id: str
    command_id: Optional[str]
    legacy_event_type: str
    legacy_source_module: str
    legacy_occurred_at: str
    diff_engine_classifications: tuple[str, ...]
    matched: bool


@dataclass
class ReplayReport:
    window_start: str
    window_end: str
    generated_at: str
    diff_report: DiffReport
    legacy_event_count: int
    comparisons: list[ReplayComparisonRow] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for c in self.comparisons if c.matched)

    @property
    def mismatched_count(self) -> int:
        return sum(1 for c in self.comparisons if not c.matched)

    def to_json_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "generated_at": self.generated_at,
            "legacy_event_count": self.legacy_event_count,
            "diff_finding_count": len(self.diff_report.findings),
            "matched_count": self.matched_count,
            "mismatched_count": self.mismatched_count,
            "diff_report": self.diff_report.to_json_dict(),
            "comparisons": [
                {
                    "position_id": c.position_id,
                    "command_id": c.command_id,
                    "legacy_event_type": c.legacy_event_type,
                    "legacy_source_module": c.legacy_source_module,
                    "legacy_occurred_at": c.legacy_occurred_at,
                    "diff_engine_classifications": list(c.diff_engine_classifications),
                    "matched": c.matched,
                }
                for c in self.comparisons
            ],
        }


def load_legacy_corrective_events(
    conn_trades: sqlite3.Connection, *, window_start: str, window_end: str
) -> list[sqlite3.Row]:
    """Read-only: every position_events row a legacy reconciler appended in
    [window_start, window_end). ISO8601 UTC text timestamps compare
    lexicographically -- no datetime parsing needed.
    """
    placeholders = ", ".join("?" for _ in LEGACY_RECONCILER_SOURCE_MODULES)
    return conn_trades.execute(
        f"""
        SELECT event_id, position_id, command_id, event_type, source_module, occurred_at
          FROM position_events
         WHERE occurred_at >= ? AND occurred_at < ?
           AND source_module IN ({placeholders})
         ORDER BY occurred_at
        """,
        (window_start, window_end, *sorted(LEGACY_RECONCILER_SOURCE_MODULES)),
    ).fetchall()


def replay_window(
    conn_trades: sqlite3.Connection,
    conn_forecasts: Optional[sqlite3.Connection],
    chain_by_asset: dict,
    *,
    window_start: str,
    window_end: str,
    now: Optional[datetime] = None,
) -> ReplayReport:
    """Run the diff engine over CURRENT persisted state (apply=False, always)
    and compare its findings against what legacy passes appended during
    [window_start, window_end).

    Simplification (documented, not chased): this compares the diff engine's
    view of CURRENT state against historical legacy WRITES in the window,
    not a byte-exact point-in-time replay of state as of each legacy write
    -- position_current/venue_commands do not carry a queryable history
    (only position_events/venue_command_events do), so a true time-travel
    replay would require rebuilding projections event-by-event, which is a
    separate, larger undertaking than this packet's scope. This is still a
    meaningful acceptance signal: a position with a legacy corrective event
    in the window and a live drift the diff engine STILL flags today is
    strong same-root-cause evidence; the mismatch/match counts are the
    reportable finding either way (see module docstring).
    """
    now = now or datetime.now(timezone.utc)
    diff_report = reconcile(conn_trades, conn_forecasts, chain_by_asset, apply=False, now=now)

    findings_by_position: dict[str, list[DiffFinding]] = {}
    for finding in diff_report.findings:
        if finding.position_id:
            findings_by_position.setdefault(finding.position_id, []).append(finding)

    legacy_rows = load_legacy_corrective_events(conn_trades, window_start=window_start, window_end=window_end)
    comparisons: list[ReplayComparisonRow] = []
    for row in legacy_rows:
        position_id = str(row["position_id"] or "")
        matches = findings_by_position.get(position_id, [])
        comparisons.append(
            ReplayComparisonRow(
                position_id=position_id,
                command_id=(str(row["command_id"]) if row["command_id"] is not None else None),
                legacy_event_type=str(row["event_type"] or ""),
                legacy_source_module=str(row["source_module"] or ""),
                legacy_occurred_at=str(row["occurred_at"] or ""),
                diff_engine_classifications=tuple(f.classification for f in matches),
                matched=len(matches) > 0,
            )
        )

    return ReplayReport(
        window_start=window_start,
        window_end=window_end,
        generated_at=now.isoformat(),
        diff_report=diff_report,
        legacy_event_count=len(legacy_rows),
        comparisons=comparisons,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Runner entry point: replay the most recent 24h window on a live DB
    opened read-only. Demonstration/ops tool -- not part of the pytest
    suite (see tests/reconcile/test_replay_harness.py for the fixture-DB
    equivalent this shares logic with).

    Usage: python -m src.reconcile.replay --trades-db PATH [--forecasts-db PATH] [--hours 24]
    """
    import argparse
    import json as json_module

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades-db", required=True)
    parser.add_argument("--forecasts-db", default=None)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(argv)

    conn_trades = sqlite3.connect(f"file:{args.trades_db}?mode=ro", uri=True)
    conn_trades.row_factory = sqlite3.Row
    conn_forecasts = None
    if args.forecasts_db:
        conn_forecasts = sqlite3.connect(f"file:{args.forecasts_db}?mode=ro", uri=True)
        conn_forecasts.row_factory = sqlite3.Row

    now = datetime.now(timezone.utc)
    window_end = now.isoformat()
    window_start_row = conn_trades.execute(
        "SELECT MAX(occurred_at) AS latest FROM position_events"
    ).fetchone()
    latest_occurred_at = str(window_start_row["latest"] or window_end) if window_start_row else window_end
    from datetime import timedelta

    try:
        latest_dt = datetime.fromisoformat(latest_occurred_at.replace("Z", "+00:00"))
    except ValueError:
        latest_dt = now
    window_start = (latest_dt - timedelta(hours=args.hours)).isoformat()
    window_end = latest_dt.isoformat()

    report = replay_window(
        conn_trades, conn_forecasts, {}, window_start=window_start, window_end=window_end, now=now
    )
    print(json_module.dumps(report.to_json_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
