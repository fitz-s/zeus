# Created: 2026-06-07
# Last reused/audited: 2026-06-09
# Authority basis: docs/the_path/REAUDIT_0_1.md §1 + REALIGN_0_1_AUTHORITY.md +
#   PR_SPEC.md §2 FIX-1 (ORIGINAL: settlement-evidence gate load-bearing on the live
#   0.1 path). SUPERSEDED 2026-06-08: operator directive REMOVED the settlement-
#   evidence promotion gate from BOTH live-authority sites (commits b646f99339 +
#   54a53334a9); LIVE_AUTHORITY is now FLAG-ONLY. The 3 tests asserting the gate
#   DENIES the live path were DEAD_TEST-removed (the gating they pinned is deleted;
#   the removal is owned by test_replacement_live_authority_evidence_gate_wiring_
#   honesty.py). The 2 surviving tests validate the still-shadow-defined gate as a
#   pure predicate and the live 0.1 path's positive (flag-on) authority stamp.
"""Tests for the (now shadow-only) replacement_0_1 live-authority evidence gate.

CURRENT LAW: the settlement-evidence gate NO LONGER gates the live 0.1 authority
path (operator-directed flag-only LIVE_AUTHORITY, 2026-06-08). Retained here:
  - the gate as a PURE PREDICATE contract (it stays defined for shadow); and
  - the live 0.1 path's POSITIVE authority stamp when the flag/path is armed.
The deleted "absent/failing evidence -> DEGRADE to None" gating is pinned removed
by tests/test_replacement_live_authority_evidence_gate_wiring_honesty.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine import event_reactor_adapter as adapter
from src.data import replacement_forecast_runtime_policy as runtime_policy
from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    VETO_FLAG,
    TRADE_AUTHORITY_FLAG,
    KELLY_INCREASE_FLAG,
    DIRECTION_FLIP_FLAG,
    replacement_live_authority_evidence_gate,
    resolve_replacement_forecast_runtime_policy,
)
from src.types.market import Bin

# Reuse the canonical passing-evidence fixtures so the positive path stays in lock
# step with the runtime-policy test law (same dataclasses, same fields).
from tests.test_replacement_forecast_runtime_policy import (
    _capital_objective_evidence,
    _passing_evidence,
)

# The REAL on-disk failing promotion_evidence.json lives in the LIVE tree (an
# uncommitted runtime-state file). The worktree has no copy; we load the LIVE
# payload READ-ONLY through the production loaders to prove the gate denies the
# exact evidence the daemon would load today.
_LIVE_EVIDENCE_PATH = Path("/Users/leofitz/zeus/state/replacement_forecast_shadow/promotion_evidence.json")


def _family() -> SimpleNamespace:
    return SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-27",
                yes_token_id="yes-27",
                no_token_id="no-27",
                bin=Bin(low=27.0, high=27.0, unit="C", label="27C"),
            ),
            SimpleNamespace(
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28C"),
            ),
        ),
    )


def _replacement_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-27": 0.20, "bin-28": 0.80},
        q_lcb=None,
        provenance_json={
            # FIX 1 (2026-06-09): live-eligible q-mode so the success-path fixture reaches the
            # proof return it asserts (the new gate runs before the evidence-gate logic here).
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "aifs_member_count": 51,
            "aifs_probabilities": {"bin-27": 10 / 51, "bin-28": 41 / 51},
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )


def _native_costs() -> dict:
    from src.contracts.execution_price import ExecutionPrice

    return {
        ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
        ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }


def _arm_flag_and_stub_forecast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag True; readiness + bundle + bin-binding all PASS, so EVIDENCE is the
    ONLY remaining gate on the 0.1 path."""
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)
    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=_replacement_bundle(), reason_code="READY"),
    )


def _real_on_disk_failing_promotion_evidence():
    from src.data.replacement_forecast_go_live_report import (
        replacement_forecast_promotion_evidence_from_payload,
        replacement_forecast_capital_objective_evidence_from_payload,
    )

    payload = json.loads(_LIVE_EVIDENCE_PATH.read_text(encoding="utf-8"))
    promo = replacement_forecast_promotion_evidence_from_payload(payload)
    cap = replacement_forecast_capital_objective_evidence_from_payload(payload)
    return promo, cap


