# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN Phase 0.G; ADR-5; ANTI_DRIFT_CHARTER §3 M1; RISK_REGISTER R2
"""Replay-correctness gate scaffold — Phase 0.G deliverable.

Wraps src/state/chronicler.py event log. Picks the last 7 days of canonical
events from state/zeus_trades.db (READ-ONLY). Re-runs a deterministic
projection snapshot. Compares against a baseline in evidence/replay_baseline/.
Returns 0 on match, non-zero on mismatch.

Emits one ritual_signal line to logs/ritual_signal/<YYYY-MM>.jsonl per
ANTI_DRIFT_CHARTER §3 M1 schema.

--bootstrap  Write baseline JSON without comparing. Does not emit a mismatch.

Non-deterministic event types excluded from the seed window (R2 mitigation):
  - model_response / model_call (LLM output)
  - web_fetch / http_fetch (external network calls)
  - market_price_snapshot (external venue state, varies in real-time)
  - external_position_sync (venue reconciliation calls)

CI lane: opt-in, NOT a merge gate (Phase 0.H gate promotes if criterion passes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "state" / "zeus_trades.db"
BASELINE_DIR = ROOT / "evidence" / "replay_baseline"
RITUAL_SIGNAL_DIR = ROOT / "logs" / "ritual_signal"
CHARTER_VERSION = "1.0.0"
HELPER_NAME = "replay_correctness_gate"

SEED_WINDOW_DAYS = 7

# R2 mitigation: exclude non-deterministic event types.
# These event types carry external state / RNG / model-output variability
# that defeats deterministic replay.
NON_DETERMINISTIC_EVENT_TYPES: frozenset[str] = frozenset(
    [
        "model_response",
        "model_call",
        "web_fetch",
        "http_fetch",
        "market_price_snapshot",
        "external_position_sync",
    ]
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(HELPER_NAME)


# ---------------------------------------------------------------------------
# Event extraction (read-only)
# ---------------------------------------------------------------------------


def _seed_cutoff() -> str:
    """ISO8601 UTC cutoff for the 7-day seed window."""
    return (datetime.now(timezone.utc) - timedelta(days=SEED_WINDOW_DAYS)).isoformat()


def _extract_chronicle_events(conn: sqlite3.Connection, cutoff: str) -> list[dict]:
    """Pull chronicle events (if the table is populated) within the seed window."""
    try:
        rows = conn.execute(
            "SELECT event_type, trade_id, timestamp, details_json, env "
            "FROM chronicle WHERE timestamp >= ? ORDER BY id",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    events = []
    for row in rows:
        if row[0] in NON_DETERMINISTIC_EVENT_TYPES:
            continue
        events.append(
            {
                "source": "chronicle",
                "event_type": row[0],
                "trade_id": row[1],
                "timestamp": row[2],
                "details": json.loads(row[3] or "{}"),
                "env": row[4],
            }
        )
    return events


def _extract_opportunity_events(conn: sqlite3.Connection, cutoff: str) -> list[dict]:
    """Pull opportunity_fact rows (deterministic trade-decision record)."""
    try:
        rows = conn.execute(
            "SELECT decision_id, candidate_id, city, target_date, range_label, "
            "direction, strategy_key, discovery_mode, entry_method, "
            "p_raw, p_cal, p_market, alpha, best_edge, should_trade, "
            "rejection_stage, recorded_at "
            "FROM opportunity_fact WHERE recorded_at >= ? ORDER BY decision_id",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    events = []
    for row in rows:
        events.append(
            {
                "source": "opportunity_fact",
                "event_type": "opportunity_evaluated",
                "decision_id": row[0],
                "candidate_id": row[1],
                "city": row[2],
                "target_date": row[3],
                "range_label": row[4],
                "direction": row[5],
                "strategy_key": row[6],
                "discovery_mode": row[7],
                "entry_method": row[8],
                "p_raw": row[9],
                "p_cal": row[10],
                "p_market": row[11],
                "alpha": row[12],
                "best_edge": row[13],
                "should_trade": bool(row[14]),
                "rejection_stage": row[15],
                "recorded_at": row[16],
            }
        )
    return events


def _extract_shadow_signals(conn: sqlite3.Connection, cutoff: str) -> list[dict]:
    """Pull shadow_signals (deterministic calibration output)."""
    try:
        rows = conn.execute(
            "SELECT id, city, target_date, timestamp, "
            "p_raw_json, p_cal_json, edges_json "
            "FROM shadow_signals WHERE timestamp >= ? ORDER BY id",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    events = []
    for row in rows:
        events.append(
            {
                "source": "shadow_signals",
                "event_type": "shadow_signal_emitted",
                "id": row[0],
                "city": row[1],
                "target_date": row[2],
                "timestamp": row[3],
                "p_raw": json.loads(row[4] or "{}"),
                "p_cal": json.loads(row[5] or "{}"),
                "edges": json.loads(row[6] or "{}"),
            }
        )
    return events


def _extract_decision_log(conn: sqlite3.Connection, cutoff: str) -> list[dict]:
    """Pull decision_log rows (deterministic mode/decision record)."""
    try:
        rows = conn.execute(
            "SELECT id, mode, started_at, completed_at, env, timestamp "
            "FROM decision_log WHERE timestamp >= ? ORDER BY id",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    events = []
    for row in rows:
        events.append(
            {
                "source": "decision_log",
                "event_type": "decision_cycle_completed",
                "id": row[0],
                "mode": row[1],
                "started_at": row[2],
                "completed_at": row[3],
                "env": row[4],
                "timestamp": row[5],
            }
        )
    return events


def extract_seed_events(db_path: Path) -> tuple[list[dict], list[str]]:
    """Return (deterministic_events, excluded_non_deterministic_types).

    Opens the DB read-only via URI. Collects all deterministic event types
    within the 7-day seed window.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cutoff = _seed_cutoff()
    try:
        chronicle = _extract_chronicle_events(conn, cutoff)
        opportunities = _extract_opportunity_events(conn, cutoff)
        shadows = _extract_shadow_signals(conn, cutoff)
        decisions = _extract_decision_log(conn, cutoff)
    finally:
        conn.close()

    all_events = chronicle + opportunities + shadows + decisions
    excluded = sorted(NON_DETERMINISTIC_EVENT_TYPES)
    return all_events, excluded


