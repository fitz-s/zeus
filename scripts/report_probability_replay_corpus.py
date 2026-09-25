#!/usr/bin/env python3
# Lifecycle: created=2026-09-25; last_reviewed=2026-09-25; last_reused=never
# Purpose: Read-only per-scope report of the current-recipe replay corpus
#   (src/calibration/probability_replay_corpus.py): rows, unique city-days,
#   rows with market features, executed-position replay coverage and
#   replay_unavailable reasons.
# Reuse: Opens the three canonical DBs with mode=ro URIs; SELECT-only; stdout only.
#   Confirms nothing about calibration quality or live admission.
# Authority basis: operator directive 2026-09-24 (current-recipe replay).
"""Read-only report of the current-recipe replay corpus."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.calibration.probability_replay_corpus import (  # noqa: E402
    POLICY_CONTRACT,
    current_settlement_contracts,
    load_replay_corpus,
)
from src.config import runtime_cities_by_name  # noqa: E402
from src.contracts.payoff_q_correction import CalibrationFitScope  # noqa: E402
from src.contracts.probability_validation import EXECUTED_ORDER, SETTLEMENT_STATE  # noqa: E402

STATE = Path("/Users/leofitz/zeus/state")


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--as-of", help="ISO8601 training cutoff (default: now)")
    args = parser.parse_args(argv)
    now = (datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of
           else datetime.now(timezone.utc))
    corpus = load_replay_corpus(
        _connect(args.state / "zeus-world.db"), _connect(args.state / "zeus_trades.db"),
        _connect(args.state / "zeus-forecasts.db"), training_cutoff=now, generated_at=now,
    )
    contracts = current_settlement_contracts(runtime_cities_by_name(), now.date())
    scopes: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in corpus.rows:
        scopes[(row.event_key[2], row.execution_mode, row.evaluation_recipe_id)].append(row)
    replayable: Counter = Counter()
    unavailable: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for (population, metric, mode, recipe, _origin, reason), count in corpus.unavailable.items():
        if population == EXECUTED_ORDER:
            unavailable[(metric, mode, recipe)][reason] += count
    for row in corpus.rows:
        if row.population == EXECUTED_ORDER:
            replayable[(row.event_key[2], row.execution_mode, row.evaluation_recipe_id)] += 1

    print(f"# Probability replay corpus (as of {now.isoformat()})")
    print(f"current settlement contracts: {len(contracts)}; replay rows: {len(corpus.rows)}")
    print("Each cell is rows/unique city-days remaining after that filter; the last is the fit input.\n")
    stages = None
    for (metric, mode, recipe), rows in sorted(scopes.items()):
        scope = CalibrationFitScope(metric=metric, execution_mode=mode,
                                    execution_contract=POLICY_CONTRACT[mode], raw_probability_revision=recipe)
        for population in (SETTLEMENT_STATE, EXECUTED_ORDER):
            funnel = corpus.fit_corpus(population=population, current_contracts=contracts,
                                       training_cutoff=now).funnel(scope)
            if stages is None:
                stages = [name for name, *_ in funnel]
                print(" | ".join(("metric", "mode", "recipe", "population", *stages, "market_features")))
            members = [row for row in rows if row.population == population]
            print(" | ".join((metric, mode, recipe, population,
                              *(f"{count}/{days}" for _name, count, days in funnel),
                              str(sum(row.p0 is not None for row in members)))))

    print("\n## Executed positions replayable under the current recipe")
    for key in sorted(set(replayable) | set(unavailable)):
        done, missing = replayable[key], sum(unavailable[key].values())
        total = done + missing
        print(f"{' / '.join(key)}: {done}/{total} = {done / total:.1%}" if total else f"{key}: 0/0")
        for reason, count in unavailable[key].most_common():
            print(f"    replay_unavailable {reason}: {count}")
    total_done = sum(replayable.values())
    total_all = total_done + sum(sum(counter.values()) for counter in unavailable.values())
    print(f"ALL: {total_done}/{total_all} = {total_done / total_all:.1%}" if total_all else "ALL: 0/0")

    print("\n## Decision states not replayed, by origin revision")
    by_origin: Counter = Counter()
    for (population, _metric, _mode, recipe, origin, reason), count in corpus.unavailable.items():
        if population == SETTLEMENT_STATE:
            by_origin[(recipe, origin, reason)] += count
    for (recipe, origin, reason), count in sorted(by_origin.items(), key=lambda item: -item[1]):
        print(f"{count:6d}  recipe={recipe}  origin={origin}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