# --------------------------------------------------------------------------- #
# §1.1 — the pure gate function itself (one builder, no IO)
# --------------------------------------------------------------------------- #


def test_evidence_gate_pure_predicate_contract() -> None:
    # None promotion -> required code
    permitted, codes = replacement_live_authority_evidence_gate(None, _capital_objective_evidence())
    assert permitted is False
    assert codes == ("REPLACEMENT_LIVE_AUTHORITY_PROMOTION_EVIDENCE_REQUIRED",)

    # None capital -> required code
    permitted, codes = replacement_live_authority_evidence_gate(_passing_evidence(), None)
    assert permitted is False
    assert codes == ("REPLACEMENT_LIVE_AUTHORITY_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED",)

    # Both present but one fails -> union of blocking codes
    promo, cap = _real_on_disk_failing_promotion_evidence()
    permitted, codes = replacement_live_authority_evidence_gate(promo, cap)
    assert permitted is False
    expected = tuple(promo.blocking_reason_codes()) + tuple(cap.blocking_reason_codes())
    assert codes == expected

    # Both passing -> permitted, no codes
    permitted, codes = replacement_live_authority_evidence_gate(
        _passing_evidence(), _capital_objective_evidence()
    )
    assert permitted is True
    assert codes == ()


# --------------------------------------------------------------------------- #
# §1.6 test 1 — RELATIONSHIP: 0.1 path denies authority on absent/failing evidence
# --------------------------------------------------------------------------- #


# DEAD_TEST removed 2026-06-09: test_replacement_0_1_authority_denied_when_settlement_
# evidence_absent_or_failing asserted the evidence gate DEGRADES the LIVE 0.1 authority
# path to None on absent/failing evidence. That gating was DELETED by operator directive
# 2026-06-08 (commits b646f99339 "remove promotion/capital-objective evidence gate from
# BOTH live-authority sites" + 54a53334a9): LIVE_AUTHORITY is now FLAG-ONLY and BAYES_PRECISION_FUSION runs
# live without the settlement-evidence gate. The removal is pinned by
# tests/test_replacement_live_authority_evidence_gate_wiring_honesty.py (the superseding
# owner of this surface). The dead gating invariant has no surviving new form here.


def test_replacement_0_1_authority_granted_when_both_evidence_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive companion: with BOTH evidence objects passing the 0.1 path
    produces its q and stamps the q_source (the success path moved BEHIND the
    evidence gate, per REAUDIT §1.6 note)."""
    _arm_flag_and_stub_forecast_path(monkeypatch)

    payload: dict[str, object] = {}
    result = adapter._replacement_authority_probability_and_fdr_proof(
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        payload=payload,
        family=_family(),
        conn=object(),
        native_costs=_native_costs(),
        decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    assert result is not None
    q_by_condition, _lcb, _p, _pre, evidence = result
    assert evidence["probability_authority"] == "replacement_0_1"
    assert payload["_edli_q_source"] == "replacement_0_1"
    assert q_by_condition == {"cond-27": pytest.approx(0.20), "cond-28": pytest.approx(0.80)}


# DEAD_TEST removed 2026-06-09: test_single_owner_gate_observed_by_both_sites and
# test_failing_evidence_keeps_legacy_backstop_enabled both asserted the evidence gate
# DENIES/degrades the LIVE 0.1 authority path (one monkeypatched a removed adapter
# symbol `replacement_live_authority_evidence_gate`, now an AttributeError). That live-
# path gating was deleted by operator directive 2026-06-08 (b646f99339 + 54a53334a9):
# LIVE_AUTHORITY is FLAG-ONLY; the gate function remains shadow-defined in runtime_policy
# but is NO LONGER imported into or called by the reactor. The removal is pinned by
# tests/test_replacement_live_authority_evidence_gate_wiring_honesty.py.
