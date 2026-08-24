# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6 follow-up (Tier-0 start-equity bookkeeping + live drawdown-kill wiring).
"""Once-per-cycle Tier-0 start-equity seed + drawdown-kill check.

Split out of src/engine/event_reactor_adapter.py (a very large, heavily
concurrently-edited file) into its own module so the call site there is a
single import + one function call, minimizing collision surface with other
in-flight work on that file. Not part of src/strategy/tier0_policy.py's pure
layer: this module does real DB I/O (control_overrides seed read/write,
position_current query) and calls the control plane's pause_entries.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

UTC = timezone.utc

logger = logging.getLogger(__name__)


def tier0_seed_and_check_drawdown_kill(
    trade_conn: sqlite3.Connection,
    *,
    bankroll_usd_provider: Callable[[], float | None] | None,
) -> None:
    """Once per reactor cycle (call once from adapter construction, NOT per
    candidate): seed the durable Tier-0 start-equity baseline the first time
    it is observed missing, then check the cumulative drawdown-kill.

    Storage: config/risk_policy.yaml's tier0.epoch selects a
    control_overrides_history override_id ("tier0:start_equity:epoch:<N>",
    src.strategy.tier0_policy.tier0_start_equity_override_id) in this same
    trade_conn's DB — the repo's existing generic, append-only, restart-safe
    control-plane KV (B070; the SAME mechanism entries_paused already uses).
    See src/strategy/tier0_policy.py's seed-semantics comment for exactly
    when a fresh episode is (and is not) started.

    Realized P&L: recomputed fresh via compute_realized_pnl_usd from each
    closed position's own chain-preferred shares/cost_basis/price (never
    position_current.realized_pnl_usd) for positions whose entry FILL
    (query_portfolio_loader_view's execution_fact_filled_at — the same
    chain/execution-fact-reconciled economics the rest of the money path
    already trusts, not a raw local column) landed at or after the seeded
    started_at. Since Tier-0 admission is the ONLY entry path admitted while
    the flag is on, entry-time >= started_at is a faithful proxy for "this
    position is a Tier-0 position" without needing new per-position tagging.

    Best-effort: any failure here logs and returns rather than raising —
    this is a secondary backstop on top of admission's own fail-closed
    price/mode/cluster/aggregate-ceiling checks, which are unaffected by it.
    """

    try:
        from src.control.control_plane import pause_entries
        from src.state.db import query_portfolio_loader_view, upsert_control_override
        from src.strategy.tier0_policy import (
            Tier0ClosedPositionFacts,
            build_tier0_seed_value,
            check_tier0_drawdown_kill,
            load_tier0_risk_ceilings,
            parse_tier0_seed,
            tier0_realized_pnl_usd,
            tier0_start_equity_override_id,
        )

        ceilings = load_tier0_risk_ceilings()
        override_id = tier0_start_equity_override_id(ceilings["epoch"])
        row = trade_conn.execute(
            "SELECT value FROM control_overrides WHERE override_id = ?",
            (override_id,),
        ).fetchone()
        if row is None:
            from src.engine.event_reactor_adapter import (
                _bankroll_usd_from_provider,
                _runtime_bankroll_usd,
            )

            bankroll_usd = (
                _bankroll_usd_from_provider(bankroll_usd_provider)
                if bankroll_usd_provider is not None
                else _runtime_bankroll_usd(cached_only=True)
            )
            started_at = datetime.now(UTC).isoformat()
            value = build_tier0_seed_value(
                started_at_utc=started_at,
                start_equity_usd=bankroll_usd,
                policy_version=str(ceilings["policy_version"]),
                epoch=ceilings["epoch"],
            )
            upsert_control_override(
                trade_conn,
                override_id=override_id,
                target_type="global",
                target_key="tier0_start_equity",
                action_type="seed",
                value=value,
                issued_by="tier0_policy",
                issued_at=started_at,
                reason="reversal_plan_tier0_2026-08-24_item6_start_equity_seed",
            )
            trade_conn.commit()
            # Freshly seeded this cycle: zero elapsed drawdown, nothing to
            # check yet -- the drawdown-kill check runs from the NEXT cycle.
            return

        seed = parse_tier0_seed(str(row["value"]))
        started_at_dt = datetime.fromisoformat(seed["started_at_utc"])
        if started_at_dt.tzinfo is None:
            started_at_dt = started_at_dt.replace(tzinfo=UTC)

        loaded = query_portfolio_loader_view(trade_conn, open_positions_only=False)
        closed_positions: list[Tier0ClosedPositionFacts] = []
        for position in loaded.get("positions", ()) or ():
            if str(position.get("phase") or "") not in {
                "economically_closed",
                "settled",
                "admin_closed",
            }:
                continue
            filled_at_raw = str(position.get("execution_fact_filled_at") or "").strip()
            if not filled_at_raw:
                continue
            try:
                filled_at_dt = datetime.fromisoformat(filled_at_raw)
            except ValueError:
                continue
            if filled_at_dt.tzinfo is None:
                filled_at_dt = filled_at_dt.replace(tzinfo=UTC)
            if filled_at_dt < started_at_dt:
                continue
            exit_price = position.get("exit_price")
            entry_price = position.get("entry_price")
            closed_positions.append(
                Tier0ClosedPositionFacts(
                    shares=float(position.get("shares") or 0.0),
                    exit_price=float(exit_price) if exit_price is not None else None,
                    cost_basis_usd=float(position.get("cost_basis_usd") or 0.0),
                    entry_price=float(entry_price) if entry_price is not None else None,
                    chain_shares=float(position.get("chain_shares") or 0.0),
                    chain_cost_basis_usd=float(position.get("chain_cost_basis_usd") or 0.0),
                    chain_avg_price=float(position.get("chain_avg_price") or 0.0),
                )
            )
        realized_pnl_usd = tier0_realized_pnl_usd(closed_positions=closed_positions)

        check_tier0_drawdown_kill(
            tier0_start_equity_usd=seed["start_equity_usd"],
            tier0_realized_pnl_usd=realized_pnl_usd,
            drawdown_kill_pct=ceilings["drawdown_kill_pct"],
            pause_fn=lambda reason: pause_entries(reason, issued_by="system_auto_pause"),
        )
    except Exception:
        logger.exception(
            "tier0 start-equity seed / drawdown-kill check failed; admission's "
            "own price/mode/cluster/aggregate-ceiling gates remain the "
            "fail-closed backstop"
        )
