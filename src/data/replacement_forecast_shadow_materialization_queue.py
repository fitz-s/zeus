"""Compatibility import for the renamed live replacement materialization queue.

The implementation is live code and now lives in
``src.data.replacement_forecast_live_materialization_queue``. This module exists
only so older tests and one-off tooling fail gradually instead of reviving a
shadow execution surface.
"""

from src.data.replacement_forecast_live_materialization_queue import *  # noqa: F401,F403
from src.data.replacement_forecast_live_materialization_queue import (
    ReplacementForecastLiveMaterializationQueueReport,
    _process_replacement_forecast_live_materialization_queue_locked,
    process_replacement_forecast_live_materialization_queue,
)

ReplacementForecastShadowMaterializationQueueReport = (
    ReplacementForecastLiveMaterializationQueueReport
)
process_replacement_forecast_shadow_materialization_queue = (
    process_replacement_forecast_live_materialization_queue
)
_process_replacement_forecast_shadow_materialization_queue_locked = (
    _process_replacement_forecast_live_materialization_queue_locked
)
