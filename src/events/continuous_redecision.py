# Created: 2026-05-31
# Last reused or audited: 2026-06-12
# Authority basis: PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md (v2, opus-critic-resolved) +
#   GOAL #36 expanded (continuous entry+exit, evidence-gated). Implements P1 (belief cache) + P2
#   (cheap screen + enqueue). screen_exit/screen_exit_cancel deleted Wave 3 (zero live callers —
#   exit path is Position.evaluate_exit in src/state/portfolio.py).
#   2026-06-12 RESURRECTION (operator: "continuous redecision没有作用中"): P1 re-enabled
#   DEADLOCK-FREE — the belief is buffered in-process by the kernel (no DB write there) and
#   persisted by the reactor through its EXISTING world conn inside the open SAVEPOINT (NOT a second
#   connection, NOT a separate commit). P2 screen wired to a scheduler job + the reactor now CONSUMES
#   EDLI_REDECISION_PENDING. §4.5 resting-order management wired (belief-decay / stale-quote / moved-book
#   pulls reuse the maker_rest_escalation cancel machinery). Flat constants replaced by the canonical
#   price-dependent fee model + documented economic bases.
#   2026-06-17: entry admission cooldown keys use stable market identity
#   (city,target_date,metric,bin_label,direction), not dynamic EDLI family hashes.
#
# DAEMON-SAFE BACKING (critical): assert_db_matches_registry (table_registry.py:285) is STRICT
# set-equality on TABLE NAMES (extra COLUMNS are permitted — subset semantics). So this module adds
# NO new table: the belief cache reuses the already-registered probability_trace_fact (synthesized
# 'edli_belief:' decision_id; trace_status='complete') plus an additive condition_ids_json column
# (idempotent ALTER in db.py; column-subset-safe). The act-once-per-edge dedup is IN-MEMORY
# (reactor-held acted_state dict), not a table. SHADOW-safe: never submits an order — only screens
# cached belief × fresh price and returns re-decisions for the reactor to route through the EXISTING
# pending cert path (so _refresh_pending_family_snapshots fires just-in-time → fresh price; critic
# SEV-1 stale-price hole closed structurally).
"""Continuous re-decision: cached belief × fresh price → cheap edge screen → enqueue + evidence exit."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from src.contracts.probability_arithmetic import one_minus


def _fee_at(price: float) -> float:
    """Canonical price-dependent Polymarket taker fee for ``price`` (probability units).

    Operator law: no unsupported hardcoded values. The flat 1¢ haircut the module shipped with
    over-charged near 0.5 (true fee 1.25¢) and over-charged ~3x near 0.9 (true fee 0.45¢) — both
    distort the edge screen. The single fee authority is ``execution_price.polymarket_fee``
    (fee_rate * p * (1-p); docs.polymarket.com/trading/fees). Fail-soft to the 0.5 worst case
    (max of the parabola) for a price outside (0,1) so the screen stays CONSERVATIVE, never
    fabricating edge from a degenerate quote."""
    from src.contracts.execution_price import polymarket_fee

    if not (0.0 < float(price) < 1.0):
        return polymarket_fee(0.5)  # parabola maximum = most conservative haircut
    return polymarket_fee(float(price))


# Tick size (probability units). Polymarket's CLOB min_tick_size is 0.01 for weather markets
# (executable_market_snapshots.min_tick_size). One tick is the smallest price increment a quote
# can move, so it is the natural quantum for "a price move that actually changed the book".
TICK_SIZE: float = 0.01
# IMPROVE_DELTA economic basis: the smallest edge improvement worth re-deciding on. A re-decision
# costs a full cert run + a potential cancel/replace round-trip; an improvement below the round-trip
# friction is noise. Friction floor = 2*tick (the book must move at least one tick AND our re-quote
# clears one tick) plus one fee-quantum of slack ≈ the worst-case fee swing across a one-tick move.
# 2*0.01 = 0.02. This REPLACES the prior bare 0.02 magic number with a derived quantity.
IMPROVE_DELTA: float = 2.0 * TICK_SIZE
# §4.5 (Dimension 3) belief-WORSENING re-price threshold — the mirror of IMPROVE_DELTA on the
# adverse side. A resting favorable order is pulled when NEW EVIDENCE has decayed its belief by
# >= this. Basis: it must be STRICTLY LARGER than the entry friction (IMPROVE_DELTA) so we never
# pull a rest for a move we would not have re-entered on — set to IMPROVE_DELTA + one tick of
# hysteresis (3*tick = 0.03) so a single-tick belief flutter against a fresh-snapshot rest does not
# thrash the order. This REPLACES the prior bare 0.03 magic number.
BELIEF_REPRICE_DELTA: float = 3.0 * TICK_SIZE
# §4.5 stale-quote cancel: a resting order priced off a book older than this (ms) is on a dead book
# and must be cancelled (re-decide next cycle on a fresh price). Mirrors config pre_submit_max_quote_age_ms.
PRE_SUBMIT_MAX_QUOTE_AGE_MS: float = 1000.0
# §4.5 moved-book pull: a resting maker quote whose limit is no longer within this many ticks of the
# current best bid is on a stale book that has walked away — pull and re-quote at the fresh price.
# One tick of tolerance: a quote exactly at best is fine; a quote a full tick or more off-best is
# unlikely to fill and bleeds queue-position.
REST_BOOK_DRIFT_TICKS: float = 1.0
REDECISION_EVENT_TYPE: str = "EDLI_REDECISION_PENDING"
_BELIEF_PREFIX: str = "edli_belief:"
_EPS: float = 1e-9
EntryScreenKey = tuple[str, str, str]
StableEntryScreenKey = tuple[str, str, str, str, str]
FamilyRedecisionScreenKey = tuple[str, str, str, str]
RedecisionScreenKey = EntryScreenKey | StableEntryScreenKey | FamilyRedecisionScreenKey
FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS: float = 30.0 * 60.0


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
    # Parallel to bin_labels: the executable condition_id per bin (empty string when the bin had
    # no market at decision time). The P2 screen needs this to join a cached belief to the freshest
    # executable_market_snapshots row (keyed by condition_id). Defaulted empty for backward-compat
    # with rows cached before the resurrection.
    condition_ids: list[str] = None  # type: ignore[assignment]
    # Parallel to bin_labels: conservative lower-bound probability for each side.
    # Entry redecision must screen on this, not on point posterior optimism.
    q_lcb_yes_vec: list[float | None] | None = None
    q_lcb_no_vec: list[float | None] | None = None
    # The family's temperature metric ("high"/"low"). Parsed from the family_id (position 4 of the
    # pipe-separated id) so the P2 job can build the (city, target_date, metric) family key for the
    # FSR re-emit restriction without re-deriving topology. Empty when unparseable.
    metric: str = ""


@dataclass(frozen=True)
class EnqueuedRedecision:
    family_id: str
    bin_label: str
    direction: str
    edge: float
    event_type: str = REDECISION_EVENT_TYPE


@dataclass(frozen=True)
class FullEconomicsReject:
    execution_price: float | None
    q_lcb_5pct: float | None
    trade_score: float | None
    created_at: str
    rejection_reason: str = ""


@dataclass(frozen=True)
class RepriceDecision:
    """§4.5 (Dimension 3) cancel/re-place decision for a RESTING order. ``action`` is one of
    {CANCEL_REPLACE, CANCEL_STALE, CANCEL_EXIT}; ``reason`` is the evidence class. SHADOW-safe — the
    reactor routes this back through the existing cert path; this module never submits."""
    family_id: str
    bin_label: str
    side: str
    action: str
    reason: str
    detail: float = 0.0  # |Δbelief| for BELIEF_WORSENING; quote_age_ms for QUOTE_STALE.


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
            temperature_metric TEXT,
            decision_snapshot_id TEXT,
            bin_labels_json TEXT,
            p_posterior_json TEXT,
            condition_ids_json TEXT,
            q_lcb_yes_json TEXT,
            q_lcb_no_json TEXT
        )
        """
    )
    # Live DBs predate condition_ids_json; ALTER catches them (duplicate-column on fresh DBs is the
    # expected no-op). Column-subset-safe per assert_db_matches_registry (extra columns permitted).
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN condition_ids_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN temperature_metric TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_yes_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_no_json TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _has_condition_ids_column(conn: sqlite3.Connection) -> bool:
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    except sqlite3.Error:
        return False
    return "condition_ids_json" in cols


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return False
    return column in cols


