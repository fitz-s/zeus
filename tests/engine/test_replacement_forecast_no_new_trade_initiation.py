# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-1 (evidence load-bearing). §0.3 antibody.
# History: re-authored 2026-06-07 to re-invert the post-f0368a188c "can initiate
#   new trade when flagged" contamination back to the original antibody: flags
#   alone (no evidence) can never grant new-trade-initiation authority.
from tests.test_replacement_forecast_runtime_policy import _flags

from src.data.replacement_forecast_runtime_policy import SHADOW_FLAG, TRADE_AUTHORITY_FLAG, VETO_FLAG, resolve_replacement_forecast_runtime_policy


def test_replacement_policy_cannot_initiate_new_trade_without_evidence() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True}))

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
