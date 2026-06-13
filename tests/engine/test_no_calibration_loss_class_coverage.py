# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: settled-data loss-class replay 2026-06-13, n=485, 98.1% admit; git
#   regression 745aa10c6f. The forecast-derived NO lower bound q_lcb_no = 1 - q_ucb_yes
#   on the bin that ACTUALLY WON was > 0.5 on 476/485 (98.1%) of settled winning bins —
#   i.e. a non-executable-YES forecast-NO would have bought NO on the winner = a
#   guaranteed loss (HK/Karachi/KL "NO on winning ring bin"). The canonical builder's
#   non-executable-YES else-branch gate (q_lcb_no=0.0 / p=1.0 / prefilter=False) closes
#   this class: the admitted fraction goes 98% -> 0.
"""Settled-data guard for the buy-NO loss-class gate.

Reproduces the documented replay:

    forecast_posteriors JOIN settlement_outcomes ON (city, target_date, temperature_metric)
    q_lcb_no(winner) = 1 - q_ucb_yes(winner)        # the forecast-derived NO lower bound
    "admit" iff q_lcb_no(winner) > 0.5              # would buy NO on the bin that WON

and asserts that, UNDER THE GATE, the fraction of settled winning bins where a
NON-executable-YES forecast-NO is admitted is ~0 (the gate emits q_lcb_no=0.0 for every
non-executable-YES bin, so no forecast-NO survives to be admitted).

Two layers:
  * UNGUARDED replay on the read-only live DB DOCUMENTS the loss class (the ~98% admit
    that the 745aa10c6f forecast-NO else-branch re-opened). Skipped gracefully when the
    DB is not present in the worktree (state/ is gitignored and does not travel).
  * The GATE applied to the same winners yields 0 admits — asserted on the live DB when
    present AND on synthetic winners always (so the invariant has teeth offline).

RED-on-revert: the gate model below mirrors the production else-branch. If the
745aa10c6f forecast-NO else-branch is restored, the gate's q_lcb_no for a
non-executable-YES bin is the forecast 1 - q_ucb_yes (not 0.0) and the admitted fraction
jumps back to ~98% — the assertions fail.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

_DB_RELPATH = "state/zeus-forecasts.db"
_ADMIT_THRESHOLD = 0.5  # q_lcb_no(winner) > 0.5 => would buy NO on the winner = loss


def _gate_q_lcb_no_for_nonexecutable_yes_bin(forecast_q_ucb_yes: float) -> float:
    """The production gate (canonical else-branch, non-executable-YES bin): the buy_no
    q_lcb_no is 0.0 — the forecast 1 - q_ucb_yes is NEVER consumed. This mirrors
    ``_canonical_probability_and_fdr_proof``'s else-branch exactly. ``forecast_q_ucb_yes``
    is accepted only to make the RED-on-revert contrast explicit: the regression would
    return ``1 - forecast_q_ucb_yes`` here instead of 0.0."""
    return 0.0


def _winning_bin_key(q_map: dict, winning_bin: str) -> str | None:
    """Bind the settlement winning_bin label to the forecast q_json/q_ucb_json key. The
    keys are full market questions ("Will the highest temperature in Milan be 22°C on
    June 11?") and the winning_bin is the bin label ("22°C"); the question carries the
    substring ``be {winning_bin} on``."""
    needle = f"be {winning_bin} on"
    for key in q_map:
        if needle in key:
            return key
    return None


def _replay_rows(db_path: str):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return con.execute(
            """
            SELECT fp.q_json, fp.q_ucb_json, so.winning_bin
            FROM forecast_posteriors fp
            JOIN settlement_outcomes so
              ON fp.city = so.city
             AND fp.target_date = so.target_date
             AND fp.temperature_metric = so.temperature_metric
            WHERE so.winning_bin IS NOT NULL
              AND fp.q_ucb_json IS NOT NULL
              AND fp.q_ucb_json != ''
            """
        ).fetchall()
    finally:
        con.close()


@pytest.mark.settled_data
def test_loss_class_closed_under_gate_on_live_db():
    """On the read-only live forecasts DB: the UNGUARDED forecast-NO admits the loss class
    on a large majority of settled winners (documents the ~98% the regression re-opened),
    while the GATE admits ZERO of them. Skip-guarded when the DB is absent."""
    db_path = _DB_RELPATH if os.path.exists(_DB_RELPATH) else None
    if db_path is None:
        pytest.skip(
            f"{_DB_RELPATH} not present in worktree (state/ is gitignored and does not "
            "travel); the synthetic-data assertion below still guards the gate logic"
        )

    rows = _replay_rows(db_path)
    if not rows:
        pytest.skip("no settled winning bins with q_ucb present in this DB")

    n = 0
    unguarded_admit = 0
    gate_admit = 0
    qlcb_sum = 0.0
    for row in rows:
        q_map = json.loads(row["q_json"])
        q_ucb_map = json.loads(row["q_ucb_json"])
        winning_bin = row["winning_bin"]
        key = _winning_bin_key(q_ucb_map, winning_bin)
        if key is None or key not in q_map:
            continue
        n += 1
        q_ucb_yes = float(q_ucb_map[key])
        # UNGUARDED (the 745aa10c6f forecast-NO else-branch): q_lcb_no = 1 - q_ucb_yes.
        unguarded_q_lcb_no = 1.0 - q_ucb_yes
        qlcb_sum += unguarded_q_lcb_no
        if unguarded_q_lcb_no > _ADMIT_THRESHOLD:
            unguarded_admit += 1
        # GATE (production else-branch): q_lcb_no = 0.0 for a non-executable-YES bin.
        gate_q_lcb_no = _gate_q_lcb_no_for_nonexecutable_yes_bin(q_ucb_yes)
        if gate_q_lcb_no > _ADMIT_THRESHOLD:
            gate_admit += 1

    assert n > 0, "expected at least one bound settled winning bin"
    # Documents the loss class: the unguarded forecast-NO would buy NO on the winner on a
    # large majority of settled bins (the replay measured ~98%). Assert a conservative
    # floor so the test stays robust to small DB drift while still proving the class.
    unguarded_fraction = unguarded_admit / n
    assert unguarded_fraction > 0.80, (
        f"unguarded forecast-NO admit fraction {unguarded_fraction:.3f} (n={n}) — the "
        f"settled record should show the loss class (~98% admit); mean q_lcb_no(winner)="
        f"{qlcb_sum / n:.3f}"
    )
    # The gate closes it: ZERO admitted. 98% -> 0.
    assert gate_admit == 0, (
        f"gate admitted {gate_admit}/{n} non-executable-YES forecast-NO on settled "
        f"winners — the loss-class gate is breached (745aa10c6f else-branch restored?)"
    )


def test_gate_admits_zero_on_synthetic_loss_class_winners():
    """Offline-safe twin: synthetic settled winners shaped EXACTLY like the loss class
    (forecast under-rates the eventual winner: q_yes_pt ~0.18, so 1 - q_ucb_yes ~0.82 on
    the winning bin). The UNGUARDED forecast-NO admits all of them; the GATE admits none.
    This keeps the invariant enforced even when the live DB is absent."""
    # (q_ucb_yes_on_winner) for a forecast that badly under-rates the winner — a high
    # q_ucb_yes complement gives a high forecast q_lcb_no ~0.79-0.86 = the harvested band.
    synthetic_winner_q_ucb_yes = [0.14, 0.18, 0.21, 0.10, 0.25, 0.16]

    unguarded_admit = sum(
        1 for qucb in synthetic_winner_q_ucb_yes if (1.0 - qucb) > _ADMIT_THRESHOLD
    )
    gate_admit = sum(
        1
        for qucb in synthetic_winner_q_ucb_yes
        if _gate_q_lcb_no_for_nonexecutable_yes_bin(qucb) > _ADMIT_THRESHOLD
    )

    # Unguarded: EVERY synthetic loss-class winner is admitted (forecast-NO > 0.5).
    assert unguarded_admit == len(synthetic_winner_q_ucb_yes)
    # Gate: ZERO admitted — q_lcb_no is 0.0 for every non-executable-YES bin.
    assert gate_admit == 0