def _json_float_or_none_vec(values: list[object] | None) -> str | None:
    if values is None:
        return None
    out: list[float | None] = []
    for value in values:
        try:
            out.append(None if value is None else float(value))
        except (TypeError, ValueError):
            out.append(None)
    return json.dumps(out)


def write_belief_row(
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
    temperature_metric: str = "",
    condition_ids: list[str] | None = None,
    q_lcb_yes_vec: list[object] | None = None,
    q_lcb_no_vec: list[object] | None = None,
) -> None:
    """Write ONE belief row through the GIVEN connection — NO commit, NO new connection.

    This is the DEADLOCK-FREE primitive (resurrection 2026-06-12). The reactor calls it while it
    already holds the world write lock inside its open SAVEPOINT, so the row lands in the SAME
    transaction the reactor's decision rows use and is released by the reactor's own per-event
    commit. The original ``persist_belief_live`` opened a SECOND world connection and committed
    WHILE this lock was held → SQLite self-deadlock that HUNG process_pending. The cure is the
    structural one: the caller owns the transaction; this function never touches it.

    Idempotent per (family, snapshot, calibrator) — a newer snapshot writes a new row; the screen
    reads latest. condition_ids is parallel to bin_labels (empty string for bins with no market)."""
    decision_id = _belief_decision_id(family_id, snapshot_id, calibrator_model_hash)
    cond_json = json.dumps([str(c or "") for c in (condition_ids or [])])
    metric = str(temperature_metric or _metric_from_family_id(family_id) or "").strip()
    has_metric_col = _has_temperature_metric_column(conn)
    has_yes_lcb_col = _has_column(conn, "probability_trace_fact", "q_lcb_yes_json")
    has_no_lcb_col = _has_column(conn, "probability_trace_fact", "q_lcb_no_json")
    q_lcb_yes_json = _json_float_or_none_vec(q_lcb_yes_vec)
    q_lcb_no_json = _json_float_or_none_vec(q_lcb_no_vec)
    if _has_condition_ids_column(conn):
        metric_col = ", temperature_metric" if has_metric_col else ""
        metric_placeholder = ", ?" if has_metric_col else ""
        metric_update = ", temperature_metric=excluded.temperature_metric" if has_metric_col else ""
        q_lcb_cols = ""
        q_lcb_placeholders = ""
        q_lcb_update = ""
        if has_yes_lcb_col:
            q_lcb_cols += ", q_lcb_yes_json"
            q_lcb_placeholders += ", ?"
            q_lcb_update += ", q_lcb_yes_json=excluded.q_lcb_yes_json"
        if has_no_lcb_col:
            q_lcb_cols += ", q_lcb_no_json"
            q_lcb_placeholders += ", ?"
            q_lcb_update += ", q_lcb_no_json=excluded.q_lcb_no_json"
        values = [
            "trace_" + decision_id, decision_id, recorded_at,
            city, target_date, snapshot_id,
            json.dumps(list(bin_labels)), json.dumps([float(x) for x in p_posterior_vec]),
            cond_json,
        ]
        if has_metric_col:
            values.insert(5, metric)
        if has_yes_lcb_col:
            values.append(q_lcb_yes_json)
        if has_no_lcb_col:
            values.append(q_lcb_no_json)
        conn.execute(
            f"""
            INSERT INTO probability_trace_fact
                (trace_id, decision_id, trace_status, missing_reason_json, recorded_at,
                 city, target_date{metric_col}, decision_snapshot_id, bin_labels_json, p_posterior_json,
                 condition_ids_json{q_lcb_cols})
            VALUES (?, ?, 'complete', '[]', ?, ?, ?{metric_placeholder}, ?, ?, ?, ?{q_lcb_placeholders})
            ON CONFLICT(decision_id) DO UPDATE SET
                recorded_at=excluded.recorded_at,
                bin_labels_json=excluded.bin_labels_json,
                p_posterior_json=excluded.p_posterior_json,
                condition_ids_json=excluded.condition_ids_json
                {metric_update}
                {q_lcb_update}
            """,
            tuple(values),
        )
    else:
        # Legacy DB not yet migrated: write without condition_ids (P2 screen will skip price-join
        # for these rows). Never fail-closed on a missing optional column.
        metric_col = ", temperature_metric" if has_metric_col else ""
        metric_placeholder = ", ?" if has_metric_col else ""
        metric_update = ", temperature_metric=excluded.temperature_metric" if has_metric_col else ""
        values = [
            "trace_" + decision_id, decision_id, recorded_at,
            city, target_date, snapshot_id,
            json.dumps(list(bin_labels)), json.dumps([float(x) for x in p_posterior_vec]),
        ]
        if has_metric_col:
            values.insert(5, metric)
        conn.execute(
            f"""
            INSERT INTO probability_trace_fact
                (trace_id, decision_id, trace_status, missing_reason_json, recorded_at,
                 city, target_date{metric_col}, decision_snapshot_id, bin_labels_json, p_posterior_json)
            VALUES (?, ?, 'complete', '[]', ?, ?, ?{metric_placeholder}, ?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET
                recorded_at=excluded.recorded_at,
                bin_labels_json=excluded.bin_labels_json,
                p_posterior_json=excluded.p_posterior_json
                {metric_update}
            """,
            tuple(values),
        )


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
    temperature_metric: str = "",
    condition_ids: list[str] | None = None,
    q_lcb_yes_vec: list[object] | None = None,
    q_lcb_no_vec: list[object] | None = None,
) -> None:
    """Standalone (test / offline) belief writer: writes the row AND commits on its own connection.

    NEVER call this from inside the reactor's write window — it commits, which on a held world lock
    is the exact deadlock the resurrection removed. The reactor path uses ``write_belief_row``
    (no commit). This entry point is for isolated/:memory: connections that own their transaction."""
    if q_lcb_yes_vec is None:
        q_lcb_yes_vec = [float(x) for x in p_posterior_vec]
    if q_lcb_no_vec is None:
        q_lcb_no_vec = [one_minus(float(x)) for x in p_posterior_vec]
    write_belief_row(
        conn,
        family_id=family_id, city=city, target_date=target_date,
        temperature_metric=temperature_metric,
        snapshot_id=snapshot_id, calibrator_model_hash=calibrator_model_hash,
        bin_labels=bin_labels, p_posterior_vec=p_posterior_vec, recorded_at=recorded_at,
        condition_ids=condition_ids,
        q_lcb_yes_vec=q_lcb_yes_vec,
        q_lcb_no_vec=q_lcb_no_vec,
    )
    conn.commit()


