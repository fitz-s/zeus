# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet
#   (Consult adjudication "DIQ CONDITIONAL"); supersedes
#   src/state/decision_integrity_quarantine.py (PR-E 2026-05-22).

"""fact_revocations — owner-local fact-revocation records (DIQ packet).

Quarantine excision (docs/rebuild/quarantine_excision_2026-07-11.md): the old
``decision_integrity_quarantine`` side-table lived ONLY in the trade DB while
tagging rows across all three physical DBs (a cross-DB existence-authority).
This module re-implements the same function — a genuine second
existence-authority, row-existence = revocation, keyed
(table_name, row_id, reason_code) with reason multiplicity and a meta_json
audit payload — as an OWNER-LOCAL record: each physical DB that owns a
revocable table gets its own local ``fact_revocations`` table
(src/state/schema/fact_revocations_schema.py), written in the SAME
transaction as (or immediately after) the fact it revokes, on THAT table's
own connection. This is a typed domain FACT (an authority), not a
ReviewWorkItem — see src/contracts/review_work_item.py module docstring
("Typed domain facts ... CertificateRevocation ... remain the AUTHORITIES").

Two independent revocation lanes share this one table shape:
  1. Forecast-snapshot linkage (PR-E, 2026-05-22): tags fact-table rows whose
     ensemble snapshot had contributes_to_target_extrema=0 or
     forecast_window_attribution_status='UNKNOWN'. Tables: opportunity_fact
     (trade), calibration_pairs (forecasts), probability_trace_fact,
     selection_family_fact, selection_hypothesis_fact, decision_events
     (world).
  2. Live money-certificate integrity: revokes LIVE decision_certificates
     rows (world) that fail current verifier semantics or whose live-money
     ancestry chain is not entirely LIVE.

Read side is generic and schema-agnostic: ``is_fact_revoked`` /
``is_certificate_revoked`` iterate every schema ATTACHed to the caller's
connection (a caller does not need to know in advance which DB the
revocation record for a given row lives in).

Write side is owner-local by construction: each bulk/single writer accepts a
``target_schema`` naming which schema on ``conn`` to write into (default
"main" — the common case where the caller's connection is already rooted at
the owning DB). Cross-DB writers (e.g. a world-main connection revoking a
trade-owned opportunity_fact row) pass ``target_schema="trade"`` after
ATTACHing it and calling ``fact_revocations_schema.ensure_table_in_schema``.

NON-destructive: rows are tagged, never deleted. Idempotent: INSERT OR
IGNORE, backed by UNIQUE(table_name, row_id, reason_code).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Reason codes written into fact_revocations.reason_code. Values renamed from
# the QUARANTINED_* predecessor vocabulary (quarantine excision word-and-shape
# removal); constant NAMES are unchanged so importers only need a module path
# update, not a call-site rename.
REASON_NON_CONTRIBUTING = "REVOKED_NON_CONTRIBUTING_FORECAST_EXTREMA"
REASON_INVALID_LIVE_ACTIONABLE = "REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE"
REASON_INVALID_LIVE_PARENT_MODE = "REVOKED_INVALID_LIVE_MONEY_PARENT_MODE"

# Table name tagged in revocation rows for the original opportunity_fact function.
TARGET_TABLE = "opportunity_fact"
DECISION_CERTIFICATES_TABLE = "decision_certificates"
LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES = (
    "ActionableTradeCertificate",
    "FinalIntentCertificate",
    "ExecutorExpressibilityCertificate",
    "PreSubmitRevalidationCertificate",
    "ExecutionCommandCertificate",
    "ExecutionReceiptCertificate",
    "LiveCapTransitionCertificate",
)


def revoke_invalid_live_actionable_certificates(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    lookback_hours: float | None = None,
    target_schema: str = "main",
) -> dict:
    """Tag LIVE ActionableTradeCertificate rows that fail current money-law semantics.

    The row remains immutable in ``decision_certificates``. The revocation tag
    is the durable live-reader exclusion proof: old VERIFIED rows may still
    exist for audit, but they are no longer consumable as executable authority.

    decision_certificates is world-owned (src/state/domains.py); ``conn`` is
    expected to be rooted at (or expose via ATTACH) the world DB, and
    ``target_schema`` names the schema fact_revocations is written into
    (default "main" — the world-main case; owner-local, no cross-DB write).
    """

    ref = _revocations_ref(conn, target_schema)
    recorded_at = datetime.now(timezone.utc).isoformat()
    params: list[object] = []
    since_clause = ""
    if lookback_hours is not None:
        from datetime import timedelta

        since = datetime.now(timezone.utc) - timedelta(hours=float(lookback_hours))
        since_clause = " AND datetime(decision_time) >= datetime(?)"
        params.append(since.isoformat())

    try:
        rows = conn.execute(
            f"""
            SELECT certificate_id, certificate_hash, decision_time, payload_json
              FROM decision_certificates
             WHERE certificate_type = 'ActionableTradeCertificate'
               AND mode = 'LIVE'
               AND verifier_status = 'VERIFIED'
               {since_clause}
             ORDER BY datetime(decision_time) DESC, certificate_id DESC
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        msg = f"invalid live actionable revocation scan failed: {exc}"
        logger.error(msg)
        return {
            "checked_count": 0,
            "candidates_found": 0,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    from src.decision_kernel.verifier import _verify_actionable_payload

    candidates: list[tuple[str, str | None, str, str]] = []
    checked_count = 0
    for row in rows:
        checked_count += 1
        cert_hash = str(row["certificate_hash"] if isinstance(row, sqlite3.Row) else row[1])
        cert_id = str(row["certificate_id"] if isinstance(row, sqlite3.Row) else row[0])
        decision_time = str(row["decision_time"] if isinstance(row, sqlite3.Row) else row[2])
        payload_json = row["payload_json"] if isinstance(row, sqlite3.Row) else row[3]
        try:
            payload = json.loads(str(payload_json or "{}"))
            if not isinstance(payload, dict):
                raise ValueError("payload_json is not an object")
            _verify_actionable_payload(type("_PayloadCarrier", (), {"payload": payload})())
        except Exception as exc:  # noqa: BLE001
            candidates.append((cert_hash, cert_id, decision_time, str(exc)))

    if dry_run or not candidates:
        return {
            "checked_count": checked_count,
            "candidates_found": len(candidates),
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_ACTIONABLE),
    ).fetchone()[0]
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        [
            (
                DECISION_CERTIFICATES_TABLE,
                cert_hash,
                REASON_INVALID_LIVE_ACTIONABLE,
                recorded_at,
                json.dumps(
                    {
                        "source": "revoke_invalid_live_actionable_certificates",
                        "certificate_id": cert_id,
                        "decision_time": decision_time,
                        "verification_error": reason,
                    },
                    sort_keys=True,
                ),
            )
            for cert_hash, cert_id, decision_time, reason in candidates
        ],
    )
    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_ACTIONABLE),
    ).fetchone()[0]
    newly_revoked = post_count - pre_count
    return {
        "checked_count": checked_count,
        "candidates_found": len(candidates),
        "already_revoked": len(candidates) - newly_revoked,
        "newly_revoked": newly_revoked,
        "dry_run": False,
    }


