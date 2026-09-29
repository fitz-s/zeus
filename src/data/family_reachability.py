# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: docs/operations/current/plans/edge_program_2026-09-25.md goal 4
#   (one universal retention rule). Extracted verbatim from src/data/forecast_retention.py
#   so forecast and trade/world retention share ONE reachability predicate.
"""Family reachability: the one retention predicate every Zeus store keys on.

A forecast/market family ``(city, target_date, metric)`` is reachable while

* its target local day has not ended everywhere (``target_date`` within
  ``REACHABLE_TARGET_LAG_DAYS`` of the UTC date: the last local day, UTC-12, ends at
  ``target_date + 1`` 12:00Z, so no materialization, Day0 or entry reader reaches it
  after that), or
* a position on it is not terminal (``position_current.phase`` outside
  settled/voided/admin_closed; ``economically_closed`` is still settleable, so kept), or
* an ENTRY rest is still open on the venue (resolved by the reactor's own
  ``_open_rest_family_rows_for_refresh``).

Unknown reachability evicts nothing: a trade-DB read failure, or a non-terminal
position with NULL city/target_date/metric, raises and the caller does no work.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REACHABLE_TARGET_LAG_DAYS = 2
TERMINAL_PHASES = frozenset({"settled", "voided", "admin_closed"})

Family = tuple[str, str, str]


def norm_city(city: str) -> str:
    return str(city).strip().replace(" ", "_")


def family(city: str, target_date: str, metric: str) -> Family:
    return (norm_city(city), str(target_date), str(metric).lower())


@dataclass(frozen=True)
class Reachability:
    """Which families any reader can still reach."""

    oldest_reachable_date: str
    open_families: frozenset[Family]

    def reachable(self, fam: Family) -> bool:
        return fam[1] >= self.oldest_reachable_date or fam in self.open_families


class ReachabilityUnknown(RuntimeError):
    """A family some reader can reach cannot be named; evict nothing."""


def read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    from src.state.db import _connect_read_only  # noqa: PLC0415

    return _connect_read_only(db_path)


def _open_rest_families(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    # Probe first: the reactor resolver swallows read errors as "no rests".
    conn.execute("SELECT count(*) FROM venue_commands").fetchone()
    from src.events.reactor import _open_rest_family_rows_for_refresh  # noqa: PLC0415

    return _open_rest_family_rows_for_refresh(conn)


def open_position_families(trade_db: Path) -> frozenset[Family]:
    """Families with a non-terminal position or an open ENTRY rest.

    Raises on any read failure and ``ReachabilityUnknown`` on a non-terminal position
    whose family is not fully named (fail closed).
    """

    conn = read_only(Path(trade_db))
    try:
        placeholders = ",".join("?" for _ in TERMINAL_PHASES)
        rows = conn.execute(
            f"""
            SELECT DISTINCT city, target_date, temperature_metric
              FROM position_current
             WHERE phase NOT IN ({placeholders})
            """,
            tuple(sorted(TERMINAL_PHASES)),
        ).fetchall()
        rests = _open_rest_families(conn)
    finally:
        conn.close()
    unnamed = [row for row in rows if any(v is None or str(v).strip() == "" for v in row)]
    if unnamed:
        raise ReachabilityUnknown(f"{len(unnamed)} non-terminal position family(ies) not named")
    return frozenset(family(*row) for row in [*rows, *rests])


def build_reachability(*, now: datetime, trade_db: Path | None = None) -> Reachability:
    """The one family-reachability law for every store and queue.

    ``trade_db`` defaults to the canonical trade DB. Raises when reachability is
    unknown (read failure or an unnamed open family); callers keep everything.
    """

    if trade_db is None:
        from src.state.db import _zeus_trade_db_path  # noqa: PLC0415

        trade_db = _zeus_trade_db_path()
    oldest = now.astimezone(timezone.utc).date() - timedelta(days=REACHABLE_TARGET_LAG_DAYS)
    return Reachability(oldest.isoformat(), open_position_families(Path(trade_db)))