def _metric_from_family_id(family_id: str) -> str:
    """Extract the temperature_metric from a pipe-separated family_id.

    Both make_hypothesis_family_id ("hyp|cycle_mode|city|target_date|metric|...") and
    make_edge_family_id ("edge|cycle_mode|city|target_date|metric|strategy_key|...") place the
    metric at index 4. Returns "" if the id is too short / not pipe-separated."""
    parts = family_id.split("|")
    if len(parts) > 4 and parts[4] in ("high", "low"):
        return parts[4]
    if len(parts) == 3 and parts[2] in ("high", "low"):
        return parts[2]
    return ""


def _stable_entry_screen_key(
    belief: CachedBelief,
    *,
    bin_label: str,
    direction: str,
) -> StableEntryScreenKey | None:
    """Stable identity for entry backoff across dynamic EDLI family hashes."""

    city = str(belief.city or "").strip()
    target_date = str(belief.target_date or "").strip()
    metric = str(belief.metric or _metric_from_family_id(belief.family_id) or "").strip()
    label = str(bin_label or "").strip()
    side = str(direction or "").strip()
    if not (city and target_date and metric in {"high", "low"} and label and side):
        return None
    return (city, target_date, metric, label, side)


def _stable_family_screen_key(belief: CachedBelief) -> FamilyRedecisionScreenKey | None:
    city = str(belief.city or "").strip()
    target_date = str(belief.target_date or "").strip()
    metric = str(belief.metric or _metric_from_family_id(belief.family_id) or "").strip()
    if not (city and target_date and metric in {"high", "low"}):
        return None
    return ("family", city, target_date, metric)


