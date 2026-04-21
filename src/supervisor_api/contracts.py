"""Typed contract surface between Zeus and Venus.

Every supervisor-facing object carries env + source + provenance.
Venus reads these. Venus NEVER writes to Zeus state directly.
Control plane is the only write interface.

Output categories (O/P/C/O):
  Observation — what is happening
  Proposal — what should change
  Command — what Zeus should do now
  Outcome — what happened after a change
"""

from dataclasses import dataclass, field
from typing import Literal, Optional


class SupervisorContractError(ValueError):
    """Raised when a supervisor API contract field violates its invariant."""
    pass


# B006: valid environment values for every supervisor-facing object.
# Matches the docstring promise at the top of this module. Update this
# tuple (and the tests in test_supervisor_contracts.py) if a new env
# is introduced; do NOT add ad-hoc env strings at call sites.
#
# B074 [critic amendment]: "unknown_env" is a provenance-preserving
# sentinel used by state/portfolio.py when the canonical projection row
# carries no env field (fallback path). It is a valid contract value
# but is UNVERIFIED authority-wise; callers should check via
# `is_unverified_env()` before making any env-scoped authority decision.
_VALID_ENVS: tuple[str, ...] = ("live", "test", "unknown_env")

_UNVERIFIED_ENVS: tuple[str, ...] = ("unknown_env",)


def is_unverified_env(env: str) -> bool:
    """B074 helper: True when env is a provenance-sentinel that must be
    treated as UNVERIFIED authority (row did not carry a real env).

    Callers that make env-scoped decisions (mode-routing, dashboard
    segmentation, live-mode P&L attribution) should refuse or
    quarantine rows where this returns True, rather than silently
    bucketing them into the current runtime mode.
    """
    return env in _UNVERIFIED_ENVS


def _check_env(obj: object) -> None:
    env = getattr(obj, "env", "")
    if not env:
        raise SupervisorContractError(
            f"{type(obj).__name__}.env must not be empty — "
            "every supervisor object must declare its environment "
            "(e.g. 'live', 'test')"
        )
    if env not in _VALID_ENVS:
        raise SupervisorContractError(
            f"{type(obj).__name__}.env={env!r} is not one of "
            f"the valid environments {_VALID_ENVS} — B006 contract"
        )


@dataclass
class Observation:
    """A fact Venus observed about Zeus's runtime state."""
    kind: Literal[
        "heartbeat", "risk_state", "portfolio_state",
        "reconciliation", "strategy_edge", "belief_check",
        "change_outcome",
    ]
    severity: Literal["INFO", "WARN", "CRITICAL"]
    payload: dict
    observed_at: str
    env: str = ""
    source: str = "zeus_runtime"
    provenance_ref: Optional[str] = None

    def __post_init__(self) -> None:
        _check_env(self)


@dataclass
class BeliefMismatch:
    """Zeus believes X, reality shows Y."""
    category: str
    expected: str
    observed: str
    evidence: dict = field(default_factory=dict)
    severity: Literal["WARN", "CRITICAL"] = "WARN"
    env: str = ""
    source: str = "venus"
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)


@dataclass
class Gap:
    """Something Zeus doesn't know yet. Venus's evolution worklist."""
    gap_id: str
    title: str
    category: Literal["semantic", "data", "execution", "research", "control"]
    description: str
    source_observation_ids: list[str] = field(default_factory=list)
    proposed_antibody: Optional[str] = None
    env: str = ""
    source: str = "venus"
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)


@dataclass
class Proposal:
    """A suggested change. Must satisfy Antibody Promotion Rules to graduate."""
    proposal_id: str
    kind: Literal["test", "type_constraint", "runtime_assertion",
                  "code_fix", "config_change"]
    title: str
    rationale: str
    related_gap_id: Optional[str] = None
    files_touched: list[str] = field(default_factory=list)
    risk_scope: Literal["paper_only", "live_path", "docs_only"] = "paper_only"
    env: str = ""
    source: str = "venus"
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)


@dataclass
class SupervisorCommand:
    """Venus's only write interface to Zeus: control plane commands."""
    command: Literal[
        "pause_entries", "resume", "tighten_risk",
        "request_status", "set_strategy_gate", "acknowledge_quarantine_clear",
    ]
    reason: str
    ttl_minutes: Optional[int] = None
    scope: Optional[str] = None
    strategy: Optional[str] = None
    enabled: Optional[bool] = None
    token_id: Optional[str] = None
    condition_id: Optional[str] = None
    note: Optional[str] = None
    env: str = ""
    source: str = "venus"
    timestamp: str = ""
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)
        if not getattr(self, "source", ""):
            raise SupervisorContractError(
                "SupervisorCommand.source must not be empty"
            )
        if not getattr(self, "reason", ""):
            raise SupervisorContractError(
                "SupervisorCommand.reason must not be empty"
            )
        if not getattr(self, "timestamp", ""):
            raise SupervisorContractError(
                "SupervisorCommand.timestamp must not be empty"
            )
        if self.command == "set_strategy_gate":
            if not self.strategy or self.enabled is None:
                raise SupervisorContractError(
                    "set_strategy_gate requires strategy and enabled flags"
                )
        elif self.command == "acknowledge_quarantine_clear":
            if not self.token_id:
                raise SupervisorContractError(
                    "acknowledge_quarantine_clear requires token_id"
                )


@dataclass
class ChangeOutcome:
    """What happened after Venus's change was deployed."""
    change_id: str
    verdict: Literal["PENDING", "IMPROVEMENT", "REGRESSION", "NEUTRAL"]
    pnl_delta: Optional[float] = None
    regressions: int = 0
    notes: str = ""
    env: str = ""
    source: str = "venus"
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)


@dataclass
class Antibody:
    """A Proposal that graduated. Permanently protects Zeus against a gap category.

    Promotion rules — a Proposal becomes an Antibody only when:
    1. New cross-module invariant test (committed + passes), OR
    2. New type boundary / semantic wrapper, OR
    3. New runtime assertion / exporter parity check, OR
    4. New control-plane guard / command block rule, OR
    5. New replay/outcome check that prevents recurrence

    Note ≠ antibody. Alert ≠ antibody. Doc ≠ antibody.
    Failing test = stage-1 antibody. Type constraint in CI = full antibody.
    """
    antibody_id: str
    source_gap_id: str
    antibody_type: Literal[
        "test", "type_constraint", "runtime_assertion",
        "control_guard", "replay_check",
    ]
    target_surface: str
    recurrence_class: str
    deployed_at: str = ""
    env: str = ""
    provenance_ref: Optional[str] = None  # B005

    def __post_init__(self) -> None:
        _check_env(self)
