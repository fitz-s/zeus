# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md §"Required object model" + docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T1

"""tail_correlation_cluster_for — weather-system cluster ID for shoulder exposure aggregation.

Design intent (plan §2 T1, dossier §7.5):
  Maps (city, regime, target_date) → cluster ID string used by ShoulderExposureLedger
  (T3) for same-direction shoulder sell prevention under correlated weather systems.

  Cluster ID grammar (from §7.5 example): "heat_dome_east_2026_07_15"
  Format: "{regime}_{region}_{YYYY}_{MM}_{DD}"

  When regime is UNKNOWN, cluster ID is empty string ("") — no aggregation:
  plan §5 R-1 antibody: UNKNOWN regime does not aggregate into any cluster.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from src.contracts.weather_regime_tag import WeatherRegimeTag


def tail_correlation_cluster_for(
    city: str,
    regime: "WeatherRegimeTag",
    target_date: "date",
) -> str:
    """Map (city, regime, target_date) to a weather-system cluster ID.

    Used by ShoulderExposureLedger (T3) and cluster cap enforcement to group
    cities under the same weather system for same-direction shoulder sell
    prevention per dossier §7.5.

    Cluster ID grammar: "{regime}_{region}_{YYYY}_{MM}_{DD}"
    Example: "heat_dome_east_2026_07_15"

    Returns empty string ("") when regime is UNKNOWN — UNKNOWN regime does not
    aggregate into any cluster (plan §5 R-1, invariant: test_inv_unknown_regime_does_not_aggregate_cluster).

    Args:
        city:        City name (canonical Zeus city string).
        regime:      WeatherRegimeTag from regime_tag_for().
        target_date: Settlement date of the shoulder market.

    Returns:
        Cluster ID string, or "" when regime is UNKNOWN.

    Raises:
        NotImplementedError: T1 production logic pending SCAFFOLD critic PASS.
    """
    raise NotImplementedError("T1 production pending SCAFFOLD critic PASS")