def _has_temperature_metric_column(conn: sqlite3.Connection) -> bool:
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    except sqlite3.Error:
        return False
    return "temperature_metric" in cols


def _row_to_belief(row: sqlite3.Row) -> CachedBelief | None:
    parsed = _parse_belief_decision_id(row["decision_id"])
    if parsed is None or not row["p_posterior_json"] or not row["bin_labels_json"]:
        return None
    family_id, snapshot_id, calib = parsed
    bin_labels = json.loads(row["bin_labels_json"])
    try:
        cond_raw = row["condition_ids_json"]
    except (IndexError, KeyError):
        cond_raw = None
    try:
        row_metric = str(row["temperature_metric"] or "").strip()
    except (IndexError, KeyError):
        row_metric = ""
    condition_ids = json.loads(cond_raw) if cond_raw else []
    try:
        q_lcb_yes_raw = row["q_lcb_yes_json"]
    except (IndexError, KeyError):
        q_lcb_yes_raw = None
    try:
        q_lcb_no_raw = row["q_lcb_no_json"]
    except (IndexError, KeyError):
        q_lcb_no_raw = None
    q_lcb_yes_vec = json.loads(q_lcb_yes_raw) if q_lcb_yes_raw else None
    q_lcb_no_vec = json.loads(q_lcb_no_raw) if q_lcb_no_raw else None
    return CachedBelief(
        family_id=family_id,
        city=row["city"] or "",
        target_date=row["target_date"] or "",
        snapshot_id=snapshot_id,
        calibrator_model_hash=calib,
        bin_labels=bin_labels,
        p_posterior_vec=json.loads(row["p_posterior_json"]),
        recorded_at=row["recorded_at"],
        condition_ids=list(condition_ids),
        q_lcb_yes_vec=list(q_lcb_yes_vec) if q_lcb_yes_vec is not None else None,
        q_lcb_no_vec=list(q_lcb_no_vec) if q_lcb_no_vec is not None else None,
        metric=row_metric or _metric_from_family_id(family_id),
    )


def latest_cached_belief(conn: sqlite3.Connection, *, family_id: str) -> CachedBelief | None:
    for belief in _all_latest_beliefs(conn):
        if belief.family_id == family_id:
            return belief
    return None


def _all_latest_beliefs(conn: sqlite3.Connection) -> list[CachedBelief]:
    cols = "decision_id, recorded_at, city, target_date, bin_labels_json, p_posterior_json"
    if _has_condition_ids_column(conn):
        cols += ", condition_ids_json"
    if _has_temperature_metric_column(conn):
        cols += ", temperature_metric"
    if _has_column(conn, "probability_trace_fact", "q_lcb_yes_json"):
        cols += ", q_lcb_yes_json"
    if _has_column(conn, "probability_trace_fact", "q_lcb_no_json"):
        cols += ", q_lcb_no_json"
    rows = conn.execute(
        f"SELECT {cols} FROM probability_trace_fact "
        "WHERE decision_id LIKE ? ORDER BY recorded_at DESC",
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
    acted_state: dict[RedecisionScreenKey, float] | None = None,
    recent_full_economics_rejections: dict[RedecisionScreenKey, FullEconomicsReject] | None = None,
) -> list[EnqueuedRedecision]:
    """Screen live entry pairs against FRESH price and conservative q_lcb evidence.

    Stale price (freshness_deadline <= decision_time) is skipped (no phantom edge). acted_state is an
    optional IN-MEMORY dict (the reactor holds it across cycles): a pair re-fires only when its edge
    improves past IMPROVE_DELTA vs the last acted edge — a short price wiggle does NOT re-fire.
    Recent full-economics no-value rejects block the same pair until price or q_lcb improves.
    """
    dt = _parse(decision_time)
    out: list[EnqueuedRedecision] = []
    for belief in _all_latest_beliefs(conn):
        family_key = _stable_family_screen_key(belief)
        for idx, label in enumerate(belief.bin_labels):
            if idx >= len(belief.p_posterior_vec):
                continue
            q_lcb_yes = _vec_float_at(belief.q_lcb_yes_vec, idx)
            q_lcb_no = _vec_float_at(belief.q_lcb_no_vec, idx)
            for direction in ("buy_yes", "buy_no"):
                legacy_key: EntryScreenKey = (belief.family_id, label, direction)
                stable_key = _stable_entry_screen_key(
                    belief,
                    bin_label=label,
                    direction=direction,
                )
                quote = price_lookup.get(legacy_key)
                if quote is None:
                    continue
                if _parse(quote.freshness_deadline) <= dt:
                    continue  # STALE → no phantom edge (R7)
                conservative_q = q_lcb_yes if direction == "buy_yes" else q_lcb_no
                if conservative_q is None:
                    continue
                edge = float(conservative_q) - float(quote.price) - _fee_at(float(quote.price))
                if edge < min_edge - _EPS:
                    continue
                rejection = None
                if recent_full_economics_rejections is not None:
                    if stable_key is not None:
                        rejection = recent_full_economics_rejections.get(stable_key)
                    if rejection is None:
                        rejection = recent_full_economics_rejections.get(legacy_key)
                candidate_refutation_cleared = False
                if rejection is not None and _full_economics_reject_still_blocks(
                    rejection,
                    current_execution_price=_all_in_cost(float(quote.price)),
                    current_q_lcb=float(conservative_q),
                ):
                    continue
                if rejection is not None:
                    candidate_refutation_cleared = True
                family_rejection = (
                    recent_full_economics_rejections.get(family_key)
                    if family_key is not None and recent_full_economics_rejections is not None
                    else None
                )
                if (
                    family_rejection is not None
                    and not (
                        candidate_refutation_cleared
                        and _candidate_refutation_is_at_least_as_fresh(rejection, family_rejection)
                    )
                    and _full_decision_family_refutation_still_blocks(
                        family_rejection,
                        decision_time=decision_time,
                    )
                ):
                    continue
                if acted_state is not None:
                    acted_key: RedecisionScreenKey = stable_key or legacy_key
                    last = acted_state.get(acted_key)
                    if last is not None and edge <= last + IMPROVE_DELTA + _EPS:
                        continue  # not materially improved → do not re-fire (anti price-noise)
                    acted_state[acted_key] = edge
                out.append(EnqueuedRedecision(belief.family_id, label, direction, edge))
    return out


