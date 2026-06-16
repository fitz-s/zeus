# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/MASTER_TIMING_FIX_PLAN_2026-06-16.md §3 ANTIBODY 1;
#   timestamp_provenance_ledger_2026-06-16.md; operator no-guessing mandate (every replacement time has a reason).
"""Canonical proof-of-possession availability time — the single correct producer of `available_at`.

`available_at` answers ONE question: the earliest time forecast data was genuinely usable BY US.
The only correct basis is **proof of possession** — when we actually held the data (the real
`fetch_time` / authority-write / file-write-complete wall-clock), optionally credited back to a
*real* provider-publication estimate (cycle + a measured release lag). It is NEVER the raw model
cycle time (`source_cycle_time` / `issue_time`), which is the run's nominal init hour — hours
before the data is published, fetched, or possessed. Stamping the cycle as `available_at` is the
~8.4h-early lie this module exists to kill (C1-AVAIL-CLOCK).

EVERY writer of `available_at` / `source_available_at` MUST route through
`proof_of_possession_available_at`. Direct assignment of a raw cycle time, or of a release-gate
estimate treated as fact, is forbidden and CI-banned (tests/test_availability_time_law.py).
When no genuine possession time exists -> the caller must write NULL, never a guess.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

TimeLike = Union[str, datetime]


def _to_dt(value: TimeLike) -> datetime:
    """Parse an ISO-8601 string or datetime into a tz-aware UTC datetime.

    Naive inputs are interpreted as UTC (Zeus persists UTC wall-clocks); mixed 'Z' and
    '+00:00' suffixes are both accepted. Raises ValueError on an unparseable value rather
    than substituting a guess.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("proof_of_possession_available_at: empty captured_at — pass a real possession time or NULL")
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def proof_of_possession_available_at(
    captured_at: TimeLike,
    nominal_available: Optional[TimeLike] = None,
) -> str:
    """Honest `available_at` as an ISO-8601 UTC string (with '+00:00').

    Parameters
    ----------
    captured_at:
        The REAL possession wall-clock — `fetch_time`, authority-write time, or
        file-write-complete time. Must be a genuine possession event, never the model cycle.
    nominal_available:
        A REAL provider-publication estimate (e.g. cycle + a *measured* release lag), if and
        only if it is a genuine publish estimate. Pass ``None`` when the only candidate is the
        raw model cycle or an unverified gate — we never credit availability *earlier* than
        possession on the strength of a guess.

    Returns
    -------
    ``min(captured_at, nominal_available)`` when a real nominal is supplied, else ``captured_at``.
    Never a time the data was not genuinely usable by.
    """
    cap = _to_dt(captured_at)
    if nominal_available is not None:
        nom = _to_dt(nominal_available)
        return min(cap, nom).isoformat()
    return cap.isoformat()
