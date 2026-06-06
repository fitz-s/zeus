# Created: 2026-05-22
# Last reused or audited: 2026-06-03
# Authority basis: PROMOTION_PIPELINE_DESIGN.md §4 (Track L-2) + §2 (EvidenceReport contract)
#                  + INV-37 (ATTACH+SAVEPOINT for cross-DB writes)
#                  + STRUCTURAL_FIX_PLAN_2026-06-03 §P0.4 (N2 — CLI repointed off
#                    dead decision_events onto live edli_no_submit_receipts; H1)
"""Track L-2: Settlement-attribution cron job.

Offline job (never imported by live daemon paths).  Matches
``WORLD.decision_events(source='shadow_decision', outcome='shadow_enter')``
to ``FCST.settlement_outcomes`` by
  (market_slug, target_date, temperature_metric)
and writes the corresponding ``shadow_experiments`` + ``regret_decompositions``
rows to WORLD so that EvidenceReport gains settled-outcome evidence (n_settled,
n_wins, CI).

Design constraints
------------------
- Offline only: zero import of cycle_runtime / evaluator live paths.
- Read-only on FCST settlement_outcomes (ATTACH-only; never opened writable).
- Idempotent: rows in regret_decompositions already keyed to a
  decision_event_id are skipped — never double-written.
- INV-37: cross-DB read (FCST) + write (WORLD) uses WORLD-as-main with
  FCST ATTACHed.  All world writes execute under a SAVEPOINT per-batch.
- Regret allocation (v1 thin): forecast_error_usd = total_regret_usd;
  all other 6 components = 0.0.  Sum invariant verified by decompose_regret.
- Sign convention POSITIVE=WIN: realized_pnl − counterfactual_pnl.
  counterfactual = 0 (no-trade = 0 reference) so total_regret = realized_pnl.
- POST-WRITE smoke: COUNT(*)>0 on WORLD targets (shadow_experiments,
  regret_decompositions) after any attribution run that writes ≥1 row.

CLI
---
  python -m src.cron.settlement_attribution [--allow-empty]

The CLI drives the RECEIPT path (``run_receipt_attribution`` over
``edli_no_submit_receipts``), not the legacy ``decision_events`` join
(``run_attribution``, kept only for the v1 regression suite). An empty join
RAISES ``AttributionInputEmptyError`` unless ``--allow-empty`` is passed.

Entry point for cron use:
  python -m src.cron.settlement_attribution
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win/loss computation
# ---------------------------------------------------------------------------

def compute_realized_pnl(
    side: str,
    winning_bin: Optional[str],
    target_price: Optional[float],
    target_size_usd: Optional[float],
) -> Optional[float]:
    """Compute realized PnL for a shadow decision given settlement outcome.

    Polymarket binary markets: a YES position worth ``target_size_usd`` notional
    at ``target_price`` cents-per-dollar settles to $1 per share if win or $0 per
    share if loss.

    PnL = (settle_price - entry_price) * shares
        where shares = target_size_usd / entry_price  (constant-notional entry)
        settle_price = 1.0 if winning_bin matches side, else 0.0

    Returns None when inputs are insufficient to compute (entry_price=None or
    target_size_usd=None → mark as un-attributable, row skipped).

    Sign convention POSITIVE=WIN (realized > counterfactual).
    counterfactual_pnl = 0.0 (no-trade reference).
    """
    if target_price is None or target_size_usd is None:
        return None
    if target_price <= 0.0 or target_price >= 1.0:
        # Degenerate price: can't compute shares; skip
        return None
    if winning_bin is None:
        return None

    # Normalise side to token side: "YES" / "NO"
    # Shadow candidates write 'buy_yes' / 'buy_no'; strip the 'buy_' prefix so
    # the comparison below works for both live ('YES'/'NO') and shadow rows.
    _side_raw = (side or "").upper()
    side_upper = _side_raw.removeprefix("BUY_")  # 'BUY_YES' → 'YES', 'YES' → 'YES'

    # winning_bin from settlement_outcomes is the bin-label that won (e.g. "above_67F").
    # For YES tokens the outcome is binary: the token wins if it resolves 1.0.
    # For shadow_decision rows, `side` is the direction of the would-be position.
    # We interpret: WIN iff the side matches the resolution direction.
    # The minimal interpretation for v1: a shadow 'YES' side wins iff the market
    # resolved YES (winning_bin IS NOT NULL and outcome recorded); a 'NO' side wins
    # iff the market resolved NO (i.e. winning_bin is the NO outcome).
    # We use winning_bin IS NOT NULL as "market settled" and the `side` field
    # as the position direction.  The resolution mapping is:
    #   side='YES' → win when winning_bin does NOT start with 'no_' prefix (YES won)
    #   side='NO'  → win when winning_bin starts with 'no_' (NO won)
    # This is a thin v1 approximation; callers should refine once market_events
    # range labels are wired.

    # For Track L-2 v1 we use a simpler convention: the settlement_value field
    # in settlement_outcomes gives the actual recorded temperature.  winning_bin is the
    # text label for the winner.  We do not have the bin-to-direction mapping here,
    # so we use a direct outcome field if available, else treat winning_bin IS NOT NULL
    # as "resolved" and use the side to determine win/loss.

    # v1 thin: treat `side` == 'YES' as a long YES position.
    # A YES token settles to 1.0 on win, 0.0 on loss.
    # winning_bin IS NOT NULL → market settled.
    # We cannot reliably infer YES/NO resolution from winning_bin alone without
    # range-label mapping, so for v1 we record outcome based on a naming convention:
    #   winning_bin ending in '_yes' or side-tag: treat as YES win.
    # Without a reliable mapping, v1 uses a POSITIVE=WIN direction:
    #   For 'YES' positions: settled_payoff = 1.0 if we assume market went in our favour.
    # Since we cannot know from winning_bin alone which direction won without the full
    # market_events join, we implement the minimal correct v1:
    #   - If winning_bin is None → skip (market not yet settled)
    #   - Realised payoff for YES: 1.0 if winning_bin does NOT contain 'no' as first word
    #   - Realised payoff for NO: 1.0 if winning_bin starts with 'no'

    # Binary settlement: shares = notional / entry_price
    shares = target_size_usd / target_price

    # Determine settled payoff (0 or 1 per share)
    wbin_lower = winning_bin.lower().strip()
    if side_upper == "YES":
        # YES position wins if the "yes" side won.  In Polymarket weather bins the
        # YES token represents "above threshold"; winning_bin for a YES win does not
        # start with "below" or "no_".  For NO-token markets the winning_bin would be
        # "below_..." or the named bin.  Without full range-label join we approximate:
        # YES wins when winning_bin does NOT start with 'no' or 'below'.
        yes_won = not (wbin_lower.startswith("no_") or wbin_lower.startswith("below"))
        settled_payoff = 1.0 if yes_won else 0.0
    else:
        # NO position wins when the NO outcome prevailed.
        no_won = wbin_lower.startswith("no_") or wbin_lower.startswith("below")
        settled_payoff = 1.0 if no_won else 0.0

    realized_pnl = (settled_payoff - target_price) * shares
    return realized_pnl


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
# Core attribution logic
# ---------------------------------------------------------------------------

def _already_attributed(decision_event_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if a regret_decompositions row already exists for this deid."""
    row = conn.execute(
        "SELECT 1 FROM regret_decompositions WHERE decision_event_id = ? LIMIT 1",
        (decision_event_id,),
    ).fetchone()
    return row is not None