def _vec_float_at(values: list[float | None] | None, idx: int) -> float | None:
    if values is None or idx >= len(values):
        return None
    try:
        value = values[idx]
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= out <= 1.0):
        return None
    return out


def _full_economics_reject_still_blocks(
    rejection: FullEconomicsReject,
    *,
    current_execution_price: float,
    current_q_lcb: float,
) -> bool:
    reason = str(rejection.rejection_reason or "")
    if (
        rejection.trade_score is not None
        and rejection.trade_score > 0.0
        and not reason.startswith("FDR_REJECTED")
    ):
        return False
    price_improved = (
        rejection.execution_price is not None
        and current_execution_price <= float(rejection.execution_price) - IMPROVE_DELTA + _EPS
    )
    belief_improved = (
        rejection.q_lcb_5pct is not None
        and current_q_lcb >= float(rejection.q_lcb_5pct) + IMPROVE_DELTA - _EPS
    )
    return not (price_improved or belief_improved)


def _full_decision_family_refutation_still_blocks(
    rejection: FullEconomicsReject,
    *,
    decision_time: str,
) -> bool:
    try:
        rejected_at = _parse(str(rejection.created_at))
        now = _parse(str(decision_time))
    except (TypeError, ValueError):
        return True
    return (now - rejected_at).total_seconds() < FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS


def _candidate_refutation_is_at_least_as_fresh(
    candidate_rejection: FullEconomicsReject | None,
    family_rejection: FullEconomicsReject,
) -> bool:
    family_reason = str(family_rejection.rejection_reason or "")
    if (
        family_reason.startswith("FDR_REJECTED")
        or family_reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    ):
        return False
    if candidate_rejection is None:
        return False
    try:
        candidate_time = _parse(str(candidate_rejection.created_at))
        family_time = _parse(str(family_rejection.created_at))
    except (TypeError, ValueError):
        return False
    return candidate_time >= family_time


def _all_in_cost(price: float) -> float:
    return float(price) + _fee_at(float(price))


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def read_recent_full_economics_rejections(
    conn: sqlite3.Connection,
    *,
    lookback_hours: float = 24.0,
) -> dict[RedecisionScreenKey, FullEconomicsReject]:
    """Latest terminal full-economics no-value rejection per candidate.

    This is live evidence backoff, not a strategy cap. A cheaper fresh price or a
    higher q_lcb clears it and sends the pair through the full reactor again.
    """
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_regret_events'"
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not has_table:
        return {}
    try:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(no_trade_regret_events)").fetchall()
        }
    except sqlite3.Error:
        return {}
    required = {
        "family_id", "bin_label", "direction", "rejection_stage", "rejection_reason",
        "c_fee_adjusted", "q_lcb_5pct", "trade_score", "created_at",
    }
    if not required.issubset(cols):
        return {}
    from datetime import timedelta, timezone as _timezone

    cutoff = (datetime.now(_timezone.utc) - timedelta(hours=max(0.0, lookback_hours))).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT family_id, city, target_date, metric, bin_label, direction,
                   c_fee_adjusted, q_lcb_5pct, trade_score, created_at, rejection_reason
             FROM no_trade_regret_events
             WHERE rejection_stage = 'TRADE_SCORE'
               AND (
                    rejection_reason IN ('TRADE_SCORE_NON_POSITIVE', 'TRADE_SCORE_BLOCKED')
                 OR rejection_reason LIKE 'TRADE_SCORE_NON_POSITIVE:%'
                 OR rejection_reason LIKE 'TRADE_SCORE_BLOCKED:%'
                 OR rejection_reason = 'FDR_REJECTED'
                 OR rejection_reason LIKE 'FDR_REJECTED:%'
                 OR rejection_reason LIKE 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:%'
               )
               AND family_id IS NOT NULL AND family_id != ''
               AND bin_label IS NOT NULL AND bin_label != ''
               AND direction IS NOT NULL AND direction != ''
               AND created_at >= ?
             ORDER BY created_at DESC
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[RedecisionScreenKey, FullEconomicsReject] = {}
    for row in rows:
        legacy_key: EntryScreenKey = (str(row[0]), str(row[4]), str(row[5]))
        stable_key: StableEntryScreenKey | None = None
        city = str(row[1] or "").strip()
        target_date = str(row[2] or "").strip()
        metric = str(row[3] or "").strip()
        bin_label = str(row[4] or "").strip()
        direction = str(row[5] or "").strip()
        if city and target_date and metric in {"high", "low"} and bin_label and direction:
            stable_key = (city, target_date, metric, bin_label, direction)
        rejection = FullEconomicsReject(
            execution_price=_optional_float(row[6]),
            q_lcb_5pct=_optional_float(row[7]),
            trade_score=_optional_float(row[8]),
            created_at=str(row[9] or ""),
            rejection_reason=str(row[10] or ""),
        )
        if stable_key is not None and stable_key not in out:
            out[stable_key] = rejection
        if legacy_key not in out:
            out[legacy_key] = rejection
        reason = rejection.rejection_reason
        if city and target_date and metric in {"high", "low"} and (
            reason.startswith("TRADE_SCORE_NON_POSITIVE")
            or reason.startswith("TRADE_SCORE_BLOCKED")
            or reason.startswith("FDR_REJECTED")
            or reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
        ):
            family_key: FamilyRedecisionScreenKey = ("family", city, target_date, metric)
            if family_key not in out:
                out[family_key] = rejection
    return out


