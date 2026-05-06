# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""World-view typed accessor layer — read-only API over zeus-world.db.

All functions take an explicit world_conn opened with get_world_connection()
(or in tests, an in-memory fixture). No ATTACH DATABASE. No module-level
connection singletons.

Trading lane callers must open world_conn with mode=ro URI when calling
from the trading process. The caller is responsible for connection lifecycle.

Public API:
    get_latest_observation(world_conn, city, target_date) -> ObservationView | None
    get_settlement_truth(world_conn, city, target_date) -> SettlementView | None
    get_active_platt_model(world_conn, city, season, metric_identity, *, cycle=None, source_id=None, horizon_profile=None) -> PlattModelView | None
        # cycle/source_id/horizon_profile are OPTIONAL (PR #65 Copilot follow-up
        # 2026-05-06). Default None preserves backward-compat for legacy callers
        # that pre-date Phase 2 stratification; callers that have the keys
        # SHOULD pass them so load_platt_model_v2 returns the cycle-matched
        # bucket rather than the schema-default 00z TIGGE Platt.
    get_latest_forecast(world_conn, city, target_date, lead_days) -> ForecastView | None
"""

from src.contracts.world_view.observations import ObservationView, get_latest_observation
from src.contracts.world_view.settlements import SettlementView, get_settlement_truth
from src.contracts.world_view.calibration import PlattModelView, get_active_platt_model
from src.contracts.world_view.forecasts import ForecastView, get_latest_forecast

__all__ = [
    "ObservationView",
    "get_latest_observation",
    "SettlementView",
    "get_settlement_truth",
    "PlattModelView",
    "get_active_platt_model",
    "ForecastView",
    "get_latest_forecast",
]
