"""Boundary-day settlement policy contracts (DT#7).

Law: docs/authority/zeus_current_architecture.md §22 (Boundary-day settlement
policy) + docs/authority/zeus_dual_track_architecture.md §DT#7.

Policy clauses:
  1. Reduce leverage on boundary-candidate positions
  2. Isolate oracle penalty for the affected city
  3. Refuse to treat boundary-ambiguous forecasts as confirmatory signal

Phase 9B scope (this module): deliver the named contract function for the
third clause — `boundary_ambiguous_refuses_signal` — so Phase 9C has a
stable seam to wire into the evaluator's candidate-decision flow when
`boundary_ambiguous` plumbing from `snapshot_ingest_contract` reaches
evaluator (blocks on monitor_refresh LOW wiring + data flow).

Phase 9C scope (deferred): the first two clauses (leverage reduction at
the Kelly boundary + oracle-penalty-per-city isolation) require runtime
threading of `boundary_ambiguous` and a new per-city oracle-isolation
field; land together when monitor_refresh LOW wiring activates.

Pre-P9B state: `boundary_ambiguous` field exists in
snapshot_ingest_contract.py + ensemble_snapshots_v2 schema (training-time
quarantine gate); ZERO runtime consumer in evaluator.py. P9B closes the
contract gap; P9C closes the enforcement gap.
"""

from __future__ import annotations

from typing import Any


def boundary_ambiguous_refuses_signal(snapshot_dict: dict[str, Any]) -> bool:
    """Return True if the snapshot is boundary-ambiguous — caller MUST refuse
    the candidate as confirmatory signal per DT#7 clause 3.

    Args:
        snapshot_dict: A dict carrying the snapshot payload. Typically the
            ensemble_snapshots_v2 row dict or the ingest-time payload from
            snapshot_ingest_contract.py. The function reads only the
            `boundary_ambiguous` key; other fields are ignored.

    Returns:
        True if the snapshot is flagged boundary-ambiguous; False otherwise
        (including the absent-key case — permissive default, safe fallback
        that does not accidentally refuse valid signals when field is
        missing during transition).

    Caller contract:
        When this returns True, the caller MUST NOT:
          - Treat the snapshot as confirmatory signal (e.g., Day0 entry
            confirmation, update-reaction amplification)
          - Enter a new position based on this snapshot alone
        The caller SHOULD:
          - Reject the candidate with a clear rejection_reason citing DT#7
          - Log the refusal for operator visibility

    Phase 9C will add wrappers for clauses 1 and 2 (leverage reduction +
    oracle-penalty isolation); those consumers will call this function
    first, then apply their own policy layer.
    """
    value = snapshot_dict.get("boundary_ambiguous", False)
    return bool(value)