def screen_reprice(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    bin_label: str,
    side: str,
    resting_posterior: float,
    resting_snapshot_id: str,
    belief_reprice_delta: float = BELIEF_REPRICE_DELTA,
) -> RepriceDecision | None:
    """§4.5 (Dimension 3) — the symmetric belief-WORSENING re-price trigger.

    The existing ``enqueue_live_redecisions`` IMPROVE_DELTA path re-fires only on edge IMPROVEMENT.
    This is its mirror: a resting favorable order whose BELIEF has DECAYED past ``belief_reprice_delta``
    must be PULLED (cancel + re-place at the new reservation), because a stale-favorable resting quote
    bleeds adverse selection.

    ANTI-TWITCH (the invariant): the trigger is keyed on EVIDENCE, not price. A re-price fires only
    when the LATEST cached belief comes from a DIFFERENT snapshot than the one the resting order was
    priced on (``resting_snapshot_id``) — i.e. a new FSR / day0 / obs landed. If the latest belief is
    still the resting order's own snapshot (no new evidence — a bare price wiggle), this returns None
    (HOLD). A favorable belief move also returns None (improvement is the IMPROVE_DELTA path's job, not
    a cancel). So a bare price move can NEVER reach a CANCEL here.
    """
    belief = latest_cached_belief(conn, family_id=family_id)
    if belief is None:
        return None
    # Evidence gate: only a NEW snapshot (new forecast/day0/obs) is evidence. Same snapshot = the
    # resting order's belief is unchanged → any price move is a bare wiggle → HOLD (anti-twitch).
    if belief.snapshot_id == resting_snapshot_id:
        return None
    try:
        idx = belief.bin_labels.index(bin_label)
    except ValueError:
        return None
    if idx >= len(belief.p_posterior_vec):
        return None
    yes_post = float(belief.p_posterior_vec[idx])
    if side not in {"buy_yes", "buy_no"}:
        return None
    current = yes_post if side == "buy_yes" else one_minus(yes_post)
    delta = float(resting_posterior) - current  # >0 means belief WORSENED against the held side
    if delta >= belief_reprice_delta - _EPS:
        return RepriceDecision(
            family_id=family_id, bin_label=bin_label, side=side,
            action="CANCEL_REPLACE", reason="BELIEF_WORSENING", detail=delta,
        )
    return None


def screen_stale_quote_cancel(
    *,
    family_id: str,
    bin_label: str,
    side: str,
    quote_age_ms: float,
    pre_submit_max_quote_age_ms: float = PRE_SUBMIT_MAX_QUOTE_AGE_MS,
) -> RepriceDecision | None:
    """§4.5 stale-quote cancel: a resting order whose backing quote is older than
    ``pre_submit_max_quote_age_ms`` is priced off a DEAD book → cancel (re-decide next cycle on a
    fresh price). This is NOT a belief move and NOT a price-driven exit — it is a "this order's price
    is meaningless now" pull. A fresh quote (within max age) is never cancelled (anti-twitch).
    """
    if float(quote_age_ms) > float(pre_submit_max_quote_age_ms) + _EPS:
        return RepriceDecision(
            family_id=family_id, bin_label=bin_label, side=side,
            action="CANCEL_STALE", reason="QUOTE_STALE", detail=float(quote_age_ms),
        )
    return None


_OPPOSITE_SIDE: dict[str, str] = {"buy_yes": "buy_no", "buy_no": "buy_yes"}


def select_exit_order_mode(
    *,
    held_side: str,
    exit_reservation: float,
    actionable_payload: dict,
    quote_payload: dict,
    best_bid: float | None,
    best_ask: float | None,
    executable_snapshot,
) -> str:
    """§4.6 6b — route the EXIT order through the SAME entry-spine order-mode machinery.

    An exit is an entry into the OPPOSITE side, gated by the same §1 governor maker/taker + §2 EV +
    reservation-cap law the entry wave built. This delegates to the entry selector
    (``event_reactor_adapter._select_edli_order_mode``) — it does NOT duplicate the maker/taker logic.
    The held side is flipped to the opposite direction, and the order is capped at the EXIT reservation
    (the break-even of remaining belief edge — the price at which holding >= exiting), so the exit can
    never pay through it (no panic-dump).
    """
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    exit_payload = dict(actionable_payload)
    exit_payload["direction"] = _OPPOSITE_SIDE.get(held_side, held_side)
    exit_payload["c_fee_adjusted"] = float(exit_reservation)  # reservation cap = no pay-through
    return _select_edli_order_mode(
        actionable_payload=exit_payload,
        quote_payload=quote_payload,
        best_bid=best_bid,
        best_ask=best_ask,
        executable_snapshot=executable_snapshot,
    )


