# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A3 (9-status oracle evidence-grade enum); split into its own module to break the circular import between oracle_penalty (loader) and oracle_estimator (classifier).
"""Oracle evidence-grade status enum.

9 statuses (PLAN.md §A3, was 4 pre-A3):

- OK                  — sufficient sample, m=0, posterior 95% upper ≤ 0.05
- INCIDENTAL          — sufficient sample, m>0, posterior 95% upper ≤ 0.05
- CAUTION             — posterior 95% upper in (0.05, 0.10]
- BLACKLIST           — posterior 95% upper > 0.10
- MISSING             — no record (city not in oracle file, or file missing)
- STALE               — artifact > 7 days old
- MALFORMED           — JSON parse / schema error (cache held; reader degraded)
- METRIC_UNSUPPORTED  — LOW track until LOW snapshot bridge ships
- INSUFFICIENT_SAMPLE — n < 10; posterior too wide to commit to a tier

The enum is ``str``-backed for SQL/JSON serialization symmetry; existing
code paths that compare ``oracle.status == "BLACKLIST"`` keep working.
"""
from __future__ import annotations

from enum import Enum


class OracleStatus(str, Enum):
    OK = "OK"
    INCIDENTAL = "INCIDENTAL"
    CAUTION = "CAUTION"
    BLACKLIST = "BLACKLIST"
    MISSING = "MISSING"
    STALE = "STALE"
    MALFORMED = "MALFORMED"
    METRIC_UNSUPPORTED = "METRIC_UNSUPPORTED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
