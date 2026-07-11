# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-1; src/contracts/entry_exposure_obligation.py;
#   src/state/schema/entry_exposure_obligations_schema.py

"""Writer/reader for entry_exposure_obligations (trade DB, sibling of review_work_items).

Foundation machinery only (see src/state/review_work_items.py module docstring for the
same caveat): not wired into cycle_runner/evaluator/fill_tracker/riskguard by this
packet. T2 consumes ``total_open_obligation_usd``/``has_unbounded_obligation`` when it
extends exposure/heat accounting to ChainOnlyFact worst case (BLOCKER-1); T4 consumes
``open_entry_exposure_obligation``/``resolve_entry_exposure_obligation`` on its
pending-entry failure paths.

INV-37: every function requires a caller-supplied conn; nothing here auto-opens.
Schema must already exist on ``conn`` (call
src.state.schema.entry_exposure_obligations_schema.ensure_table first).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.contracts.entry_exposure_obligation import EntryExposureObligation
from src.contracts.review_work_item import FamilyKey

_COLUMNS = (
    "command_id",
    "owner_domain",
    "token_id",
    "condition_id",
    "shares",
    "cost_basis_usd",
    "unbounded",
    "family_city",
    "family_target_date",
    "family_temperature_metric",
    "family_market_family_id",
    "status",
    "created_at",
    "updated_at",
    "resolved_at",
)
_SELECT_COLUMNS_SQL = ", ".join(_COLUMNS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_obligation(row: tuple) -> EntryExposureObligation:
    (
        command_id,
        owner_domain,
        token_id,
        condition_id,
        shares,
        cost_basis_usd,
        unbounded,
        family_city,
        family_target_date,
        family_temperature_metric,
        family_market_family_id,
        status,
        created_at,
        updated_at,
        resolved_at,
    ) = row
    family_key = None
    if family_city is not None:
        family_key = FamilyKey(
            city=str(family_city),
            target_date=str(family_target_date or ""),
            temperature_metric=str(family_temperature_metric or ""),
            market_family_id=str(family_market_family_id or ""),
        )
    return EntryExposureObligation(
        command_id=str(command_id),
        owner_domain=str(owner_domain),
        token_id=str(token_id or ""),
        condition_id=str(condition_id or ""),
        shares=(float(shares) if shares is not None else None),
        cost_basis_usd=(float(cost_basis_usd) if cost_basis_usd is not None else None),
        unbounded=bool(unbounded),
        family_key=family_key,
        status=str(status),
        created_at=str(created_at),
        updated_at=str(updated_at),
        resolved_at=(str(resolved_at) if resolved_at is not None else None),
    )


def open_entry_exposure_obligation(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    owner_domain: str,
    token_id: str = "",
    condition_id: str = "",
    shares: Optional[float] = None,
    cost_basis_usd: Optional[float] = None,
    unbounded: bool = False,
    family_key: Optional[FamilyKey] = None,
    now: Optional[str] = None,
) -> EntryExposureObligation:
    """Upsert-open one obligation row keyed by ``command_id``.

    Idempotent by design as a LIVE fact, not an append log: calling this
    twice for the same command_id updates the SAME row with the caller's
    newest read of that command's economics (e.g. a bounded estimate refined
    by a later retry). Meant to be called ATOMICALLY on a command's failure
    path (BLOCKER-1: "created ATOMICALLY on the failure path... before
    return, not on a later load/reconcile") in the same transaction as the
    failure it is recovering from.

    ``EntryExposureObligation.__post_init__`` validates the candidate (the
    bounded/unbounded XOR) before any write is attempted.

    INV-37: caller supplies conn.
    """

    at = now or _now_iso()
    candidate = EntryExposureObligation(
        command_id=command_id,
        owner_domain=owner_domain,
        token_id=token_id,
        condition_id=condition_id,
        shares=shares,
        cost_basis_usd=cost_basis_usd,
        unbounded=unbounded,
        family_key=family_key,
        status="OPEN",
        created_at=at,
        updated_at=at,
    )

    fam = candidate.family_key
    conn.execute(
        """
        INSERT INTO entry_exposure_obligations (
            command_id, owner_domain, token_id, condition_id,
            shares, cost_basis_usd, unbounded,
            family_city, family_target_date, family_temperature_metric, family_market_family_id,
            status, created_at, updated_at, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, NULL)
        ON CONFLICT(command_id) DO UPDATE SET
            token_id = excluded.token_id,
            condition_id = excluded.condition_id,
            shares = excluded.shares,
            cost_basis_usd = excluded.cost_basis_usd,
            unbounded = excluded.unbounded,
            family_city = excluded.family_city,
            family_target_date = excluded.family_target_date,
            family_temperature_metric = excluded.family_temperature_metric,
            family_market_family_id = excluded.family_market_family_id,
            status = 'OPEN',
            updated_at = excluded.updated_at,
            resolved_at = NULL
        """,
        (
            candidate.command_id,
            candidate.owner_domain,
            candidate.token_id,
            candidate.condition_id,
            candidate.shares,
            candidate.cost_basis_usd,
            int(bool(candidate.unbounded)),
            fam.city if fam is not None else None,
            fam.target_date if fam is not None else None,
            fam.temperature_metric if fam is not None else None,
            fam.market_family_id if fam is not None else None,
            at,
            at,
        ),
    )
    row = conn.execute(
        f"SELECT {_SELECT_COLUMNS_SQL} FROM entry_exposure_obligations WHERE command_id = ?",
        (candidate.command_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"open_entry_exposure_obligation: no row found after upsert for command_id={command_id!r}"
        )
    return _row_to_obligation(row)


def resolve_entry_exposure_obligation(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    resolved_at: Optional[str] = None,
) -> bool:
    """Mark a command's obligation RESOLVED once authoritative settled
    economics supersede the conservative/unbounded estimate. Returns True
    iff an OPEN row existed and was resolved.

    INV-37: caller supplies conn.
    """

    at = resolved_at or _now_iso()
    cur = conn.execute(
        """
        UPDATE entry_exposure_obligations
           SET status = 'RESOLVED', resolved_at = ?, updated_at = ?
         WHERE command_id = ? AND status = 'OPEN'
        """,
        (at, at, command_id),
    )
    return cur.rowcount == 1


def total_open_obligation_usd(conn: sqlite3.Connection) -> float:
    """Sum of conservative bounded exposure (``shares`` x $1/share, long-only
    CTF bound — see src.contracts.entry_exposure_obligation module docstring)
    across OPEN, bounded obligations. Excludes unbounded rows entirely —
    those must be surfaced via ``has_unbounded_obligation`` and routed to
    DATA_DEGRADED, never folded into this dollar total as if they were zero.

    INV-37: caller supplies conn.
    """

    row = conn.execute(
        "SELECT COALESCE(SUM(shares), 0.0) FROM entry_exposure_obligations "
        "WHERE status = 'OPEN' AND unbounded = 0"
    ).fetchone()
    return float(row[0]) if row is not None else 0.0


def has_unbounded_obligation(conn: sqlite3.Connection) -> bool:
    """True iff any OPEN obligation carries unknown (unbounded) exposure —
    BLOCKER-1's "unbounded obligation -> DATA_DEGRADED" leg.

    INV-37: caller supplies conn.
    """

    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM entry_exposure_obligations WHERE status = 'OPEN' AND unbounded = 1)"
    ).fetchone()
    return bool(row[0]) if row is not None else False


def open_obligation_family_keys(conn: sqlite3.Connection) -> set[FamilyKey]:
    """Distinct family keys carried by OPEN entry_exposure_obligations rows.

    T2 (docs/rebuild/quarantine_excision_2026-07-11.md, family-scoped block):
    an OPEN obligation is real-or-unknown exposure for a command whose fate
    (fill vs. no-fill) is not yet settled truth — the same category of "must
    not admit a sibling bin in this family" risk as an open ChainOnlyFact or
    family-blocking ReviewWorkItem. Consumed by
    ``src.state.review_work_items.blocked_family_keys`` so the candidate
    filter sees ONE unioned blocked-family view. Rows with no family_key
    (NULL family_city) are omitted here — they still count toward
    ``total_open_obligation_usd``/``has_unbounded_obligation``, just not
    toward a family-scoped block.

    INV-37: caller supplies conn.
    """

    rows = conn.execute(
        "SELECT DISTINCT family_city, family_target_date, family_temperature_metric, "
        "family_market_family_id FROM entry_exposure_obligations "
        "WHERE status = 'OPEN' AND family_city IS NOT NULL"
    ).fetchall()
    return {
        FamilyKey(
            city=str(city),
            target_date=str(target_date or ""),
            temperature_metric=str(metric or ""),
            market_family_id=str(market_family_id or ""),
        )
        for city, target_date, metric, market_family_id in rows
    }


__all__ = [
    "open_entry_exposure_obligation",
    "resolve_entry_exposure_obligation",
    "total_open_obligation_usd",
    "has_unbounded_obligation",
    "open_obligation_family_keys",
]