# ── P2 SCREEN ORCHESTRATION (resurrection 2026-06-12) ──────────────────────────────────────────
# The reactor-held in-memory dedup state for the entry screen. Held across cycles by the scheduler
# job module (process-global) so a price wiggle does not re-fire (anti price-noise, R6).
def read_freshest_executable_prices(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: set[str],
) -> dict[tuple[str, str], PriceQuote]:
    """Build a ``(condition_id, direction) → PriceQuote`` map from the freshest already-captured
    ``executable_market_snapshots`` rows. NO new HTTP — reads only what the warm/fast lanes persisted.

    YES side prices off ``orderbook_top_ask`` (the cost to BUY yes); NO side prices off
    ``1 - orderbook_top_bid`` (buying NO == selling the YES at best bid). Each quote carries the
    snapshot's own ``freshness_deadline`` so the screen's stale-price guard (R7) is exact. Crossed or
    non-finite books are skipped (no phantom edge). Append-only table indexed by
    (condition_id, captured_at DESC) → the freshest row per condition is one bounded index seek."""
    if not condition_ids:
        return {}
    try:
        cols = {row[1] for row in trade_conn.execute(
            "PRAGMA table_info(executable_market_snapshots)").fetchall()}
    except sqlite3.Error:
        return {}
    if not {"condition_id", "orderbook_top_bid", "orderbook_top_ask",
            "freshness_deadline", "captured_at"}.issubset(cols):
        return {}
    rows = _freshest_executable_price_rows_by_condition(trade_conn, condition_ids=condition_ids)
    out: dict[tuple[str, str], PriceQuote] = {}
    for row in rows:
        cid = str(row[0] or "")
        deadline = str(row[3] or "")
        if not cid or not deadline:
            continue
        try:
            top_bid = float(row[1])
            top_ask = float(row[2])
        except (TypeError, ValueError):
            continue
        # YES buy: pay the ask. NO buy: pay 1 - best_bid (the NO ask implied by the YES book).
        if 0.0 < top_ask < 1.0:
            out[(cid, "buy_yes")] = PriceQuote(price=top_ask, freshness_deadline=deadline)
        no_ask = one_minus(top_bid)
        if 0.0 < no_ask < 1.0:
            out[(cid, "buy_no")] = PriceQuote(price=no_ask, freshness_deadline=deadline)
    return out


def read_freshest_resting_best_bids(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: set[str],
) -> dict[tuple[str, str], PriceQuote]:
    """Build a ``(condition_id, direction) -> best bid`` map for maker-rest checks.

    Entry edge screening consumes executable ask cost. Resting maker orders need
    same-side best bid; using ask cost here turns ordinary spread into false
    ``BOOK_MOVED`` churn.
    """
    if not condition_ids:
        return {}
    try:
        cols = {row[1] for row in trade_conn.execute(
            "PRAGMA table_info(executable_market_snapshots)").fetchall()}
    except sqlite3.Error:
        return {}
    if not {"condition_id", "orderbook_top_bid", "orderbook_top_ask",
            "freshness_deadline", "captured_at"}.issubset(cols):
        return {}
    rows = _freshest_executable_price_rows_by_condition(trade_conn, condition_ids=condition_ids)
    out: dict[tuple[str, str], PriceQuote] = {}
    for row in rows:
        cid = str(row[0] or "")
        deadline = str(row[3] or "")
        if not cid or not deadline:
            continue
        try:
            yes_bid = float(row[1])
            yes_ask = float(row[2])
        except (TypeError, ValueError):
            continue
        if 0.0 < yes_bid < 1.0:
            out[(cid, "buy_yes")] = PriceQuote(price=yes_bid, freshness_deadline=deadline)
        no_bid = one_minus(yes_ask)
        if 0.0 < no_bid < 1.0:
            out[(cid, "buy_no")] = PriceQuote(price=no_bid, freshness_deadline=deadline)
    return out


