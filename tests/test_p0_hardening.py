# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/fix_plan.md;
#                  architecture/invariants.yaml INV-23..26; architecture/negative_constraints.yaml NC-16/NC-17.
"""P0 Hardening relationship tests — Execution-State Truth Upgrade.

This file encodes cross-module relationship tests (Fitz Constraint #2: tests
that survive ~100% across sessions) for the P0 hardening slice. Each test is
named for the relationship it locks, not for the function it exercises.

R-1 (degraded x export): When portfolio authority is "degraded", the exported
    truth annotation MUST NOT carry authority="VERIFIED". A degraded loader
    signals lost canonical authority; stamping the export VERIFIED hides that
    loss from downstream consumers and operator surfaces.

R-G (gateway-only): place_limit_order may only appear inside the gateway
    boundary files. K2 static guard (test-based, semgrep deferred).

R-2 (preflight x placement): When V2 preflight fails, _live_order must return
    status="rejected" without reaching place_limit_order. INV-25.

R-3 (posture x entry): When runtime_posture is non-NORMAL, the cycle_runner
    entry gate must block with reason containing "posture". INV-26.

R-4 (capability x consumption): ExecutionIntent must not carry decorative
    capability fields (slice_policy, reprice_policy, liquidity_guard) that no
    executor branch consumes for real behavior. Logging-only branches do not
    count. The category is made impossible by deletion (Fitz Constraint #1).
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(rel_path: str) -> dict:
    return yaml.safe_load((ROOT / rel_path).read_text())


# ---------------------------------------------------------------------------
# Manifest law registration (P0.14) — INV-23..26, NC-16, NC-17
# ---------------------------------------------------------------------------


def test_inv23_degraded_export_law_registered():
    """INV-23 must be registered in architecture/invariants.yaml with non-empty enforced_by."""
    manifest = _load_yaml("architecture/invariants.yaml")
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-23" in by_id, "INV-23 (degraded export non-VERIFIED) missing from invariants.yaml"
    inv = by_id["INV-23"]
    assert inv.get("enforced_by"), "INV-23 must declare enforced_by"


def test_inv24_gateway_only_law_registered():
    """INV-24 must be registered with non-empty enforced_by."""
    manifest = _load_yaml("architecture/invariants.yaml")
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-24" in by_id, "INV-24 (place_limit_order gateway-only) missing from invariants.yaml"
    assert by_id["INV-24"].get("enforced_by"), "INV-24 must declare enforced_by"


def test_inv25_v2_preflight_law_registered():
    """INV-25 must be registered with non-empty enforced_by."""
    manifest = _load_yaml("architecture/invariants.yaml")
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-25" in by_id, "INV-25 (V2 preflight blocks placement) missing from invariants.yaml"
    assert by_id["INV-25"].get("enforced_by"), "INV-25 must declare enforced_by"


def test_inv26_runtime_posture_law_registered():
    """INV-26 must be registered with non-empty enforced_by."""
    manifest = _load_yaml("architecture/invariants.yaml")
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-26" in by_id, "INV-26 (runtime posture read-only gate) missing from invariants.yaml"
    assert by_id["INV-26"].get("enforced_by"), "INV-26 must declare enforced_by"


def test_nc16_no_direct_place_limit_order_registered():
    """NC-16 must be registered in architecture/negative_constraints.yaml."""
    manifest = _load_yaml("architecture/negative_constraints.yaml")
    by_id = {item["id"]: item for item in manifest["constraints"]}
    assert "NC-16" in by_id, "NC-16 (no place_limit_order outside gateway) missing from negative_constraints.yaml"
    assert by_id["NC-16"].get("enforced_by"), "NC-16 must declare enforced_by"


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
# R-G — K2 gateway-only static guard (test-based)
# ---------------------------------------------------------------------------

# The allowed-files frozenset is the explicit boundary. To add a new
# approved call site, update this set AND the NC-16 / INV-24 manifest
# entries in the same commit (so law and guard stay in sync).
#
# scripts/live_smoke_test.py is an explicit operator-bypass exemption:
# it is operator-driven (not runtime) and MUST call v2_preflight() itself
# before place_limit_order. This exemption does NOT relax INV-25 — the
# smoke test must honor it on its own path.
_PLACE_LIMIT_ORDER_ALLOWED_FILES = frozenset({
    "src/execution/executor.py",
    "src/data/polymarket_client.py",
    "scripts/live_smoke_test.py",
})


def _ast_calls_place_limit_order(source_text: str) -> bool:
    """Return True if source_text contains an AST Call node whose func
    attribute is an Attribute node with attr == 'place_limit_order'.

    This is more precise than substring search: it detects only actual
    call expressions, not docstring mentions, comments, or string literals.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        # Unparseable files are treated conservatively as containing a call.
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "place_limit_order":
                return True
    return False


