"""City-local calendar identity tests for market-anchored correction."""

# Created: 2026-09-08
# Last reused or audited: 2026-09-08
# Authority basis: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
from __future__ import annotations

from datetime import date, datetime, timezone
import sqlite3
from types import SimpleNamespace

from src.calibration.market_anchored_live_fit import (
    MarketAnchoredFitProvider,
    _city_local_target_date,
    corrected_probability,
)
from src.calibration.market_anchored_residual import (
    CLIP_D,
    LEAD_BUCKETS,
    P_CLIP_HI,
    P_CLIP_LO,
    ResidualCalibratorArtifact,
    FitRow,
    fit,
)
from src.calibration.market_anchored_live_fit import (
    get_active_provider,
    load_fit_rows,
    register_active_provider,
)
from src.engine import global_batch_runtime as runtime
from src.state import portfolio as portfolio_module
from src.state.portfolio import ExitContext, Position


def _artifact(*, snapshot, revision="city_local_target_date_v1"):
    return ResidualCalibratorArtifact(
        alpha={"day0": 0.10, "day1": 0.20, "day2": 0.30},
        beta=0.0,
        lambda_=1.0,
        clip_d=CLIP_D,
        p_clip=(P_CLIP_LO, P_CLIP_HI),
        lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-01-01T00:00:00Z",
        n_train=20,
        n_excluded=0,
        excluded_reasons={},
        param_hash="test",
        lead_calendar_revision=revision,
        city_timezone_snapshot=snapshot,
    )


def test_new_york_and_tokyo_use_city_local_target_date_for_serving():
    snapshot = (("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo"))
    artifact = _artifact(snapshot=snapshot)
    target = date(2026, 1, 2)
    ny = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)
    tokyo = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    assert _city_local_target_date(ny, "New York", snapshot) == date(2026, 1, 1)
    assert _city_local_target_date(tokyo, "Tokyo", snapshot) == target
    ny_result = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        city="New York",
        decision_at=ny,
        target_date=target,
        side="YES",
    )
    tokyo_result = corrected_probability(
        artifact,
        p0=0.35,
        q_raw=0.9,
        city="Tokyo",
        decision_at=tokyo,
        target_date=target,
        side="YES",
    )
    assert ny_result and ny_result[1] == "day1"
    assert tokyo_result and tokyo_result[1] == "day0"


