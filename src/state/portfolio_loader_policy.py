"""Portfolio loader authority policy.

The module makes DB-vs-fallback authority decisions explicit. It does not load
data itself; callers remain responsible for using the selected surface.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoaderPolicyDecision:
    source: str
    reason: str
    escalate: bool = False


def choose_portfolio_truth_source(
    snapshot_status: str | None,
    *,
    merge_supported: bool = False,
) -> LoaderPolicyDecision:
    status = (snapshot_status or "unknown").strip().lower()
    if status in ("ok", "empty"):
        # "empty" is a valid canonical state (zero positions, e.g. post-nuke
        # or fresh start). An empty DB is healthy, not degraded.
        return LoaderPolicyDecision(
            source="canonical_db",
            reason="canonical snapshot healthy" if status == "ok" else "canonical DB healthy but empty (zero positions)",
        )
    if status == "partial_stale":
        if merge_supported:
            return LoaderPolicyDecision(
                source="canonical_db_with_merge",
                reason="partial_stale allowed because stale-open merge is explicitly supported",
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