# ---------------------------------------------------------------------------
# Deterministic projection
# ---------------------------------------------------------------------------


def _compute_projection(events: list[dict]) -> dict:
    """Deterministic reduction of the event stream into a projection snapshot.

    This is intentionally simple: counts by (source, event_type), sorted keys,
    plus a SHA-256 content hash of the serialized event list. The hash is the
    primary correctness signal; the counts aid human debugging of mismatches.
    """
    counts: dict[str, int] = {}
    for ev in events:
        key = f"{ev['source']}::{ev['event_type']}"
        counts[key] = counts.get(key, 0) + 1

    # Stable serialisation for hashing: sort keys at every level.
    canonical_json = json.dumps(events, sort_keys=True, ensure_ascii=True)
    content_hash = hashlib.sha256(canonical_json.encode()).hexdigest()

    return {
        "event_count": len(events),
        "counts_by_type": dict(sorted(counts.items())),
        "content_hash": content_hash,
        "seed_window_days": SEED_WINDOW_DAYS,
        "seed_cutoff_utc": _seed_cutoff(),
    }


# ---------------------------------------------------------------------------
# Baseline I/O
# ---------------------------------------------------------------------------


def baseline_path(date_str: str | None = None) -> Path:
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return BASELINE_DIR / f"{date_str}.json"


def load_latest_baseline() -> tuple[Path, dict] | tuple[None, None]:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BASELINE_DIR.glob("*.json"), reverse=True)
    if not files:
        return None, None
    path = files[0]
    return path, json.loads(path.read_text())


def write_baseline(projection: dict, events: list[dict], excluded: list[str]) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    path = baseline_path()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "0.1.0",
        "projection": projection,
        "non_deterministic_excluded": excluded,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    logger.info("baseline written → %s", path)
    return path


# ---------------------------------------------------------------------------
# Ritual signal (CHARTER §3 M1)
# ---------------------------------------------------------------------------


