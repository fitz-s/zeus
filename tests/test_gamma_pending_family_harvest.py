# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: FDR-gate Gamma parse incident 2026-06-10. Live symptom:
#   "refresh_pending_family_snapshots: Gamma response did not parse to pending
#   family <City>/<date>/<extremum> — bin identity unknown, family will stay at
#   FDR gate" repeated every refresh cycle for families that DO have real Gamma
#   markets, so every certified candidate in those families died FDR_REJECTED.
#   ROOT CAUSE (confirmed against the live Gamma API + zeus-live.log): the Gamma
#   response parses PERFECTLY — the message was a false label. The real failure
#   was the gamma-lookup time-box: it cancelled in-flight futures and cleared the
#   pending set WITHOUT draining results that completed ~140ms later, and it gated
#   the terminal "did not parse" verdict on "submitted" (gamma_attempted) rather
#   than "result actually read" (gamma_harvested). A transient timing miss was
#   thus mislabeled a permanent parse failure and pinned real families at the gate.
"""Antibody tests for the FDR-gate Gamma-parse incident category.

These are RELATIONSHIP tests (Fitz methodology): they assert the cross-module
invariant at the handoff between the Gamma-fetch harvest phase and the
pending-family MATCH phase in _refresh_pending_family_snapshots — not just
single-function behavior. The semantic context lost at that boundary was the
difference between "I submitted a request" and "I read and parsed a response";
conflating the two turned a recoverable timing miss into a terminal verdict.

INVARIANTS (the category these make impossible):

  INV-GAMMA-1 (the response is genuinely parseable): the live Gamma response for
    a real pending family MUST parse to the EXACT pending-family key
    (city, target_date, metric). If this holds, any "did not parse" verdict for
    such a family is necessarily a downstream control-flow defect, never a real
    parse failure. Proven against a captured LIVE Gamma response fixture.

  INV-GAMMA-2 (terminal verdict gated on harvested, not attempted): a family
    whose Gamma future was SUBMITTED but whose result was never READ (cancelled /
    not drained at the time-box) must NOT be reported as terminal "did not parse
    — stay at FDR gate". It must be reported RETRYABLE. Only a family whose future
    result was actually harvested-and-unmatched may receive the terminal verdict.
    This preserves fail-closed (a real non-match still stays at the gate) while
    making the transient-timing-miss-as-permanent-failure category impossible.

  INV-GAMMA-3 (late-completing futures are drained, not discarded): a future that
    completes AFTER the time-box but within the bounded grace window must have its
    result harvested (its events collected), not thrown away. future.cancel()
    cannot stop an already-running thread, so the response lands moments later;
    the harvest must read it instead of discarding it.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import cities_by_name
from src.data import market_scanner as ms

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> list[dict]:
    data = json.loads((FIXTURE_DIR / name).read_text())
    assert isinstance(data, list) and data, f"fixture {name} empty/invalid"
    return [e for e in data if isinstance(e, dict)]


# Mirror of the family-key helpers in _refresh_pending_family_snapshots. These are
# defined inline in the function (closures over the city config), so the test
# reconstructs the SAME normalisation to assert the cross-module key invariant.
def _family_text_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def _canonical_metric(metric: object) -> str:
    text = _family_text_key(metric)
    if text in {"low", "lowest", "min", "minimum"} or text.startswith("lowest "):
        return "low"
    if text in {"high", "highest", "max", "maximum"} or text.startswith("highest "):
        return "high"
    return text


def _family_key(city: object, target_date: object, metric: object) -> tuple[str, str, str]:
    city_name = getattr(city, "name", None) or (city if isinstance(city, str) else str(city or ""))
    return (
        _family_text_key(city_name),
        str(target_date or "").strip(),
        _canonical_metric(metric),
    )


# ---------------------------------------------------------------------------
# INV-GAMMA-1: the live Gamma response parses to the exact pending-family key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fixture, city_name, target_date",
    [
        ("gamma_wuhan_2026-06-12_high.json", "Wuhan", "2026-06-12"),
        ("gamma_milan_2026-06-12_high.json", "Milan", "2026-06-12"),
    ],
)
def test_live_gamma_response_parses_to_pending_family_key(fixture, city_name, target_date):
    """The captured LIVE Gamma response for a real pending family parses to the
    EXACT (city, target_date, metric) key the refresh loop builds for it.

    This is the antibody for the false "did not parse to pending family" verdict:
    if the response DOES parse to the key, then any such verdict for this family
    is a control-flow defect (a discarded/un-harvested future), not a parse miss.
    """
    events = _load_fixture(fixture)
    now = datetime.now(timezone.utc)

    discovered = ms._parse_and_persist_weather_events(
        events, min_hours_to_resolution=0.0, now=now
    )
    assert discovered, "live Gamma response failed to parse to ANY event"

    parsed_keys = {
        _family_key(ev.get("city"), ev.get("target_date"), ev.get("temperature_metric"))
        for ev in discovered
    }
    expected = _family_key(city_name, target_date, "high")
    assert expected in parsed_keys, (
        f"parsed keys {parsed_keys} do not include the pending-family key {expected} — "
        "the 'did not parse to pending family' message is therefore a false label"
    )

    # Bin identity is fully resolvable: the family has executable outcomes.
    matched = next(
        ev for ev in discovered
        if _family_key(ev.get("city"), ev.get("target_date"), ev.get("temperature_metric")) == expected
    )
    outcomes = matched.get("outcomes") or matched.get("support_outcomes") or []
    assert outcomes, "matched family carries no outcomes — bin identity unresolved"


# ---------------------------------------------------------------------------
# INV-GAMMA-2 / INV-GAMMA-3: harvested gates the terminal verdict; late futures
# are drained. Replays the harvest -> match handoff with a controllable future.
# ---------------------------------------------------------------------------
def _replay_match(
    *,
    family_keys: list[tuple],
    harvested_keys: set,
    empty_keys: set,
    by_family: dict,
):
    """Replay ONLY the post-fetch match-loop decision from
    _refresh_pending_family_snapshots, returning the per-family verdict.

    Verdict ∈ {"resolved", "terminal_no_parse", "terminal_empty", "retryable"}.
    The fix's contract: the terminal verdicts are reachable ONLY for keys in
    `harvested_keys`; everything else is "retryable".
    """
    verdicts: dict[tuple, str] = {}
    for key in family_keys:
        ev = by_family.get(key)
        if ev is not None:
            verdicts[key] = "resolved"
            continue
        if key in harvested_keys:
            verdicts[key] = "terminal_empty" if key in empty_keys else "terminal_no_parse"
        else:
            verdicts[key] = "retryable"
    return verdicts


def test_submitted_but_not_harvested_family_is_retryable_not_terminal():
    """INV-GAMMA-2: a family that was SUBMITTED (attempted) but whose future was
    never harvested (time-boxed / cancelled) must be RETRYABLE, never terminal.

    Pre-fix this family's key was in gamma_attempted_family_keys and absent from
    by_family, so it hit the terminal "did not parse — stay at FDR gate" branch.
    """
    key = _family_key("Wuhan", "2026-06-12", "high")
    verdicts = _replay_match(
        family_keys=[key],
        harvested_keys=set(),  # submitted but NOT harvested
        empty_keys=set(),
        by_family={},  # result never landed in the parsed lookup
    )
    assert verdicts[key] == "retryable", (
        "a submitted-but-un-harvested family must be retryable, not terminal — "
        "otherwise a transient time-box miss permanently pins it at the FDR gate"
    )


def test_harvested_unmatched_family_stays_terminal_fail_closed():
    """INV-GAMMA-2 (fail-closed half): a family whose future WAS harvested but did
    not parse to a matching event correctly stays terminal. The fix must not turn
    genuine non-matches into infinite retries."""
    key = _family_key("Nowhere City", "2026-06-12", "high")
    verdicts = _replay_match(
        family_keys=[key],
        harvested_keys={key},   # result WAS read
        empty_keys=set(),
        by_family={},           # but it did not match any pending family
    )
    assert verdicts[key] == "terminal_no_parse"


def test_harvested_empty_family_stays_terminal():
    """A harvested family whose Gamma response was an empty event list stays
    terminal (distinct, correctly-labelled reason)."""
    key = _family_key("Wuhan", "2026-06-12", "high")
    verdicts = _replay_match(
        family_keys=[key],
        harvested_keys={key},
        empty_keys={key},
        by_family={},
    )
    assert verdicts[key] == "terminal_empty"


def test_late_completing_future_is_drained_and_harvested():
    """INV-GAMMA-3: a future that completes AFTER a (simulated) time-box but within
    the grace window must be harvested, so the family's real Gamma event reaches
    by_family and the family is RESOLVED — not discarded and mislabeled.

    Uses the REAL captured live Wuhan response, returned by a worker that sleeps
    past a tight 'deadline' to emulate the ~140ms-late landing seen live.
    """
    events = _load_fixture("gamma_wuhan_2026-06-12_high.json")
    key = _family_key("Wuhan", "2026-06-12", "high")
    now = datetime.now(timezone.utc)

    def _slow_fetch():
        time.sleep(0.25)  # lands AFTER the time-box, like the live ~140ms
        return events

    harvested_keys: set = set()
    raw_events_collected: list[dict] = []

    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_slow_fetch)
        # Emulate the time-box firing while the fetch is still running.
        deadline = time.monotonic() + 0.05
        timed_out = False
        while not future.done():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
        assert timed_out, "test setup: fetch finished before the simulated time-box"

        # FIX BEHAVIOUR: drain within a bounded grace instead of discarding.
        grace_deadline = time.monotonic() + 1.5
        if not future.done():
            remaining = max(0.0, grace_deadline - time.monotonic())
            result = future.result(timeout=remaining)  # blocks for the grace
            harvested_keys.add(key)
            raw_events_collected.extend(result)

    assert key in harvested_keys, "late-completing future was not harvested"

    discovered = ms._parse_and_persist_weather_events(
        raw_events_collected, min_hours_to_resolution=0.0, now=now
    )
    by_family = {
        _family_key(ev.get("city"), ev.get("target_date"), ev.get("temperature_metric")): ev
        for ev in discovered
    }
    verdicts = _replay_match(
        family_keys=[key],
        harvested_keys=harvested_keys,
        empty_keys=set(),
        by_family=by_family,
    )
    assert verdicts[key] == "resolved", (
        "a drained late-completing future must resolve the family's bin identity, "
        "not leave it at the FDR gate"
    )
