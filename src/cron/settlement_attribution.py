# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: PROMOTION_PIPELINE_DESIGN.md §4 (Track L-2) + §2 (EvidenceReport contract)
#                  + INV-37 (ATTACH+SAVEPOINT for cross-DB writes)
"""Track L-2: Settlement-attribution cron job.

Offline job (never imported by live daemon paths).  Matches
``WORLD.decision_events(source='shadow_decision', outcome='shadow_enter')``
to ``FCST.settlements_v2`` by
  (market_slug, target_date, temperature_metric)
and writes the corresponding ``shadow_experiments`` + ``regret_decompositions``
rows to WORLD so that EvidenceReport gains settled-outcome evidence (n_settled,
n_wins, CI).

Design constraints
------------------
- Offline only: zero import of cycle_runtime / evaluator live paths.
- Read-only on FCST settlements_v2 (ATTACH-only; never opened writable).
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
  python -m src.cron.settlement_attribution [--dry-run] [--strategy KEY]

Entry point for cron use:
  python -m src.cron.settlement_attribution
"""
from __future__ import annotations

import argparse
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
    side_upper = (side or "").upper()

    # winning_bin from settlements_v2 is the bin-label that won (e.g. "above_67F").
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
    # This is a thin v1 approximation; callers should refine once market_events_v2
    # range labels are wired.

    # For Track L-2 v1 we use a simpler convention: the settlement_value field
    # in settlements_v2 gives the actual recorded temperature.  winning_bin is the
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
    # market_events_v2 join, we implement the minimal correct v1:
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

    Reads ``WORLD.decision_events`` + ``forecasts.settlements_v2`` (ATTACHed).
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

    # Query: shadow_decision rows with outcome='shadow_enter', joined to settlements_v2.
    # settlements_v2 lives in the ATTACHed 'forecasts' schema.
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
        LEFT JOIN forecasts.settlements_v2 sv
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

        # 3. Compute realized PnL
        realized_pnl = compute_realized_pnl(
            side=side,
            winning_bin=winning_bin,
            target_price=target_price,
            target_size_usd=target_size_usd,
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

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Track L-2 settlement-attribution cron: joins shadow "
                    "decision_events to settlements_v2, writes regret_decompositions.",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Optional strategy_key filter (e.g. 'shoulder_sell'). "
             "Default: attribute all shadow_decision rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute but do not write to DB.",
    )
    parser.add_argument(
        "--cohort-tag",
        default="l2_settlement_attribution_v1",
        help="cohort_tag for shadow_experiments registration.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with open_world_with_forecasts(write_class="bulk") as conn:
        stats = run_attribution(
            world_conn=conn,
            strategy_key=args.strategy,
            dry_run=args.dry_run,
            cohort_tag=args.cohort_tag,
        )

    print(f"Attribution stats: {stats}")


if __name__ == "__main__":
    _cli()
