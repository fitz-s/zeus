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


@dataclass
class SupervisorCommand:
    """Venus's only write interface to Zeus: control plane commands."""
    command: Literal[
        "pause_entries", "resume", "tighten_risk",
        "request_status", "request_reconcile", "set_strategy_mode",
    ]
    reason: str
    ttl_minutes: Optional[int] = None
    scope: Optional[str] = None
    env: str = ""
    source: str = "venus"


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
