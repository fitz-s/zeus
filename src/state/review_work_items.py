# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   (adopted target shape); src/contracts/review_work_item.py;
#   src/state/schema/review_work_items_schema.py

"""Writer/reader for review_work_items — the owner-local ReviewWorkItem table.

Foundation machinery only (docs/rebuild/quarantine_excision_2026-07-11.md T2/T4/T5
gate): this module provides open/resolve/supersede/due-work/family-block primitives.
It is NOT wired into cycle_runner/evaluator/fill_tracker/riskguard by this packet —
later packets (T2, T4) consume these functions from their own seams.

INV-37: every function requires a caller-supplied conn; nothing here auto-opens or
ATTACHes a connection. Schema must already exist on ``conn`` (call
src.state.schema.review_work_items_schema.ensure_table first — boot wiring lives in
src.state.db.init_schema_trade_only for the trade DB instance).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.contracts.review_work_item import (
    FAMILY_BLOCKING_REASON_CODES,
    FamilyKey,
    ReviewReasonCode,
    ReviewWorkItem,
    WorkItemStatus,
)
from src.strategy.family_exclusive_dedup import WeatherFamilyKey

_COLUMNS = (
    "work_id",
    "owner_domain",
    "owner_table",
    "subject_id",
    "reason_code",
    "authority_revision",
    "evidence_refs_json",
    "evidence_hash",
    "first_seen_at",
    "last_seen_at",
    "family_city",
    "family_target_date",
    "family_temperature_metric",
    "family_market_family_id",
    "exposure_bound_usd",
    "unbounded",
    "attempt_count",
    "next_attempt_at",
    "priority",
    "last_error_class",
    "last_error_detail",
    "status",
    "resolver_identity",
    "resolution_evidence",
    "resolved_at",
)
_SELECT_COLUMNS_SQL = ", ".join(_COLUMNS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_work_item(row: tuple) -> ReviewWorkItem:
    (
        work_id,
        owner_domain,
        owner_table,
        subject_id,
        reason_code,
        authority_revision,
        evidence_refs_json,
        evidence_hash,
        first_seen_at,
        last_seen_at,
        family_city,
        family_target_date,
        family_temperature_metric,
        family_market_family_id,
        exposure_bound_usd,
        unbounded,
        attempt_count,
        next_attempt_at,
        priority,
        last_error_class,
        last_error_detail,
        status,
        resolver_identity,
        resolution_evidence,
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
    return ReviewWorkItem(
        work_id=str(work_id),
        owner_domain=str(owner_domain),
        owner_table=str(owner_table),
        subject_id=str(subject_id),
        reason_code=ReviewReasonCode(str(reason_code)),
        authority_revision=int(authority_revision),
        evidence_refs=tuple(json.loads(evidence_refs_json or "[]")),
        evidence_hash=str(evidence_hash or ""),
        first_seen_at=str(first_seen_at),
        last_seen_at=str(last_seen_at),
        family_key=family_key,
        exposure_bound_usd=(float(exposure_bound_usd) if exposure_bound_usd is not None else None),
        unbounded=bool(unbounded),
        attempt_count=int(attempt_count),
        next_attempt_at=str(next_attempt_at),
        priority=int(priority),
        last_error_class=str(last_error_class or ""),
        last_error_detail=str(last_error_detail or ""),
        status=WorkItemStatus(str(status)),
        resolver_identity=str(resolver_identity or ""),
        resolution_evidence=str(resolution_evidence or ""),
        resolved_at=(str(resolved_at) if resolved_at is not None else None),
    )


def open_work_item(
    conn: sqlite3.Connection,
    *,
    owner_domain: str,
    owner_table: str,
    subject_id: str,
    reason_code: ReviewReasonCode,
    authority_revision: int = 0,
    evidence_refs: tuple[str, ...] = (),
    evidence_hash: str = "",
    family_key: Optional[FamilyKey] = None,
    exposure_bound_usd: Optional[float] = None,
    unbounded: bool = False,
    next_attempt_at: Optional[str] = None,
    priority: int = 100,
    last_error_class: str = "",
    last_error_detail: str = "",
    now: Optional[str] = None,
) -> ReviewWorkItem:
    """Idempotent open: two calls with the same (owner_table, subject_id,
    reason_code, authority_revision) converge on ONE OPEN row — the second
    call returns the row the FIRST call created (enforced by the partial
    unique index, not by re-checking in Python; safe under concurrent callers
    on separate connections to the same DB file).

    ``ReviewWorkItem.__post_init__`` validates the candidate (e.g. the
    bounded/unbounded XOR) before any write is attempted.

    INV-37: caller supplies conn.
    """

    at = now or _now_iso()
    candidate = ReviewWorkItem(
        work_id=f"rwi_{uuid.uuid4().hex}",
        owner_domain=owner_domain,
        owner_table=owner_table,
        subject_id=subject_id,
        reason_code=ReviewReasonCode(reason_code),
        authority_revision=int(authority_revision),
        evidence_refs=tuple(evidence_refs),
        evidence_hash=evidence_hash,
        first_seen_at=at,
        last_seen_at=at,
        family_key=family_key,
        exposure_bound_usd=exposure_bound_usd,
        unbounded=unbounded,
        attempt_count=0,
        next_attempt_at=next_attempt_at or at,
        priority=int(priority),
        last_error_class=last_error_class,
        last_error_detail=last_error_detail,
        status=WorkItemStatus.OPEN,
    )

    fam = candidate.family_key
    conn.execute(
        """
        INSERT OR IGNORE INTO review_work_items (
            work_id, owner_domain, owner_table, subject_id, reason_code,
            authority_revision, evidence_refs_json, evidence_hash,
            first_seen_at, last_seen_at,
            family_city, family_target_date, family_temperature_metric, family_market_family_id,
            exposure_bound_usd, unbounded, attempt_count, next_attempt_at, priority,
            last_error_class, last_error_detail, status,
            resolver_identity, resolution_evidence, resolved_at,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, 0, ?, ?,
            ?, ?, 'OPEN',
            '', '', NULL,
            ?, ?
        )
        """,
        (
            candidate.work_id,
            candidate.owner_domain,
            candidate.owner_table,
            candidate.subject_id,
            candidate.reason_code.value,
            candidate.authority_revision,
            json.dumps(list(candidate.evidence_refs)),
            candidate.evidence_hash,
            candidate.first_seen_at,
            candidate.last_seen_at,
            fam.city if fam is not None else None,
            fam.target_date if fam is not None else None,
            fam.temperature_metric if fam is not None else None,
            fam.market_family_id if fam is not None else None,
            candidate.exposure_bound_usd,
            int(bool(candidate.unbounded)),
            candidate.next_attempt_at,
            candidate.priority,
            candidate.last_error_class,
            candidate.last_error_detail,
            at,
            at,
        ),
    )
    row = conn.execute(
        f"SELECT {_SELECT_COLUMNS_SQL} FROM review_work_items "
        "WHERE owner_table = ? AND subject_id = ? AND reason_code = ? AND authority_revision = ? "
        "AND status = 'OPEN'",
        (owner_table, subject_id, candidate.reason_code.value, int(authority_revision)),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "open_work_item: no OPEN row found immediately after INSERT OR IGNORE — "
            "unexpected concurrent resolve/supersede race on "
            f"({owner_table!r}, {subject_id!r}, {candidate.reason_code.value!r}, {authority_revision!r})"
        )
    return _row_to_work_item(row)


def resolve_work_item(
    conn: sqlite3.Connection,
    *,
    work_id: str,
    authority_revision: int,
    resolver_identity: str,
    resolution_evidence: str,
    resolved_at: Optional[str] = None,
) -> bool:
    """CAS resolve: OPEN -> RESOLVED iff ``authority_revision`` matches the
    live row. Returns False (never raises) when the row is missing, already
    resolved/superseded, or was opened under a DIFFERENT authority_revision —
    a stale-revision resolve request is refused, per the adjudicated CAS law.

    INV-37: caller supplies conn.
    """

    at = resolved_at or _now_iso()
    cur = conn.execute(
        """
        UPDATE review_work_items
           SET status = 'RESOLVED',
               resolver_identity = ?,
               resolution_evidence = ?,
               resolved_at = ?,
               updated_at = ?
         WHERE work_id = ? AND status = 'OPEN' AND authority_revision = ?
        """,
        (resolver_identity, resolution_evidence, at, at, work_id, int(authority_revision)),
    )
    return cur.rowcount == 1


def supersede_on_new_revision(
    conn: sqlite3.Connection,
    *,
    owner_table: str,
    subject_id: str,
    reason_code: ReviewReasonCode,
    new_authority_revision: int,
    at: Optional[str] = None,
) -> int:
    """Mark OPEN rows for (owner_table, subject_id, reason_code) whose
    authority_revision is strictly older than ``new_authority_revision`` as
    SUPERSEDED. A fresh fact revision makes any OPEN work item scheduled
    against a stale revision moot — retrying it would evaluate against dead
    authority. Returns the number of rows superseded.

    Does not open the new-revision item itself; call open_work_item
    separately with ``authority_revision=new_authority_revision``.

    INV-37: caller supplies conn.
    """

    ts = at or _now_iso()
    reason_value = ReviewReasonCode(reason_code).value
    cur = conn.execute(
        """
        UPDATE review_work_items
           SET status = 'SUPERSEDED',
               updated_at = ?
         WHERE owner_table = ? AND subject_id = ? AND reason_code = ?
           AND status = 'OPEN' AND authority_revision < ?
        """,
        (ts, owner_table, subject_id, reason_value, int(new_authority_revision)),
    )
    return cur.rowcount


def due_work(
    conn: sqlite3.Connection,
    *,
    now: Optional[str] = None,
    limit: int = 50,
) -> list[ReviewWorkItem]:
    """Return OPEN items due for another attempt, priority-then-time ordered.

    INV-37: caller supplies conn.
    """

    at = now or _now_iso()
    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS_SQL} FROM review_work_items "
        "WHERE status = 'OPEN' AND next_attempt_at <= ? "
        "ORDER BY priority ASC, next_attempt_at ASC LIMIT ?",
        (at, int(limit)),
    ).fetchall()
    return [_row_to_work_item(row) for row in rows]


def open_items_by_family(
    conn: sqlite3.Connection,
    family_key: WeatherFamilyKey | FamilyKey,
) -> list[ReviewWorkItem]:
    """Return OPEN items scoped to one weather outcome family.

    Accepts either the live ``WeatherFamilyKey`` or the K0 ``FamilyKey`` —
    both expose ``.city``/``.target_date``/``.temperature_metric``.
    ``market_family_id`` does not narrow the match (mirrors
    src.strategy.family_exclusive_dedup._family_keys_conflict: sibling
    temperature bins for one city/date/metric are one settlement partition
    regardless of per-bin market/condition id).

    INV-37: caller supplies conn.
    """

    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS_SQL} FROM review_work_items "
        "WHERE status = 'OPEN' AND family_city = ? AND family_target_date = ? "
        "AND family_temperature_metric = ? "
        "ORDER BY priority ASC, next_attempt_at ASC",
        (str(family_key.city), str(family_key.target_date), str(family_key.temperature_metric)),
    ).fetchall()
    return [_row_to_work_item(row) for row in rows]


def open_unbounded_count(conn: sqlite3.Connection) -> int:
    """Count of OPEN work items carrying unbounded (unknown) exposure —
    BLOCKER-1's DATA_DEGRADED trigger. INV-37: caller supplies conn.
    """

    row = conn.execute(
        "SELECT COUNT(*) FROM review_work_items WHERE status = 'OPEN' AND unbounded = 1"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _attached_schema_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return ("main",)
    names = [str(row[1]) for row in rows if len(row) > 1]
    return tuple(dict.fromkeys(names)) or ("main",)


def _family_key_for_chain_only_fact(conn: sqlite3.Connection, fact: object) -> Optional[WeatherFamilyKey]:
    """Best-effort market_events lookup for a ChainOnlyFact's family identity.

    Prefilter read helper (adjudication: "evaluator filtering stays a
    prefilter, never an enforcement authority") — returns None rather than
    raising when market_events is not reachable on this connection or the
    fact's token/condition id has no match; callers must not treat None as
    proof the fact is unscoped.
    """

    condition_id = str(getattr(fact, "condition_id", "") or "")
    token_id = str(getattr(fact, "token_id", "") or "")
    if not condition_id and not token_id:
        return None
    for schema in _attached_schema_names(conn):
        table_ref = "market_events" if schema == "main" else f'"{schema}".market_events'
        try:
            row = conn.execute(
                f"SELECT city, target_date, temperature_metric FROM {table_ref} "
                "WHERE (condition_id = ? AND ? != '') OR (token_id = ? AND ? != '') LIMIT 1",
                (condition_id, condition_id, token_id, token_id),
            ).fetchone()
        except sqlite3.Error:
            continue
        if row is not None:
            city, target_date, metric = row
            if city and target_date and metric:
                return WeatherFamilyKey(str(city), str(target_date), str(metric), "")
    return None


def blocked_family_keys(
    conn: sqlite3.Connection,
    portfolio: object = None,
) -> set[WeatherFamilyKey]:
    """Return the set of WeatherFamilyKey values currently blocked by open
    family-scoped review work items (FAMILY_BLOCKING_REASON_CODES) or by
    blocking ChainOnlyFacts on ``portfolio.chain_only_facts``.

    This is the seam T2's candidate filter will consult — a read helper, not
    an enforcement authority itself. ``portfolio`` may be None or any object
    exposing a ``chain_only_facts`` iterable of ChainOnlyFact-like objects
    (duck-typed: reads ``.condition_id``/``.token_id``/``.blocks_entry``).

    INV-37: caller supplies conn.
    """

    keys: set[WeatherFamilyKey] = set()
    placeholders = ", ".join("?" for _ in FAMILY_BLOCKING_REASON_CODES)
    rows = conn.execute(
        "SELECT DISTINCT family_city, family_target_date, family_temperature_metric, "
        "family_market_family_id FROM review_work_items "
        f"WHERE status = 'OPEN' AND family_city IS NOT NULL AND reason_code IN ({placeholders})",
        tuple(code.value for code in FAMILY_BLOCKING_REASON_CODES),
    ).fetchall()
    for city, target_date, metric, market_family_id in rows:
        keys.add(WeatherFamilyKey(str(city), str(target_date), str(metric), str(market_family_id or "")))

    chain_only_facts = list(getattr(portfolio, "chain_only_facts", None) or ())
    for fact in chain_only_facts:
        if not bool(getattr(fact, "blocks_entry", True)):
            continue
        key = _family_key_for_chain_only_fact(conn, fact)
        if key is not None:
            keys.add(key)
    return keys


__all__ = [
    "open_work_item",
    "resolve_work_item",
    "supersede_on_new_revision",
    "due_work",
    "open_items_by_family",
    "open_unbounded_count",
    "blocked_family_keys",
]
