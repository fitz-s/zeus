"""Tests for supervisor_api.contracts env enforcement (K1-A4, Bug #28)."""

import pytest

from src.supervisor_api.contracts import (
    Antibody,
    BeliefMismatch,
    ChangeOutcome,
    Gap,
    Observation,
    Proposal,
    SupervisorCommand,
    SupervisorContractError,
)


@pytest.mark.parametrize("cls,kwargs", [
    (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="2026-01-01T00:00:00Z")),
    (BeliefMismatch, dict(category="drift", expected="x", observed="y")),
    (Gap, dict(gap_id="G1", title="t", category="semantic", description="d")),
    (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r")),
    (SupervisorCommand, dict(command="pause_entries", reason="test")),
    (ChangeOutcome, dict(change_id="C1", verdict="PENDING")),
    (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r")),
])
def test_empty_env_raises(cls, kwargs):
    with pytest.raises(SupervisorContractError, match="env must not be empty"):
        cls(**kwargs)


@pytest.mark.parametrize("cls,kwargs", [
    (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="2026-01-01T00:00:00Z", env="live")),
    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
    (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
    (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
    (SupervisorCommand, dict(command="pause_entries", reason="test", env="paper")),
    (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
    (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r", env="test")),
])
def test_nonempty_env_passes(cls, kwargs):
    obj = cls(**kwargs)
    assert obj.env
