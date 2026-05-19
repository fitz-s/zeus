# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.5
# SCAFFOLD: INV-decision-events-completeness antibody — pending T1 production pass

"""
Antibody test: INV-decision-events-completeness

Invariant: for any recent live cycle, COUNT(decision_events WHERE cycle_id=X)
== COUNT(ensemble_snapshots_v2 WHERE cycle_id=X AND decision_group_id IS NOT NULL).

Join key: decision_group_id (not strategy_key).
Non-empty precondition: skip if cycle has no decision-tagged forecasts.

Read path (production): get_forecasts_connection_with_world() — sanctioned ATTACH
(forecasts=MAIN, world=ATTACHED). Read-only use only.

AMBIGUITY (for production pass): get_forecasts_connection_with_world() signature
is `*, write_class: WriteClass | str = "bulk"` — no `mode="ro"` kwarg exists.
§4.5 pseudocode uses mode="ro" which is incorrect. Production pass must open
with write_class="bulk" or use independent read connections.

xfail-strict: test will fail until T1 production is complete (table does not exist yet).
"""

import pytest


@pytest.mark.xfail(strict=True, reason="SCAFFOLD — antibody pending T1 production")
def test_inv_decision_events_completeness_per_recent_cycle() -> None:
    """SCAFFOLD: cross-DB count comparison via sanctioned ATTACH read.

    Production implementation (pending T1 production pass):
    1. Identify a recent live cycle_id with decision-tagged forecasts.
    2. Open get_forecasts_connection_with_world(write_class="bulk").
       (NOTE: no mode="ro" — production pass resolves correct read path)
    3. COUNT ensemble_snapshots_v2 WHERE cycle_id=? AND decision_group_id IS NOT NULL.
    4. COUNT decision_events WHERE cycle_id=?.
    5. Skip if n_forecast == 0 (non-degenerate precondition).
    6. Assert n_events == n_forecast.
    """
    pytest.fail("SCAFFOLD — decision_events table does not exist yet; antibody pending T1 production")
