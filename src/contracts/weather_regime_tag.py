# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md §"Required object model" + docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T1

"""WeatherRegimeTag — 6-member StrEnum classifying weather regime context for shoulder strategy.

Design intent (§2 T1, dossier §7.5):
  - HEAT_DOME / COLD_SNAP / NORMAL / SHOULDER_SEASON / SOURCE_ANOMALY / UNKNOWN
  - 6-member minimal taxonomy; operator-extensible via PROMOTION_PLAYBOOK post-T1.
  - regime_tag_for(): fail-open to UNKNOWN when observation history is insufficient
    for HEAT_DOME/COLD_SNAP classification (G2: no silent default).

Usage:
    tag = regime_tag_for(city, target_date, decision_time, conn)
    # Returns WeatherRegimeTag.UNKNOWN when evidence is thin; never silently
    # returns a strong regime label without sufficient observation history.
"""

from __future__ import annotations

from enum import auto
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from datetime import date, datetime


class WeatherRegimeTag(StrEnum):
    """Six-member weather-regime taxonomy for shoulder strategy context.

    Verbatim from authority 04_PHASE_3_SHOULDER.md §"Required object model":
        tail_regime_tag: WeatherRegimeTag  # heat_dome / cold_snap / normal /
                                           # shoulder_season / anomaly / unknown

    Members (6 per plan §2 T1 + P-3-2 verifier probe):
        HEAT_DOME        — persistent high-pressure blocking event; correlated upper-shoulder risk
        COLD_SNAP        — sustained cold-air outbreak; correlated lower-shoulder risk
        NORMAL           — observations within climatological norms; standard Kelly applies
        SHOULDER_SEASON  — transitional period (spring/autumn); intermediate regime uncertainty
        SOURCE_ANOMALY   — sensor spike or station reporting anomaly detected (Paris 2026 class)
        UNKNOWN          — insufficient observation history to classify; classifier fail-open value
    """

    HEAT_DOME = auto()
    COLD_SNAP = auto()
    NORMAL = auto()
    SHOULDER_SEASON = auto()
    SOURCE_ANOMALY = auto()
    UNKNOWN = auto()


def regime_tag_for(
    city: str,
    target_date: "date",
    decision_time: "datetime",
    conn: "sqlite3.Connection",
) -> WeatherRegimeTag:
    """Rule-based classifier: read observation history + forecast median → WeatherRegimeTag.

    Returns WeatherRegimeTag.UNKNOWN when observation history is insufficient
    for HEAT_DOME or COLD_SNAP classification (G2 invariant — fail-open, never
    silently default to NORMAL or any strong regime label).

    Args:
        city:          City name (canonical Zeus city string).
        target_date:   The settlement date of the shoulder market.
        decision_time: Wall-clock time of the decision (Chicago local).
        conn:          Read-only sqlite3 connection to zeus-world.db.

    Returns:
        WeatherRegimeTag member appropriate to the observation + forecast context.

    Raises:
        NotImplementedError: T1 production logic pending SCAFFOLD critic PASS.
    """
    raise NotImplementedError("T1 production pending SCAFFOLD critic PASS")
