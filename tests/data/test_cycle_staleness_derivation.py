# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 — "如果没有新的数据,我们就应该使用上一次
#   获取的数据,而不是一直死,除非旧数据极度的不新鲜,这个数字取决于该发布频率的tolerance
#   而不是瞎猜". The staleness horizon must be DERIVED from the measured publication
#   rhythm (2 missed live cycles + measured publication lag), never a bare literal.
"""Category killed: a staleness gate whose number floats free of the publication-rhythm
derivation — either someone edits the constant without re-deriving (silent gate move) or
the formula inputs drift from their measured/policy bases without anyone noticing."""
from __future__ import annotations

import inspect

from src.data import replacement_forecast_cycle_policy as policy


class TestStalenessDerivation:
    def test_bound_is_derived_not_bare(self):
        # The default MUST equal the formula over its named inputs — editing any one
        # without the others (or replacing the expression with a literal) fails here.
        assert policy.REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT == (
            2.0 * policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS
            + policy.MEASURED_P50_PUBLICATION_LAG_HOURS
        )

    def test_inputs_match_their_declared_bases(self):
        # Live-eligible synoptic phases: all four standard UTC cycles. The
        # serving-staleness bound remains a 12h refresh-cadence policy because
        # that bound is empirical tolerance, not cycle-phase admission.
        assert policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS == 12.0
        assert policy._SYNOPTIC_CYCLE_HOURS == frozenset({0, 6, 12, 18})
        # Measured P50 publication lag (2026-06-11 evidence): 6h. If re-measured,
        # update BOTH the constant and the derivation comment evidence pointer.
        assert policy.MEASURED_P50_PUBLICATION_LAG_HOURS == 6.0

    def test_tolerates_exactly_one_missed_cycle(self):
        # The semantic the operator ordered: serve the LAST data through ONE full
        # missed live cycle (the 2026-06-10 12Z provider skip: 00Z data at 26.8h was
        # lawfully served); a SECOND consecutive miss crosses the bound.
        bound = policy.replacement_source_cycle_max_age_hours()
        one_miss_worst = (
            policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS  # waited for the next cycle
            + policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS  # that cycle was skipped
            + policy.MEASURED_P50_PUBLICATION_LAG_HOURS  # plus normal publication wait
        )
        assert bound >= one_miss_worst - 1e-9  # one miss: still served
        two_miss = one_miss_worst + policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS
        assert bound < two_miss  # two consecutive misses: extremely stale, refused

    def test_no_new_bare_30_literal(self):
        # The module must compute the default; a regression to a bare "30.0" literal
        # in the assignment is unconstructable.
        src = inspect.getsource(policy)
        assign = src.split("REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT = ", 1)[1]
        first_stmt = assign.split("\n_MAX_AGE_ENV", 1)[0]
        assert "30" not in first_stmt
        assert "LIVE_CYCLE_REFRESH_INTERVAL_HOURS" in first_stmt


class TestSingleReadinessAuthority:
    """Operator 2026-06-11 RULE-1 twin-clock incident: readiness expiry must derive from
    the SAME staleness bound as the cycle-age gate — a second guessed clock (the old
    computed_at+3h) is unconstructable."""

    def test_readiness_expiry_equals_cycle_plus_bound(self):
        from datetime import datetime, timedelta, timezone

        from src.data.replacement_forecast_cycle_policy import (
            replacement_readiness_expires_at,
            replacement_source_cycle_max_age_hours,
        )

        cycle = datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)
        assert replacement_readiness_expires_at(cycle) == cycle + timedelta(
            hours=replacement_source_cycle_max_age_hours()
        )

    def test_no_three_hour_clock_remains_at_stamp_sites(self):
        import inspect

        import src.data.replacement_forecast_materializer as mat
        import src.data.replacement_forecast_materialization_request_builder as rb

        for mod in (mat, rb):
            src = inspect.getsource(mod)
            assert "timedelta(hours=3)" not in src, mod.__name__
            assert "replacement_readiness_expires_at" in src, mod.__name__

    def test_registry_ttl_is_the_derived_bound(self):
        from src.contracts.time_semantics import _readiness_ttl_hours
        from src.data.replacement_forecast_cycle_policy import (
            replacement_source_cycle_max_age_hours,
        )

        assert _readiness_ttl_hours() == replacement_source_cycle_max_age_hours()
