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
        # Live-eligible cadence: 00Z/12Z only (operator cycle policy) => 12h.
        assert policy.LIVE_CYCLE_REFRESH_INTERVAL_HOURS == 12.0
        assert policy._SYNOPTIC_CYCLE_HOURS == frozenset({0, 12})
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
