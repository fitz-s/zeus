# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Authority basis: docs/operations/task_2026-05-17_f109_fix/TRACE.md
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
from typing import Iterable

logger = logging.getLogger(__name__)

_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit", "unknown")
_PHASE_LIST_SQL = ", ".join(f"'{p}'" for p in _OPEN_PHASES)
_VOIDED_REASON = "duplicate_consolidated_2026_05_17_f109"
_MICRO_PER_SHARE = 1_000_000  # ctf_token_balances_json is in micro-units


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
        f"""
        SELECT token_id
          FROM position_current
         WHERE phase IN ({_PHASE_LIST_SQL}) AND token_id IS NOT NULL
         GROUP BY token_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    out: list[tuple[str, list[tuple[str, float, str]]]] = []
    for (token_id,) in token_rows:
        rows = conn.execute(
            f"""
            SELECT pc.position_id, pc.shares,
                   (SELECT MIN(occurred_at) FROM position_events pe
                     WHERE pe.position_id = pc.position_id) AS first_at
              FROM position_current pc
             WHERE pc.token_id = ? AND pc.phase IN ({_PHASE_LIST_SQL})
            """,
            (str(token_id),),
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
    chain_by_token = _load_chain_shares_by_token(conn)
    chain_snapshot_used = bool(chain_by_token)
    duplicates = _enumerate_duplicates(conn)
    report = {
        "scanned_tokens": len(duplicates),
        "overbook_tokens": [],
        "divergent_tokens": [],
        "voided_positions": [],
        "chain_snapshot_used": chain_snapshot_used,
    }
    if not duplicates:
        return report

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
            excess = db_sum - chain_shares
            voided_here: list[str] = []
            for position_id, shares, _first_at in triples:
                if excess <= 1e-9:
                    break
                _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
                voided_here.append(position_id)
                excess -= shares
                logger.warning(
                    "[CONSOLIDATOR_OVERBOOK_VOID] token=%s voided=%s "
                    "shares=%.6f remaining_excess=%.6f",
                    token_id,
                    position_id,
                    shares,
                    excess,
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
    chain_by_token = _load_chain_shares_by_token(conn)
    duplicates = [(t, rows) for t, rows in _enumerate_duplicates(conn) if t == str(token_id)]
    report = {
        "scanned_tokens": len(duplicates),
        "overbook_tokens": [],
        "divergent_tokens": [],
        "voided_positions": [],
        "chain_snapshot_used": bool(chain_by_token),
    }
    if not duplicates:
        return report

    sp = f"sp_f109_token_{secrets.token_hex(6)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        triples = duplicates[0][1]
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
            report["divergent_tokens"].append(str(token_id))
        else:
            excess = db_sum - chain_shares
            for position_id, shares, _first_at in triples:
                if excess <= 1e-9:
                    break
                _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
                report["voided_positions"].append(position_id)
                excess -= shares
            report["overbook_tokens"].append(str(token_id))
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    return report
