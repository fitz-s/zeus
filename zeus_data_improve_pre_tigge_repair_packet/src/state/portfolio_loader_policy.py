from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoaderPolicyDecision:
    source: str
    reason: str
    escalate: bool = False



def choose_portfolio_truth_source(snapshot_status: str | None, *, merge_supported: bool = False) -> LoaderPolicyDecision:
    status = (snapshot_status or "unknown").strip().lower()
    if status == "ok":
        return LoaderPolicyDecision(source="canonical_db", reason="canonical snapshot fully healthy")
    if status == "partial_stale":
        if merge_supported:
            return LoaderPolicyDecision(
                source="canonical_db_with_merge",
                reason="partial stale allowed because stale-open merge is explicitly supported",
            )
        return LoaderPolicyDecision(
            source="json_fallback",
            reason="partial_stale must not silently hide open positions before merge logic exists",
            escalate=True,
        )
    return LoaderPolicyDecision(
        source="json_fallback",
        reason=f"canonical snapshot unavailable: {status}",
        escalate=True,
    )
