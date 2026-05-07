"""Strategy attribution surface — K1-compliant read-only projection.

Historical note: this module used to maintain a persistent JSON file
(`strategy_tracker-{mode}.json`) populated by record_entry / record_exit /
record_settlement callbacks fired from the runtime. That design violated
K1 (derived surfaces must not have write authority): the tracker was a
second, out-of-band ledger that drifted from position_events across
bankroll resets, producing phantom PnL totals (e.g. opening_inertia
showed +$210.68 while position_events said -$13.03). It also fed the
`strategy_edge_compression_alerts` signal that in turn drove RiskGuard's
strategy_signal_level — so "not authority" was a lie in practice.

Post-K1 contract:

1. StrategyTracker has NO write path. record_* methods are retained as
   no-op shims for backward compatibility so existing callers in
   cycle_runtime.py, fill_tracker.py, harvester.py, and event hooks do not
   break. They log at debug level only.
2. summary() queries the canonical settlement rows via
   query_authoritative_settlement_rows (which reads from position_events
   through _normalize_position_settlement_event). Results are deduped by
   trade_id so a duplicate SETTLED event (pre-bug-#9-fix data) cannot
   double-count PnL.
3. edge_compression_check() returns an empty list. Edge compression is
   a real signal that should be recomputed from a canonical event-log
   time-series, not from the tracker's persisted trades. Re-enablement
   is deferred to a follow-up packet that wires it through the
   settlement_fact / decision_fact tables.
4. save_tracker() is a no-op. It does not touch the disk. If a legacy
   `strategy_tracker-*.json` file still exists on disk, the nuke runbook
   deletes it; the new code never regenerates one.
5. load_tracker() returns a fresh empty StrategyTracker — there is no
   state to load. The disk file (if present) is ignored.

Callers MUST call summary() to read values. They MUST NOT look at
tracker.strategies[*].trades directly — that list is permanently empty.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.config import STATE_DIR, state_path

logger = logging.getLogger(__name__)

STRATEGIES = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]
TRACKER_PATH = state_path("strategy_tracker.json")
_TRACKER_SINGLETON: "StrategyTracker | None" = None

# Kept as module-level constants for backward compat with tests that import
# them. The thresholds themselves are unused now that edge_compression_check
# returns an empty list.
EDGE_COMPRESSION_MIN_TRADES = 20
EDGE_COMPRESSION_MIN_SPAN_DAYS = 3.0


def _default_accounting() -> dict[str, Any]:
    """Metadata describing the tracker's authority role.

    Kept for backward compat with callers that read this dict from
    status_summary. The new contract: authority_mode is always
    'canonical_projection', not 'non_authority_compatibility'.
    """
    status_path = state_path("status_summary.json")
    return {
        "accounting_scope": "canonical_projection",
        "performance_headline_authority": str(status_path),
        "tracker_role": "canonical_projection",
        "authority_mode": "projection_from_position_events",
        "includes_legacy_history": False,
        "current_regime_started_at": "",
        "history_archive_path": "",
        "projection_source": "position_events via query_authoritative_settlement_rows",
    }


def _normalized_accounting(accounting: dict[str, Any] | None = None) -> dict[str, Any]:
    # Accept a dict for backward compat but ignore its contents — the new
    # accounting metadata is fully derived, not user-supplied.
    return _default_accounting()


class StrategyMetrics:
    """Legacy shim. Post-K1 this is a dummy container.

    The old implementation kept a `.trades: list[dict]` list populated by
    record_trade and scanned it for PnL / edge-trend computation. That list
    is now permanently empty because record_trade is a no-op. Any caller
    that reads `.trades` directly will see [] and must migrate to
    StrategyTracker.summary(conn).
    """

    def __init__(self) -> None:
        self.trades: list[dict] = []

    def record(self, trade: dict) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyMetrics.record is a no-op post-K1; ignored")

    def cumulative_pnl(self) -> float:
        return 0.0

    def edge_trend(self, window_days: int = 30) -> float:
        return 0.0

    def recent_edge_points(self, window_days: int = 30) -> list:
        return []

    def count(self) -> int:
        return 0

    def to_dict(self) -> dict:
        return {"trades": []}

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyMetrics":  # backward compat
        return cls()


class StrategyTracker:
    """Canonical-projection read surface. No internal mutable state.

    All write methods (record_trade, record_entry, record_exit,
    record_settlement, record_chronicle_event) are no-op shims that
    log at debug level and return immediately. They exist only so
    that existing call sites in cycle_runtime.py / fill_tracker.py /
    harvester.py do not need to be touched in the same commit as K1.
    A follow-up packet can delete those call sites entirely.

    Reads (summary, edge_compression_check) derive from position_events
    on demand.
    """

    def __init__(self) -> None:
        self.strategies: dict[str, StrategyMetrics] = {
            s: StrategyMetrics() for s in STRATEGIES
        }
        self.accounting: dict[str, Any] = _default_accounting()

    # -- write path (all no-ops post-K1) -------------------------------------

    def record_trade(self, trade: dict) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.record_trade is a no-op post-K1; position_events is the ledger")

    def record_entry(self, position: Any) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.record_entry is a no-op post-K1")

    def record_exit(self, position: Any) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.record_exit is a no-op post-K1")

    def record_settlement(self, position: Any) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.record_settlement is a no-op post-K1")

    def record_chronicle_event(self, event_type: str, details: dict) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.record_chronicle_event is a no-op post-K1")

    # -- read path (projection from position_events) -------------------------

    def summary(self, conn=None) -> dict:
        """Project per-strategy settled counts + PnL from position_events.

        Reads via query_authoritative_settlement_rows which already
        normalizes both canonical and legacy settlement row sources.
        Deduped by trade_id so a duplicate SETTLED event (pre-bug-#9-fix
        data) cannot inflate the count or pnl bucket.

        conn: optional open DB connection. If None, a fresh trade DB
        connection for the current mode is opened and closed internally.
        """
        blank = {name: {"trades": 0, "pnl": 0.0} for name in STRATEGIES}

        # Lazy imports break the src.state.strategy_tracker →
        # src.state.db circular dependency.
        try:
            from src.state.db import (
                get_trade_connection,
                query_authoritative_settlement_rows,
            )
        except Exception as exc:
            logger.warning("strategy_tracker.summary import failed: %s", exc)
            return blank

        close_conn = False
        if conn is None:
            try:
                conn = get_trade_connection()
                close_conn = True
            except Exception as exc:
                logger.warning("strategy_tracker.summary could not open trade conn: %s", exc)
                return blank

        try:
            try:
                rows = query_authoritative_settlement_rows(conn, limit=None)
            except Exception as exc:
                logger.warning("strategy_tracker.summary settlement query failed: %s", exc)
                return blank

            seen_trade_ids: set[str] = set()
            for row in rows:
                if not row.get("metric_ready", False):
                    continue
                trade_id = str(row.get("trade_id") or "")
                if not trade_id or trade_id in seen_trade_ids:
                    continue
                seen_trade_ids.add(trade_id)
                strategy = str(row.get("strategy") or "")
                if strategy not in blank:
                    continue
                blank[strategy]["trades"] += 1
                pnl = row.get("pnl")
                if pnl is not None:
                    try:
                        blank[strategy]["pnl"] += float(pnl)
                    except (TypeError, ValueError):
                        pass
            for bucket in blank.values():
                bucket["pnl"] = round(bucket["pnl"], 2)
            return blank
        finally:
            if close_conn and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def edge_compression_check(self, window_days: int = 30) -> list[str]:
        """Edge compression detection is disabled post-K1.

        The previous implementation read tracker.strategies[*].trades (now
        permanently empty) and ran a linear regression over per-trade edge
        values. A proper event-log-backed replacement must source edge
        values from decision_fact / opportunity_fact, not from the
        in-memory tracker. Until that packet lands, this returns [].

        Empty list preserves the riskguard.py:654 code path:
        strategy_signal_level stays GREEN unless other signals fire.
        """
        return []

    def set_accounting_metadata(
        self,
        *,
        current_regime_started_at: str = "",
        includes_legacy_history: bool = False,
        history_archive_path: str = "",
    ) -> None:  # pragma: no cover - deprecated no-op
        logger.debug("StrategyTracker.set_accounting_metadata is a no-op post-K1")

    def to_dict(self) -> dict:
        return {
            "strategies": {name: {"trades": []} for name in STRATEGIES},
            "accounting": dict(self.accounting),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyTracker":
        # Back-compat constructor. The data argument is ignored — we return
        # a fresh empty tracker regardless of whatever the JSON said.
        return cls()


def load_tracker(path: Optional[Path] = None) -> StrategyTracker:
    """Return a fresh empty tracker. The disk file is intentionally ignored.

    The legacy disk format stored per-trade lists that drifted from the
    canonical event log. Post-K1 we never read them; the nuke runbook
    deletes the file entirely. A file that lingers on disk is harmless
    — it is simply not consulted.
    """
    if path and path.exists():
        logger.debug(
            "strategy_tracker.load_tracker ignoring legacy file at %s (K1: no disk authority)",
            path,
        )
    return StrategyTracker()


def save_tracker(tracker: StrategyTracker, path: Optional[Path] = None) -> None:
    """No-op. The tracker has no state to save.

    Retained as a callable so existing call sites do not crash. Callers
    should migrate to calling nothing (there is nothing to persist), but
    this shim absorbs the call harmlessly in the meantime.
    """
    logger.debug("strategy_tracker.save_tracker is a no-op post-K1; position_events is the ledger")


def get_tracker() -> StrategyTracker:
    global _TRACKER_SINGLETON
    if _TRACKER_SINGLETON is None:
        _TRACKER_SINGLETON = StrategyTracker()
    return _TRACKER_SINGLETON
