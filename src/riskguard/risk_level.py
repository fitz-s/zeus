"""Risk levels and graduated response. Spec §7.3."""

from enum import Enum


class RiskLevel(Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


# Actions per level
LEVEL_ACTIONS = {
    RiskLevel.GREEN: "Normal operation",
    RiskLevel.YELLOW: "No new entries, continue monitoring held positions",
    RiskLevel.ORANGE: "No new entries, exit positions at favorable prices",
    RiskLevel.RED: "Cancel all pending orders, exit all positions immediately",
}


def overall_level(*levels: RiskLevel) -> RiskLevel:
    """Compute overall risk level as max of all individual levels."""
    if not levels:
        return RiskLevel.GREEN
    order = {RiskLevel.GREEN: 0, RiskLevel.YELLOW: 1,
             RiskLevel.ORANGE: 2, RiskLevel.RED: 3}
    worst = max(levels, key=lambda l: order[l])
    return worst
