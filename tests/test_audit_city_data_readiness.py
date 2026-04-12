from scripts.audit_city_data_readiness import _paper_status, _runtime_status


def test_paper_status_keeps_archive_gaps_shadow_only():
    assert _runtime_status(runtime_blockers=[]) == "paper_ready"
    assert _paper_status(runtime_blockers=[], archive_gaps=["tigge_coverage_incomplete"]) == "shadow_only"


def test_paper_status_keeps_runtime_blockers_hard():
    assert _runtime_status(runtime_blockers=["no_market_events"]) == "no_active_market"
    assert _paper_status(runtime_blockers=["no_market_events"], archive_gaps=[]) == "no_active_market"
    assert _paper_status(runtime_blockers=["no_observations"], archive_gaps=[]) == "data_unavailable"
