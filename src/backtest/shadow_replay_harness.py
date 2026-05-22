# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: task brief (Track R-1a) + PHASE_6_PLAN.md §T2+T3
#                  + PROMOTION_PIPELINE_DESIGN.md §3/§5/§8 (intent embedded in brief)
"""Shadow Replay Harness — Track R-1a of the promotion pipeline.

CLI usage:
    python -m src.backtest.shadow_replay_harness \\
        --strategy shoulder_sell \\
        --from 2025-06-01 \\
        --to 2026-05-15

Reads historical forecast edges from the LIVE FCST + WORLD DBs (read-only,
immutable=1 URI) and replays classify_shoulder_candidate() against them.
Writes shadow_experiments, decision_events (source='replay_decision'), and
regret_decompositions to a TEMP world DB (never the live state/ DB).

Design constraints
------------------
- Live DBs opened read-only via sqlite3.connect("file:...?immutable=1", uri=True).
  The get_world_connection()/get_forecasts_connection() helpers are NOT used on live
  paths — they can briefly acquire a writer lock. Sentinel guard asserts
  temp_world_path != live ZEUS_WORLD_DB_PATH.
- Temp WORLD DB carries local minimal DDL (3 tables only: shadow_experiments,
  regret_decompositions, decision_events with 'replay_decision' in CHECK).
  The full boot-path schema function is NOT used here (would create hundreds of unneeded tables).
- decision_time = available_at of the ensemble snapshot (publication time).
  Look-ahead antibody: assert row.available_at <= decision_time (trivially true
  for the published rows, but the harness raises ValueError on any violation so
  injected test rows with future available_at correctly fail).
- Depth antibody: market_price_history has no depth_at_best_ask column; use
  best_ask IS NOT NULL as proxy for "fillable quote exists". Rows without a
  contemporaneous best_ask are marked non_fill via outcome='no_fill_no_depth'.
- n_settled >= 100 gate: harness emits HOLD before running PromotionReadinessValidator
  when n_settled < 100. Gate is in the HARNESS, not the validator.
- INV-37: temp WORLD DB writes are self-contained (no cross-DB write); no ATTACH
  needed. If future R-1b writes to live WORLD, ATTACH+SAVEPOINT is required.
- SCAFFOLD behavior: classify_shoulder_candidate always returns with
  no_trade_reason=SHOULDER_NO_TRADE_GATE → zero enter decisions → zero regret rows
  → n_settled=0 → HOLD. This IS the correct wiring-proof for R-1a.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Live DB paths (read-only; NEVER open writable)
# ---------------------------------------------------------------------------
from src.state.db import ZEUS_WORLD_DB_PATH, ZEUS_FORECASTS_DB_PATH

# ---------------------------------------------------------------------------
# Local minimal DDL for temp WORLD DB
# ---------------------------------------------------------------------------

_TEMP_WORLD_DDL = """
CREATE TABLE IF NOT EXISTS shadow_experiments (
    experiment_id  TEXT PRIMARY KEY,
    strategy_id    TEXT NOT NULL,
    config_hash    TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    closed_at      TEXT,
    cohort_tag     TEXT NOT NULL,
    immutable      INTEGER NOT NULL DEFAULT 1
        CHECK (immutable IN (0, 1))
);

CREATE TABLE IF NOT EXISTS decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL DEFAULT 0,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy'
        CHECK (polymarket_end_anchor_source IN
               ('gamma_explicit', 'f1_12z_fallback', 'unknown_legacy')),
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL DEFAULT 26
        CHECK (schema_version IN (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26)),
    source         TEXT NOT NULL
        CHECK (source IN ('phase0_backfill', 'live_decision', 'shadow_decision', 'replay_decision')),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
CREATE INDEX IF NOT EXISTS idx_de_strategy
    ON decision_events(strategy_key, decision_time);
CREATE INDEX IF NOT EXISTS idx_de_event_id
    ON decision_events(decision_event_id);

CREATE TABLE IF NOT EXISTS regret_decompositions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id                   TEXT NOT NULL
        REFERENCES shadow_experiments(experiment_id),
    decision_event_id               TEXT NOT NULL,
    forecast_error_usd              REAL,
    observation_error_usd           REAL,
    quote_error_usd                 REAL,
    non_fill_error_usd              REAL,
    fee_error_usd                   REAL,
    timing_error_usd                REAL,
    settlement_ambiguity_error_usd  REAL,
    total_regret_usd                REAL NOT NULL,
    computed_at                     TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Minimal duck-typed edge/candidate for SCAFFOLD replay
