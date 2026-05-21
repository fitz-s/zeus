# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/08_PHASE_7_SETTLEMENT_TYPE_GATE.md §T3
#                  + docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T3
"""SettlementCaptureVerifier — coherence gate for settlement timestamp chains.

3-valued verdict:
  COHERENT   — all 4 timestamps populated AND fact_known ≤ source_published ≤ venue_resolved ≤ redeemed.
  INCOHERENT — all 4 populated BUT ordering violated (e.g. venue_resolved < source_published).
  INCOMPLETE — subset populated; ordering cannot be fully evaluated.

Writes one audit row per (city, target_date, temperature_metric) into
settlement_capture_verifications on zeus-forecasts.db under INV-37
ATTACH+SAVEPOINT atomicity (caller-conn mode bypasses SAVEPOINT to avoid
with-conn collision per MEMORY: feedback_with_conn_nested_savepoint_audit).

Pre-promotion gate: resolution_window_maker / settlement_capture strategies
require a configurable recent count of COHERENT verdicts before promotion.
Threshold sourced from config/settings.json::settlement_capture_coherent_gate_n.
Default 5 when key absent.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Outcome of a single settlement capture verification check."""
    city: str
    target_date: str
    temperature_metric: str
    fact_known_time: Optional[str]
    source_published_time: Optional[str]
    venue_resolved_time: Optional[str]
    redeemed_time: Optional[str]
    coherence_verdict: str  # 'COHERENT' | 'INCOHERENT' | 'INCOMPLETE'
    incoherence_reason: Optional[str]
    evidence_tier: Optional[str]


