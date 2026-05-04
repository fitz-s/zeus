# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A4 + Bug review §D (scattered strategy lists) + §E (LIVE_SAFE / _LIVE_ALLOWED divergence).
"""StrategyProfile registry — single source of per-strategy authority.

What this module replaces
-------------------------
Before A4 the per-strategy authority was scattered across 5 hardcoded sites:

  - ``src/strategy/kelly.py:67``               STRATEGY_KELLY_MULTIPLIERS
  - ``src/control/control_plane.py:49``        LIVE_SAFE_STRATEGIES (boot allowlist)
  - ``src/control/control_plane.py:321``       _LIVE_ALLOWED_STRATEGIES (runtime allowlist)
  - ``src/engine/cycle_runner.py:77``          KNOWN_STRATEGIES (buildable universe)
  - ``src/engine/evaluator.py``                hardcoded direction/bin checks

The two control_plane sets diverged: shoulder_sell was in LIVE_SAFE but not
_LIVE_ALLOWED. Bug review §E flagged the divergence as the canonical "two
allowlists, one drift" failure. Post-A4 every authority reads
``strategy_profile.get(key)`` and the boot/runtime gates derive uniformly
from ``live_status`` — the divergence becomes un-constructable.

How callers use it
------------------
::

    from src.strategy.strategy_profile import get, all_keys, live_safe_keys, live_allowed_keys

    profile = get("settlement_capture")          # ProfileNotFound if unknown
    profile.kelly_default_multiplier             # 1.0
    profile.kelly_for_phase("settlement_day")    # 1.0 (override) or default
    profile.is_phase_allowed("post_trading")     # False
    profile.is_runtime_live()                    # True for live_status=="live"

    live_safe_keys()        # frozenset of boot-allowable strategies
    live_allowed_keys()     # frozenset of runtime-entry-allowed strategies

Fail-closed contract: ``get(unknown_key)`` raises ``ProfileNotFound``;
callers gating live entries should treat unknown keys as fully blocked.
The registry is loaded once at import time from
``architecture/strategy_profile_registry.yaml``; tests can call
``_reload_for_test(path)`` to redirect.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from src.state.paths import REPO_ROOT

logger = logging.getLogger(__name__)


REGISTRY_PATH: Path = REPO_ROOT / "architecture" / "strategy_profile_registry.yaml"


# ── live status ────────────────────────────────────────────────────── #


_VALID_LIVE_STATUSES: frozenset[str] = frozenset({
    "live",        # boot-OK + runtime-OK
    "shadow",      # boot-OK + runtime-blocked (decisions logged for promotion)
    "blocked",     # boot-rejected + runtime-blocked
    "deprecated",  # synonym for blocked, kept for grep history
})


_VALID_METRIC_SUPPORTS: frozenset[str] = frozenset({"live", "shadow", "blocked"})


# ── exceptions ─────────────────────────────────────────────────────── #


class ProfileNotFound(KeyError):
    """Raised when a caller asks for a strategy_key not in the registry.

    Inherits ``KeyError`` so existing ``try/except KeyError`` blocks in
    legacy code keep their semantics — but the dedicated type lets new
    code distinguish "unknown strategy" from "missing dict key".
    """


class RegistrySchemaError(ValueError):
    """Raised at load time when the YAML registry violates the schema.

    Catching it at boot is preferable to silently muting a constraint —
    e.g. a typo'd ``allowed_market_phazes`` would otherwise make the
    strategy fire under any phase.
    """


# ── ProfileEntry dataclass ─────────────────────────────────────────── #


@dataclass(frozen=True)
class StrategyProfile:
    """In-memory representation of one registry row."""
    key: str
    thesis: str
    live_status: str
    allowed_market_phases: frozenset[str]
    allowed_discovery_modes: frozenset[str]
    cycle_axis_dispatch_mode: Optional[str]
    allowed_directions: frozenset[str]
    allowed_bin_topology: frozenset[str]
    metric_support: dict[str, str]
    kelly_default_multiplier: float
    kelly_phase_overrides: dict[str, float]
    min_shadow_decisions: int
    min_settled_decisions: int
    promotion_evidence_ref: Optional[str]

    def is_runtime_live(self) -> bool:
        """True iff entries placed by this strategy hit the live order book.

        Equivalent to the pre-A4 ``key in _LIVE_ALLOWED_STRATEGIES``."""
        return self.live_status == "live"

    def is_boot_allowed(self) -> bool:
        """True iff the daemon may have this strategy enabled at boot.

        Equivalent to the pre-A4 ``key in LIVE_SAFE_STRATEGIES``. Boot
        status is broader than runtime: shadow strategies boot (collect
        decision logs for promotion evidence) but never enter."""
        return self.live_status in {"live", "shadow"}

    def is_phase_allowed(self, market_phase: str) -> bool:
        """True iff the strategy is semantically valid in this market phase.

        ``market_phase`` is the lowercase enum value
        (``MarketPhase.SETTLEMENT_DAY.value`` etc.). Empty allow-list = no
        phase passes; this is how blocked/dormant strategies stay dormant."""
        return market_phase in self.allowed_market_phases

    def is_mode_allowed(self, discovery_mode: str) -> bool:
        return discovery_mode in self.allowed_discovery_modes

    def is_direction_allowed(self, direction: str) -> bool:
        return direction in self.allowed_directions

    def is_bin_topology_allowed(self, topology: str) -> bool:
        return topology in self.allowed_bin_topology

    def kelly_for_phase(self, market_phase: Optional[str]) -> float:
        """Phase-aware Kelly multiplier (PLAN.md §A6 resolver input).

        Pre-A6 callers pass ``market_phase=None`` to get the legacy
        per-strategy default. A6 layers this through a richer resolver
        that also accounts for oracle status, observed_target_day_fraction,
        and phase_source quality.
        """
        if market_phase is None:
            return self.kelly_default_multiplier
        return self.kelly_phase_overrides.get(market_phase, self.kelly_default_multiplier)

    def metric_is_live(self, temperature_metric: str) -> bool:
        """True iff entries on this metric reach the live order book.
        ``shadow`` and ``blocked`` both return False."""
        return self.metric_support.get(temperature_metric) == "live"


# ── registry loader ────────────────────────────────────────────────── #


_registry: Optional[dict[str, StrategyProfile]] = None


def _coerce_frozenset(value, *, field_name: str, key: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise RegistrySchemaError(
            f"{key}.{field_name}: expected list, got {type(value).__name__}"
        )
    out = []
    for v in value:
        if not isinstance(v, str):
            raise RegistrySchemaError(
                f"{key}.{field_name}: list members must be str, got {type(v).__name__}"
            )
        out.append(v)
    return frozenset(out)


def _coerce_metric_support(value, *, key: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RegistrySchemaError(
            f"{key}.metric_support: expected dict, got {type(value).__name__}"
        )
    out: dict[str, str] = {}
    for metric in ("high", "low"):
        if metric not in value:
            raise RegistrySchemaError(
                f"{key}.metric_support: missing required key '{metric}'"
            )
        v = value[metric]
        if v not in _VALID_METRIC_SUPPORTS:
            raise RegistrySchemaError(
                f"{key}.metric_support.{metric}: must be one of "
                f"{sorted(_VALID_METRIC_SUPPORTS)}, got {v!r}"
            )
        out[metric] = v
    extras = set(value.keys()) - {"high", "low"}
    if extras:
        raise RegistrySchemaError(
            f"{key}.metric_support: unexpected keys {sorted(extras)}"
        )
    return out


def _coerce_phase_overrides(value, *, key: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RegistrySchemaError(
            f"{key}.kelly_phase_overrides: expected dict, got {type(value).__name__}"
        )
    out: dict[str, float] = {}
    for phase, mult in value.items():
        if not isinstance(phase, str):
            raise RegistrySchemaError(
                f"{key}.kelly_phase_overrides: phase keys must be str, got {type(phase).__name__}"
            )
        if not isinstance(mult, (int, float)):
            raise RegistrySchemaError(
                f"{key}.kelly_phase_overrides[{phase!r}]: must be numeric, got {type(mult).__name__}"
            )
        if not (0.0 <= float(mult) <= 1.0):
            raise RegistrySchemaError(
                f"{key}.kelly_phase_overrides[{phase!r}]={mult}: must be in [0.0, 1.0]"
            )
        out[phase] = float(mult)
    return out


_REQUIRED_FIELDS = {
    "thesis",
    "live_status",
    "allowed_market_phases",
    "allowed_discovery_modes",
    "cycle_axis_dispatch_mode",
    "allowed_directions",
    "allowed_bin_topology",
    "metric_support",
    "kelly_default_multiplier",
    "kelly_phase_overrides",
    "min_shadow_decisions",
    "min_settled_decisions",
    "promotion_evidence_ref",
}


_VALID_DISCOVERY_MODES: frozenset[str] = frozenset({
    "day0_capture", "opening_hunt", "update_reaction",
})


def _build_profile(key: str, raw: dict) -> StrategyProfile:
    if not isinstance(raw, dict):
        raise RegistrySchemaError(
            f"{key}: expected dict, got {type(raw).__name__}"
        )
    extras = set(raw.keys()) - _REQUIRED_FIELDS
    if extras:
        raise RegistrySchemaError(
            f"{key}: unexpected fields {sorted(extras)} (typo? unrecognized field "
            f"silently mutes the constraint)"
        )
    missing = _REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise RegistrySchemaError(
            f"{key}: missing required fields {sorted(missing)}"
        )

    live_status = raw["live_status"]
    if live_status not in _VALID_LIVE_STATUSES:
        raise RegistrySchemaError(
            f"{key}.live_status: must be one of {sorted(_VALID_LIVE_STATUSES)}, "
            f"got {live_status!r}"
        )

    kelly_default = raw["kelly_default_multiplier"]
    if not isinstance(kelly_default, (int, float)) or not (0.0 <= float(kelly_default) <= 1.0):
        raise RegistrySchemaError(
            f"{key}.kelly_default_multiplier={kelly_default!r}: must be numeric in [0.0, 1.0]"
        )

    cycle_axis_mode = raw["cycle_axis_dispatch_mode"]
    if cycle_axis_mode is not None:
        if not isinstance(cycle_axis_mode, str):
            raise RegistrySchemaError(
                f"{key}.cycle_axis_dispatch_mode: must be a discovery_mode string or null, "
                f"got {type(cycle_axis_mode).__name__}"
            )
        if cycle_axis_mode not in _VALID_DISCOVERY_MODES:
            raise RegistrySchemaError(
                f"{key}.cycle_axis_dispatch_mode={cycle_axis_mode!r}: must be one of "
                f"{sorted(_VALID_DISCOVERY_MODES)} or null"
            )

    return StrategyProfile(
        key=key,
        thesis=str(raw["thesis"]).strip(),
        live_status=live_status,
        allowed_market_phases=_coerce_frozenset(
            raw["allowed_market_phases"], field_name="allowed_market_phases", key=key
        ),
        allowed_discovery_modes=_coerce_frozenset(
            raw["allowed_discovery_modes"], field_name="allowed_discovery_modes", key=key
        ),
        cycle_axis_dispatch_mode=cycle_axis_mode if cycle_axis_mode else None,
        allowed_directions=_coerce_frozenset(
            raw["allowed_directions"], field_name="allowed_directions", key=key
        ),
        allowed_bin_topology=_coerce_frozenset(
            raw["allowed_bin_topology"], field_name="allowed_bin_topology", key=key
        ),
        metric_support=_coerce_metric_support(raw["metric_support"], key=key),
        kelly_default_multiplier=float(kelly_default),
        kelly_phase_overrides=_coerce_phase_overrides(raw["kelly_phase_overrides"], key=key),
        min_shadow_decisions=int(raw["min_shadow_decisions"]),
        min_settled_decisions=int(raw["min_settled_decisions"]),
        promotion_evidence_ref=(
            None if raw["promotion_evidence_ref"] in (None, "null", "")
            else str(raw["promotion_evidence_ref"])
        ),
    )


def _load(path: Path) -> dict[str, StrategyProfile]:
    if not path.exists():
        raise RegistrySchemaError(
            f"strategy_profile_registry.yaml not found at {path} — "
            f"the registry is required, not optional"
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise RegistrySchemaError(
            f"{path}: top level must be a mapping of strategy_key -> profile"
        )
    return {key: _build_profile(key, value) for key, value in raw.items()}


def _ensure_loaded() -> dict[str, StrategyProfile]:
    global _registry
    if _registry is None:
        _registry = _load(REGISTRY_PATH)
    return _registry


# ── public API ─────────────────────────────────────────────────────── #


def get(strategy_key: str) -> StrategyProfile:
    """Return the registered profile for ``strategy_key``.

    Raises ``ProfileNotFound`` for unknown keys. Callers gating live
    entries should treat the exception as fully-blocked.
    """
    if not strategy_key:
        raise ProfileNotFound("empty strategy_key")
    registry = _ensure_loaded()
    profile = registry.get(strategy_key)
    if profile is None:
        raise ProfileNotFound(f"unknown strategy_key: {strategy_key!r}")
    return profile


def try_get(strategy_key: str) -> Optional[StrategyProfile]:
    """Non-raising variant for callers that want to handle "unknown" inline."""
    try:
        return get(strategy_key)
    except ProfileNotFound:
        return None


def all_keys() -> frozenset[str]:
    """All registered strategy keys — replaces ``KNOWN_STRATEGIES`` in
    cycle_runner."""
    return frozenset(_ensure_loaded().keys())


def all_profiles() -> dict[str, StrategyProfile]:
    """Snapshot of the whole registry. Returns a fresh dict so callers
    cannot mutate the cache."""
    return dict(_ensure_loaded())


def live_safe_keys() -> frozenset[str]:
    """Strategies allowed at daemon boot — replaces
    ``LIVE_SAFE_STRATEGIES`` in control_plane. Includes every strategy
    with ``live_status in {live, shadow}`` (shadow strategies boot to
    collect decision logs but never enter)."""
    return frozenset(
        k for k, p in _ensure_loaded().items() if p.is_boot_allowed()
    )


def live_allowed_keys() -> frozenset[str]:
    """Strategies allowed to place live orders — replaces
    ``_LIVE_ALLOWED_STRATEGIES`` in control_plane. Strict subset of
    live_safe_keys (every live entry is also boot-allowed; not every
    boot-allowed strategy enters live)."""
    return frozenset(
        k for k, p in _ensure_loaded().items() if p.is_runtime_live()
    )


def cycle_axis_dispatch_inverse() -> dict[str, frozenset[str]]:
    """Return the discovery_mode → strategies inverse map for cycle-axis dispatch.

    Each strategy's ``cycle_axis_dispatch_mode`` field names the SINGLE
    legacy mode under which the strategy is routed by evaluator clauses 1-4.
    This helper inverts that field so cycle_runtime can reject strategies
    that fall outside the cycle-axis dispatch contract for the active mode.

    Replaces the pre-A4-then-restored hardcoded
    ``STRATEGY_KEYS_BY_DISCOVERY_MODE`` in cycle_runtime.py — H2 critic R6
    finding (no hardcoded inverse map outside the registry).

    Strategies whose ``cycle_axis_dispatch_mode`` is None (blocked) are
    omitted from the returned map.
    """
    out: dict[str, set[str]] = {}
    for key, profile in _ensure_loaded().items():
        mode = profile.cycle_axis_dispatch_mode
        if mode:
            out.setdefault(mode, set()).add(key)
    return {mode: frozenset(keys) for mode, keys in out.items()}


def kelly_default_multiplier(strategy_key: str) -> float:
    """Replacement for ``STRATEGY_KELLY_MULTIPLIERS.get(key, 0.0)``.

    Fail-closed: unknown key returns 0.0 (no entries). This matches the
    pre-A4 behavior and is the safe direction — a typo'd strategy_key
    in a caller cannot accidentally enable a different strategy's Kelly.
    """
    profile = try_get(strategy_key)
    if profile is None:
        return 0.0
    return profile.kelly_default_multiplier


# ── test helper ────────────────────────────────────────────────────── #


def _reload_for_test(path: Optional[Path] = None) -> None:
    """Force a registry reload, optionally from a custom path. Used by
    pytest fixtures that need to inject a synthetic registry. NOT public
    API; production code should never reload mid-process.
    """
    global _registry
    if path is None:
        _registry = None  # next access lazy-loads from REGISTRY_PATH
    else:
        _registry = _load(path)
