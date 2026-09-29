# Created: 2026-09-29
# Authority basis: operator law "universality and exclusivity must coexist";
#   live cut stalls 8df58fddc / 14244e742 / 3d9154568 (a family verdict that was
#   missing from a hand-kept list failed every global auction cut).
"""Which prepare faults exclude one weather family, and which stop the whole cut.

A family's current probability is built from that family's own rows: its
forecast, observations, carriers and bin topology.  Inside that build a
``ValueError`` is a verdict on this family's evidence ("right type, wrong
value").  It removes only this family from the auction, by construction, so a
new evidence reason never needs registering.

The whole cut stops for everything that makes shared state unsafe:

* a DB or OS fault (``sqlite3.Error``, ``OSError``), raised directly or chained
  anywhere under the verdict; a SQLite lock or busy fault alone is transient and
  still only defers its own family;
* a ``GlobalValueFault``: a value fault identical for every family (a caller
  contract, a missing authority schema);
* every other exception type, which is unknown and fails closed.

SCOPE: one city/date/metric family.  DRAIN: the next cut prepares it again from
current rows.  RESET: its prepare returns a witness.
"""

from __future__ import annotations

import sqlite3

FAMILY_AUTHORITY_UNAVAILABLE = "FamilyAuthorityUnavailable"
TRANSIENT_FAMILY_AUTHORITY_UNAVAILABLE = "TransientFamilyAuthorityUnavailable"


class GlobalValueFault(ValueError):
    """A value fault in shared state or in the caller, never in one family's evidence."""


def is_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None and code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def _causal_chain(exc: BaseException):
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        yield link
        # ``raise X from None`` hides the context from tracebacks, not from us.
        link = link.__cause__ if link.__cause__ is not None else link.__context__


def family_fault_tag(exc: BaseException) -> str | None:
    """Return the family-scoped tag for ``exc``, or None when the cut must stop."""

    transient = False
    for link in _causal_chain(exc):
        if isinstance(link, GlobalValueFault):
            return None
        if isinstance(link, (sqlite3.Error, OSError)):
            if not is_sqlite_lock_error(link):
                return None
            transient = True
    if transient:
        return TRANSIENT_FAMILY_AUTHORITY_UNAVAILABLE
    if isinstance(exc, ValueError):
        return FAMILY_AUTHORITY_UNAVAILABLE
    return None
