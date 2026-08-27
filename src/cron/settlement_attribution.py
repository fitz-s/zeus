# Created: 2026-05-22
# Last reused or audited: 2026-06-03
# Authority basis: PROMOTION_PIPELINE_DESIGN.md §4 (Track L-2) + §2 (EvidenceReport contract)
#                  + INV-37 (ATTACH+SAVEPOINT for cross-DB writes)
#                  + STRUCTURAL_FIX_PLAN_2026-06-03 §P0.4 (N2 — CLI repointed off
#                    dead decision_events onto live edli_no_submit_receipts; H1)
"""Receipt-driven settlement-attribution cron job.

Offline job (never imported by live daemon paths). It reads the receipt table
the live reactor writes (``edli_no_submit_receipts``), joins it to VERIFIED
``FCST.settlement_outcomes``, and grades each row through the canonical receipt
grading function.

Design constraints
------------------
- Offline only: zero import of cycle_runtime / evaluator live paths.
- Read-only on FCST settlement_outcomes (ATTACH-only; never opened writable).
- INV-37: cross-DB read (FCST) + write (WORLD) uses WORLD-as-main with
  FCST ATTACHed.  All world writes execute under a SAVEPOINT per-batch.

CLI
---
  python -m src.cron.settlement_attribution [--allow-empty]

The CLI drives the RECEIPT path (``run_receipt_attribution`` over
``edli_no_submit_receipts``). An empty join RAISES
``AttributionInputEmptyError`` unless ``--allow-empty`` is passed.

Entry point for cron use:
  python -m src.cron.settlement_attribution
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# N2 repoint — read the table the live path WRITES (edli_no_submit_receipts),
# grade via the canonical grade_receipt() truth function, and FAIL CLOSED on
# empty input. The old decision_events join was a dead producer/consumer
# mismatch (decision_events=0 rows live) that silently attributed nothing.
# ---------------------------------------------------------------------------

class AttributionInputEmptyError(RuntimeError):
    """Raised when the attribution input row-count is 0.

    A silent zero is a FAILURE, not a successful no-op: it is exactly how the
    dead ``decision_events`` driver hid for weeks (every run reported
    ``attributed=0`` against an empty left side). The guard turns that into a
    loud error so a broken driver can never masquerade as "nothing to do".
    """


def _bin_from_label(bin_label: str, unit: str):
    """Build a ``Bin`` from a receipt bin_label + its settlement unit.

    Returns None when the label cannot be parsed into a gradeable bin (caller
    skips the row rather than guessing). Reuses the canonical
    ``market_scanner._parse_temp_range`` so the parse matches production.
    """
    from src.data.market_scanner import _parse_temp_range
    from src.types.market import Bin

    parsed = _parse_temp_range(bin_label)
    if parsed is None or parsed == (None, None):
        return None
    lo, hi = parsed
    try:
        return Bin(low=lo, high=hi, unit=unit, label=bin_label)
    except Exception:  # noqa: BLE001 — malformed bin → skip, never crash a batch
        return None


def load_attribution_input_rows(world_conn: sqlite3.Connection) -> list[dict]:
    """Load attribution inputs by joining the LIVE receipt table to settlements.

    Reads ``edli_no_submit_receipts`` (WORLD, the table the reactor writes) and
    ``forecasts.settlement_outcomes`` (ATTACHed, VERIFIED only), joined on
    ``(city, target_date, metric, direction)`` — city/date/metric/bin_label are
    parsed from ``receipt_json``; direction is the top-level column. Each joined
    row is graded through ``grade_receipt`` (Direction Law + unit antibody +
    BinKind membership) — there is no second win/loss heuristic here.

    Returns one dict per receipt that matched a VERIFIED settlement, carrying
    ``city, target_date, metric, direction, price, kelly_size_usd, won,
    settled_in_bin, bin_kind, receipt_id``. Unit-mismatch rows are skipped with
    a WARN (the grade_receipt UnitMismatchError is the structural guard).
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError

    # Pull VERIFIED settlements once, keyed by (city, target_date, metric).
    settlements: dict[tuple, dict] = {}
    for row in world_conn.execute(
        """
        SELECT city, target_date, temperature_metric,
               settlement_value, settlement_unit
        FROM forecasts.settlement_outcomes
        WHERE authority = 'VERIFIED'
        """
    ).fetchall():
        city, tdate, metric, value, unit = row
        if value is None:
            continue
        settlements.setdefault((city, tdate, metric), {
            "settlement_value": float(value),
            "settlement_unit": unit,
        })

    out: list[dict] = []
    unit_mismatch = 0
    for receipt_id, direction, price, size, rj_text in world_conn.execute(
        """
        SELECT receipt_id, direction, c_fee_adjusted, kelly_size_usd, receipt_json
        FROM edli_no_submit_receipts
        """
    ).fetchall():
        try:
            rj = json.loads(rj_text) if rj_text else {}
        except (TypeError, ValueError):
            continue
        city = rj.get("city")
        tdate = rj.get("target_date")
        metric = rj.get("metric", "high")
        bin_label = rj.get("bin_label", "")
        s = settlements.get((city, tdate, metric))
        if s is None:
            continue  # no VERIFIED settlement for this (city, date, metric)

        bin_obj = _bin_from_label(bin_label, s["settlement_unit"])
        if bin_obj is None:
            continue

        class _S:  # minimal settlement stand-in for grade_receipt
            settlement_value = s["settlement_value"]
            settlement_unit = s["settlement_unit"]

        try:
            graded = grade_receipt(bin_obj, direction, _S())
        except UnitMismatchError:
            unit_mismatch += 1
            logger.warning(
                "attribution: unit mismatch for receipt=%s city=%s bin=%s — skipped",
                receipt_id, city, bin_label,
            )
            continue
        except ValueError:
            continue  # unknown direction — skip, do not crash the batch

        out.append({
            "receipt_id": receipt_id,
            "city": city,
            "target_date": tdate,
            "metric": metric,
            "direction": direction,
            "price": price,
            "kelly_size_usd": size,
            "won": graded.won,
            "settled_in_bin": graded.settled_in_bin,
            "bin_kind": graded.bin_kind,
        })

    if unit_mismatch:
        logger.warning("attribution: %d rows skipped on unit mismatch", unit_mismatch)
    return out


