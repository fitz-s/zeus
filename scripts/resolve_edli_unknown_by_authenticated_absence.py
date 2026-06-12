#!/usr/bin/env python
# Created: 2026-06-12 (thin wrapper; core moved same day)
# Last reused or audited: 2026-06-12
# Authority basis: operator-ratified absence-proof recovery (settings.json
#   _unpause_note_2026_06_12). Core now lives in
#   src/execution/edli_absence_resolver.py so the daemon boot path runs the
#   SAME resolution automatically (boot crash-loop antibody, 3 incidents
#   2026-06-12) — this CLI remains for manual/targeted use.
"""Resolve EDLI post-submit unknowns using authenticated venue absence proof.

Thin CLI wrapper around src.execution.edli_absence_resolver.resolve — see
that module for the contract (authenticated CLOB reads, refusal on any
matching exposure, canonical-ledger appends only).
"""

from __future__ import annotations

import argparse
import sys

from src.execution.edli_absence_resolver import resolve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate-id", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return resolve(aggregate_id=args.aggregate_id, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
