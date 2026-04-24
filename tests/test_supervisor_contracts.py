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
    is_unverified_env,
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


# ---------------------------------------------------------------------------
# B006 relationship tests: env must be one of the Literal enum values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_value", ["prod", "PROD", "staging", "Live", "  paper  ", "dev"])
def test_b006_env_rejects_value_outside_literal(env_value):
    """env must be exactly one of ("live","paper","test"); any other
    spelling (case, whitespace, unknown envs) must be rejected."""
    with pytest.raises(SupervisorContractError, match="is not one of"):
        Observation(
            kind="heartbeat",
            severity="INFO",
            payload={},
            observed_at="2026-01-01T00:00:00Z",
            env=env_value,
        )


@pytest.mark.parametrize("env_value", ["live", "paper", "test"])
def test_b006_env_accepts_all_literal_values(env_value):
    o = Observation(
        kind="heartbeat",
        severity="INFO",
        payload={},
        observed_at="2026-01-01T00:00:00Z",
        env=env_value,
    )
    assert o.env == env_value


def test_b006_env_reject_message_names_offending_value():
    try:
        Gap(
            gap_id="G1",
            title="t",
            category="semantic",
            description="d",
            env="prod",
        )
    except SupervisorContractError as exc:
        assert "prod" in str(exc)
        assert "Gap" in str(exc)
    else:
        pytest.fail("expected SupervisorContractError for env=prod")


# ---------------------------------------------------------------------------
# B005 relationship tests: every supervisor-facing object carries
#   provenance_ref (the module docstring promise).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls,base_kwargs", [
    (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="t", env="live")),
    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
    (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
    (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
    (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
    (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r", env="test")),
])
def test_b005_provenance_ref_field_exists_on_all_classes(cls, base_kwargs):
    obj = cls(**base_kwargs)
    assert hasattr(obj, "provenance_ref"), (
        f"{cls.__name__} lacks provenance_ref (B005 contract)"
    )
    # default is None
    assert obj.provenance_ref is None


@pytest.mark.parametrize("cls,base_kwargs", [
    (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="t", env="live")),
    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
    (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
    (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
    (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
    (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r", env="test")),
])
def test_b005_provenance_ref_accepts_string_and_preserves(cls, base_kwargs):
    """provenance_ref must round-trip when explicitly set."""
    obj = cls(**base_kwargs, provenance_ref="obs:abc123")
    assert obj.provenance_ref == "obs:abc123"


# ---------------------------------------------------------------------------
# B074 provenance-sentinel env: "unknown_env" is a VALID contract value but
# must be flagged UNVERIFIED by `is_unverified_env()`.
# ---------------------------------------------------------------------------


def test_b074_unknown_env_is_accepted_by_contract():
    """`unknown_env` is a provenance-preserving sentinel for canonical
    projection rows that carry no env. It must pass the B006 contract
    check so those rows can still reach Venus for observation."""
    o = Observation(
        kind="heartbeat",
        severity="INFO",
        payload={},
        observed_at="2026-01-01T00:00:00Z",
        env="unknown_env",
    )
    assert o.env == "unknown_env"


@pytest.mark.parametrize("env_value,expected", [
    ("unknown_env", True),
    ("live", False),
    ("paper", False),
    ("test", False),
])
def test_b074_is_unverified_env(env_value, expected):
    assert is_unverified_env(env_value) is expected
