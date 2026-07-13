# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Authority basis: docs/archive/2026-Q2/task_2026-05-17_f109_fix/TRACE.md
#                  + feedback_no_manual_precedent_for_any_structural_defect
#
# Purpose: Programmatic, autonomous consolidation of position_current rows that
# violate the F109 invariant "at most one open-phase row per token_id". This
# module is the REPLAY half of the structural fix; the WRITER half lives in
# src/state/projection.py + the partial UNIQUE INDEX added in
# scripts/migrations/202605_position_current_idempotent_open_per_token.py.
#
# This module is the ONLY supported path to reduce duplicate-open rows. There
# is no operator-CLI equivalent; the structural defect must self-heal at boot
# or it has not actually been fixed (per directive
# feedback_no_manual_precedent_for_any_structural_defect).
#
# Classification (chain-truth-aware, per advisor note 2026-05-17):
#   For each token holding >1 open-phase rows:
#     1. Read on-chain shares from collateral_ledger_snapshots latest CHAIN row.
#     2. Sum DB shares across the open-phase rows.
#     3. If db_sum <= chain_shares  → DIVERGENT (legitimately split exposure):
#          skip + log [CONSOLIDATOR_DIVERGENT].
#        If db_sum >  chain_shares  → OVERBOOK:
#          determine excess = db_sum - chain_shares; void the OLDEST rows
#          (by min position_events.occurred_at ascending) until db_sum
#          collapses to chain_shares. The youngest active row owns the
#          on-chain exposure going forward.
#
# Why oldest-first void on OVERBOOK: in the observed F109 case (London 5/19),
# the older position completed its EXIT cycle on-chain but the EXIT command
# stuck in MATCHED (not CONFIRMED) state in DB, so the DB phase remained
# pending_exit. The newer position then opened a fresh row representing the
# re-entry. Chain truth: only the newer position holds real shares.
#
# No exception is silently swallowed. All exits are explicit, logged, and
# leave a position_events VOIDED row for audit.
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import uuid

logger = logging.getLogger(__name__)

_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit", "unknown")
_VOIDED_REASON = "duplicate_consolidated_2026_05_17_f109"
_MERGED_REASON = "duplicate_open_rows_merged_same_identity_2026_06_17"
_MICRO_PER_SHARE = 1_000_000  # ctf_token_balances_json is in micro-units
_MERGE_IDENTITY_COLUMNS = (
    "phase",
    "market_id",
    "city",
    "target_date",
    "bin_label",
    "direction",
    "unit",
    "strategy_key",
    "condition_id",
    "temperature_metric",
)


def _load_chain_shares_by_token(conn: sqlite3.Connection) -> dict[str, float]:
    """Return latest authoritative on-chain CTF balances keyed by token_id.

    Returns {} if no CHAIN-tier snapshot exists. The consolidator does NOT
    auto-void in that case — DIVERGENT-skip is the conservative default.
    """
    row = conn.execute(
        """
        SELECT ctf_token_balances_json
          FROM collateral_ledger_snapshots
         WHERE authority_tier = 'CHAIN'
         ORDER BY captured_at DESC, id DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None or not row[0]:
        return {}
    try:
        raw = json.loads(row[0])
    except json.JSONDecodeError:
        logger.error("[CONSOLIDATOR] ctf_token_balances_json unparseable")
        return {}
    out: dict[str, float] = {}
    for token, micro in raw.items():
        try:
            out[str(token)] = int(micro) / _MICRO_PER_SHARE
        except (TypeError, ValueError):
            continue
    return out


def _enumerate_duplicates(
    conn: sqlite3.Connection,
) -> list[tuple[str, list[tuple[str, float, str]]]]:
    """Return [(token_id, [(position_id, shares, first_event_iso), ...]), ...].

    Only tokens with >1 open-phase rows are returned. Per-row list is sorted
    by first-event occurred_at ASCENDING (oldest first).
    """
    token_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(token_id, ''), NULLIF(no_token_id, '')) AS exposure_token
          FROM position_current
         WHERE phase IN (?, ?, ?, ?, ?)
           AND COALESCE(NULLIF(token_id, ''), NULLIF(no_token_id, '')) IS NOT NULL
         GROUP BY exposure_token
        HAVING COUNT(*) > 1
        """,
        _OPEN_PHASES,
    ).fetchall()
    out: list[tuple[str, list[tuple[str, float, str]]]] = []
    for (token_id,) in token_rows:
        rows = conn.execute(
            """
            SELECT pc.position_id, pc.shares,
                   (SELECT MIN(occurred_at) FROM position_events pe
                     WHERE pe.position_id = pc.position_id) AS first_at
              FROM position_current pc
             WHERE (pc.token_id = ? OR pc.no_token_id = ?)
               AND pc.phase IN (?, ?, ?, ?, ?)
            """,
            (str(token_id), str(token_id), *_OPEN_PHASES),
        ).fetchall()
        triples = [
            (str(r[0]), float(r[1] or 0.0), str(r[2] or "9999"))
            for r in rows
        ]
        triples.sort(key=lambda t: t[2])  # oldest first
        out.append((str(token_id), triples))
    return out