_VALID_VERDICTS = frozenset({"COHERENT", "INCOHERENT", "INCOMPLETE"})


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class SettlementCaptureVerifier:
    """Verify settlement timestamp coherence for a given position or row dict.

    Usage::

        verifier = SettlementCaptureVerifier()
        result = verifier.verify(position_or_dict)
        # result.coherence_verdict in {'COHERENT', 'INCOHERENT', 'INCOMPLETE'}

    Write to DB::

        verifier.write_result(result)           # uses get_forecasts_connection_with_world
        verifier.write_result(result, conn=c)   # caller owns transaction
    """

    # ------------------------------------------------------------------
    # Core verdict logic (pure function, no I/O)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_verdict(
        fact_known_time: Optional[str],
        source_published_time: Optional[str],
        venue_resolved_time: Optional[str],
        redeemed_time: Optional[str],
    ) -> tuple[str, Optional[str]]:
        """Derive coherence_verdict + optional incoherence_reason.

        Returns:
            (verdict, reason) where verdict is 'COHERENT'|'INCOHERENT'|'INCOMPLETE'
            and reason is None on COHERENT/INCOMPLETE.
        """
        timestamps = [fact_known_time, source_published_time, venue_resolved_time, redeemed_time]
        populated = [t for t in timestamps if t is not None and t != ""]

        if len(populated) < 4:
            return "INCOMPLETE", None

        # All 4 populated — check ordering
        t_fact, t_src, t_venue, t_redeem = (
            fact_known_time,
            source_published_time,
            venue_resolved_time,
            redeemed_time,
        )
        # Compare as ISO-8601 strings (lexicographic order is correct for UTC timestamps)
        violations: list[str] = []
        if t_fact > t_src:  # type: ignore[operator]
            violations.append(f"fact_known({t_fact}) > source_published({t_src})")
        if t_src > t_venue:  # type: ignore[operator]
            violations.append(f"source_published({t_src}) > venue_resolved({t_venue})")
        if t_venue > t_redeem:  # type: ignore[operator]
            violations.append(f"venue_resolved({t_venue}) > redeemed({t_redeem})")

        if violations:
            return "INCOHERENT", "; ".join(violations)
        return "COHERENT", None

    # ------------------------------------------------------------------
    # verify() — accepts a Position dataclass or a plain dict
    # ------------------------------------------------------------------

    def verify(self, position_or_dict: Any) -> VerificationResult:
        """Compute a VerificationResult for the given position.

        Accepts either a ``Position`` dataclass instance or a plain dict with
        the same field names (for testing without a full Position object).
        """
        if hasattr(position_or_dict, "__dataclass_fields__"):
            # Position dataclass
            d = {
                "city": getattr(position_or_dict, "city", ""),
                "target_date": getattr(position_or_dict, "target_date", ""),
                "temperature_metric": getattr(position_or_dict, "temperature_metric", ""),
                "fact_known_time": getattr(position_or_dict, "fact_known_time", None),
                "source_published_time": getattr(position_or_dict, "source_published_time", None),
                "venue_resolved_time": getattr(position_or_dict, "venue_resolved_time", None),
                "redeemed_time": getattr(position_or_dict, "redeemed_time", None),
                "evidence_tier": getattr(position_or_dict, "evidence_tier", None),
            }
        else:
            d = dict(position_or_dict)

        fact_known_time = d.get("fact_known_time") or None
        source_published_time = d.get("source_published_time") or None
        venue_resolved_time = d.get("venue_resolved_time") or None
        redeemed_time = d.get("redeemed_time") or None

        # Normalize evidence_tier to string
        raw_tier = d.get("evidence_tier")
        tier_str: Optional[str] = None
        if isinstance(raw_tier, EvidenceTier):
            tier_str = raw_tier.name
        elif isinstance(raw_tier, str) and raw_tier:
            tier_str = raw_tier

        verdict, reason = self.compute_verdict(
            fact_known_time, source_published_time, venue_resolved_time, redeemed_time,
        )

        return VerificationResult(
            city=str(d.get("city") or ""),
            target_date=str(d.get("target_date") or ""),
            temperature_metric=str(d.get("temperature_metric") or ""),
            fact_known_time=fact_known_time,
            source_published_time=source_published_time,
            venue_resolved_time=venue_resolved_time,
            redeemed_time=redeemed_time,
            coherence_verdict=verdict,
            incoherence_reason=reason,
            evidence_tier=tier_str,
        )

    # ------------------------------------------------------------------
    # write_result() — INV-37 ATTACH+SAVEPOINT
    # ------------------------------------------------------------------

    def write_result(
        self,
        result: VerificationResult,
        *,
        conn: Optional[Any] = None,
    ) -> None:
        """Upsert a VerificationResult row into settlement_capture_verifications.

        INV-37 ATTACH+SAVEPOINT PATTERN (mirrors settlement_writers.py):
          conn=None  → acquires get_forecasts_connection_with_world + SAVEPOINT.
          conn!=None → caller owns the transaction; no SAVEPOINT added.

        Raises:
            ValueError: if result.coherence_verdict is not one of VALID_VERDICTS.
        """
        if result.coherence_verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"Invalid coherence_verdict {result.coherence_verdict!r}; "
                f"must be one of {sorted(_VALID_VERDICTS)}"
            )

        def _execute(active_conn: Any) -> None:
            active_conn.execute(
                """
                INSERT INTO settlement_capture_verifications
                    (city, target_date, temperature_metric,
                     fact_known_time, source_published_time,
                     venue_resolved_time, redeemed_time,
                     coherence_verdict, incoherence_reason, evidence_tier)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                    fact_known_time     = excluded.fact_known_time,
                    source_published_time = excluded.source_published_time,
                    venue_resolved_time = excluded.venue_resolved_time,
                    redeemed_time       = excluded.redeemed_time,
                    coherence_verdict   = excluded.coherence_verdict,
                    incoherence_reason  = excluded.incoherence_reason,
                    evidence_tier       = excluded.evidence_tier,
                    recorded_at         = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                """,
                (
                    result.city,
                    result.target_date,
                    result.temperature_metric,
                    result.fact_known_time,
                    result.source_published_time,
                    result.venue_resolved_time,
                    result.redeemed_time,
                    result.coherence_verdict,
                    result.incoherence_reason,
                    result.evidence_tier,
                ),
            )

        if conn is not None:
            _execute(conn)
        else:
            from src.state.db import get_forecasts_connection_with_world
            with get_forecasts_connection_with_world() as _conn:
                _conn.execute("SAVEPOINT scv_write")
                try:
                    _execute(_conn)
                    _conn.execute("RELEASE SAVEPOINT scv_write")
                except Exception:
                    _conn.execute("ROLLBACK TO SAVEPOINT scv_write")
                    raise


# ---------------------------------------------------------------------------
# Pre-promotion gate
# ---------------------------------------------------------------------------

def check_pre_promotion_gate(
    city: str,
    temperature_metric: str,
    *,
    conn: Optional[Any] = None,
    threshold: Optional[int] = None,
) -> bool:
    """Return True if recent COHERENT count meets the promotion threshold.

    Threshold sourced from config/settings.json::settlement_capture_coherent_gate_n.
    Default 5 when absent.

    Args:
        city: City to check.
        temperature_metric: 'high' or 'low'.
        conn: Optional pre-established connection. When None, opens a fresh
              read-only connection to zeus-forecasts.db.
        threshold: Override threshold (for testing). When None, reads from settings.

    Returns:
        True if COUNT(COHERENT) >= threshold for the given city+metric.
    """
    if threshold is None:
        try:
            from src.config import settings as _settings
            threshold = int(_settings.get("settlement_capture_coherent_gate_n", 5))
        except Exception:
            threshold = 5

    def _query(active_conn: Any) -> bool:
        row = active_conn.execute(
            """
            SELECT COUNT(*) FROM settlement_capture_verifications
            WHERE city = ? AND temperature_metric = ? AND coherence_verdict = 'COHERENT'
            """,
            (city, temperature_metric),
        ).fetchone()
        count = row[0] if row else 0
        return int(count) >= threshold  # type: ignore[arg-type]

    if conn is not None:
        return _query(conn)

    from src.state.db import get_forecasts_connection
    with get_forecasts_connection() as _conn:
        return _query(_conn)
