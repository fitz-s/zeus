# Created: 2026-10-02
# Last reused or audited: 2026-10-02
# Authority basis: book_epoch_start deadline kills traced to a cold canonical
#   corpus load inside the cut (26.4 s cold vs a 45 s cut budget).
"""The canonical corpus is built off every decision path and swapped in whole."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import timedelta

import pytest

import src.calibration.market_anchored_live_fit as live_fit
from src.calibration.market_anchored_live_fit import (
    CanonicalCorpusBuilder,
    CanonicalCorpusCache,
    CanonicalMarketAnchoredFitProvider,
    MarketAnchoredArtifactCache,
)
from tests.calibration.test_market_anchored_live_fit import (
    NOW,
    _TEST_CITY_TIMEZONES,
    _canonical_corpus_fixture,
    _canonical_scope,
)


@pytest.fixture
def files(tmp_path):
    world, trade, _, forecast = _canonical_corpus_fixture(
        return_forecast=True, forecast_lineage=True,
    )
    forecast.commit()
    paths = []
    for source, name in zip((world, trade, forecast), ("world", "trade", "forecast"), strict=True):
        path = tmp_path / f"{name}.db"
        destination = sqlite3.connect(path)
        source.backup(destination)
        destination.close()
        source.close()
        paths.append(path)
    return tuple(paths)


def _open(paths):
    handles = []
    for path in paths:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        handles.append(conn)
    return tuple(handles)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def _builder(files, tmp_path, clock, *, cache=None, build=None):
    cache = cache or CanonicalCorpusCache()

    def in_process(path):
        live_fit.build_canonical_corpus_file(
            path, connects=lambda: _open(files),
            city_timezones=_TEST_CITY_TIMEZONES, now=clock,
        )

    builder = CanonicalCorpusBuilder(
        cache, path=tmp_path / "corpus.json", city_timezones=_TEST_CITY_TIMEZONES,
        connects=lambda: _open(files), build=build or in_process, now=clock,
    )
    cache.builder = builder
    return cache, builder


def _provider(handles, cache):
    return CanonicalMarketAnchoredFitProvider(
        lambda: handles, city_timezones=_TEST_CITY_TIMEZONES, min_train_rows=1,
        cache=MarketAnchoredArtifactCache(), corpus_cache=cache,
    )


def _add_finalized_payout(paths, condition="condition-late"):
    trade = sqlite3.connect(paths[1])
    trade.execute(
        "INSERT INTO venue_commands VALUES ('late','11',?,'o','snap-late','ENTRY','BUY','envelope')",
        (NOW.isoformat(),),
    )
    trade.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-late',?,'11','12','11',.4,'h',?,'{}')",
        (condition, NOW.isoformat()),
    )
    trade.execute(
        "INSERT INTO venue_trade_facts VALUES (90,'late','f','o','CONFIRMED',1,'tx',?,?,?,1,'{}')",
        (NOW.isoformat(),) * 3,
    )
    trade.execute(
        "INSERT INTO payout_observations VALUES (90,?,0,1,1,'RESOLVED_NONZERO','s',1,'h',?,NULL)",
        (condition, NOW.isoformat()),
    )
    trade.commit()
    trade.close()


def test_cut_never_loads_the_corpus_inline(files, tmp_path, monkeypatch):
    clock = Clock(NOW)
    cache, builder = _builder(files, tmp_path, clock)
    loads = []
    original = live_fit.load_canonical_fit_corpus

    def counted(*args, **kwargs):
        loads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(live_fit, "load_canonical_fit_corpus", counted)
    handles = _open(files)
    provider = _provider(handles, cache)
    # Unbuilt: the cut answers "unavailable" immediately and never loads.
    assert not provider.warm_corpus(now=NOW, deadline_monotonic=time.monotonic() + 45)
    assert provider.artifact(scope=_canonical_scope(), now=NOW) is None
    assert loads == []
    assert builder._wake.is_set()
    # Built off the decision path: the same cut serves the installed corpus.
    assert builder.step()
    assert loads == [threading.current_thread().name]
    assert provider.warm_corpus(now=NOW + timedelta(seconds=1))
    artifact = provider.artifact(scope=_canonical_scope(), now=NOW + timedelta(hours=1))
    assert artifact is not None and artifact.training_cutoff == NOW.isoformat()
    assert len(loads) == 1


def test_unchanged_inputs_do_not_rebuild_and_a_new_payout_does(files, tmp_path):
    clock = Clock(NOW)
    builds = []
    cache, builder = _builder(files, tmp_path, clock)
    inner = builder._build

    def counted(path):
        builds.append(clock())
        inner(path)

    builder._build = counted
    assert builder.step()
    clock.at = NOW + timedelta(minutes=30)
    assert not builder.step()
    _add_finalized_payout(files)
    assert builder.step()
    assert builds == [NOW, NOW + timedelta(minutes=30)]
    assert cache.built_cutoff(builder._key) == NOW + timedelta(minutes=30)


def test_validity_window_rebuilds_before_expiry_without_a_blind_period(files, tmp_path):
    clock = Clock(NOW)
    cache, builder = _builder(files, tmp_path, clock)
    assert builder.step()
    builder._build_seconds = 600.0  # a slow build must start that much earlier
    lead = timedelta(seconds=2 * 600 + builder._poll_seconds)
    clock.at = NOW + builder._ttl - lead - timedelta(seconds=1)
    assert not builder.step()
    clock.at = NOW + builder._ttl - lead
    assert builder.step()
    assert cache.built_cutoff(builder._key) == clock.at
    # The corpus it replaced still covers every instant before its expiry.
    handles = _open(files)
    provider = _provider(handles, cache)
    assert provider.warm_corpus(now=clock.at)


def test_interrupted_build_leaves_the_old_corpus_serving(files, tmp_path):
    clock = Clock(NOW)
    cache, builder = _builder(files, tmp_path, clock)
    assert builder.step()
    served = cache._built[builder._key][0][0]

    def interrupted(path):
        path.write_text("{\"format\":")  # a torn write must never be read
        raise KeyboardInterrupt

    builder._build = interrupted
    _add_finalized_payout(files)
    clock.at = NOW + timedelta(minutes=10)
    with pytest.raises(KeyboardInterrupt):
        builder.step()
    handles = _open(files)
    provider = _provider(handles, cache)
    assert provider.warm_corpus(now=clock.at)
    assert cache._built[builder._key][0][0] is served

    def failing(path):
        raise RuntimeError("child died")

    builder._build = failing
    builder._stop.set()
    builder._run()  # logs and keeps serving
    assert cache._built[builder._key][0][0] is served


def test_restart_serves_the_persisted_corpus_before_any_build(files, tmp_path, monkeypatch):
    clock = Clock(NOW)
    _, first = _builder(files, tmp_path, clock)
    assert first.step()
    monkeypatch.setattr(
        live_fit, "load_canonical_fit_corpus",
        lambda *a, **k: pytest.fail("a restart must not reload a valid corpus"),
    )
    clock.at = NOW + timedelta(hours=2)
    cache, builder = _builder(
        files, tmp_path, clock, build=lambda path: pytest.fail("no build at boot"),
    )
    assert builder.warm_from_disk()
    handles = _open(files)
    provider = _provider(handles, cache)
    assert provider.warm_corpus(now=clock.at, deadline_monotonic=time.monotonic() + 45)
    assert provider.artifact(scope=_canonical_scope(), now=clock.at) is not None
    # The persisted watermark still matches, so the first step builds nothing.
    assert not builder.step()


@pytest.mark.parametrize("field", ["format", "reader", "key"])
def test_persisted_corpus_with_any_other_identity_is_refused(files, tmp_path, field):
    clock = Clock(NOW)
    _, first = _builder(files, tmp_path, clock)
    assert first.step()
    path = tmp_path / "corpus.json"
    envelope = json.loads(path.read_text())
    envelope[field] = ["other"] if field == "key" else "other"
    path.write_text(json.dumps(envelope))
    cache, builder = _builder(files, tmp_path, clock)
    assert not builder.warm_from_disk()
    assert cache.built_cutoff(first._key) is None


def test_expired_persisted_corpus_is_installed_but_never_served(files, tmp_path):
    clock = Clock(NOW)
    _, first = _builder(files, tmp_path, clock)
    assert first.step()
    clock.at = NOW + timedelta(hours=6)
    cache, builder = _builder(files, tmp_path, clock)
    builder.warm_from_disk()
    handles = _open(files)
    assert not _provider(handles, cache).warm_corpus(now=clock.at)
    assert builder.step()  # the window law rebuilds it immediately
    assert _provider(handles, cache).warm_corpus(now=clock.at)


def test_actuation_replay_resolves_the_corpus_its_selection_saw(files, tmp_path):
    clock = Clock(NOW)
    cache, builder = _builder(files, tmp_path, clock)
    assert builder.step()
    handles = _open(files)
    provider = _provider(handles, cache)
    selected_at = NOW + timedelta(minutes=5)
    selected = provider.artifact(scope=_canonical_scope(), now=selected_at)
    _add_finalized_payout(files)
    clock.at = NOW + timedelta(minutes=6)
    assert builder.step()  # swap lands between selection and actuation
    replayed = provider.artifact(scope=_canonical_scope(), now=selected_at)
    assert replayed is not None and replayed.param_hash == selected.param_hash
    assert replayed.training_cutoff == NOW.isoformat()
    later = provider.artifact(scope=_canonical_scope(), now=clock.at)
    assert later.training_cutoff == clock.at.isoformat()


def test_boot_start_installs_from_disk_and_attaches_before_any_cut(files, tmp_path):
    clock = Clock(NOW)
    _, first = _builder(files, tmp_path, clock)
    assert first.step()
    cache = CanonicalCorpusCache()
    gate = threading.Event()
    builder = CanonicalCorpusBuilder(
        cache, path=tmp_path / "corpus.json", city_timezones=_TEST_CITY_TIMEZONES,
        connects=lambda: _open(files), build=lambda path: gate.wait(5), now=clock,
        poll_seconds=0.01,
    )
    builder.start()
    try:
        assert cache.builder is builder
        handles = _open(files)
        assert _provider(handles, cache).warm_corpus(now=NOW + timedelta(minutes=1))
    finally:
        builder.stop()
        gate.set()