def _freshest_executable_price_rows_by_condition(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: set[str],
) -> list[sqlite3.Row | tuple]:
    """Return the newest snapshot price row per condition via bounded index seeks.

    The previous window query sorted every matching snapshot in a growing
    high-frequency table. Continuous redecision only needs one current row per
    condition, so use the existing ``(condition_id, captured_at DESC)`` index
    directly and keep the scheduler cycle bounded by the number of live
    conditions it is actually screening.
    """

    rows: list[sqlite3.Row | tuple] = []
    seen: set[str] = set()
    for raw_condition_id in sorted(condition_ids):
        condition_id = str(raw_condition_id or "").strip()
        if not condition_id or condition_id in seen:
            continue
        seen.add(condition_id)
        row = trade_conn.execute(
            """
            SELECT condition_id, orderbook_top_bid, orderbook_top_ask, freshness_deadline
              FROM executable_market_snapshots
             WHERE condition_id = ?
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (condition_id,),
        ).fetchone()
        if row is not None:
            rows.append(row)
    return rows


def screen_entry_redecisions(
    world_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    *,
    decision_time: str,
    min_edge: float,
    acted_state: dict[tuple[str, str, str], float] | None = None,
) -> list[EnqueuedRedecision]:
    """P2 ENTRY screen end-to-end: cached beliefs (world) × freshest executable prices (trade) →
    cheap edge screen → re-decisions. Joins each belief's per-bin condition_ids to the price map, so
    the ``(family_id, bin_label, direction)`` price_lookup ``enqueue_live_redecisions`` consumes is
    keyed correctly without any market-topology re-derivation.

    Pure read on both DBs. NO HTTP, NO writes. The reactor's scheduler job owns ``acted_state``."""
    beliefs = _all_latest_beliefs(world_conn)
    # Collect every condition_id referenced by a cached belief (one price read for the batch).
    all_cids: set[str] = set()
    for belief in beliefs:
        all_cids.update(c for c in (belief.condition_ids or []) if c)
    price_by_cid = read_freshest_executable_prices(trade_conn, condition_ids=all_cids)
    recent_rejections = read_recent_full_economics_rejections(world_conn)
    # Re-key the price map onto (family_id, bin_label, direction) the screen expects.
    price_lookup: dict[tuple[str, str, str], PriceQuote] = {}
    for belief in beliefs:
        conds = belief.condition_ids or []
        for idx, label in enumerate(belief.bin_labels):
            if idx >= len(conds):
                continue
            cid = str(conds[idx] or "")
            if not cid:
                continue
            for direction in ("buy_yes", "buy_no"):
                quote = price_by_cid.get((cid, direction))
                if quote is not None:
                    price_lookup[(belief.family_id, label, direction)] = quote
    return enqueue_live_redecisions(
        world_conn,
        decision_time=decision_time,
        price_lookup=price_lookup,
        min_edge=min_edge,
        acted_state=acted_state,
        recent_full_economics_rejections=recent_rejections,
    )


def screened_family_keys(
    world_conn: sqlite3.Connection,
    redecisions: list[EnqueuedRedecision],
) -> set[tuple[str, str, str]]:
    """Map firing redecisions → the ``(city, target_date, metric)`` family keys the P2 job feeds to
    the FSR re-emitter's ``restrict_to_families``. Resolved from each redecision's family_id via the
    cached belief (city/target_date/metric), so only screened families re-emit — never the universe."""
    by_family: dict[str, tuple[str, str, str]] = {}
    for belief in _all_latest_beliefs(world_conn):
        by_family[belief.family_id] = (belief.city, belief.target_date, belief.metric)
    out: set[tuple[str, str, str]] = set()
    for rd in redecisions:
        key = by_family.get(rd.family_id)
        if key is not None and all(key):
            out.add(key)
    return out


@dataclass(frozen=True)
class OpenRest:
    """One open maker rest joined to the belief snapshot it was priced on. Built by the scheduler
    job from venue_commands + venue_order_facts (the rest) and the command's decision belief."""
    command_id: str
    venue_order_id: str
    family_id: str
    bin_label: str
    side: str
    condition_id: str
    resting_posterior: float
    resting_snapshot_id: str
    limit_price: float
    quote_age_ms: float


def screen_resting_orders(
    world_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    *,
    open_rests: list[OpenRest],
) -> list[tuple[OpenRest, RepriceDecision]]:
    """§4.5 resting-order management: for each OPEN maker rest, fire a PULL (cancel+re-decide) when
    EITHER its belief has decayed past BELIEF_REPRICE_DELTA on NEW evidence (screen_reprice), OR its
    backing quote is stale (screen_stale_quote_cancel), OR the live book has walked away from our
    limit by more than REST_BOOK_DRIFT_TICKS (moved-book pull). Evidence-keyed, anti-twitch: a bare
    price wiggle on the SAME snapshot never reaches a cancel. Pure read; returns decisions only — the
    scheduler job enqueues the redecision and the reactor performs the actual cancel via the existing
    maker_rest_escalation cancel path (never a new venue call site)."""
    bid_by_cid = read_freshest_resting_best_bids(
        trade_conn, condition_ids={r.condition_id for r in open_rests if r.condition_id}
    )
    out: list[tuple[OpenRest, RepriceDecision]] = []
    for rest in open_rests:
        # 1) Belief-decay pull (evidence-gated, anti-twitch by snapshot identity).
        decision = screen_reprice(
            world_conn,
            family_id=rest.family_id,
            bin_label=rest.bin_label,
            side=rest.side,
            resting_posterior=rest.resting_posterior,
            resting_snapshot_id=rest.resting_snapshot_id,
        )
        if decision is None:
            # 2) Stale-quote pull (the order's backing book is too old to be meaningful).
            decision = screen_stale_quote_cancel(
                family_id=rest.family_id,
                bin_label=rest.bin_label,
                side=rest.side,
                quote_age_ms=rest.quote_age_ms,
            )
        if decision is None:
            # 3) Moved-book pull: our limit has fallen >1 tick behind the live best bid for our side.
            bid = bid_by_cid.get((rest.condition_id, rest.side))
            if bid is not None:
                drift = float(bid.price) - float(rest.limit_price)
                if drift > REST_BOOK_DRIFT_TICKS * TICK_SIZE + _EPS:
                    decision = RepriceDecision(
                        family_id=rest.family_id, bin_label=rest.bin_label, side=rest.side,
                        action="CANCEL_REPLACE", reason="BOOK_MOVED", detail=drift,
                    )
        if decision is not None:
            out.append((rest, decision))
    return out