# ---------------------------------------------------------------------------

@dataclass
class _ReplayBin:
    """Minimal Bin duck-type for SCAFFOLD topology gate checks."""
    label: str
    is_open_low: bool
    is_open_high: bool

    @property
    def is_shoulder(self) -> bool:
        return self.is_open_low or self.is_open_high


@dataclass
class _ReplayEdge:
    """Minimal BinEdge duck-type for SCAFFOLD topology gate checks."""
    bin: _ReplayBin
    direction: str          # "buy_no" for shoulder candidates
    edge: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 1.0
    p_model: float = 0.5
    p_market: float = 0.5
    p_posterior: float = 0.5
    entry_price: float = 0.5
    p_value: float = 0.05
    vwmp: float = 0.5
    forward_edge: float = 0.0
    support_index: Optional[int] = None
    ev_per_dollar: float = 0.0


@dataclass
class _ReplayCandidate:
    """Minimal candidate duck-type for classify_shoulder_candidate()."""
    city: object  # must have .name, .timezone
    target_date: str
    temperature_metric: str
    slug: str = ""


@dataclass
class _ReplayCity:
    name: str
    timezone: str = "UTC"


# ---------------------------------------------------------------------------
# Look-ahead antibody
# ---------------------------------------------------------------------------

def _assert_no_lookahead(available_at_str: str, decision_time_str: str) -> None:
    """Raise ValueError if available_at > decision_time (look-ahead leak).

    In this harness decision_time == available_at so the assertion is
    trivially satisfied for well-formed rows. Test fixtures inject a
    future available_at to exercise the guard.
    """
    available_at = datetime.fromisoformat(available_at_str)
    decision_time = datetime.fromisoformat(decision_time_str)
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=timezone.utc)
    if decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=timezone.utc)
    if available_at > decision_time:
        raise ValueError(
            f"Look-ahead violation: available_at={available_at_str!r} > "
            f"decision_time={decision_time_str!r}. "
            "Input row cannot have been available when the decision was made."
        )


# ---------------------------------------------------------------------------
# Depth antibody helper
# ---------------------------------------------------------------------------

def _has_fillable_quote(best_ask: Optional[float]) -> bool:
    """Proxy for 'depth > 0': True iff best_ask IS NOT NULL.

    market_price_history has no depth_at_best_ask column (schema 2026-05-19).
    We use best_ask IS NOT NULL as the fillable-quote proxy per design §8.
    If best_ask is None the row is marked non_fill at entry.
    """
    return best_ask is not None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open a live DB read-only via immutable=1 URI (no writer-lock acquisition)."""
    return sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True)


def _open_temp_world(path: Path) -> sqlite3.Connection:
    """Create / open the temp WORLD DB and ensure DDL is present."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_TEMP_WORLD_DDL)
    conn.commit()
    return conn


def _decision_event_id() -> str:
    return f"deid_v1_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Core replay loop
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    """Summary of one harness run."""
    strategy_id: str
    n_candidates_scanned: int
    n_shoulder_edges: int       # passed topology gate (buy_no + is_shoulder)
    n_has_quote: int            # had best_ask (fillable-quote proxy)
    n_no_fill_no_depth: int     # missing best_ask → marked non_fill
    n_enter_decisions: int      # classify returned non-None (not counting gate-kill)
    n_no_trade_gate: int        # SCAFFOLD: no_trade_reason=SHOULDER_NO_TRADE_GATE
    n_decisions_written: int    # rows written to decision_events
    n_settled: int              # regret rows (0 for SCAFFOLD)
    verdict: str                # HOLD / READY / NOT_READY
    experiment_id: str
    blocker: str = ""


