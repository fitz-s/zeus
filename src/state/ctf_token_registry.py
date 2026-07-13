# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Attack F;
#   src/state/schema/ctf_token_registry_schema.py

"""Writer/reader for ctf_token_registry — the durable CTF token discovery log.

``record_token_seen`` is idempotent-open: the FIRST call for a token_id fixes
``first_source``/``first_seen_at`` permanently; every later call (any source)
only advances ``last_confirmed_at``. This is deliberate — first_source is
provenance ("how did Zeus first learn this token exists"), not a live
authority column, so it must never be overwritten by a later, unrelated
observation.

LAW (Attack F, docs/rebuild/local_ledger_excision_2026-07-12.md): a token's
absence from a later ``/positions`` read is NEVER grounds to delete or
otherwise erase its row. There is no delete function in this module by
design (the schema's BEFORE DELETE trigger makes an accidental delete fail
loudly instead of silently).

INV-37: every function requires a caller-supplied conn; nothing here
auto-opens or ATTACHes a connection. Schema must already exist on ``conn``
(call src.state.schema.ctf_token_registry_schema.ensure_table first — boot
wiring lives in src.state.db.init_schema_trade_only for the trade DB
instance).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.state.schema.ctf_token_registry_schema import FIRST_SOURCES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CtfTokenRegistryRow:
    """Read-only view of one ctf_token_registry row."""

    token_id: str
    condition_id: str
    first_source: str
    first_seen_at: str
    last_confirmed_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_token_seen(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    condition_id: str,
    source: str,
    seen_at: Optional[str] = None,
) -> CtfTokenRegistryRow:
    """Register that ``token_id`` (belonging to ``condition_id``) was observed via ``source``.

    Idempotent-open: a brand-new token_id inserts a row with first_source=source.
    An already-known token_id only advances last_confirmed_at — first_source and
    first_seen_at are immutable provenance. If a later observation reports a
    DIFFERENT condition_id for an already-registered token_id, that is a real
    identity conflict (one ERC-1155 token_id belongs to exactly one condition
    on-chain): the row's original condition_id is kept and the conflict is
    logged rather than silently overwritten, since a wrong overwrite could
    misattribute a redemption to the wrong market.
    """

    token_id = str(token_id or "").strip()
    condition_id = str(condition_id or "").strip()
    if not token_id:
        raise ValueError("ctf_token_registry_missing_token_id")
    if not condition_id:
        raise ValueError("ctf_token_registry_missing_condition_id")
    if source not in FIRST_SOURCES:
        raise ValueError(f"ctf_token_registry_invalid_source:{source!r}")

    now = seen_at or _now_iso()

    existing = conn.execute(
        "SELECT token_id, condition_id, first_source, first_seen_at, last_confirmed_at "
        "FROM ctf_token_registry WHERE token_id = ?",
        (token_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO ctf_token_registry (
                token_id, condition_id, first_source, first_seen_at, last_confirmed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (token_id, condition_id, source, now, now),
        )
        return CtfTokenRegistryRow(token_id, condition_id, source, now, now)

    existing_condition_id = existing[1]
    if existing_condition_id and condition_id and existing_condition_id != condition_id:
        logger.error(
            "ctf_token_registry: condition_id conflict for token_id=%s — "
            "registered=%s new_observation(source=%s)=%s; keeping registered "
            "condition_id (never overwritten by a later observation).",
            token_id,
            existing_condition_id,
            source,
            condition_id,
        )

    conn.execute(
        "UPDATE ctf_token_registry SET last_confirmed_at = ? WHERE token_id = ?",
        (now, token_id),
    )
    return CtfTokenRegistryRow(
        token_id, existing_condition_id, existing[2], existing[3], now
    )


def get_token_registry_row(
    conn: sqlite3.Connection, *, token_id: str
) -> Optional[CtfTokenRegistryRow]:
    """Return the registry row for token_id, or None if never observed."""

    row = conn.execute(
        "SELECT token_id, condition_id, first_source, first_seen_at, last_confirmed_at "
        "FROM ctf_token_registry WHERE token_id = ?",
        (str(token_id or "").strip(),),
    ).fetchone()
    if row is None:
        return None
    return CtfTokenRegistryRow(*row)


def known_token_ids(conn: sqlite3.Connection) -> frozenset[str]:
    """Return every token_id Zeus has ever registered (never shrinks — Attack F)."""

    return frozenset(
        row[0] for row in conn.execute("SELECT token_id FROM ctf_token_registry").fetchall()
    )


__all__ = [
    "CtfTokenRegistryRow",
    "record_token_seen",
    "get_token_registry_row",
    "known_token_ids",
]