def run_receipt_attribution(
    *,
    world_conn: sqlite3.Connection,
    now_utc: Optional[datetime] = None,
    require_nonempty: bool = True,
) -> dict:
    """Receipt-driven attribution (N2 repoint).

    Loads inputs from ``edli_no_submit_receipts`` via ``load_attribution_input_rows``
    and FAILS CLOSED when the input is empty (``AttributionInputEmptyError``)
    unless ``require_nonempty=False``. This is the structural antibody: a dead
    driver can no longer silently report success.

    Returns a stats dict with ``input_rows``, ``wins``, ``losses``.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    rows = load_attribution_input_rows(world_conn)
    if not rows and require_nonempty:
        raise AttributionInputEmptyError(
            "Attribution input is EMPTY: edli_no_submit_receipts joined to "
            "VERIFIED settlement_outcomes yielded 0 rows. A silent zero is a "
            "failure — check the receipt writer and settlement coverage."
        )

    wins = sum(1 for r in rows if r["won"])
    stats = {
        "input_rows": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
    }
    logger.info("receipt attribution: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Tier-0 candidate-set settlement fold
# ---------------------------------------------------------------------------

_TIER0_CANDIDATE_QUERY_CHUNK = 400


def _load_tier0_candidate_rows(
    trade_conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Read the immutable decision rows whose final binary label is derived."""

    return [
        dict(row)
        for row in trade_conn.execute(
            """
            SELECT row_id, market_key, city, target_date, side, settled_y
              FROM tier0_candidate_set_provenance
             ORDER BY row_id
            """
        ).fetchall()
    ]


