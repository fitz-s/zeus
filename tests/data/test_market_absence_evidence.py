from __future__ import annotations

from datetime import datetime, timezone

from src.data.market_absence_evidence import (
    clear_gamma_empty_families,
    has_recent_gamma_empty_evidence,
    record_gamma_empty_families,
)


def test_gamma_empty_absence_evidence_round_trip_with_metric_alias(tmp_path) -> None:
    path = tmp_path / "market_absence_evidence.json"
    now = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)

    record_gamma_empty_families(
        [("Auckland", "2026-06-26", "lowest temperature")],
        ttl_seconds=300,
        observed_at=now,
        path=path,
    )

    assert has_recent_gamma_empty_evidence(
        city="auckland",
        target_date="2026-06-26",
        metric="low",
        now=datetime(2026, 6, 25, 18, 4, tzinfo=timezone.utc),
        path=path,
    )
    assert not has_recent_gamma_empty_evidence(
        city="Auckland",
        target_date="2026-06-26",
        metric="high",
        now=datetime(2026, 6, 25, 18, 4, tzinfo=timezone.utc),
        path=path,
    )
    assert not has_recent_gamma_empty_evidence(
        city="Auckland",
        target_date="2026-06-26",
        metric="low",
        now=datetime(2026, 6, 25, 18, 6, tzinfo=timezone.utc),
        path=path,
    )


def test_gamma_empty_absence_evidence_is_cleared_by_later_listing(tmp_path) -> None:
    path = tmp_path / "market_absence_evidence.json"
    now = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)

    record_gamma_empty_families(
        [("Auckland", "2026-06-26", "lowest temperature")],
        ttl_seconds=300,
        observed_at=now,
        path=path,
    )

    removed = clear_gamma_empty_families(
        [("auckland", "2026-06-26", "low")],
        cleared_at=datetime(2026, 6, 25, 18, 1, tzinfo=timezone.utc),
        path=path,
    )

    assert removed == 1
    assert not has_recent_gamma_empty_evidence(
        city="Auckland",
        target_date="2026-06-26",
        metric="low",
        now=datetime(2026, 6, 25, 18, 2, tzinfo=timezone.utc),
        path=path,
    )