def run_attribution(
    *,
    world_conn: sqlite3.Connection,
    strategy_key: Optional[str] = None,
    dry_run: bool = False,
    cohort_tag: str = "l2_settlement_attribution_v1",
    now_utc: Optional[datetime] = None,
) -> dict:
    """Attribute shadow decisions to settlement outcomes.

    Reads ``WORLD.decision_events`` + ``forecasts.settlement_outcomes`` (ATTACHed).
    Writes to ``WORLD.shadow_experiments`` + ``WORLD.regret_decompositions``.

    Parameters
    ----------
    world_conn:
        WORLD DB connection with ``forecasts`` DB already ATTACHed (caller
        responsibility — use ``open_world_with_forecasts()`` context manager).
    strategy_key:
        Optional filter; if None, processes all shadow_decision rows.
    dry_run:
        When True, compute but do not write.
    cohort_tag:
        Tag applied to registered shadow_experiments row(s).
    now_utc:
        Attribution timestamp override (default: utcnow).

    Returns
    -------
    dict with keys: attributed, skipped_already_attributed, skipped_no_settlement,
        skipped_bad_price, experiments_registered, world_rows_written.
    """
    from src.analysis.regret_decomposer import decompose_regret, write_regret_decomposition
    from src.state.shadow_experiment_registry import register_shadow_experiment

    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    stats: dict = {
        "attributed": 0,
        "skipped_already_attributed": 0,
        "skipped_no_settlement": 0,
        "skipped_bad_price": 0,
        "experiments_registered": 0,
        "world_rows_written": 0,
    }

    # Per-strategy experiment registry (lazily created per strategy encountered)
    _experiment_ids: dict[str, str] = {}

    def _get_or_register_experiment(sk: str) -> str:
        if sk not in _experiment_ids:
            config = {"strategy_key": sk, "attribution_version": "l2_v1"}
            exp_id = register_shadow_experiment(
                strategy_id=sk,
                config=config,
                cohort_tag=cohort_tag,
                started_at=now_utc,
                conn=world_conn,
            )
            _experiment_ids[sk] = exp_id
            stats["experiments_registered"] += 1
            logger.info("Registered experiment %s for strategy %s", exp_id[:12], sk)
        return _experiment_ids[sk]

    # Query: shadow_decision rows with outcome='shadow_enter', joined to settlement_outcomes.
    # settlement_outcomes lives in the ATTACHed 'forecasts' schema.
    strategy_filter = ""
    params: list = []
    if strategy_key is not None:
        strategy_filter = " AND de.strategy_key = ?"
        params.append(strategy_key)

    query = f"""
        SELECT
            de.market_slug,
            de.temperature_metric,
            de.target_date,
            de.observation_time,
            de.decision_seq,
            de.decision_event_id,
            de.strategy_key,
            de.side,
            de.target_price,
            de.target_size_usd,
            sv.winning_bin,
            sv.settlement_value
        FROM decision_events de
        LEFT JOIN forecasts.settlement_outcomes sv
            ON  sv.market_slug = de.market_slug
            AND sv.target_date  = de.target_date
            AND sv.temperature_metric = de.temperature_metric
        WHERE de.source = 'shadow_decision'
          AND de.outcome = 'shadow_enter'
          {strategy_filter}
        ORDER BY de.target_date, de.market_slug, de.decision_seq
    """
    rows = world_conn.execute(query, params).fetchall()
    logger.info("Attribution query: %d candidate rows", len(rows))

    for row in rows:
        (
            market_slug, temperature_metric, target_date,
            observation_time, decision_seq,
            decision_event_id, sk, side,
            target_price, target_size_usd,
            winning_bin, settlement_value,
        ) = row

        # 1. Skip if already attributed (idempotent gate)
        if _already_attributed(decision_event_id, world_conn):
            stats["skipped_already_attributed"] += 1
            continue

        # 2. Skip if market not yet settled
        if winning_bin is None:
            stats["skipped_no_settlement"] += 1
            continue

        # 3. Compute realized PnL.
        # Shadow candidates write target_size_usd=None (no real capital at risk).
        # Use a $1 notional for research PnL so shadow rows are not silently skipped.
        # The resulting PnL is per-dollar: comparable across strategies regardless of
        # hypothetical sizing; not a real cash figure.
        effective_size_usd = target_size_usd if target_size_usd is not None else 1.0
        realized_pnl = compute_realized_pnl(
            side=side,
            winning_bin=winning_bin,
            target_price=target_price,
            target_size_usd=effective_size_usd,
        )
        if realized_pnl is None:
            stats["skipped_bad_price"] += 1
            logger.debug(
                "skip bad_price: %s %s %s side=%s price=%s size=%s",
                market_slug, target_date, temperature_metric,
                side, target_price, target_size_usd,
            )
            continue

        if dry_run:
            stats["attributed"] += 1
            logger.debug(
                "dry_run: would attribute %s %s %s → pnl=%.4f",
                market_slug, target_date, side, realized_pnl,
            )
            continue

        # 4. Write shadow_experiment + regret_decomposition under SAVEPOINT
        exp_id = _get_or_register_experiment(sk)

        # v1 thin allocation: all realized advantage attributed to forecast_error
        # (timing catch-all residual = 0).  verify_sum enforced by decompose_regret.
        components = decompose_regret(
            forecast_error_usd=realized_pnl,   # v1: full allocation to forecast error
            realized_pnl_usd=realized_pnl,
            counterfactual_pnl_usd=0.0,        # counterfactual = no-trade = 0
        )

        savepoint = f"l2_attr_{decision_event_id[:16].replace('-', '_')}"
        try:
            world_conn.execute(f"SAVEPOINT {savepoint}")
            write_regret_decomposition(
                experiment_id=exp_id,
                decision_event_id=decision_event_id,
                components=components,
                conn=world_conn,
                computed_at=now_utc,
            )
            world_conn.execute(f"RELEASE {savepoint}")
        except Exception:
            world_conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            logger.exception(
                "Attribution SAVEPOINT failed for deid=%s; rolled back",
                decision_event_id,
            )
            continue

        stats["attributed"] += 1
        stats["world_rows_written"] += 1

    # Post-write smoke
    if stats["world_rows_written"] > 0:
        rd_count = world_conn.execute(
            "SELECT COUNT(*) FROM regret_decompositions"
        ).fetchone()[0]
        se_count = world_conn.execute(
            "SELECT COUNT(*) FROM shadow_experiments"
        ).fetchone()[0]
        if rd_count == 0:
            raise AssertionError(
                "Post-write smoke FAILED: regret_decompositions COUNT=0 after attribution writes"
            )
        if se_count == 0:
            raise AssertionError(
                "Post-write smoke FAILED: shadow_experiments COUNT=0 after attribution writes"
            )
        logger.info(
            "Post-write smoke PASS: regret_decompositions=%d, shadow_experiments=%d",
            rd_count, se_count,
        )

    logger.info("Attribution complete: %s", stats)
    return stats


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
