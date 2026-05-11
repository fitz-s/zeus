# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: status_summary.py silent-failure antibody — _safe_component error propagation
#
# Relationship invariant: when _collateral_summary raises sqlite3.OperationalError,
# the collateral_ledger_global component's details dict MUST expose error_type and error,
# not silently swallow them as configured=False / authority_tier=UNKNOWN.

import sqlite3
from unittest.mock import patch

import pytest

from src.observability.status_summary import _get_execution_capability_status


def _find_collateral_component(result: dict) -> dict | None:
    """Locate collateral_ledger_global component from any action's components list."""
    for action_key in ("entry", "exit"):
        action = result.get(action_key, {})
        for comp in action.get("components", []):
            if comp.get("component") == "collateral_ledger_global":
                return comp
    return None


class TestCollateralSilentFailureAntibody:
    """
    Relationship test: _safe_component loader exception must be visible in downstream
    _collateral_component's details, never silently collapsed into UNKNOWN/False.
    """

    def test_db_lock_error_propagates_into_collateral_component_details(self):
        """
        When _collateral_summary raises sqlite3.OperationalError("database is locked"),
        the collateral_ledger_global component's details must contain:
          - error_type == "OperationalError"
          - error == "database is locked"
        and must NOT appear as a normal unconfigured state (configured=False, authority_tier=UNKNOWN
        without any error signal).
        """
        db_lock_exc = sqlite3.OperationalError("database is locked")

        with patch(
            "src.observability.status_summary._collateral_summary",
            side_effect=db_lock_exc,
        ):
            result = _get_execution_capability_status()

        comp = _find_collateral_component(result)
        assert comp is not None, "collateral_ledger_global component not found in entry/exit"

        details = comp.get("details", {})

        # Relationship assertion: loader exception must surface in details
        assert details.get("error_type") == "OperationalError", (
            f"expected error_type='OperationalError' in details, got: {details}"
        )
        assert details.get("error") == "database is locked", (
            f"expected error='database is locked' in details, got: {details}"
        )

        # Sanity: component must be blocked (not allowed) when loader failed
        assert comp.get("allowed") is False, (
            f"collateral_ledger_global must be blocked on loader failure, got allowed={comp.get('allowed')}"
        )

    def test_db_lock_sets_loader_failed_flag(self):
        """
        _safe_component on exception must inject loader_failed=True into details
        so operators can distinguish DB-lock from genuine unconfigured state.
        """
        db_lock_exc = sqlite3.OperationalError("database is locked")

        with patch(
            "src.observability.status_summary._collateral_summary",
            side_effect=db_lock_exc,
        ):
            result = _get_execution_capability_status()

        comp = _find_collateral_component(result)
        assert comp is not None
        details = comp.get("details", {})

        assert details.get("loader_failed") is True, (
            f"expected loader_failed=True in details to distinguish DB-lock from unconfigured, got: {details}"
        )

    def test_db_lock_sets_degraded_authority_tier(self):
        """
        On loader failure, authority_tier must be 'DEGRADED' (not 'UNKNOWN'),
        so operators can distinguish 'never configured' from 'failed at load time'.
        """
        db_lock_exc = sqlite3.OperationalError("database is locked")

        with patch(
            "src.observability.status_summary._collateral_summary",
            side_effect=db_lock_exc,
        ):
            result = _get_execution_capability_status()

        comp = _find_collateral_component(result)
        assert comp is not None
        details = comp.get("details", {})

        assert details.get("authority_tier") == "DEGRADED", (
            f"expected authority_tier='DEGRADED' on loader failure, got: {details}"
        )
