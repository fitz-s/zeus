"""Portfolio risk limits and constraint enforcement. Spec §5.4."""

from dataclasses import dataclass

from src.config import sizing_defaults


@dataclass(frozen=True)
class RiskLimits:
    """Legacy risk knobs.

    Live sizing uses continuous Kelly pressure, not hard cap rejection. The
    fields remain for config compatibility and telemetry, but
    ``check_position_allowed`` does not use them as submit blockers.
    """
    max_single_position_pct: float | None = None
    max_portfolio_heat_pct: float | None = None
    max_correlated_pct: float | None = None
    max_city_pct: float | None = None
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
    current_city_exposure: float,
    current_portfolio_heat: float,
    limits: RiskLimits,
) -> tuple[bool, str]:
    """Check non-strategy execution preconditions for a proposed position.

    Returns: (allowed, reason). If not allowed, reason explains why.
    Removed: cluster/max_region_pct check (K3 cluster collapse — no regional
    tier). Single-position, heat, and city percentage caps are intentionally not
    hard gates here; portfolio exposure belongs in continuous Kelly pressure.
    """
    if size_usd < limits.min_order_usd:
        return False, f"Size ${size_usd:.2f} below minimum ${limits.min_order_usd:.2f}"

    if bankroll <= 0:
        return False, "Bankroll is zero or negative"

    return True, "OK"
