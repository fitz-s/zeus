# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/fix_plan.md;
#                  architecture/invariants.yaml INV-23; architecture/negative_constraints.yaml NC-17.
"""P0 Hardening relationship tests — Execution-State Truth Upgrade.

This file encodes cross-module relationship tests (Fitz Constraint #2: tests
that survive ~100% across sessions) for the P0 hardening slice. Each test is
named for the relationship it locks, not for the function it exercises.

R-1 (degraded x export): When portfolio authority is "degraded", the exported
    truth annotation MUST NOT carry authority="VERIFIED". A degraded loader
    signals lost canonical authority; stamping the export VERIFIED hides that
    loss from downstream consumers and operator surfaces.

R-4 (capability x consumption): ExecutionIntent must not carry decorative
    capability fields (slice_policy, reprice_policy, liquidity_guard) that no
    executor branch consumes for real behavior. Logging-only branches do not
    count. The category is made impossible by deletion (Fitz Constraint #1).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(rel_path: str) -> dict:
    return yaml.safe_load((ROOT / rel_path).read_text())


# ---------------------------------------------------------------------------
# Manifest law registration (P0.14)
# ---------------------------------------------------------------------------


def test_inv23_degraded_export_law_registered():
    """INV-23 must be registered in architecture/invariants.yaml with non-empty enforced_by."""
    manifest = _load_yaml("architecture/invariants.yaml")
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-23" in by_id, "INV-23 (degraded export non-VERIFIED) missing from invariants.yaml"
    inv = by_id["INV-23"]
    assert inv.get("enforced_by"), "INV-23 must declare enforced_by"


def test_nc17_no_decorative_capability_labels_registered():
    """NC-17 must be registered in architecture/negative_constraints.yaml."""
    manifest = _load_yaml("architecture/negative_constraints.yaml")
    by_id = {item["id"]: item for item in manifest["constraints"]}
    assert "NC-17" in by_id, "NC-17 (no decorative capability labels) missing from negative_constraints.yaml"
    nc = by_id["NC-17"]
    assert nc.get("enforced_by"), "NC-17 must declare enforced_by"


# ---------------------------------------------------------------------------
# R-1 — degraded x export
# ---------------------------------------------------------------------------


class TestR1DegradedExportNeverVerified:
    """R-1: a degraded portfolio export MUST NOT stamp authority="VERIFIED".

    Anchors INV-23. Reverses the 2026-04-17 MAJOR-4 round-2 ruling that
    treated degraded as VERIFIED — that ruling is identified as wrong by
    PR #18 review (`Refresh execution-state truth operations package`).
    """

    def test_truth_authority_map_does_not_collapse_degraded_to_verified(self):
        """The portfolio truth-authority map must distinguish degraded from canonical_db."""
        from src.state.portfolio import _TRUTH_AUTHORITY_MAP

        canonical = _TRUTH_AUTHORITY_MAP.get("canonical_db")
        degraded = _TRUTH_AUTHORITY_MAP.get("degraded")

        assert canonical == "VERIFIED", (
            f"canonical_db must still map to VERIFIED, got {canonical!r}"
        )
        assert degraded != "VERIFIED", (
            f"degraded must not collapse to VERIFIED. Got {degraded!r}. "
            f"Use a distinct non-VERIFIED label such as DEGRADED_PROJECTION."
        )

    def test_save_portfolio_degraded_does_not_export_verified(self, tmp_path):
        """End-to-end: save_portfolio with degraded state must not write authority='VERIFIED'."""
        import json
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(
            positions=[],
            bankroll=150.0,
            portfolio_loader_degraded=True,
            authority="degraded",
        )

        path = tmp_path / "positions-test-r1.json"
        save_portfolio(state, path=path)
        written = json.loads(path.read_text())

        truth_authority = written.get("truth", {}).get("authority")
        assert truth_authority != "VERIFIED", (
            f"R-1 violation: degraded save exported authority={truth_authority!r}; "
            f"VERIFIED is reserved for canonical_db. INV-23."
        )
        # Positive shape: it should still carry SOME label so operators see the state.
        assert truth_authority, (
            "R-1 corollary: degraded save must still expose an authority label, "
            "not silently drop the field."
        )


# ---------------------------------------------------------------------------
# R-4 — capability x consumption
# ---------------------------------------------------------------------------


DECORATIVE_LABEL_FIELDS = ("slice_policy", "reprice_policy", "liquidity_guard")


class TestR4ExecutionIntentNoDecorativeLabels:
    """R-4: ExecutionIntent must not carry capability fields no branch consumes.

    Anchors NC-17. The fields slice_policy, reprice_policy, liquidity_guard
    were emitted by create_execution_intent and only appeared in two
    logger.info branches inside executor.py. They were not real capabilities;
    they were labels. P0 deletes them (option-a, recommended in
    decisions.md::O4) so the category is impossible until a real
    state machine ships.
    """

    def test_execution_intent_has_no_decorative_fields(self):
        """Introspection: ExecutionIntent dataclass fields must not include the decorative trio."""
        from dataclasses import fields

        from src.contracts.execution_intent import ExecutionIntent

        present = {f.name for f in fields(ExecutionIntent)}
        leaked = present.intersection(DECORATIVE_LABEL_FIELDS)
        assert not leaked, (
            f"R-4 violation: ExecutionIntent still carries decorative labels {sorted(leaked)!r}. "
            f"Remove them per NC-17."
        )

    def test_executor_does_not_branch_on_decorative_labels(self):
        """Source-text inspection: executor.py must not contain branches on the dropped labels."""
        executor_src = (ROOT / "src/execution/executor.py").read_text()
        for label in DECORATIVE_LABEL_FIELDS:
            offending = f"intent.{label}"
            assert offending not in executor_src, (
                f"R-4 violation: src/execution/executor.py still references {offending!r}. "
                f"NC-17 requires removal."
            )


# ---------------------------------------------------------------------------
# Lifecycle hint — explicit deferrals
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="R-2 (preflight x placement) lands with K5 / V2 preflight slice; not in this micro-slice.")
def test_r2_v2_preflight_blocks_placement_PLACEHOLDER():
    pass


@pytest.mark.skip(reason="R-3 (posture x entry) lands with the runtime_posture slice (O2-c); not in this micro-slice.")
def test_r3_runtime_posture_blocks_new_entry_PLACEHOLDER():
    pass


@pytest.mark.skip(reason="R-5 (RED x command-emission) is a P2 slice; P0 keeps the existing local-marking regression guard elsewhere.")
def test_r5_red_emits_durable_commands_PLACEHOLDER():
    pass