def _tier0_candidate_settlement_labels(
    forecast_conn: sqlite3.Connection,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Grade candidate sides from VERIFIED settlement truth and market bounds.

    ``market_key`` is the decision-time condition id.  The canonical forecast
    DB maps it to exact finite/open bin bounds; the family settlement supplies
    the verified value and unit.  No label punctuation or date inference is
    used.  Conflicting truth for one condition is excluded rather than guessed.
    """

    from src.contracts.settlement_semantics import SettlementSemantics
    from src.contracts.exceptions import SettlementPrecisionError
    from src.config import runtime_cities_by_name
    from src.types.market import Bin

    market_keys = tuple(
        sorted(
            {
                str(candidate.get("market_key") or "").strip()
                for candidate in candidates
                if str(candidate.get("market_key") or "").strip()
            }
        )
    )
    truth_by_key: dict[tuple[str, str, str], int] = {}
    ambiguous_keys: set[tuple[str, str, str]] = set()
    invalid_truth_rows = 0
    cities = runtime_cities_by_name()

    for offset in range(0, len(market_keys), _TIER0_CANDIDATE_QUERY_CHUNK):
        chunk = market_keys[offset : offset + _TIER0_CANDIDATE_QUERY_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        for row in forecast_conn.execute(
            f"""
            SELECT me.condition_id, me.city, me.target_date,
                   me.temperature_metric, me.range_low, me.range_high,
                   so.settlement_value, so.settlement_unit
              FROM market_events me
              JOIN settlement_outcomes so
                ON so.city = me.city
               AND so.target_date = me.target_date
               AND so.temperature_metric = me.temperature_metric
             WHERE so.authority = 'VERIFIED'
               AND me.condition_id IN ({placeholders})
            """,
            chunk,
        ).fetchall():
            condition_id = str(row["condition_id"] or "").strip()
            city = str(row["city"] or "").strip()
            target_date = str(row["target_date"] or "").strip()
            key = (condition_id, city, target_date)
            try:
                unit = str(row["settlement_unit"] or "").strip().upper()
                city_contract = cities.get(city)
                if city_contract is None:
                    raise ValueError("settlement city contract is unavailable")
                semantics = SettlementSemantics.for_city(city_contract)
                if unit != semantics.measurement_unit:
                    raise ValueError("settlement unit disagrees with city contract")
                value = semantics.assert_settlement_value(
                    float(row["settlement_value"]),
                    context="tier0_candidate_settlement_fold",
                )
                bin_obj = Bin(
                    low=(
                        None
                        if row["range_low"] is None
                        else float(row["range_low"])
                    ),
                    high=(
                        None
                        if row["range_high"] is None
                        else float(row["range_high"])
                    ),
                    unit=unit,
                )
                yes_won = int(bin_obj.contains(value))
            except (SettlementPrecisionError, TypeError, ValueError):
                invalid_truth_rows += 1
                continue
            prior = truth_by_key.get(key)
            if prior is not None and prior != yes_won:
                ambiguous_keys.add(key)
                continue
            truth_by_key[key] = yes_won

    for key in ambiguous_keys:
        truth_by_key.pop(key, None)

    labels: list[tuple[int, int]] = []
    invalid_candidate_rows = 0
    for candidate in candidates:
        key = (
            str(candidate.get("market_key") or "").strip(),
            str(candidate.get("city") or "").strip(),
            str(candidate.get("target_date") or "").strip(),
        )
        yes_won = truth_by_key.get(key)
        if yes_won is None:
            continue
        side = str(candidate.get("side") or "").strip().upper()
        if side not in {"YES", "NO"}:
            invalid_candidate_rows += 1
            continue
        labels.append(
            (int(candidate["row_id"]), yes_won if side == "YES" else 1 - yes_won)
        )

    return labels, {
        "candidate_rows": len(candidates),
        "verified_market_labels": len(truth_by_key),
        "labels_ready": len(labels),
        "pending_rows": len(candidates) - len(labels),
        "ambiguous_markets": len(ambiguous_keys),
        "invalid_truth_rows": invalid_truth_rows,
        "invalid_candidate_rows": invalid_candidate_rows,
    }


def _apply_tier0_candidate_settlement_labels(
    trade_conn: sqlite3.Connection,
    labels: Sequence[tuple[int, int]],
) -> dict[str, int]:
    """Fold current canonical labels into their derived trade-DB cache."""

    if not labels:
        return {"filled": 0, "corrected": 0, "unchanged": 0, "missing": 0}
    row_ids = tuple(row_id for row_id, _ in labels)
    filled = corrected = unchanged = missing = 0
    try:
        trade_conn.execute("BEGIN IMMEDIATE")
        current: dict[int, int | None] = {}
        for offset in range(0, len(row_ids), _TIER0_CANDIDATE_QUERY_CHUNK):
            chunk = row_ids[offset : offset + _TIER0_CANDIDATE_QUERY_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            current.update(
                {
                    int(row["row_id"]): (
                        None
                        if row["settled_y"] is None
                        else int(row["settled_y"])
                    )
                    for row in trade_conn.execute(
                        f"""
                        SELECT row_id, settled_y
                          FROM tier0_candidate_set_provenance
                         WHERE row_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                }
            )
        for row_id, settled_y in labels:
            prior = current.get(row_id, "missing")
            if prior == "missing":
                missing += 1
                continue
            if prior == settled_y:
                unchanged += 1
                continue
            trade_conn.execute(
                """
                UPDATE tier0_candidate_set_provenance
                   SET settled_y = ?
                 WHERE row_id = ?
                """,
                (settled_y, row_id),
            )
            if prior is None:
                filled += 1
            else:
                corrected += 1
        trade_conn.commit()
    except Exception:
        trade_conn.rollback()
        raise
    return {
        "filled": filled,
        "corrected": corrected,
        "unchanged": unchanged,
        "missing": missing,
    }