def _task_id(db_path: Path, projection: dict) -> str:
    """Short hash of (db_path, content_hash) — stable per run."""
    raw = f"{db_path}:{projection.get('content_hash', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def emit_ritual_signal(
    *,
    db_path: Path,
    projection: dict,
    outcome: str,
    fit_score: float,
) -> None:
    RITUAL_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    signal_path = RITUAL_SIGNAL_DIR / f"{month_key}.jsonl"

    record = {
        "helper": HELPER_NAME,
        "task_id": _task_id(db_path, projection),
        "fit_score": round(fit_score, 4),
        "advisory_or_blocking": "advisory",
        "outcome": outcome,
        "diff_paths_touched": [str(db_path)],
        "invocation_ts": datetime.now(timezone.utc).isoformat(),
        "charter_version": CHARTER_VERSION,
    }
    with signal_path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    logger.info("ritual_signal emitted → %s (outcome=%s)", signal_path, outcome)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare(projection: dict, baseline: dict) -> tuple[bool, dict]:
    """Return (match: bool, diff: dict)."""
    base_proj = baseline.get("projection", {})
    diff: dict = {}

    if projection["content_hash"] != base_proj.get("content_hash"):
        diff["content_hash"] = {
            "current": projection["content_hash"],
            "baseline": base_proj.get("content_hash"),
        }

    if projection["event_count"] != base_proj.get("event_count"):
        diff["event_count"] = {
            "current": projection["event_count"],
            "baseline": base_proj.get("event_count"),
        }

    # Report per-type count deltas for debugging
    current_counts = projection.get("counts_by_type", {})
    baseline_counts = base_proj.get("counts_by_type", {})
    all_keys = set(current_counts) | set(baseline_counts)
    count_deltas = {
        k: {"current": current_counts.get(k, 0), "baseline": baseline_counts.get(k, 0)}
        for k in all_keys
        if current_counts.get(k, 0) != baseline_counts.get(k, 0)
    }
    if count_deltas:
        diff["count_deltas"] = count_deltas

    return len(diff) == 0, diff


# ---------------------------------------------------------------------------
# DB copy for seeded testing
# ---------------------------------------------------------------------------


def copy_db_readonly_temp(db_path: Path) -> Path:
    """Copy DB to a tempfile for mutation in tests (gate itself never mutates)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    shutil.copy2(db_path, tmp.name)
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay-correctness gate (Phase 0.G scaffold)"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="Path to zeus_trades.db (read-only)",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Write a fresh baseline; do not compare",
    )
    parser.add_argument(
        "--baseline-date",
        default=None,
        help="Compare against this date's baseline (YYYY-MM-DD); default = latest",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print projection JSON to stdout",
    )
    args = parser.parse_args(argv)

    db_path: Path = args.db.resolve()
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 2

    logger.info("extracting seed window (last %d days) from %s", SEED_WINDOW_DAYS, db_path)
    events, excluded = extract_seed_events(db_path)
    logger.info(
        "seed window: %d deterministic events; %d non-deterministic types excluded",
        len(events),
        len(excluded),
    )

    projection = _compute_projection(events)
    logger.info("projection hash: %s", projection["content_hash"])

    if args.verbose:
        print(json.dumps(projection, indent=2))

    if args.bootstrap:
        path = write_baseline(projection, events, excluded)
        print(json.dumps({"status": "baseline_written", "path": str(path)}, indent=2))
        emit_ritual_signal(
            db_path=db_path,
            projection=projection,
            outcome="applied",
            fit_score=1.0,
        )
        return 0

    # Comparison mode
    if args.baseline_date:
        baseline_file = baseline_path(args.baseline_date)
        if not baseline_file.exists():
            logger.error("baseline not found: %s", baseline_file)
            return 2
        baseline = json.loads(baseline_file.read_text())
    else:
        baseline_file, baseline = load_latest_baseline()
        if baseline is None:
            logger.error(
                "no baseline found in %s — run with --bootstrap first", BASELINE_DIR
            )
            return 2

    logger.info("comparing against baseline: %s", baseline_file)
    matched, diff = compare(projection, baseline)

    result = {
        "status": "match" if matched else "mismatch",
        "baseline_file": str(baseline_file),
        "projection": projection,
        "diff": diff,
        "excluded_non_deterministic": excluded,
    }
    print(json.dumps(result, indent=2))

    emit_ritual_signal(
        db_path=db_path,
        projection=projection,
        outcome="applied" if matched else "blocked",
        fit_score=1.0,
    )

    if not matched:
        logger.error("MISMATCH: replay projection differs from baseline")
        logger.error("diff: %s", json.dumps(diff))
        return 1

    logger.info("OK: replay projection matches baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
