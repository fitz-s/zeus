from scripts.audit_city_data_readiness import _live_status, _runtime_status


def test_live_status_marks_archive_gaps_pending():
    assert _runtime_status(runtime_blockers=[]) == "live_ready"
    assert _live_status(runtime_blockers=[], archive_gaps=["tigge_coverage_incomplete"]) == "archive_pending"


def test_live_status_keeps_runtime_blockers_hard():
    assert _runtime_status(runtime_blockers=["no_market_events"]) == "no_active_market"
    assert _live_status(runtime_blockers=["no_market_events"], archive_gaps=[]) == "no_active_market"
    assert _live_status(runtime_blockers=["no_observations"], archive_gaps=[]) == "data_unavailable"