def test_training_uses_the_same_city_local_boundary_as_serving():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE settlement_attribution (
        q_in_bin REAL, market_in_bin_prob REAL, settled_in_bin INTEGER,
        decision_posterior_computed_at TEXT, target_date TEXT,
        settled_at TEXT, graded_at TEXT, city TEXT, temperature_metric TEXT,
        traded_bin_label TEXT, direction TEXT)"""
    )
    conn.executemany(
        "INSERT INTO settlement_attribution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                0.9,
                0.35,
                1,
                "2026-01-02T00:30:00+00:00",
                "2026-01-02",
                "2026-01-03T00:00:00+00:00",
                None,
                "New York",
                "high",
                "bin-a",
                "buy_yes",
            ),
            (
                0.9,
                0.35,
                1,
                "2026-01-01T15:30:00+00:00",
                "2026-01-02",
                "2026-01-03T00:00:00+00:00",
                None,
                "Tokyo",
                "high",
                "bin-b",
                "buy_yes",
            ),
        ],
    )
    rows = load_fit_rows(
        conn,
        training_cutoff=datetime(2026, 1, 4, tzinfo=timezone.utc),
        city_timezone_snapshot=(
            ("New York", "America/New_York"),
            ("Tokyo", "Asia/Tokyo"),
        ),
    )
    assert [row.lead_bucket for row in rows] == ["day1", "day0"]


def test_calendar_revision_and_snapshot_are_part_of_artifact_identity():
    rows = [FitRow(p0=0.3, q_raw=0.4, lead_bucket="day0", y=1)]
    old = fit(rows, lambda_=1.0, training_cutoff="2026-01-01T00:00:00Z")
    local = fit(
        rows,
        lambda_=1.0,
        training_cutoff="2026-01-01T00:00:00Z",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("Tokyo", "Asia/Tokyo"),),
    )
    changed = fit(
        rows,
        lambda_=1.0,
        training_cutoff="2026-01-01T00:00:00Z",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(
            ("Tokyo", "Asia/Tokyo"),
            ("New York", "America/New_York"),
        ),
    )
    assert old.param_hash != local.param_hash
    assert local.param_hash != changed.param_hash
    assert local.city_timezone_snapshot == (("Tokyo", "Asia/Tokyo"),)


def test_dst_and_unmodeled_leads_remain_exact_and_fail_closed():
    snapshot = (("New York", "America/New_York"),)
    assert _city_local_target_date(
        datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), "New York", snapshot
    ) == date(2026, 3, 8)
    artifact = _artifact(snapshot=snapshot)
    decision = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert (
        corrected_probability(
            artifact,
            p0=0.3,
            q_raw=0.4,
            city="New York",
            decision_at=decision,
            target_date=date(2026, 1, 4),
            side="YES",
        )
        is None
    )
    assert (
        corrected_probability(
            artifact,
            p0=0.3,
            q_raw=0.4,
            city="New York",
            decision_at=decision.replace(tzinfo=None),
            target_date=date(2026, 1, 1),
            side="YES",
        )
        is None
    )


def test_old_or_invalid_artifact_identity_cannot_apply():
    old = _artifact(snapshot=(), revision="UNBOUND")
    invalid = _artifact(snapshot=(("New York", "No/Such_Zone"),))
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    for artifact in (old, invalid):
        assert (
            corrected_probability(
                artifact,
                p0=0.3,
                q_raw=0.4,
                city="New York",
                decision_at=now,
                target_date=date(2026, 1, 1),
                side="YES",
            )
            is None
        )
    assert (
        MarketAnchoredFitProvider(
            lambda: None, city_timezones={"New York": "No/Such_Zone"}
        ).artifact(now=now)
        is None
    )


def test_provider_rejects_naive_artifact_time_without_using_cache():
    calls = []
    provider = MarketAnchoredFitProvider(
        lambda: calls.append(True), city_timezones={"Tokyo": "Asia/Tokyo"}
    )
    assert provider.artifact(now=datetime(2026, 1, 1, 12)) is None
    assert calls == []


def test_target_context_uses_exact_payload_city_and_date():
    event = SimpleNamespace()
    contexts = runtime._target_context_by_family(
        {"opaque-family-hash": event},
        payload_reader=lambda _event: {"city": "Tokyo", "target_date": "2026-01-02"},
    )
    assert contexts == {"opaque-family-hash": ("Tokyo", date(2026, 1, 2))}
    missing = runtime._target_context_by_family(
        {"opaque-family-hash": event},
        payload_reader=lambda _event: {"city": " Tokyo ", "target_date": "2026-01-02"},
    )
    assert missing == {"opaque-family-hash": (" Tokyo ", date(2026, 1, 2))}


def test_resolver_snapshots_all_runtime_cities(monkeypatch):
    class CapturingProvider:
        seen = None

        def __init__(self, connect, **kwargs):
            self.__class__.seen = kwargs["city_timezones"]

        def artifact(self, *, now):
            return None

    monkeypatch.setattr(
        runtime,
        "_market_anchored_correction_resolver",
        runtime._market_anchored_correction_resolver,
    )
    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.MarketAnchoredFitProvider",
        CapturingProvider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "Tokyo": SimpleNamespace(timezone="Asia/Tokyo"),
            "New York": SimpleNamespace(timezone="America/New_York"),
            "Broken": SimpleNamespace(timezone="No/Such_Zone"),
        },
    )
    resolver = runtime._market_anchored_correction_resolver(
        object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))}
    )
    assert resolver is not None
    assert CapturingProvider.seen == {
        "Tokyo": "Asia/Tokyo",
        "New York": "America/New_York",
        "Broken": "No/Such_Zone",
    }


def test_entry_resolver_and_held_exit_use_actual_city_local_callers(monkeypatch):
    artifact = _artifact(
        snapshot=(("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo"))
    )

    class StubProvider:
        def __init__(self, connect, **kwargs):
            self.city_timezones = kwargs["city_timezones"]

        def artifact(self, *, now):
            return artifact

    monkeypatch.setattr(
        "src.calibration.market_anchored_live_fit.MarketAnchoredFitProvider",
        StubProvider,
    )
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "New York": SimpleNamespace(timezone="America/New_York"),
            "Tokyo": SimpleNamespace(timezone="Asia/Tokyo"),
        },
    )
    resolver = runtime._market_anchored_correction_resolver(
        object(),
        target_context_by_family={
            "ny": ("New York", date(2026, 1, 2)),
            "tokyo": ("Tokyo", date(2026, 1, 2)),
        },
    )
    ny = SimpleNamespace(family_key="ny", side="YES", bin_id="b", token_id="t")
    tokyo = SimpleNamespace(family_key="tokyo", side="YES", bin_id="b", token_id="t")
    ny_correction = resolver(
        ny, 0.9, 0.35, datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)
    )
    tokyo_correction = resolver(
        tokyo, 0.9, 0.35, datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
    )
    assert ny_correction and ny_correction.lead_bucket == "day1"
    assert tokyo_correction and tokyo_correction.lead_bucket == "day0"

    fixed_now = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

    monkeypatch.setattr(portfolio_module, "datetime", FixedDateTime)
    provider = StubProvider(None, city_timezones={})
    monkeypatch.setattr(provider, "artifact", lambda *, now: artifact)
    register_active_provider(provider)
    context = ExitContext(
        fresh_prob=0.9,
        fresh_prob_is_fresh=True,
        current_market_price=0.35,
        current_market_price_is_fresh=True,
        best_bid=0.3,
        best_ask=0.4,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        current_ci=(0.05, 0.95),
        belief_available=True,
    )
    ny_position = Position(
        trade_id="ny",
        market_id="m",
        city="New York",
        cluster="x",
        target_date="2026-01-02",
        bin_label="b",
        direction="buy_yes",
    )
    tokyo_position = Position(
        trade_id="tokyo",
        market_id="m",
        city="Tokyo",
        cluster="x",
        target_date="2026-01-02",
        bin_label="b",
        direction="buy_yes",
    )
    ny_q = ny_position._exit_q_mean_and_source(context)
    tokyo_q = tokyo_position._exit_q_mean_and_source(context)
    assert ny_q[2] == tokyo_q[2] == "market_anchored"
    assert ny_q[0] != tokyo_q[0]
    register_active_provider(None)


def test_snapshot_failure_and_empty_context_clear_stale_provider(monkeypatch, caplog):
    stale = object()
    register_active_provider(stale)
    assert (
        runtime._market_anchored_correction_resolver(
            object(), target_context_by_family={}
        )
        is None
    )
    assert get_active_provider() is None

    register_active_provider(stale)
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: (_ for _ in ()).throw(ValueError("bad city snapshot")),
    )
    assert (
        runtime._market_anchored_correction_resolver(
            object(), target_context_by_family={"one": ("Tokyo", date(2026, 1, 2))}
        )
        is None
    )
    assert get_active_provider() is None
    assert "MARKET_ANCHORED_CITY_SNAPSHOT_UNAVAILABLE:ValueError" in caplog.text
