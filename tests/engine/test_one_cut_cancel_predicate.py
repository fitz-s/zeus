# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: cut-cancel throughput task (2026-09-29): one predicate
#   decides whether a wake invalidates a running cut; a structural antibody
#   prevents a second copy.
"""``reactor_wake.cut_invalidating_wakes`` is the only cut-cancel predicate.

Antibody: the modules that judge a running cut (the live adapter, the global
batch runtime and the reactor's wake-cancellation probe) name no wake reason
and read no wake queue or urgent marker directly; they ask the one predicate.
The predicate's truth table is pinned below.
"""

from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.events.candidate_binding import weather_family_id
from src.runtime import reactor_wake
from src.runtime.reactor_wake import CutDependency, cut_invalidating_wakes

_ROOT = Path(__file__).resolve().parents[2]
_WAKE_REASONS = frozenset(reactor_wake._WAKE_KIND_BY_REASON) | {
    reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
}
_DIRECT_READERS = frozenset(
    {
        "reactor_wakes_since",
        "reactor_urgent_wake_identity",
        "reactor_urgent_wake_reason",
        "_read_reactor_wake_path",
    }
)


def _judging_nodes() -> list[tuple[str, ast.AST]]:
    nodes: list[tuple[str, ast.AST]] = []
    for relative in (
        "src/engine/event_reactor_adapter.py",
        "src/engine/global_batch_runtime.py",
    ):
        nodes.append((relative, ast.parse((_ROOT / relative).read_text())))
    reactor = ast.parse((_ROOT / "src/events/reactor.py").read_text())
    probe = next(
        node
        for node in reactor.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_reactor_wake_cancellation_probe"
    )
    nodes.append(("src/events/reactor.py::_reactor_wake_cancellation_probe", probe))
    return nodes


def test_no_second_cancel_predicate_names_a_wake_reason():
    offenders = [
        f"{where}:{node.lineno}:{node.value}"
        for where, tree in _judging_nodes()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _WAKE_REASONS
    ]
    assert offenders == []


def _names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def test_no_second_cancel_predicate_reads_the_wake_queue_directly():
    for where, tree in _judging_nodes():
        # The reactor probe gates its queue read on the marker revision; it
        # still judges every wake with cut_invalidating_wakes.
        allowed = (
            {"reactor_wakes_since", "reactor_urgent_wake_identity"}
            if where.endswith("_reactor_wake_cancellation_probe")
            else set()
        )
        assert (_names(tree) & _DIRECT_READERS) - allowed == set(), where


def test_every_judging_module_calls_the_one_predicate():
    for where, tree in _judging_nodes():
        if "global_batch_runtime" in where:
            continue  # the runtime publishes scope; the adapter judges
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert "cut_invalidating_wakes" in calls, where


PARIS = ("Paris", "2026-07-20", "high")
TOKYO = ("Tokyo", "2026-07-20", "high")
PARIS_KEY = weather_family_id(city="Paris", target_date="2026-07-20", metric="high")


def _wake(reason, families=(), requests=()):
    return SimpleNamespace(
        wake_id=f"{reason}-{len(families)}",
        reason=reason,
        forecast_families=families,
        held_sell_reauction_requests=requests,
    )


def _verdict(wake, dependency):
    verdict = cut_invalidating_wakes((wake,), dependency)
    return "hard" if verdict.hard else "epoch" if verdict.epoch else None


UNPUBLISHED = CutDependency(published=False, hard_family_keys=None, belief_family_keys=None)
SCOPED = CutDependency(
    published=True,
    hard_family_keys=frozenset({PARIS_KEY}),
    belief_family_keys=frozenset({PARIS_KEY}),
)
Q_FROZEN = CutDependency(
    published=True, hard_family_keys=frozenset({PARIS_KEY}), belief_family_keys=frozenset()
)


@pytest.mark.parametrize(
    ("wake", "dependency", "expected"),
    [
        # BOOK and REQUEST never invalidate.
        (_wake("market_price_advanced"), SCOPED, None),
        (_wake("money_path_substrate_refreshed", (PARIS,)), SCOPED, None),
        (_wake(reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON), SCOPED, None),
        # CAPITAL always supersedes the epoch.
        (_wake("position_fill_projected"), UNPUBLISHED, "epoch"),
        (_wake(reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON, (), (object(),)), SCOPED, "epoch"),
        (_wake("an_unknown_reason"), Q_FROZEN, "epoch"),
        # HARD: in scope cancels, out of scope waits; (a) before the scope a
        # well-formed fact defers; a familyless or malformed fact keeps its veto.
        (_wake("day0_extreme_event_committed", (PARIS,)), SCOPED, "hard"),
        (_wake("day0_extreme_event_committed", (TOKYO,)), SCOPED, None),
        (_wake("day0_extreme_event_committed", (PARIS,)), UNPUBLISHED, None),
        (_wake("day0_extreme_event_committed"), UNPUBLISHED, "hard"),
        (_wake("day0_extreme_event_committed", (("Paris", "bad", "high"),)), SCOPED, "hard"),
        # (e) after the winner froze, only its own family's hard fact.
        (_wake("day0_extreme_event_committed", (PARIS,)), Q_FROZEN, "hard"),
        # BELIEF: supersedes only while the cut reads that family's posterior.
        (_wake("forecast_posterior_advanced", (PARIS,)), SCOPED, "epoch"),
        (_wake("forecast_posterior_advanced", (TOKYO,)), SCOPED, None),
        (_wake("forecast_posterior_advanced", (PARIS,)), UNPUBLISHED, None),
        (_wake("forecast_posterior_advanced", (PARIS,)), Q_FROZEN, None),
        (_wake("forecast_posterior_advanced"), SCOPED, "epoch"),
    ],
)
def test_the_one_predicate_truth_table(wake, dependency, expected):
    assert _verdict(wake, dependency) == expected


def test_grace_applies_to_belief_only():
    belief = cut_invalidating_wakes((_wake("forecast_posterior_advanced", (PARIS,)),), SCOPED)
    capital = cut_invalidating_wakes((_wake("position_fill_projected"),), SCOPED)
    assert belief.epoch_grace_eligible is True
    assert capital.epoch_grace_eligible is False


def test_marker_is_judged_as_a_wake_and_old_facts_are_not(tmp_path):
    """(b)/(c) The cutoff is the cut's decision time; the urgent marker is the
    full wake record, judged by the same predicate as a queued wake."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    cutoff = _dt.datetime(2026, 7, 20, 8, 0, tzinfo=_dt.timezone.utc)
    reactor_wake.publish_reactor_wake(
        source="t", reason="day0_extreme_event_committed", path=path,
        published_at=cutoff - _dt.timedelta(seconds=5), forecast_families=(PARIS,),
    )
    assert reactor_wake.wakes_after_cutoff(
        cutoff.isoformat(), exclude_wake_ids=(), path=path
    ) == ()
    newer = reactor_wake.publish_reactor_wake(
        source="t", reason="day0_extreme_event_committed", path=path,
        published_at=cutoff + _dt.timedelta(seconds=1), forecast_families=(PARIS,),
    )
    # Drop the queue record: only the marker names the fact.
    for queued in (path.parent / (path.name + reactor_wake.REACTOR_WAKE_QUEUE_SUFFIX)).glob("*.json"):
        if newer.wake_id in queued.name:
            queued.unlink()
    wakes = reactor_wake.wakes_after_cutoff(cutoff.isoformat(), exclude_wake_ids=(), path=path)
    assert [wake.wake_id for wake in wakes] == [newer.wake_id]
    assert cut_invalidating_wakes(wakes, SCOPED).hard == wakes
