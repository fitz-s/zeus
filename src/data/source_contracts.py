# Created: 2026-05-24
# Relocated: 2026-07-08 (R3 ingest contractualization, BUILD-INTO-TARGET) -> src/ingest/contract.py.
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E2 constitution rule 1
#   ("legacy files only touched by delete / seam-wire / R0-stanch"). This module is now a
#   one-line seam: the SourceContract composing view (and its extension with clock_law/
#   dependents/fetch_ref/parse_ref/clock_check_ref) lives in src/ingest/contract.py. Import
#   from there directly in new code; this re-export exists only so the one pre-existing caller
#   (tests/test_source_contracts_view.py) and any external references keep working unchanged.
"""Seam re-export — see src/ingest/contract.py for the SourceContract implementation."""
from __future__ import annotations

from src.ingest.contract import SourceContract, load_source_contract  # noqa: F401
