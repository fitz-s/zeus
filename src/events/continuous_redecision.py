# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md (v2, opus-critic-resolved) +
#   GOAL #36 expanded (continuous entry+exit, evidence-gated). Implements P1 (belief cache) + P2
#   (cheap screen + enqueue) + exit screen (SD6/SD7) for continuous re-decision.
#
# DAEMON-SAFE BACKING (critical): assert_db_matches_registry (table_registry.py:285) is STRICT
# set-equality — an extra on-disk table not in the ownership registry is a FATAL boot crash. So this
# module adds NO new table: the belief cache reuses the already-registered probability_trace_fact
# (synthesized 'edli_belief:' decision_id; trace_status='complete'); the act-once-per-edge dedup is
# IN-MEMORY (reactor-held acted_state dict), not a table. SHADOW-safe: never submits an order — only
# screens cached belief × fresh price and returns re-decisions for the reactor to route through the
# EXISTING pending cert path (so _refresh_pending_family_snapshots fires just-in-time → fresh price;
# critic SEV-1 stale-price hole closed structurally).
"""Continuous re-decision: cached belief × fresh price → cheap edge screen → enqueue + evidence exit."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

FEE: float = 0.01  # 1¢ haircut (lambda_edge; event_reactor_adapter.py:3363).
IMPROVE_DELTA: float = 0.02  # edge must improve by this to re-fire (anti price-noise).
REDECISION_EVENT_TYPE: str = "EDLI_REDECISION_PENDING"
_BELIEF_PREFIX: str = "edli_belief:"
_EPS: float = 1e-9


@dataclass(frozen=True)
class PriceQuote:
    price: float
    freshness_deadline: str  # ISO-8601 with offset


@dataclass(frozen=True)
class CachedBelief:
    family_id: str
    city: str
    target_date: str
    snapshot_id: str
    calibrator_model_hash: str
    bin_labels: list[str]
    p_posterior_vec: list[float]
    recorded_at: str


@dataclass(frozen=True)
class EnqueuedRedecision:
    family_id: str
    bin_label: str
    direction: str
    edge: float
    event_type: str = REDECISION_EVENT_TYPE


@dataclass(frozen=True)
class ExitDecision:
    family_id: str
    bin_label: str
    side: str
    entry_posterior: float
    current_posterior: float
    reason: str = "BELIEF_EDGE_REVERSAL"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _belief_decision_id(family_id: str, snapshot_id: str, calibrator_model_hash: str) -> str:
    # family_id is pipe-separated (no ':'); snapshot_id / calib hashes carry no ':'. Encode all three
    # so the read can recover provenance. Parsed via rsplit(':', 2) below.
    return f"{_BELIEF_PREFIX}{family_id}:{snapshot_id}:{calibrator_model_hash}"


def _parse_belief_decision_id(decision_id: str) -> tuple[str, str, str] | None:
    if not decision_id.startswith(_BELIEF_PREFIX):
        return None
    body = decision_id[len(_BELIEF_PREFIX):]
    parts = body.rsplit(":", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]  # (family_id, snapshot_id, calibrator_model_hash)


def ensure_belief_cache_schema(conn: sqlite3.Connection) -> None:
    """Create a MINIMAL probability_trace_fact if absent (unit tests). Live already has the full,
    registered table — CREATE TABLE IF NOT EXISTS is a no-op there (column-shape is subset-checked)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            trace_status TEXT NOT NULL,
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            decision_snapshot_id TEXT,
            bin_labels_json TEXT,
            p_posterior_json TEXT
        )
        """
    )
    conn.commit()


def cache_belief(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    city: str,
    target_date: str,
    snapshot_id: str,
    calibrator_model_hash: str,
    bin_labels: list[str],
    p_posterior_vec: list[float],
    recorded_at: str,
) -> None:
    """Persist the latest COMPLETE belief for a family (P1) into probability_trace_fact. Idempotent
    per (family, snapshot, calibrator) — a newer snapshot writes a new row; the screen reads latest."""
    decision_id = _belief_decision_id(family_id, snapshot_id, calibrator_model_hash)
    conn.execute(
        """
        INSERT INTO probability_trace_fact
            (trace_id, decision_id, trace_status, missing_reason_json, recorded_at,
             city, target_date, decision_snapshot_id, bin_labels_json, p_posterior_json)
        VALUES (?, ?, 'complete', '[]', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id) DO UPDATE SET
            recorded_at=excluded.recorded_at,
            bin_labels_json=excluded.bin_labels_json,
            p_posterior_json=excluded.p_posterior_json
        """,
        (
            "trace_" + decision_id, decision_id, recorded_at,
            city, target_date, snapshot_id,
            json.dumps(list(bin_labels)), json.dumps([float(x) for x in p_posterior_vec]),
        ),
    )
    conn.commit()


def persist_belief_live(
    *,
    family_id: str,
    city: str,
    target_date: str,
    snapshot_id: str,
    calibrator_model_hash: str,
    bin_labels: list[str],
    p_posterior_vec: list[float],
    recorded_at: str,
) -> bool:
    """Live P1 entry point: open the canonical world connection (K1-correct, mirrors
    log_probability_trace_fact) and cache the belief. Best-effort — returns False on any failure and
    NEVER raises, so a cache-write hiccup can never break the live decision path. The caller wraps it
    in its own try/except too (belt and suspenders)."""
    try:
        from src.state.db import get_world_connection
        conn = get_world_connection()
        try:
            cache_belief(
                conn,
                family_id=family_id, city=city, target_date=target_date,
                snapshot_id=snapshot_id, calibrator_model_hash=calibrator_model_hash,
                bin_labels=bin_labels, p_posterior_vec=p_posterior_vec, recorded_at=recorded_at,
            )
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — cache write is non-critical; never break the decision
        return False


def _row_to_belief(row: sqlite3.Row) -> CachedBelief | None:
    parsed = _parse_belief_decision_id(row["decision_id"])
    if parsed is None or not row["p_posterior_json"] or not row["bin_labels_json"]:
        return None
    family_id, snapshot_id, calib = parsed
    return CachedBelief(
        family_id=family_id,
        city=row["city"] or "",
        target_date=row["target_date"] or "",
        snapshot_id=snapshot_id,
        calibrator_model_hash=calib,
        bin_labels=json.loads(row["bin_labels_json"]),
        p_posterior_vec=json.loads(row["p_posterior_json"]),
        recorded_at=row["recorded_at"],
    )


def latest_cached_belief(conn: sqlite3.Connection, *, family_id: str) -> CachedBelief | None:
    for belief in _all_latest_beliefs(conn):
        if belief.family_id == family_id:
            return belief
    return None


def _all_latest_beliefs(conn: sqlite3.Connection) -> list[CachedBelief]:
    rows = conn.execute(
        "SELECT decision_id, recorded_at, city, target_date, bin_labels_json, p_posterior_json "
        "FROM probability_trace_fact WHERE decision_id LIKE ? ORDER BY recorded_at DESC",
        (_BELIEF_PREFIX + "%",),
    ).fetchall()
    seen: set[str] = set()
    out: list[CachedBelief] = []
    for row in rows:
        belief = _row_to_belief(row)
        if belief is None or belief.family_id in seen:
            continue
        seen.add(belief.family_id)
        out.append(belief)
    return out


def enqueue_live_redecisions(
    conn: sqlite3.Connection,
    *,
    decision_time: str,
    price_lookup: dict[tuple[str, str, str], PriceQuote],
    min_edge: float,
    acted_state: dict[tuple[str, str, str], float] | None = None,
) -> list[EnqueuedRedecision]:
    """Cheap-screen every live (family, bin, direction) against a FRESH price; enqueue on edge.

    Stale price (freshness_deadline <= decision_time) is skipped (no phantom edge). acted_state is an
    optional IN-MEMORY dict (the reactor holds it across cycles): a pair re-fires only when its edge
    improves past IMPROVE_DELTA vs the last acted edge — a short price wiggle does NOT re-fire.
    """
    dt = _parse(decision_time)
    out: list[EnqueuedRedecision] = []
    for belief in _all_latest_beliefs(conn):
        for idx, label in enumerate(belief.bin_labels):
            if idx >= len(belief.p_posterior_vec):
                continue
            yes_post = float(belief.p_posterior_vec[idx])
            for direction, posterior in (("buy_yes", yes_post), ("buy_no", 1.0 - yes_post)):
                key = (belief.family_id, label, direction)
                quote = price_lookup.get(key)
                if quote is None:
                    continue
                if _parse(quote.freshness_deadline) <= dt:
                    continue  # STALE → no phantom edge (R7)
                edge = posterior - float(quote.price) - FEE
                if edge < min_edge - _EPS:
                    continue
                if acted_state is not None:
                    last = acted_state.get(key)
                    if last is not None and edge <= last + IMPROVE_DELTA + _EPS:
                        continue  # not materially improved → do not re-fire (anti price-noise)
                    acted_state[key] = edge
                out.append(EnqueuedRedecision(belief.family_id, label, direction, edge))
    return out


def screen_exit(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    bin_label: str,
    side: str,
    entry_posterior: float,
    reversal_belief_delta: float = 0.15,
) -> ExitDecision | None:
    """Evidence-gated exit (SD6/SD7): exit a held position only when the BELIEF moved materially
    against the held side — never on a bare price move (posterior changes only on forecast/obs
    evidence). Returns None (hold) when no belief, no such bin, or no material reversal."""
    belief = latest_cached_belief(conn, family_id=family_id)
    if belief is None:
        return None
    try:
        idx = belief.bin_labels.index(bin_label)
    except ValueError:
        return None
    if idx >= len(belief.p_posterior_vec):
        return None
    yes_post = float(belief.p_posterior_vec[idx])
    current = yes_post if side == "buy_yes" else 1.0 - yes_post
    if current < entry_posterior - reversal_belief_delta - _EPS:
        return ExitDecision(
            family_id=family_id, bin_label=bin_label, side=side,
            entry_posterior=float(entry_posterior), current_posterior=current,
        )
    return None