def revoke_invalid_live_money_parent_modes(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    lookback_hours: float | None = None,
    target_schema: str = "main",
) -> dict:
    """Tag LIVE money-boundary certificates whose parent ancestry is not LIVE.

    Historical mixed-mode certificates are immutable evidence, but they cannot
    remain consumable execution authority. This writes a non-destructive
    revocation row keyed by the child certificate hash. Owner-local: see
    ``revoke_invalid_live_actionable_certificates`` docstring.
    """

    ref = _revocations_ref(conn, target_schema)
    recorded_at = datetime.now(timezone.utc).isoformat()
    params: list[object] = list(LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES)
    placeholders = ",".join("?" for _ in LIVE_MONEY_CERTIFICATE_PARENT_MODE_TYPES)
    since_clause = ""
    if lookback_hours is not None:
        from datetime import timedelta

        since = datetime.now(timezone.utc) - timedelta(hours=float(lookback_hours))
        since_clause = " AND datetime(child.decision_time) >= datetime(?)"
        params.append(since.isoformat())

    try:
        candidates = conn.execute(
            f"""
            SELECT
                child.certificate_id,
                child.certificate_hash,
                child.certificate_type,
                child.decision_time,
                COUNT(*) AS bad_parent_count,
                GROUP_CONCAT(
                    edge.parent_certificate_type || ':' || COALESCE(parent.mode, 'MISSING'),
                    ','
                ) AS bad_parent_modes
              FROM decision_certificates child
              JOIN decision_certificate_edges edge
                ON edge.child_certificate_id = child.certificate_id
              LEFT JOIN decision_certificates parent
                ON parent.certificate_hash = edge.parent_certificate_hash
             WHERE child.certificate_type IN ({placeholders})
               AND child.mode = 'LIVE'
               AND child.verifier_status = 'VERIFIED'
               {since_clause}
               AND COALESCE(parent.mode, '') != 'LIVE'
             GROUP BY
                child.certificate_id,
                child.certificate_hash,
                child.certificate_type,
                child.decision_time
             ORDER BY datetime(child.decision_time) DESC, child.certificate_id DESC
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        msg = f"invalid live money parent-mode revocation scan failed: {exc}"
        logger.error(msg)
        return {
            "checked_count": 0,
            "candidates_found": 0,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(candidates)
    if dry_run or candidates_found == 0:
        return {
            "checked_count": candidates_found,
            "candidates_found": candidates_found,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_PARENT_MODE),
    ).fetchone()[0]
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        [
            (
                DECISION_CERTIFICATES_TABLE,
                str(row["certificate_hash"] if isinstance(row, sqlite3.Row) else row[1]),
                REASON_INVALID_LIVE_PARENT_MODE,
                recorded_at,
                json.dumps(
                    {
                        "source": "revoke_invalid_live_money_parent_modes",
                        "certificate_id": str(row["certificate_id"] if isinstance(row, sqlite3.Row) else row[0]),
                        "certificate_type": str(row["certificate_type"] if isinstance(row, sqlite3.Row) else row[2]),
                        "decision_time": str(row["decision_time"] if isinstance(row, sqlite3.Row) else row[3]),
                        "bad_parent_count": int(row["bad_parent_count"] if isinstance(row, sqlite3.Row) else row[4]),
                        "bad_parent_modes": str(row["bad_parent_modes"] if isinstance(row, sqlite3.Row) else row[5]),
                    },
                    sort_keys=True,
                ),
            )
            for row in candidates
        ],
    )
    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (DECISION_CERTIFICATES_TABLE, REASON_INVALID_LIVE_PARENT_MODE),
    ).fetchone()[0]
    newly_revoked = post_count - pre_count
    return {
        "checked_count": candidates_found,
        "candidates_found": candidates_found,
        "already_revoked": candidates_found - newly_revoked,
        "newly_revoked": newly_revoked,
        "dry_run": False,
    }


def revoke_decisions_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag opportunity_fact rows whose forecast snapshot has contributes=0 or attribution UNKNOWN.

    opportunity_fact is trade-owned (src/state/domains.py). Args:
        conn: connection exposing opportunity_fact and ensemble_snapshots
              (production: a world-main connection with forecasts ATTACHed as
              'forecasts' for the snapshot join, and trade ATTACHed for the
              owner-local revocation write — see target_schema).
        dry_run: If True, return counts without writing anything.
        target_schema: schema on ``conn`` fact_revocations is written into
              (default "main"; production callers pass "trade" since
              opportunity_fact's revocation record is owner-local to the
              trade DB, cross-DB from a world-main connection).

    Returns:
        Dict with keys:
          - candidates_found: int — opportunity rows matching bad-snapshot criteria
          - already_revoked: int — rows already tagged (skipped by INSERT OR IGNORE)
          - newly_revoked: int — rows newly written this run
          - dry_run: bool

    INV-37: caller supplies conn; never auto-opens.

    Note on the cross-DB join:
        In production the query uses 'forecasts.ensemble_snapshots', which requires
        the forecasts DB to be ATTACHed as alias 'forecasts'. When 'forecasts' is not
        attached (detected via PRAGMA database_list), the query falls back to the
        unqualified 'ensemble_snapshots' — this supports in-memory test DBs that
        carry the table without an ATTACH.
    """
    recorded_at = datetime.now(timezone.utc).isoformat()

    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    snap_ref = "forecasts.ensemble_snapshots" if "forecasts" in attached else "ensemble_snapshots"

    find_sql = f"""
        SELECT
            of.decision_id,
            of.snapshot_id
        FROM opportunity_fact of
        JOIN {snap_ref} esv
          ON CAST(of.snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE of.snapshot_id IS NOT NULL
          -- Align with the live reader gate (PR-A), which only acts when
          -- contributes_to_target_extrema is EXPLICITLY set; legacy NULL rows
          -- pass through live and must NOT be revoked.
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY of.decision_id
    """

    try:
        candidates = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"revocation query failed — ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
        )
        logger.error(msg)
        return {
            "candidates_found": 0,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(candidates)
    logger.info(
        "revoke_decisions: found %d candidate opportunity_fact rows with non-contributing snapshot",
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
        }

    ref = _revocations_ref(conn, target_schema)

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    insert_sql = f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    meta = json.dumps({"source": "revoke_decisions_for_noncontributing_forecast"})
    rows_to_insert = [
        (TARGET_TABLE, decision_id, REASON_NON_CONTRIBUTING, str(snapshot_id), recorded_at, meta)
        for decision_id, snapshot_id in candidates
    ]
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    newly_revoked = post_count - pre_count
    already_revoked = candidates_found - newly_revoked

    logger.info(
        "revoke_decisions: newly=%d already=%d total_after=%d",
        newly_revoked,
        already_revoked,
        post_count,
    )

    return {
        "candidates_found": candidates_found,
        "already_revoked": already_revoked,
        "newly_revoked": newly_revoked,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _revocations_ref(conn: sqlite3.Connection, schema: str = "main") -> str:
    """Return the fact_revocations table reference for ``schema`` on ``conn``.

    Owner-local write helper: unlike the predecessor's auto-detecting
    ``_quarantine_ref`` (which guessed at a single, always-trade target), this
    is a thin explicit qualifier — the caller already knows which physical DB
    owns the table it is revoking a row from (src/state/domains.py) and names
    that schema. "main" (the default) covers the common case where the
    caller's connection is already rooted at the owning DB.
    """
    if schema in ("", "main"):
        return "fact_revocations"
    if not all(ch.isalnum() or ch == "_" for ch in schema):
        raise ValueError(f"unsafe sqlite schema identifier: {schema!r}")
    return f"{schema}.fact_revocations"


def _attached_schema_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return ("main",)
    names: list[str] = []
    for row in rows:
        try:
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        except (IndexError, KeyError, TypeError):
            continue
        text = str(name or "").strip()
        if text:
            names.append(text)
    return tuple(dict.fromkeys(names)) or ("main",)


def _quote_sql_identifier(identifier: str) -> str:
    if not identifier or not all(ch.isalnum() or ch == "_" for ch in identifier):
        raise ValueError(f"unsafe sqlite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _schema_has_fact_revocations_table(conn: sqlite3.Connection, schema: str) -> bool:
    schema_sql = _quote_sql_identifier(schema)
    row = conn.execute(
        f"SELECT 1 FROM {schema_sql}.sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        ("fact_revocations",),
    ).fetchone()
    return row is not None


def is_fact_revoked(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    row_id: str,
    reason_codes: tuple[str, ...] | None = None,
) -> bool:
    """Return True if (table_name, row_id) is tagged revoked in any schema
    ATTACHed to ``conn`` (optionally filtered to ``reason_codes``).

    Generic read predicate — the DIQ-adjudicated "shared is_fact_revoked
    (owner_domain, table, row_id) API". Iterates every attached schema name
    (a strict superset of any single hardcoded owner schema) and swallows
    per-schema sqlite3.Error defensively (a locked or missing companion DB
    must not abort a caller's gate check).

    INV-37: caller supplies conn; never auto-opens/attaches.
    """
    row_id = str(row_id or "").strip()
    if not row_id:
        return False
    if reason_codes:
        placeholders = ",".join("?" for _ in reason_codes)
        reason_clause = f" AND reason_code IN ({placeholders})"
        reason_params: tuple = tuple(reason_codes)
    else:
        reason_clause = ""
        reason_params = ()
    for schema in _attached_schema_names(conn):
        try:
            if not _schema_has_fact_revocations_table(conn, schema):
                continue
            schema_sql = _quote_sql_identifier(schema)
            row = conn.execute(
                f"""
                SELECT 1
                  FROM {schema_sql}.fact_revocations
                 WHERE table_name = ?
                   AND row_id = ?
                   {reason_clause}
                 LIMIT 1
                """,
                (table_name, row_id, *reason_params),
            ).fetchone()
        except sqlite3.Error:
            continue
        if row is not None:
            return True
    return False


def is_certificate_revoked(conn: sqlite3.Connection, certificate_hash: str) -> bool:
    """Return True if ``certificate_hash`` is tagged invalid-live in fact_revocations.

    Drop-in replacement for the predecessor's
    ``decision_certificate_is_quarantined`` (excision T-consolidations #1
    consolidated executor.py:1384 and command_recovery.py:3042 into one
    shared implementation; this DIQ packet re-implements that shared
    implementation as a fact-revocation lookup). Semantics unchanged: union
    over every attached schema, optional per-schema errors swallowed.
    """
    return is_fact_revoked(
        conn,
        table_name=DECISION_CERTIFICATES_TABLE,
        row_id=certificate_hash,
        reason_codes=(REASON_INVALID_LIVE_ACTIONABLE, REASON_INVALID_LIVE_PARENT_MODE),
    )


def revoke_fact(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    row_id: str,
    reason_code: str,
    meta: dict | None = None,
    forecast_snapshot_id: str | None = None,
    target_schema: str = "main",
    recorded_at: str | None = None,
) -> bool:
    """Generic single-row owner-local revocation writer.

    Returns True iff a new row was inserted (False when already revoked for
    this exact (table_name, row_id, reason_code) — idempotent via INSERT OR
    IGNORE on the UNIQUE constraint).

    INV-37: caller supplies conn; never auto-opens.
    """
    ref = _revocations_ref(conn, target_schema)
    at = recorded_at or datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            table_name,
            str(row_id),
            reason_code,
            forecast_snapshot_id,
            at,
            json.dumps(meta or {}, sort_keys=True),
        ),
    )
    return cur.rowcount == 1


def _revoke_table_via_snapshot(
    conn: sqlite3.Connection,
    *,
    target_table: str,
    find_sql: str,
    dry_run: bool,
    target_schema: str = "main",
) -> dict:
    """Generic revocation writer: execute find_sql, tag qualifying rows.

    find_sql must SELECT (row_id TEXT, snapshot_id INTEGER|TEXT, source_run_id TEXT|NULL).
    Caller builds find_sql; this function handles INSERT OR IGNORE + counting.

    INV-37: caller supplies conn; never auto-opens.
    """
    recorded_at = datetime.now(timezone.utc).isoformat()
    ref = _revocations_ref(conn, target_schema)

    try:
        candidates = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"revocation query for {target_table} failed — "
            f"ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
        )
        logger.error(msg)
        return {
            "candidates_found": 0,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(candidates)
    logger.info(
        "revoke %s: found %d candidate rows with non-contributing snapshot",
        target_table,
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (target_table, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    insert_sql = f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    rows_to_insert = []
    for row in candidates:
        row_id = str(row[0])
        snapshot_id = str(row[1]) if row[1] is not None else None
        source_run_id = row[2] if len(row) > 2 else None
        meta: dict = {"source": f"revoke_{target_table}_for_noncontributing_forecast"}
        if source_run_id is not None:
            meta["source_run_id"] = source_run_id
        rows_to_insert.append(
            (target_table, row_id, REASON_NON_CONTRIBUTING, snapshot_id, recorded_at, json.dumps(meta))
        )
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name=? AND reason_code=?",
        (target_table, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    newly_revoked = post_count - pre_count
    already_revoked = candidates_found - newly_revoked

    logger.info(
        "revoke %s: newly=%d already=%d total_after=%d",
        target_table,
        newly_revoked,
        already_revoked,
        post_count,
    )
    return {
        "candidates_found": candidates_found,
        "already_revoked": already_revoked,
        "newly_revoked": newly_revoked,
        "dry_run": False,
    }


def _snap_ref(conn: sqlite3.Connection) -> str:
    """Return qualified or unqualified ensemble_snapshots reference."""
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    return "forecasts.ensemble_snapshots" if "forecasts" in attached else "ensemble_snapshots"


# ---------------------------------------------------------------------------
# Per-table revocation entry points
# ---------------------------------------------------------------------------

def revoke_calibration_pairs_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag calibration_pairs rows whose forecast snapshot is non-contributing.

    calibration_pairs is forecasts-owned (src/state/domains.py); in
    production ``conn`` is a forecasts connection, so the owner-local
    revocation write targets "main" (default) — no cross-DB ATTACH needed.

    row_id = str(pair_id)  (INTEGER PK, stored as TEXT in fact_revocations).
    forecast_snapshot_id = str(snapshot_id).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            CAST(cp2.pair_id AS TEXT) AS row_id,
            cp2.snapshot_id           AS snapshot_id,
            esv.source_run_id         AS source_run_id
        FROM calibration_pairs cp2
        JOIN {snap_ref} esv ON cp2.snapshot_id = esv.snapshot_id
        WHERE cp2.snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY cp2.pair_id
    """
    return _revoke_table_via_snapshot(
        conn,
        target_table="calibration_pairs",
        find_sql=find_sql,
        dry_run=dry_run,
        target_schema=target_schema,
    )


def revoke_probability_trace_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag probability_trace_fact rows whose forecast snapshot is non-contributing.

    probability_trace_fact is world-owned; owner-local write targets "main"
    (default) on a world-main connection.

    probability_trace_fact.decision_snapshot_id is TEXT; cast to INTEGER for join.
    row_id = ptf.trace_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            ptf.trace_id      AS row_id,
            ptf.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM probability_trace_fact ptf
        JOIN {snap_ref} esv
          ON CAST(ptf.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE ptf.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY ptf.trace_id
    """
    return _revoke_table_via_snapshot(
        conn,
        target_table="probability_trace_fact",
        find_sql=find_sql,
        dry_run=dry_run,
        target_schema=target_schema,
    )


def revoke_selection_family_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag selection_family_fact rows whose forecast snapshot is non-contributing.

    selection_family_fact is world-owned; owner-local write targets "main"
    (default) on a world-main connection.

    selection_family_fact.decision_snapshot_id is TEXT; cast to INTEGER for join.
    row_id = sff.family_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            sff.family_id     AS row_id,
            sff.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM selection_family_fact sff
        JOIN {snap_ref} esv
          ON CAST(sff.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE sff.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY sff.family_id
    """
    return _revoke_table_via_snapshot(
        conn,
        target_table="selection_family_fact",
        find_sql=find_sql,
        dry_run=dry_run,
        target_schema=target_schema,
    )


def revoke_selection_hypothesis_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag selection_hypothesis_fact rows whose backing family has a non-contributing snapshot.

    selection_hypothesis_fact is world-owned; owner-local write targets
    "main" (default) on a world-main connection.

    Joins: selection_hypothesis_fact → selection_family_fact → ensemble_snapshots.
    row_id = shf.hypothesis_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            shf.hypothesis_id AS row_id,
            sff.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM selection_hypothesis_fact shf
        JOIN selection_family_fact sff ON shf.family_id = sff.family_id
        JOIN {snap_ref} esv
          ON CAST(sff.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE sff.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY shf.hypothesis_id
    """
    return _revoke_table_via_snapshot(
        conn,
        target_table="selection_hypothesis_fact",
        find_sql=find_sql,
        dry_run=dry_run,
        target_schema=target_schema,
    )


def _de_natural_pk_hash(
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
    decision_seq: int,
) -> str:
    """Return a deterministic hex-digest row_id for a decision_events row.

    decision_event_id is only an INDEX (not UNIQUE) and may be the sentinel
    'deid_v1_BACKSTOP_NULL_WRITER_BYPASS' for multiple rows. The natural PK
    (market_slug, temperature_metric, target_date, observation_time, decision_seq)
    is the true uniqueness anchor for decision_events rows.
    """
    key = f"{market_slug}|{temperature_metric}|{target_date}|{observation_time}|{decision_seq}"
    # [:32] = 128-bit hex prefix of SHA-256. Collision-safe for this key space
    # (five structured fields with bounded cardinality). Not the full 256-bit digest.
    return "de_pk_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def revoke_decision_events_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target_schema: str = "main",
) -> dict:
    """Tag decision_events rows whose backing opportunity_fact snapshot is non-contributing.

    decision_events is world-owned; owner-local write targets "main"
    (default) on a world-main connection.

    Joins: decision_events → opportunity_fact (via decision_event_id = decision_id)
           → ensemble_snapshots.

    row_id = _de_natural_pk_hash(market_slug, temperature_metric, target_date,
                                  observation_time, decision_seq)
    Using the 5-col natural PK hash avoids the BACKSTOP sentinel collision
    (decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS' repeats across rows).

    Only rows with a non-NULL, non-BACKSTOP decision_event_id are linked to
    opportunity_fact and tagged; pure-BACKSTOP rows (no decision_id in
    opportunity_fact) are skipped — they have no forecast linkage to verify.

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            de.market_slug       AS market_slug,
            de.temperature_metric AS temperature_metric,
            de.target_date       AS target_date,
            de.observation_time  AS observation_time,
            de.decision_seq      AS decision_seq,
            of.snapshot_id       AS snapshot_id,
            esv.source_run_id    AS source_run_id
        FROM decision_events de
        JOIN opportunity_fact of ON de.decision_event_id = of.decision_id
        JOIN {snap_ref} esv
          ON CAST(of.snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE de.decision_event_id IS NOT NULL
          AND de.decision_event_id != 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
          AND of.snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY de.market_slug, de.temperature_metric, de.target_date,
                 de.observation_time, de.decision_seq
    """

    recorded_at = datetime.now(timezone.utc).isoformat()
    ref = _revocations_ref(conn, target_schema)

    try:
        raw_rows = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"revocation query for decision_events failed — "
            f"ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
        )
        logger.error(msg)
        return {
            "candidates_found": 0,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(raw_rows)
    logger.info(
        "revoke decision_events: found %d candidate rows with non-contributing snapshot",
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_revoked": 0,
            "newly_revoked": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name='decision_events' AND reason_code=?",
        (REASON_NON_CONTRIBUTING,),
    ).fetchone()[0]

    insert_sql = f"""
        INSERT OR IGNORE INTO {ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES ('decision_events', ?, ?, ?, ?, ?)
    """
    rows_to_insert = []
    for row in raw_rows:
        market_slug, temperature_metric, target_date, observation_time, decision_seq = (
            row[0], row[1], row[2], row[3], row[4]
        )
        snapshot_id = str(row[5]) if row[5] is not None else None
        source_run_id = row[6]
        row_id = _de_natural_pk_hash(
            market_slug, temperature_metric, target_date, observation_time, decision_seq
        )
        meta: dict = {
            "source": "revoke_decision_events_for_noncontributing_forecast",
            "natural_pk": {
                "market_slug": market_slug,
                "temperature_metric": temperature_metric,
                "target_date": target_date,
                "observation_time": observation_time,
                "decision_seq": int(decision_seq),
            },
        }
        if source_run_id is not None:
            meta["source_run_id"] = source_run_id
        rows_to_insert.append(
            (row_id, REASON_NON_CONTRIBUTING, snapshot_id, recorded_at, json.dumps(meta))
        )
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {ref} WHERE table_name='decision_events' AND reason_code=?",
        (REASON_NON_CONTRIBUTING,),
    ).fetchone()[0]

    newly_revoked = post_count - pre_count
    already_revoked = candidates_found - newly_revoked

    logger.info(
        "revoke decision_events: newly=%d already=%d total_after=%d",
        newly_revoked,
        already_revoked,
        post_count,
    )
    return {
        "candidates_found": candidates_found,
        "already_revoked": already_revoked,
        "newly_revoked": newly_revoked,
        "dry_run": False,
    }


def revoke_all_tables_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Run revocation across all supported tables on a SINGLE connection.

    This convenience wrapper requires conn to have BOTH 'forecasts' and 'trade'
    ATTACHed (or be an in-memory test DB with all tables co-located); it
    always writes to "main" (co-located test/one-shot usage) — production
    per-DB fan-out uses the per-table functions directly with an explicit
    target_schema (see scripts/revoke_bad_forecast_decisions.py).

    Raises ValueError if 'forecasts' is not attached/present (calibration_pairs
    cannot be revoked without it and would silently no-op).

    INV-37: caller supplies conn; never auto-opens.
    """
    try:
        conn.execute("SELECT 1 FROM ensemble_snapshots LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute("SELECT 1 FROM forecasts.ensemble_snapshots LIMIT 0")
        except sqlite3.OperationalError:
            raise ValueError(
                "revoke_all_tables_for_noncontributing_forecast: "
                "ensemble_snapshots not found — ensure the forecasts DB is "
                "ATTACHed as 'forecasts' OR all tables are co-located (in-memory test)."
            )
    fn_table_pairs = [
        (revoke_decisions_for_noncontributing_forecast, "opportunity_fact"),
        (revoke_calibration_pairs_for_noncontributing_forecast, "calibration_pairs"),
        (revoke_probability_trace_fact_for_noncontributing_forecast, "probability_trace_fact"),
        (revoke_selection_family_fact_for_noncontributing_forecast, "selection_family_fact"),
        (revoke_selection_hypothesis_fact_for_noncontributing_forecast, "selection_hypothesis_fact"),
        (revoke_decision_events_for_noncontributing_forecast, "decision_events"),
    ]
    aggregate: dict = {
        "candidates_found": 0,
        "already_revoked": 0,
        "newly_revoked": 0,
        "dry_run": dry_run,
        "per_table": {},
    }
    for fn, tname in fn_table_pairs:
        result = fn(conn, dry_run=dry_run)
        aggregate["per_table"][tname] = result
        aggregate["candidates_found"] += result.get("candidates_found", 0)
        aggregate["already_revoked"] += result.get("already_revoked", 0)
        aggregate["newly_revoked"] += result.get("newly_revoked", 0)
    return aggregate