def _void_row(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    reason: str,
) -> None:
    """Atomically void a single position_current row + append audit event.

    Called inside the caller's SAVEPOINT; raises propagate.
    """
    now_row = conn.execute(
        "SELECT phase, strategy_key FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if now_row is None:
        raise RuntimeError(f"void target {position_id} not found")
    phase_before, strategy_key = str(now_row[0]), str(now_row[1])

    seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    next_seq = int(seq_row[0]) + 1 if seq_row else 1

    from datetime import datetime, timezone

    iso_now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"reason": reason, "consolidator_run_id": secrets.token_hex(6)})
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'ADMIN_VOIDED', ?, ?, 'voided', ?,
                  'src.state.position_duplicate_consolidator', ?, 'live')
        """,
        (
            str(uuid.uuid4()),
            position_id,
            next_seq,
            iso_now,
            phase_before,
            strategy_key,
            payload,
        ),
    )

    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               shares = 0.0,
               cost_basis_usd = 0.0,
               updated_at = ?
         WHERE position_id = ?
        """,
        (iso_now, position_id),
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _row_dicts_for_token(conn: sqlite3.Connection, token_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT pc.*,
               (SELECT MIN(occurred_at) FROM position_events pe
                 WHERE pe.position_id = pc.position_id) AS first_at
          FROM position_current pc
         WHERE (pc.token_id = ? OR pc.no_token_id = ?)
           AND pc.phase IN (?, ?, ?, ?, ?)
        """,
        (str(token_id), str(token_id), *_OPEN_PHASES),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _norm_identity(value) -> str:
    return str(value or "").strip()


def _merge_identity_key(row: dict) -> tuple[str, ...] | None:
    values = tuple(_norm_identity(row.get(col)) for col in _MERGE_IDENTITY_COLUMNS)
    # condition_id, direction, strategy, metric, date, and phase are load-bearing
    # for CTF operations and strategy attribution. Missing any of them means the
    # rows are not safely mergeable.
    required_indexes = (0, 3, 5, 7, 8, 9)
    if any(not values[i] for i in required_indexes):
        return None
    return values


def _position_sort_key(row: dict) -> tuple[str, str, str]:
    return (
        _norm_identity(row.get("first_at")) or "9999",
        _norm_identity(row.get("updated_at")) or "",
        _norm_identity(row.get("position_id")),
    )


def _float_or_none(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_cost_basis(row: dict) -> float:
    shares = _float_or_none(row.get("shares")) or 0.0
    entry_price = _float_or_none(row.get("entry_price"))
    if entry_price is not None and 0.0 < entry_price < 1.0:
        return shares * entry_price
    return _float_or_none(row.get("cost_basis_usd")) or 0.0


def _chain_observed_cost_basis(rows: list[dict], chain_shares: float) -> float | None:
    best_cost: float | None = None
    for row in rows:
        row_chain_shares = _float_or_none(row.get("chain_shares")) or 0.0
        if row_chain_shares + 1e-9 < chain_shares:
            continue
        row_chain_cost = _float_or_none(row.get("chain_cost_basis_usd"))
        if row_chain_cost is not None and row_chain_cost > 0.0:
            best_cost = max(best_cost or 0.0, row_chain_cost)
    return best_cost


def _merge_equivalent_rows(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    rows: list[dict],
    chain_shares: float,
) -> tuple[str, list[str]] | None:
    if len(rows) <= 1:
        return None
    identity_keys = {_merge_identity_key(row) for row in rows}
    if len(identity_keys) != 1 or None in identity_keys:
        return None

    sorted_rows = sorted(rows, key=_position_sort_key)
    keeper = sorted_rows[-1]
    absorbed = sorted_rows[:-1]
    keeper_id = _norm_identity(keeper.get("position_id"))
    absorbed_ids = [_norm_identity(row.get("position_id")) for row in absorbed]
    if not keeper_id or any(not pid for pid in absorbed_ids):
        return None

    db_total_shares = sum((_float_or_none(row.get("shares")) or 0.0) for row in rows)
    if db_total_shares <= 0.0 or db_total_shares > float(chain_shares) + 1e-9:
        return None
    target_shares = float(chain_shares)
    db_total_cost = sum(_row_cost_basis(row) for row in rows)
    chain_cost = _chain_observed_cost_basis(rows, target_shares)
    if chain_cost is not None:
        target_cost = chain_cost
    elif db_total_shares > 0.0:
        target_cost = (db_total_cost / db_total_shares) * target_shares
    else:
        target_cost = db_total_cost
    avg_entry = target_cost / target_shares if target_shares > 0 else None

    for position_id in absorbed_ids:
        _void_row(conn, position_id=position_id, reason=_MERGED_REASON)

    now_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (keeper_id,),
    ).fetchone()
    next_seq = int(now_row[0]) + 1 if now_row else 1

    from datetime import datetime, timezone

    iso_now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {
            "reason": _MERGED_REASON,
            "absorbed_position_ids": absorbed_ids,
            "token_id": token_id,
            "shares_before": [(_norm_identity(r.get("position_id")), _float_or_none(r.get("shares")) or 0.0) for r in rows],
            "db_total_shares": db_total_shares,
            "chain_shares": target_shares,
            "shares_after": target_shares,
            "db_total_cost_basis_usd": db_total_cost,
            "cost_basis_usd_after": target_cost,
        },
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MANUAL_OVERRIDE_APPLIED', ?, ?, ?, ?,
                  'src.state.position_duplicate_consolidator', ?, 'live')
        """,
        (
            str(uuid.uuid4()),
            keeper_id,
            next_seq,
            iso_now,
            _norm_identity(keeper.get("phase")),
            _norm_identity(keeper.get("phase")),
            _norm_identity(keeper.get("strategy_key")),
            payload,
        ),
    )

    columns = _table_columns(conn, "position_current")
    updates: dict[str, object] = {
        "shares": target_shares,
        "cost_basis_usd": target_cost,
        "size_usd": target_cost,
        "chain_shares": target_shares,
        "updated_at": iso_now,
    }
    if avg_entry is not None:
        updates["entry_price"] = avg_entry
    assignments = []
    values: list[object] = []
    for col, value in updates.items():
        if col in columns:
            assignments.append(f"{col} = ?")
            values.append(value)
    if assignments:
        values.append(keeper_id)
        conn.execute(
            f"UPDATE position_current SET {', '.join(assignments)} WHERE position_id = ?",
            values,
        )
    logger.warning(
        "[CONSOLIDATOR_MERGED_EQUIVALENT] token=%s keeper=%s absorbed=%s shares=%.6f chain=%.6f",
        token_id,
        keeper_id,
        absorbed_ids,
        target_shares,
        chain_shares,
    )
    return keeper_id, absorbed_ids