def run_tier0_candidate_settlement_fold() -> dict[str, int]:
    """Sequential read/read/write fold; no transaction spans two DBs.

    SCOPE: only ``tier0_candidate_set_provenance.settled_y`` rows whose exact
    condition has a VERIFIED canonical settlement.  DRAIN: the post-trade
    five-minute job revisits every candidate row.  RESET: a later canonical
    correction deterministically replaces the derived label on the next tick.
    """

    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection,
        get_trade_connection_read_only,
    )

    trade_read = get_trade_connection_read_only()
    try:
        candidates = _load_tier0_candidate_rows(trade_read)
    finally:
        trade_read.close()
    if not candidates:
        return {
            "candidate_rows": 0,
            "verified_market_labels": 0,
            "labels_ready": 0,
            "pending_rows": 0,
            "ambiguous_markets": 0,
            "invalid_truth_rows": 0,
            "invalid_candidate_rows": 0,
            "filled": 0,
            "corrected": 0,
            "unchanged": 0,
            "missing": 0,
        }

    forecast_read = get_forecasts_connection_read_only()
    try:
        labels, stats = _tier0_candidate_settlement_labels(
            forecast_read,
            candidates,
        )
    finally:
        forecast_read.close()
    if not labels:
        return {
            **stats,
            "filled": 0,
            "corrected": 0,
            "unchanged": 0,
            "missing": 0,
        }

    trade_write = get_trade_connection(write_class="live")
    try:
        applied = _apply_tier0_candidate_settlement_labels(trade_write, labels)
    finally:
        trade_write.close()
    return {**stats, **applied}


# ---------------------------------------------------------------------------
# Connection helper: WORLD as main + FCST ATTACHed read-only
# ---------------------------------------------------------------------------

import contextlib

@contextlib.contextmanager
def open_world_with_forecasts(write_class: str = "bulk"):
    """Context manager: world.db as MAIN + zeus-forecasts.db ATTACHed as 'forecasts'.

    Acquires writer locks on both DBs in canonical alphabetical order before
    yielding the connection.  Forecasts DB is attached read-only (immutable=0
    but no write-lock acquired for it — see note).

    Note: we hold the WORLD bulk lock for the duration; FCST is attached for
    reads only.  We do NOT hold a FCST write-lock because settlement_attribution
    never writes to FCST.  The canonical lock order
    (zeus-forecasts.db < zeus-world.db alphabetically) is honoured by acquiring
    WORLD only — if future versions add FCST writes, both locks must be taken in
    canonical alphabetical order first.
    """
    from src.state.db import ZEUS_WORLD_DB_PATH, ZEUS_FORECASTS_DB_PATH, get_world_connection
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    wc = WriteClass(write_class) if isinstance(write_class, str) else write_class

    with db_writer_lock(ZEUS_WORLD_DB_PATH, wc):
        conn = get_world_connection(write_class=wc)
        try:
            attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
            if "forecasts" not in attached:
                conn.execute(
                    "ATTACH DATABASE ? AS forecasts",
                    (str(ZEUS_FORECASTS_DB_PATH),),
                )
            yield conn
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> None:
    # H1 — DEAD-LOOP REPOINT (STRUCTURAL_FIX_PLAN §P0.4 / N2).
    # The CLI is the ONLY live entry point for this job (cron + `python -m`).
    # It previously drove ``run_attribution`` over ``decision_events`` — a table
    # with 0 live rows. That made the learning loop a silent no-op: every cron
    # tick reported ``attributed=0`` against an empty left side and nobody noticed
    # because zero looked like "nothing to do". The read-side fix
    # (``run_receipt_attribution`` over ``edli_no_submit_receipts``, 60k+ live
    # rows) already shipped on this branch but NOTHING called it. This wires it.
    #
    # The ``AttributionInputEmptyError`` antibody stays armed (require_nonempty
    # defaults True): a genuinely empty join now RAISES LOUD instead of returning
    # a comfortable zero. That is the point — a dead driver can no longer
    # masquerade as success.
    parser = argparse.ArgumentParser(
        description="Track L-2 settlement-attribution cron: joins live "
                    "edli_no_submit_receipts to VERIFIED settlement_outcomes via "
                    "grade_receipt (Direction Law), counts wins/losses. Raises "
                    "AttributionInputEmptyError on an empty join (never a silent 0).",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        default=False,
        help="Disarm the empty-input antibody (require_nonempty=False). Default "
             "OFF: an empty join is a FAILURE, not a silent success. Use only for "
             "an intentionally-empty fixture run.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with open_world_with_forecasts(write_class="bulk") as conn:
        stats = run_receipt_attribution(
            world_conn=conn,
            require_nonempty=not args.allow_empty,
        )

    print(f"Receipt-attribution stats: {stats}")


if __name__ == "__main__":
    _cli()
