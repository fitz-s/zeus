# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a (synthetic implementation only -- see src/reduce/position_economics.py
#   module docstring for the full scope/boundary statement).
"""src.reduce -- the derive-on-read position-economics reducer package.

SYNTHETIC-FIXTURE-ONLY as of this packet. Nothing in this package is wired
into a live trade-DB init path, a production reader, or a writer. See
src/reduce/position_economics.py and src/reduce/generation.py module
docstrings for the honesty invariant this package implements and the
operator gates that must close before any production use.
"""
