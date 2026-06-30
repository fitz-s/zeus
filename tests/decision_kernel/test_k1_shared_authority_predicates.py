# Created: 2026-06-10
# Last reused or audited: 2026-06-26
# Authority basis: docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md K1.3
# (twin-authority elimination: maturity ALT tuple verifier L945 + compiler L582 must be
# ONE shared constant + ONE shared predicate; divergent carve-out tuples caused 53/h
# false rejects when FUSED_BOOTSTRAP was added to one side only — CERT BRIDGE 2026-06-10).
"""K1 antibody: calibration maturity carve-out is ONE rule with ONE implementation.

The incident category: a money-path predicate implemented as two independent
formulas at two seams (decision_kernel/verifier.py and decision_kernel/compiler.py).
When one side learned about FUSED_BOOTSTRAP and the other did not, every replacement
chain certificate compiled against the stale tuple was falsely rejected with
"maturity_level too low" (53/h). These tests make the divergence category
unconstructible:

1. Function-identity: both modules dispatch to the SAME predicate object.
2. AST: neither _validate_calibration_payload contains a local maturity comparison
   or a local ALT-authority tuple — the rule lives ONLY in the shared predicate.
3. Golden-case equivalence matrix through the shared predicate.
"""

import ast
import inspect

import pytest

import src.decision_kernel.compiler as compiler_module
import src.decision_kernel.verifier as verifier_module


def test_alt_credential_constant_is_shared_single_source():
    """ONE constant: the ALT-credential authority set lives in verifier and is the
    object compiler uses (no copy, no parallel tuple)."""
    shared = verifier_module.ALT_CREDENTIAL_CALIBRATION_AUTHORITIES
    assert isinstance(shared, frozenset)
    assert shared == frozenset(
        {
            verifier_module.IDENTITY_FALLBACK_CALIBRATION_AUTHORITY,
            verifier_module.FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY,
            verifier_module.FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
            verifier_module.DAY0_OBSERVATION_CALIBRATION_AUTHORITY,
        }
    )
    # Compiler must reference the same object (imported, not redefined).
    assert compiler_module.ALT_CREDENTIAL_CALIBRATION_AUTHORITIES is shared


def test_maturity_predicate_is_same_function_object_at_both_seams():
    """ONE implementation: verifier and compiler dispatch to the same predicate."""
    shared = verifier_module.calibration_maturity_too_low
    assert callable(shared)
    assert compiler_module.calibration_maturity_too_low is shared


@pytest.mark.parametrize(
    "module", [verifier_module, compiler_module], ids=["verifier", "compiler"]
)
def test_no_local_maturity_formula_at_either_seam(module):
    """AST antibody: _validate_calibration_payload must CALL the shared predicate and
    must NOT re-implement the rule (no `> 3` comparison, no local ALT tuple literal)."""
    src = inspect.getsource(module._validate_calibration_payload)
    tree = ast.parse(src)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "calibration_maturity_too_low" in calls, (
        f"{module.__name__}._validate_calibration_payload does not dispatch to the "
        "shared maturity predicate"
    )
    # No local re-implementation: a `maturity > 3`-style Compare against the literal 3.
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                assert not (
                    isinstance(comparator, ast.Constant) and comparator.value == 3
                ), (
                    f"{module.__name__}._validate_calibration_payload re-implements the "
                    "maturity threshold locally — the rule must live ONLY in "
                    "calibration_maturity_too_low"
                )
    # No local ALT-authority tuple rebuilt from the two authority constants.
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert "IDENTITY_FALLBACK_CALIBRATION_AUTHORITY" not in names, (
        f"{module.__name__}._validate_calibration_payload rebuilds the ALT tuple locally"
    )


def test_mode_hysteresis_margin_is_shared_not_hardcoded():
    """K1.3 regression pin for a8a1c80536 (mode hysteresis at both seams).

    Architecture: the mode is DECIDED once (proof-side select_mode_consistent_ev;
    execution_mode_intent is submit authority). The fresh-side _ev_boundary_favors_cross
    is a fail-closed divergence DETECTOR, not a second authority (P0 mode-authority:
    divergence aborts SUBMIT_ABORTED_MODE_FLIPPED, never inline-rebuilds). The one
    shared element both seams must agree on is the hysteresis margin — this test pins
    that the detector imports TAKER_OVER_MAKER_MARGIN from mode_consistent_ev and does
    not hardcode its own margin constant (the divergent-twin-constant category).
    """
    import ast as _ast
    import inspect as _inspect

    import src.engine.event_reactor_adapter as adapter_module

    src = _inspect.getsource(adapter_module._ev_boundary_favors_cross)
    tree = _ast.parse(src)
    imported = [
        alias.name
        for node in _ast.walk(tree)
        if isinstance(node, _ast.ImportFrom)
        and node.module == "src.strategy.live_inference.mode_consistent_ev"
        for alias in node.names
    ]
    assert "TAKER_OVER_MAKER_MARGIN" in imported, (
        "_ev_boundary_favors_cross no longer imports the shared hysteresis margin "
        "from mode_consistent_ev — the margin must never fork into a local constant"
    )
    # No local float literal used as a margin multiplier: the only numeric literals
    # allowed in this function are structural (0.0, 1.0, 2.0 for half-spread/identity).
    allowed = {0.0, 1.0, 2.0}
    literals = {
        node.value
        for node in _ast.walk(tree)
        if isinstance(node, _ast.Constant) and isinstance(node.value, float)
    }
    assert literals <= allowed, (
        f"_ev_boundary_favors_cross contains unexpected float literals {literals - allowed} "
        "— a hardcoded margin/threshold would re-fork the twin-constant category"
    )


def test_settlement_preimage_single_source():
    """K1.3 regression pin for 1687be9343: settlement_preimage_offsets is defined ONCE
    (the per-city contract) and every consumer imports it from there."""
    import subprocess

    out = subprocess.run(
        ["grep", "-rn", "def settlement_preimage_offsets", "src/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip().splitlines()
    assert len(out) == 1, f"settlement_preimage_offsets defined at {len(out)} sites: {out}"
    assert "src/contracts/settlement_semantics.py" in out[0]


@pytest.mark.parametrize(
    "maturity,authority,expected_too_low",
    [
        # Real Platt models: maturity placeholder >3 is too low.
        (4, "VERIFIED", True),
        (5, "LIVE", True),
        (4, "APPROVED", True),
        # Mature Platt models pass.
        (1, "VERIFIED", False),
        (3, "LIVE", False),
        # ALT credentials use maturity_level=4 as placeholder; guard must not apply.
        (4, "IDENTITY_FALLBACK_NO_PLATT_BUCKET", False),
        (4, "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB", False),
        (4, "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE", False),
        (4, "DAY0_LIVE_OBSERVATION_HARD_FACT", False),
        # Unapproved/unknown authority with placeholder maturity stays too-low here
        # (the authority gate rejects it earlier anyway; this predicate stays strict).
        (4, "FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED", True),
        (4, "SOMETHING_ELSE", True),
    ],
)
def test_shared_predicate_golden_matrix(maturity, authority, expected_too_low):
    assert (
        verifier_module.calibration_maturity_too_low(maturity, authority)
        is expected_too_low
    )