def consolidate(conn: sqlite3.Connection) -> dict:
    """Run one pass of F109 consolidation; idempotent.

    Returns a structured report:
        {
          "scanned_tokens": int,
          "overbook_tokens": [token_id, ...],
          "divergent_tokens": [token_id, ...],
          "voided_positions": [position_id, ...],
          "chain_snapshot_used": bool,
        }

    No-op on healthy state (no token has >1 open-phase rows). Raises on
    internal inconsistency (target row vanished mid-consolidation).
    """
    duplicates = _enumerate_duplicates(conn)
    report = {
        "scanned_tokens": len(duplicates),
        "overbook_tokens": [],
        "divergent_tokens": [],
        "voided_positions": [],
        "merged_tokens": [],
        "merged_positions": [],
        "chain_snapshot_used": False,
    }
    if not duplicates:
        return report

    chain_by_token = _load_chain_shares_by_token(conn)
    chain_snapshot_used = bool(chain_by_token)
    report["chain_snapshot_used"] = chain_snapshot_used

    sp = f"sp_f109_consolidate_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        for token_id, triples in duplicates:
            db_sum = sum(t[1] for t in triples)
            # MAJ-2 fix (2026-05-17 phase critic): token-absence in the chain
            # snapshot must NOT default to chain=0.0 — that conflates "token
            # has 0 shares on chain" with "token not measured in this snapshot"
            # and could classify DIVERGENT cases as OVERBOOK. Per the spec
            # (TRACE.md §4): chain-snapshot stale → SKIP.
            if token_id not in chain_by_token:
                report["divergent_tokens"].append(token_id)
                logger.error(
                    "[CONSOLIDATOR_DIVERGENT] token=%s reason=token_not_in_snapshot "
                    "db_sum=%.6f rows=%d",
                    token_id,
                    db_sum,
                    len(triples),
                )
                continue
            chain_shares = float(chain_by_token[token_id])
            if not chain_snapshot_used:
                # Conservative: without chain truth we cannot safely void.
                report["divergent_tokens"].append(token_id)
                logger.error(
                    "[CONSOLIDATOR_DIVERGENT] token=%s reason=no_chain_snapshot "
                    "db_sum=%.6f rows=%d",
                    token_id,
                    db_sum,
                    len(triples),
                )
                continue
            if db_sum <= chain_shares + 1e-9:
                merge_result = _merge_equivalent_rows(
                    conn,
                    token_id=token_id,
                    rows=_row_dicts_for_token(conn, token_id),
                    chain_shares=chain_shares,
                )
                if merge_result is not None:
                    _keeper_id, absorbed_ids = merge_result
                    report["merged_tokens"].append(token_id)
                    report["merged_positions"].extend(absorbed_ids)
                    report["voided_positions"].extend(absorbed_ids)
                    continue
                report["divergent_tokens"].append(token_id)
                logger.warning(
                    "[CONSOLIDATOR_DIVERGENT] token=%s db_sum=%.6f chain=%.6f "
                    "rows=%d (legitimate split; operator review)",
                    token_id,
                    db_sum,
                    chain_shares,
                    len(triples),
                )
                continue
            # OVERBOOK: void oldest rows until db_sum collapses to chain_shares.
            # MAJ-1 fix (2026-05-17 PR critic): only safe when a prefix of oldest
            # rows has cumulative shares == excess (within tol). Otherwise voiding
            # any prefix either under-shoots (leaves excess) or over-shoots (loses
            # chain authority). Counter-example: shares=[3,5], chain=4, excess=4 →
            # void 3 leaves excess=1; void 3+5 over-shoots to -4 (loses 4 chain
            # shares from DB authority). Conservative fail-closed: classify
            # DIVERGENT, void nothing, log loudly for operator triage. Matches
            # feedback_no_manual_precedent_for_any_structural_defect (the
            # consolidator cannot guess which row owns chain truth on asymmetric
            # overbook; that's an operator-mediated reconciliation decision).
            excess = db_sum - chain_shares
            cum = 0.0
            clean_prefix_len = -1
            for i, (_pid_p, shares_p, _first_p) in enumerate(triples):
                cum += shares_p
                if abs(cum - excess) <= 1e-9:
                    clean_prefix_len = i + 1
                    break
                if cum > excess + 1e-9:
                    break  # this row over-shoots; no longer prefix can recover
            if clean_prefix_len < 0:
                report["divergent_tokens"].append(token_id)
                logger.error(
                    "[CONSOLIDATOR_DIVERGENT_ASYMMETRIC] token=%s db_sum=%.6f "
                    "chain=%.6f excess=%.6f rows=%s "
                    "(no clean void prefix; operator triage required)",
                    token_id, db_sum, chain_shares, excess,
                    [(p, s) for p, s, _ in triples],
                )
                continue
            voided_here: list[str] = []
            for position_id, shares, _first_at in triples[:clean_prefix_len]:
                _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
                voided_here.append(position_id)
                logger.warning(
                    "[CONSOLIDATOR_OVERBOOK_VOID] token=%s voided=%s shares=%.6f",
                    token_id, position_id, shares,
                )
            report["overbook_tokens"].append(token_id)
            report["voided_positions"].extend(voided_here)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    return report