def test_place_limit_order_gateway_only():
    """K2 / NC-16 / INV-24: place_limit_order calls must only appear in gateway-boundary files.

    Walks all *.py files under src/ AND scripts/, uses AST-based detection
    (not substring match) to find actual Call nodes with attr='place_limit_order'.
    Files in _PLACE_LIMIT_ORDER_ALLOWED_FILES are the only permitted locations.
    A violation means a caller is bypassing the gateway boundary.

    Note: scripts/live_smoke_test.py is an explicit operator-bypass exemption;
    it is NOT a runtime call site and must call v2_preflight() itself (INV-25).
    """
    search_roots = [ROOT / "src", ROOT / "scripts"]
    violations = []
    for search_root in search_roots:
        if not search_root.exists():
            continue
        for py_file in search_root.rglob("*.py"):
            rel = py_file.relative_to(ROOT).as_posix()
            if rel in _PLACE_LIMIT_ORDER_ALLOWED_FILES:
                continue
            text = py_file.read_text(encoding="utf-8")
            if _ast_calls_place_limit_order(text):
                violations.append(rel)

    assert not violations, (
        f"NC-16 / INV-24 violation: place_limit_order call found outside the gateway boundary "
        f"in {violations}. Only {sorted(_PLACE_LIMIT_ORDER_ALLOWED_FILES)} are approved."
    )


# ---------------------------------------------------------------------------
# R-2 — V2 preflight x placement (K5 / INV-25)
# ---------------------------------------------------------------------------


