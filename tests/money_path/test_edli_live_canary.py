# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
from pathlib import Path


def test_live_canary_runtime_remains_disabled_until_executor_cut():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    assert edli["reactor_mode"] == "live_no_submit"
    assert edli["real_order_submit_enabled"] is False
    assert edli["day0_extreme_trigger_enabled"] is False
    assert edli["market_channel_ingestor_enabled"] is False
    assert "live_canary_enabled" not in edli


def test_live_canary_groundwork_has_live_cap_schema_and_verifiers():
    from src.decision_kernel import claims
    from src.decision_kernel.verifier import verify_actionable_trade, verify_execution_command
    from src.events.live_cap import LiveCapLedger

    assert claims.LIVE_CAP == "LiveCapCertificate"
    assert claims.FINAL_INTENT == "FinalIntentCertificate"
    assert claims.EXECUTOR_EXPRESSIBILITY == "ExecutorExpressibilityCertificate"
    assert callable(verify_actionable_trade)
    assert callable(verify_execution_command)
    assert LiveCapLedger.__name__ == "LiveCapLedger"