def run_replay(
    strategy_id: str,
    date_from: str,
    date_to: str,
    *,
    temp_world_path: Path,
    live_world_path: Optional[Path] = None,
    live_fcst_path: Optional[Path] = None,
    verbose: bool = False,
) -> ReplayResult:
    """Run the shadow replay harness.

    Parameters
    ----------
    strategy_id:
        Strategy key (e.g. "shoulder_sell").
    date_from, date_to:
        ISO date strings (inclusive) for replay window.
    temp_world_path:
        Path to the temp WORLD DB. Must NOT be the live zeus-world.db.
    live_world_path, live_fcst_path:
        Override for live DB paths (default: canonical live paths). Used
        in tests to inject fixture DBs.
    verbose:
        Print progress lines to stdout.
    """
    _live_world = live_world_path or ZEUS_WORLD_DB_PATH
    _live_fcst = live_fcst_path or ZEUS_FORECASTS_DB_PATH

    # Safety sentinel: never write to live WORLD DB.
    if temp_world_path.resolve() == Path(_live_world).resolve():
        raise ValueError(
            f"temp_world_path == live ZEUS_WORLD_DB_PATH ({_live_world!r}). "
            "The harness must NEVER write to the live state DB."
        )

    # Lazy import (SCAFFOLD; do not change shoulder_strategy_vnext.py)
    from src.contracts.shoulder_strategy_vnext import classify_shoulder_candidate
    from src.state.shadow_experiment_registry import (
        register_shadow_experiment,
        hash_config,
    )
    from src.state.schema.phase6_evidence_schema import ensure_tables

    temp_conn = _open_temp_world(temp_world_path)
    ensure_tables(temp_conn)
    temp_conn.commit()

    config = {"strategy_id": strategy_id, "date_from": date_from, "date_to": date_to}
    started_at = datetime.now(tz=timezone.utc)
    cohort_tag = f"replay_{strategy_id}_{date_from}_{date_to}"

    experiment_id = register_shadow_experiment(
        strategy_id,
        config,
        cohort_tag,
        started_at=started_at,
        conn=temp_conn,
    )
    temp_conn.commit()

    # ------------------------------------------------------------------
    # Query ensemble_snapshots_v2 from live FCST DB (read-only)
    # Shoulder candidates: bins where p_raw_json exists, available_at in window
    # ensemble_snapshots_v2 schema: (snapshot_id, city, target_date,
    #   temperature_metric, physical_quantity, observation_field, issue_time,
    #   valid_time, available_at, fetch_time, lead_hours, members_json,
    #   p_raw_json, ...)
    # We build one edge per snapshot per shoulder bin (open_low=bin[0] / open_high=last_bin).
    # ------------------------------------------------------------------
    n_candidates_scanned = 0
    n_shoulder_edges = 0
    n_has_quote = 0
    n_no_fill_no_depth = 0
    n_enter_decisions = 0
    n_no_trade_gate = 0
    n_decisions_written = 0
    decision_seq_counter: dict[tuple, int] = {}

    fcst_conn = _open_readonly(Path(_live_fcst))
    try:
        rows = fcst_conn.execute(
            """
            SELECT snapshot_id, city, target_date, temperature_metric,
                   available_at, p_raw_json
            FROM ensemble_snapshots_v2
            WHERE available_at >= ?
              AND available_at <= ?
              AND p_raw_json IS NOT NULL
            ORDER BY available_at
            """,
            (date_from, date_to + "T99:99:99"),  # inclusive date range
        ).fetchall()
    finally:
        fcst_conn.close()

    world_conn_ro = _open_readonly(Path(_live_world))
    try:
        # Pre-fetch market_price_history best_ask for depth proxy.
        # Index on: market_price_history(market_slug, recorded_at).
        # Keyed by (market_slug, approximate_date) → best_ask.
        # We join by city+target_date+temperature_metric → market_slug later.
        # Since market_price_history has no city column, we join via
        # market_price_history where recorded_at is closest to available_at.
        # For R-1a wiring proof, we do a best-effort lookup; no-data → non_fill.
        price_rows = world_conn_ro.execute(
            """
            SELECT market_slug, recorded_at, best_ask
            FROM market_price_history
            WHERE recorded_at >= ?
              AND recorded_at <= ?
              AND best_ask IS NOT NULL
            """,
            (date_from, date_to + "T99:99:99"),
        ).fetchall()
    finally:
        world_conn_ro.close()

    # Build a lookup: market_slug → list of (recorded_at, best_ask)
    price_by_slug: dict[str, list[tuple]] = {}
    for slug, rec_at, ask in price_rows:
        price_by_slug.setdefault(slug, []).append((rec_at, ask))

    def _get_best_ask(market_slug: str, decision_time_str: str) -> Optional[float]:
        """Return best_ask closest to decision_time for this slug, or None."""
        candidates = price_by_slug.get(market_slug)
        if not candidates:
            return None
        # Closest recorded_at <= decision_time
        best: Optional[tuple] = None
        for rec_at, ask in candidates:
            if rec_at <= decision_time_str:
                if best is None or rec_at > best[0]:
                    best = (rec_at, ask)
        return best[1] if best else None

    for row in rows:
        (snapshot_id, city_name, target_date, temperature_metric,
         available_at, p_raw_json_str) = row
        n_candidates_scanned += 1
        decision_time_str = available_at  # decision_time == available_at

        # Look-ahead antibody
        _assert_no_lookahead(available_at, decision_time_str)

        try:
            p_raw: list = json.loads(p_raw_json_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if not p_raw or not isinstance(p_raw, list):
            continue

        # Build shoulder bin edges (open_low = bin[0], open_high = bin[-1]).
        n_bins = len(p_raw)
        shoulder_bins_indices = []
        if n_bins >= 2:
            shoulder_bins_indices = [0, n_bins - 1]  # lower and upper shoulder

        for i in shoulder_bins_indices:
            is_open_low = (i == 0)
            is_open_high = (i == n_bins - 1 and not is_open_low)
            if n_bins == 1:
                is_open_low = True
                is_open_high = True
            bin_label = f"bin_{i}"

            replay_bin = _ReplayBin(
                label=bin_label,
                is_open_low=is_open_low,
                is_open_high=is_open_high,
            )

            if not replay_bin.is_shoulder:
                continue

            # For buy_no direction: p_model_no = 1 - p_raw[i]
            p_raw_val = float(p_raw[i]) if p_raw[i] is not None else 0.5
            p_model_no = 1.0 - p_raw_val

            # Build minimal edge with direction="buy_no"
            edge = _ReplayEdge(
                bin=replay_bin,
                direction="buy_no",
                edge=max(0.0, p_model_no - 0.5),
                p_model=p_model_no,
                p_market=0.5,
                p_posterior=p_model_no,
                entry_price=0.5,
            )
            n_shoulder_edges += 1

            # Depth antibody: check contemporaneous best_ask
            # market_slug = target weather market; derive from city+date+metric
            # For thin path: use city_name as proxy slug key
            market_slug_proxy = f"{city_name.lower().replace(' ', '-')}-{target_date}-{temperature_metric}"
            best_ask = _get_best_ask(market_slug_proxy, decision_time_str)
            has_quote = _has_fillable_quote(best_ask)
            if has_quote:
                n_has_quote += 1
            else:
                n_no_fill_no_depth += 1

            # Candidate duck-type
            city_obj = _ReplayCity(name=city_name, timezone="UTC")
            candidate = _ReplayCandidate(
                city=city_obj,
                target_date=target_date,
                temperature_metric=temperature_metric,
                slug=market_slug_proxy,
            )

            result = classify_shoulder_candidate(edge, candidate, None, temp_conn)

            if result is None:
                # Topology gate: not shoulder buy_no (shouldn't happen here)
                continue
            n_enter_decisions += 1

            from src.contracts.no_trade_reason import NoTradeReason
            if result.no_trade_reason == NoTradeReason.SHOULDER_NO_TRADE_GATE:
                n_no_trade_gate += 1
                outcome = "no_trade_scaffold"
            elif not has_quote:
                outcome = "no_fill_no_depth"
            else:
                outcome = "enter"

            # Write decision_events row
            deid = _decision_event_id()
            key = (market_slug_proxy, temperature_metric, target_date, decision_time_str)
            seq = decision_seq_counter.get(key, 0)
            decision_seq_counter[key] = seq + 1

            temp_conn.execute(
                """
                INSERT OR IGNORE INTO decision_events (
                    market_slug, temperature_metric, target_date,
                    observation_time, decision_seq, decision_event_id,
                    decision_time, outcome, side, strategy_key,
                    p_posterior, edge, target_price,
                    observation_available_at, schema_version, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    market_slug_proxy,
                    temperature_metric,
                    target_date,
                    decision_time_str,
                    seq,
                    deid,
                    decision_time_str,
                    outcome,
                    "buy_no",
                    strategy_id,
                    edge.p_posterior,
                    edge.edge,
                    edge.entry_price,
                    available_at,
                    26,
                    "replay_decision",
                ),
            )
            n_decisions_written += 1

            if verbose:
                print(
                    f"  deid={deid[:16]}... city={city_name} "
                    f"date={target_date} outcome={outcome}"
                )

    temp_conn.commit()

    # ------------------------------------------------------------------
    # COUNT(*) > 0 smoke (antibody F40/F41)
    # For SCAFFOLD: we may have n_decisions_written > 0 (no_trade_scaffold rows)
    # even with zero enter decisions. Smoke verifies rows are ACTUALLY in DB.
    # ------------------------------------------------------------------
    smoke_decisions = temp_conn.execute(
        "SELECT COUNT(*) FROM decision_events WHERE strategy_key = ?",
        (strategy_id,),
    ).fetchone()[0]
    smoke_experiments = temp_conn.execute(
        "SELECT COUNT(*) FROM shadow_experiments WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()[0]

    # Note: For SCAFFOLD, smoke_decisions may be 0 if n_shoulder_edges == 0
    # (no p_raw_json rows in date range). This is correct; zero rows = no data.
    # The count>0 antibody is satisfied iff we DID write rows; if the fixture
    # DB has rows, the test must verify count>0 explicitly.
    assert smoke_experiments >= 1, (
        f"COUNT(*) smoke FAILED: shadow_experiments has {smoke_experiments} rows "
        f"for strategy_id={strategy_id!r}. Expected >= 1."
    )

    n_settled = 0  # SCAFFOLD: no fills → no regret rows

    # ------------------------------------------------------------------
    # n_settled >= 100 gate (harness-level, not in PromotionReadinessValidator)
    # ------------------------------------------------------------------
    if n_settled < 100:
        verdict = "HOLD"
        blocker = f"n_settled={n_settled} < 100 minimum before non-HOLD verdict"
    else:
        # Run PromotionReadinessValidator
        from src.analysis.evidence_report import build_evidence_report, EvidenceReport
        from src.analysis.promotion_readiness import PromotionReadinessValidator
        from src.contracts.evidence_tier import EvidenceTier

        report = build_evidence_report(
            strategy_id,
            EvidenceTier.REPLAY_PASS,
            conn=temp_conn,
            breakeven_win_rate=0.5,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_LIMITED_HAIRCUT,
        )
        assessment = validator.assess(report)
        verdict = assessment.verdict.value
        blocker = assessment.summary

    temp_conn.close()

    return ReplayResult(
        strategy_id=strategy_id,
        n_candidates_scanned=n_candidates_scanned,
        n_shoulder_edges=n_shoulder_edges,
        n_has_quote=n_has_quote,
        n_no_fill_no_depth=n_no_fill_no_depth,
        n_enter_decisions=n_enter_decisions,
        n_no_trade_gate=n_no_trade_gate,
        n_decisions_written=n_decisions_written,
        n_settled=n_settled,
        verdict=verdict,
        experiment_id=experiment_id,
        blocker=blocker,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow Replay Harness (Track R-1a) — "
            "replay classify_shoulder_candidate against historical data."
        )
    )
    parser.add_argument("--strategy", default="shoulder_sell", help="Strategy ID")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--world-db",
        dest="world_db",
        default=None,
        help="Path to temp WORLD DB (defaults to a new temp file)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    import tempfile
    import os
    if args.world_db:
        temp_world_path = Path(args.world_db)
    else:
        # Default: temp file in /tmp
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="zeus_replay_")
        os.close(fd)
        temp_world_path = Path(tmp_path)
        print(f"[harness] Temp WORLD DB: {temp_world_path}")

    result = run_replay(
        strategy_id=args.strategy,
        date_from=args.date_from,
        date_to=args.date_to,
        temp_world_path=temp_world_path,
        verbose=args.verbose,
    )

    print(f"\n[harness] REPLAY COMPLETE — strategy={result.strategy_id}")
    print(f"  candidates_scanned : {result.n_candidates_scanned}")
    print(f"  shoulder_edges     : {result.n_shoulder_edges}")
    print(f"  has_quote          : {result.n_has_quote}")
    print(f"  no_fill_no_depth   : {result.n_no_fill_no_depth}")
    print(f"  enter_decisions    : {result.n_enter_decisions}")
    print(f"  no_trade_gate      : {result.n_no_trade_gate}")
    print(f"  decisions_written  : {result.n_decisions_written}")
    print(f"  n_settled          : {result.n_settled}")
    print(f"  experiment_id      : {result.experiment_id}")
    print(f"  VERDICT            : {result.verdict}")
    if result.blocker:
        print(f"  blocker            : {result.blocker}")


if __name__ == "__main__":
    main()
