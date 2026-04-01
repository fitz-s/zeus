"""Portfolio risk limits and constraint enforcement. Spec §5.4."""

from dataclasses import dataclass

from src.config import sizing_defaults


@dataclass(frozen=True)
class RiskLimits:
    """Hard caps from settings.json. Spec §5.4."""
    max_single_position_pct: float | None = None
    max_portfolio_heat_pct: float | None = None
    max_correlated_pct: float | None = None
    max_city_pct: float | None = None
    max_region_pct: float | None = None
    min_order_usd: float | None = None

    def __post_init__(self) -> None:
        defaults = sizing_defaults()
        for field_name, default_value in defaults.items():
            if getattr(self, field_name) is None:
                object.__setattr__(self, field_name, default_value)


def check_position_allowed(
    size_usd: float,
    bankroll: float,
    city: str,
    cluster: str,
    current_city_exposure: float,
    current_cluster_exposure: float,
    current_portfolio_heat: float,
    limits: RiskLimits,
) -> tuple[bool, str]:
    """Check if a proposed position passes all risk limits.

    Returns: (allowed, reason). If not allowed, reason explains why.
    """
    if size_usd < limits.min_order_usd:
        return False, f"Size ${size_usd:.2f} below minimum ${limits.min_order_usd:.2f}"

    if bankroll <= 0:
        return False, "Bankroll is zero or negative"

    position_pct = size_usd / bankroll

    if position_pct > limits.max_single_position_pct:
        return False, (
            f"Position {position_pct:.1%} exceeds single position limit "
            f"{limits.max_single_position_pct:.1%}"
        )

    new_heat = current_portfolio_heat + position_pct
    if new_heat > limits.max_portfolio_heat_pct:
        return False, (
            f"Portfolio heat would be {new_heat:.1%}, "
            f"exceeds limit {limits.max_portfolio_heat_pct:.1%}"
        )

    new_city = current_city_exposure + position_pct
    if new_city > limits.max_city_pct:
        return False, (
            f"City {city} exposure would be {new_city:.1%}, "
            f"exceeds limit {limits.max_city_pct:.1%}"
        )

    new_cluster = current_cluster_exposure + position_pct
    if new_cluster > limits.max_region_pct:
        return False, (
            f"Region {cluster} exposure would be {new_cluster:.1%}, "
            f"exceeds limit {limits.max_region_pct:.1%}"
        )

    return True, "OK"