def consolidate_token(conn: sqlite3.Connection, token_id: str) -> dict:
    """Single-token variant for invocation from update_trade_lifecycle.

    Intended call site: just-in-time consolidation when a lifecycle event
    references a token that still holds duplicates. Returns the same shape
    as consolidate() but scoped to one token.
    """
    rows = _row_dicts_for_token(conn, str(token_id))
    triples = [
        (
            _norm_identity(row.get("position_id")),
            _float_or_none(row.get("shares")) or 0.0,
            _norm_identity(row.get("first_at")) or "9999",
        )
        for row in rows
    ]
    triples = [triple for triple in triples if triple[0]]
    triples.sort(key=lambda t: t[2])
    duplicates = [(str(token_id), triples)] if len(triples) > 1 else []
    report = {
        "scanned_tokens": len(duplicates),
        "overbook_tokens": [],
        "divergent_tokens": [],
        "voided_positions": [],
        "merged_tokens": [],
        "merged_positions": [],
        "chain_snapshot_used": False,
    }
    if not duplicates:
        return report

    chain_by_token = _load_chain_shares_by_token(conn)
    report["chain_snapshot_used"] = bool(chain_by_token)

    sp = f"sp_f109_token_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        db_sum = sum(t[1] for t in triples)
        # MAJ-2 fix (2026-05-17 phase critic): token-absence != chain=0.
        # Detect and classify DIVERGENT, do NOT default to overbook.
        if str(token_id) not in chain_by_token:
            report["divergent_tokens"].append(str(token_id))
            logger.error(
                "[CONSOLIDATOR_DIVERGENT] token=%s reason=token_not_in_snapshot "
                "db_sum=%.6f rows=%d (consolidate_token)",
                token_id, db_sum, len(triples),
            )
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            return report
        chain_shares = float(chain_by_token[str(token_id)])
        if not chain_by_token or db_sum <= chain_shares + 1e-9:
            merge_result = _merge_equivalent_rows(
                conn,
                token_id=str(token_id),
                rows=_row_dicts_for_token(conn, str(token_id)),
                chain_shares=chain_shares,
            )
            if merge_result is not None:
                _keeper_id, absorbed_ids = merge_result
                report["merged_tokens"].append(str(token_id))
                report["merged_positions"].extend(absorbed_ids)
                report["voided_positions"].extend(absorbed_ids)
            else:
                report["divergent_tokens"].append(str(token_id))
        else:
            # MAJ-1 fix (2026-05-17 PR critic): asymmetric-share overbook is
            # fail-closed DIVERGENT — see consolidate() for the counter-example
            # and rationale. Same guard mirrored here for the single-token API.
            excess = db_sum - chain_shares
            cum = 0.0
            clean_prefix_len = -1
            for i, (_pid_p, shares_p, _first_p) in enumerate(triples):
                cum += shares_p
                if abs(cum - excess) <= 1e-9:
                    clean_prefix_len = i + 1
                    break
                if cum > excess + 1e-9:
                    break
            if clean_prefix_len < 0:
                report["divergent_tokens"].append(str(token_id))
                logger.error(
                    "[CONSOLIDATOR_DIVERGENT_ASYMMETRIC] token=%s db_sum=%.6f "
                    "chain=%.6f excess=%.6f rows=%s "
                    "(no clean void prefix; consolidate_token; operator triage)",
                    token_id, db_sum, chain_shares, excess,
                    [(p, s) for p, s, _ in triples],
                )
            else:
                for position_id, _shares, _first_at in triples[:clean_prefix_len]:
                    _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
                    report["voided_positions"].append(position_id)
                report["overbook_tokens"].append(str(token_id))
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    return report