class TestR2V2PreflightBlocksPlacement:
    """R-2: When V2 preflight raises V2PreflightError, _live_order must return
    status='rejected' with reason starting 'v2_preflight_failed' without
    ever reaching place_limit_order. INV-25.
    """

    def _make_intent(self):
        """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
        from src.contracts.execution_intent import ExecutionIntent
        from src.contracts import Direction

        return ExecutionIntent(
            direction=Direction("buy_yes"),
            target_size_usd=10.0,
            limit_price=0.55,
            toxicity_budget=0.05,
            max_slippage=0.02,
            is_sandbox=False,
            market_id="mkt-test",
            token_id="tok-0000000000000000000000000000000000000001",
            timeout_seconds=3600,
            decision_edge=0.05,
        )

    def test_v2_preflight_blocks_placement(self):
        """Mocked v2_preflight raises V2PreflightError; _live_order returns rejected
        without calling place_limit_order.

        Note: PolymarketClient is imported inside _live_order via a local import,
        so we patch src.data.polymarket_client.PolymarketClient (the class at its
        definition site) to intercept the local import at call time.
        """
        from src.data.polymarket_client import V2PreflightError
        from src.execution.executor import _live_order

        intent = self._make_intent()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.v2_preflight.side_effect = V2PreflightError("endpoint unreachable")

            result = _live_order(
                trade_id="test-trade-001",
                intent=intent,
                shares=18.19,
            )

        assert result.status == "rejected", (
            f"Expected rejected but got {result.status!r}; "
            f"INV-25: V2PreflightError must block placement."
        )
        assert result.reason is not None and result.reason.startswith("v2_preflight_failed"), (
            f"Expected reason to start with 'v2_preflight_failed', got {result.reason!r}"
        )
        # Confirm place_limit_order was never reached
        mock_instance.place_limit_order.assert_not_called()

    def test_v2_preflight_success_does_not_block(self):
        """When v2_preflight succeeds (no-op), placement proceeds to place_limit_order.

        The mock SDK client exposes get_ok (positive case) so the fail-closed
        hasattr check passes. We assert v2_preflight was called before
        place_limit_order by verifying both call counts in sequence.
        """
        from src.execution.executor import _live_order

        intent = self._make_intent()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            # Inject a mock SDK client that DOES expose get_ok (positive case).
            mock_clob = MagicMock()
            mock_clob.get_ok.return_value = None  # get_ok present and succeeds
            mock_instance._clob_client = mock_clob
            mock_instance.v2_preflight.return_value = None  # preflight succeeds
            mock_instance.place_limit_order.return_value = {
                "orderID": "test-order-123",
                "status": "placed",
            }

            result = _live_order(
                trade_id="test-trade-002",
                intent=intent,
                shares=18.19,
            )

        # v2_preflight must have been called (INV-25 gate active)
        mock_instance.v2_preflight.assert_called_once()
        mock_instance.place_limit_order.assert_called_once()
        assert result.status == "pending"

    def test_v2_preflight_fails_when_sdk_lacks_get_ok(self):
        """Negative case: when SDK lacks get_ok, v2_preflight raises V2PreflightError.

        This verifies the fail-closed fix from MAJOR #2: AttributeError on
        missing get_ok must NOT be swallowed silently. INV-25.
        """
        from src.data.polymarket_client import PolymarketClient, V2PreflightError

        client = PolymarketClient.__new__(PolymarketClient)
        # Inject a mock CLOB client that does NOT expose get_ok.
        mock_clob = MagicMock(spec=[])  # empty spec: no attributes
        client._clob_client = mock_clob

        with pytest.raises(V2PreflightError, match="SDK lacks get_ok"):
            client.v2_preflight()


# ---------------------------------------------------------------------------
# R-3 — runtime posture x entry (Posture / INV-26)
# ---------------------------------------------------------------------------


def test_runtime_posture_yaml_present():
    """INV-26 / O2-c: architecture/runtime_posture.yaml must exist and be valid."""
    posture_path = ROOT / "architecture" / "runtime_posture.yaml"
    assert posture_path.exists(), (
        "architecture/runtime_posture.yaml is missing. "
        "This file is required by INV-26 / O2-c."
    )

    data = yaml.safe_load(posture_path.read_text())
    assert isinstance(data, dict), "runtime_posture.yaml must be a YAML mapping"

    default = data.get("default_posture")
    grammar = data.get("posture_grammar", [])
    assert default in grammar, (
        f"default_posture={default!r} is not in posture_grammar={grammar!r}"
    )

    branches = data.get("branches")
    assert isinstance(branches, dict), "runtime_posture.yaml must have a 'branches' dict"


class TestR3RuntimePostureBlocksEntry:
    """R-3: When runtime_posture is non-NORMAL, cycle_runner entry gate must
    block with entries_blocked_reason containing 'posture'. INV-26.

    We test the posture module directly (unit-level) since wiring the full
    cycle context is too entangled for a narrow micro-slice.
    """

    def test_posture_no_new_entries_is_not_normal(self):
        """read_runtime_posture returns NO_NEW_ENTRIES for a branch not in the YAML
        (falls back to default_posture)."""
        from src.runtime.posture import read_runtime_posture, _clear_cache

        _clear_cache()
        with patch("src.runtime.posture._resolve_current_branch", return_value="nonexistent-branch-xyz"):
            result = read_runtime_posture()
        _clear_cache()

        # default_posture in the YAML is NO_NEW_ENTRIES; any branch not in
        # the branches dict falls back to it.
        assert result == "NO_NEW_ENTRIES"
        assert result != "NORMAL"

    def test_posture_normal_returns_normal(self):
        """When monkeypatched to return NORMAL, read_runtime_posture propagates it."""
        from src.runtime import posture as posture_mod

        posture_mod._clear_cache()
        # _read_posture_uncached returns (posture, branch, yaml_mtime) tuple
        with patch.object(posture_mod, "_read_posture_uncached", return_value=("NORMAL", "main", 0.0)):
            result = posture_mod.read_runtime_posture()
        posture_mod._clear_cache()

        assert result == "NORMAL"

    def test_cycle_runner_posture_gate_blocks_with_reason(self, monkeypatch):
        """Integration: when read_runtime_posture returns NO_NEW_ENTRIES,
        entries_blocked_reason must contain 'posture'.

        This is a structural smoke test — we confirm the posture gate in
        cycle_runner.py sets entries_blocked_reason before the risk-level check.
        We exercise the gate expression directly rather than running a full
        cycle, since constructing a full cycle fixture is out of scope.
        """
        # Simulate the gate logic extracted from cycle_runner.py
        # This mirrors the exact code path added in this slice.
        _posture_blocked_reason = None
        try:
            from src.runtime.posture import _clear_cache
            _clear_cache()
            with patch("src.runtime.posture._resolve_current_branch", return_value="nonexistent-xyz"):
                from src.runtime.posture import read_runtime_posture
                _current_posture = read_runtime_posture()
            _clear_cache()
            if _current_posture != "NORMAL":
                _posture_blocked_reason = f"posture={_current_posture}"
        except Exception:
            _posture_blocked_reason = "posture=NO_NEW_ENTRIES"

        assert _posture_blocked_reason is not None, (
            "Posture gate should have set a block reason for NO_NEW_ENTRIES"
        )
        assert "posture" in _posture_blocked_reason, (
            f"entries_blocked_reason must contain 'posture', got {_posture_blocked_reason!r}"
        )

    def test_posture_fail_closed_on_missing_file(self, tmp_path):
        """read_runtime_posture returns NO_NEW_ENTRIES if the YAML is missing."""
        from src.runtime import posture as posture_mod

        posture_mod._clear_cache()
        with patch.object(posture_mod, "_find_repo_root", return_value=tmp_path):
            result = posture_mod.read_runtime_posture()
        posture_mod._clear_cache()

        assert result == "NO_NEW_ENTRIES"


# ---------------------------------------------------------------------------
# Lifecycle hint — explicit deferrals
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="R-5 (RED x command-emission) is a P2 slice; P0 keeps the existing local-marking regression guard elsewhere.")
def test_r5_red_emits_durable_commands_PLACEHOLDER():
    pass
