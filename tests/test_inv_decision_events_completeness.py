# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.4 (Path D natural-key antibody, v3)
# SCAFFOLD: INV-decision-events-completeness antibody — pending T1 production pass

"""
Antibody test: INV-decision-events-completeness (natural-key, no ATTACH)

Invariant: for every decision-tagged forecast in the last 7 days (ensemble_snapshots_v2
WHERE causality_status='OK'), decision_events must carry at least one row keyed by
the matching natural tuple (market_slug, temperature_metric, target_date).

v3 changes from v2:
- Join key is market_slug (NOT market_id or condition_id).
  condition_id excluded because market_events_v2.condition_id is nullable
  (pre-discovery markets) — SQL "= NULL" would be silent failure.
- Uses get_world_connection_read_only() and get_forecasts_connection_read_only()
  thin wrappers added in PR-T1-A (= get_*_connection(write_class=None)).

Cross-module relationship test (Fitz §3 invariant pattern):
  forecasts.ensemble_snapshots_v2 → forecasts.market_events_v2 (city→market_slug)
  → world.decision_events (natural-key lookup by market_slug)

Independent read connections — INV-37 trivially honored (no ATTACH path).
Non-empty precondition: skip (not fail) if no decision-tagged forecasts in window.

xfail-strict: test will fail until T1 production is complete
(decision_events table does not exist yet).
"""

import pytest


@pytest.mark.xfail(strict=True, reason="SCAFFOLD — antibody pending T1 production")
def test_inv_decision_events_completeness_natural_key() -> None:
    """Cross-module: every decision-tagged forecast (7d, causality_status='OK')
    in ensemble_snapshots_v2 must have >= 1 decision_events row keyed by
    (market_slug, temperature_metric, target_date).

    market_slug join (NOT condition_id — per ultraplan v3 §4.4 critic-round-2 SEV-1).
    Independent read connections (INV-37 trivially honored — no ATTACH).
    city→market_slug resolved Python-side via market_events_v2.
    pytest.skip (not fail) if no candidates in 7d window.

    See PHASE_1_ULTRAPLAN.md §4.4 for full pseudocode.
    """
    pytest.fail(
        "SCAFFOLD — decision_events table does not exist yet; "
        "antibody pending T1 production (market_slug join, no condition_id)"
    )
