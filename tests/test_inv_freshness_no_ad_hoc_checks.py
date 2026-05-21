# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §6.3 INV-freshness-no-ad-hoc-checks
"""INV-freshness-no-ad-hoc-checks — T3 production antibody.

Scope: regex-scans src/ for ad-hoc freshness gates that compare variables named
``age_seconds``, ``age_hours``, or ``artifact_age_hours`` directly against a
threshold constant, instead of routing through FreshnessRegistry.evaluate().

This antibody covers ONLY these three variable-name families.  It does NOT cover:
  - ``written_at_age``, ``cached_age``, ``staleness_h``, ``staleness``
  - timedelta-based gates (e.g. ``staleness > TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE``)
  - ``freshness_gate.py`` per-source budget loops

Phase-3 carryover gates (out of scope here, tracked in T3_FRESHNESS_REGISTRY_SCAFFOLD.md §4):
  - src/control/freshness_gate.py:177 — ``written_at_age > ABSENT_MID_RUN_THRESHOLD_SECONDS``
  - src/control/freshness_gate.py:185 — ``written_at_age > 90``
  - src/control/freshness_gate.py:206 — per-source FRESHNESS_BUDGETS loop
  - src/control/live_health.py:104 — ``age > STATUS_FRESH_BUDGET_SECONDS``
  - src/control/live_health.py:174 — ``age > STATUS_FRESH_BUDGET_SECONDS``
  - src/runtime/bankroll_provider.py:128 — ``cached_age > fail_closed_after_seconds``  [PRIORITY: safety-adjacent fail-closed]
  - src/ingest_main.py:479 — ``staleness_h > threshold_h``
  - src/ingest_main.py:506 — ``solar_staleness_h > threshold_h``
  - src/riskguard/riskguard.py:327 — timedelta staleness gate

Allowlist criteria: a match is ALLOWLISTED iff ANY of:
  (a) Threshold is exactly zero (sign-only check: age_seconds < 0 or age_seconds > 0).
  (b) Threshold name is in _NON_GATE_THRESHOLD_NAMES (cadence/rate-limit, not max-age).
  (c) Call site carries ``# allowlist:freshness-non-gate`` marker.

Status: GREEN once T3 production-pass migration is complete (xfail removed after 0 offenders).

See T3_FRESHNESS_REGISTRY_SCAFFOLD.md §4 for production-pass checklist.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Root of the src/ tree — relative to this test file's repo root
_SRC_ROOT = Path(__file__).parent.parent / "src"

# Names that indicate a SIGN check (criterion a): the comparison direction
# means "negative age / not-yet-captured" rather than "too old".
_SIGN_CHECK_TOKENS = frozenset({"< 0", ">= 0", "< -", "<= 0", "> -"})

# Threshold name tokens that indicate a staleness-max-age gate (NOT allowlisted).
# Any threshold that includes these tokens IS a freshness gate.
_GATE_TOKENS_RE = re.compile(
    r"(?i)(MAX_AGE|STALE|FRESH|AGE_HOURS|AGE_SECONDS|_MAX_|_LIMIT_)",
    re.IGNORECASE,
)

# Threshold names that are explicitly NOT max-age staleness gates (criterion b).
# These are cadence / rate-limit / clock-drift bounds — not "is the data too old?".
_NON_GATE_THRESHOLD_NAMES = frozenset({
    "COLLATERAL_HEARTBEAT_REFRESH_SECONDS",
    # Add more here as discovered; document rationale next to each entry.
})

# In-code allowlist marker string (criterion c).
_ALLOWLIST_MARKER = "allowlist:freshness-non-gate"

# Patterns the antibody scans for in the raw source text.
#
# Pattern families:
#   P1: age_seconds > <identifier or integer>
#   P2: age_hours > <identifier or integer>
#   P3: artifact_age_hours > <identifier or integer>   (oracle_estimator variant)
#   P4: age_seconds is None or age_seconds > <expr>   (db.py variant — still a gate)
_COMPARE_RE = re.compile(
    r"\b(age_seconds|age_hours|artifact_age_hours)\s*>\s*"
    r"([A-Za-z_][A-Za-z0-9_\.]*|[0-9]+(?:\.[0-9]*)?)"
)


# ---------------------------------------------------------------------------
# Offender representation
# ---------------------------------------------------------------------------

class FreshnessOffender(NamedTuple):
    path: str      # relative path under src/
    lineno: int
    text: str      # stripped source line
    reason: str    # why it is NOT allowlisted


# ---------------------------------------------------------------------------
# Regex scanner
# ---------------------------------------------------------------------------

def _is_allowlisted(
    line_text: str,
    threshold_name: str,
    file_lines: list[str],
    lineno: int,
) -> tuple[bool, str]:
    """Return (is_allowlisted, reason_string) for a candidate freshness gate."""
    stripped = line_text.strip()

    # Criterion (c): explicit marker in the same line or the preceding line.
    prev_line = file_lines[lineno - 2].strip() if lineno >= 2 else ""
    if _ALLOWLIST_MARKER in stripped or _ALLOWLIST_MARKER in prev_line:
        return True, "explicit allowlist marker"

    # Criterion (a): SIGN check only — the line has ONLY sign checks (>= 0 / < 0)
    # and does NOT also contain a positive max-age threshold comparison.
    # A compound condition like `age_seconds < 0 or age_seconds > 60` is NOT
    # a pure sign check — the `> 60` part is a real freshness gate.
    has_sign_check = any(tok in stripped for tok in _SIGN_CHECK_TOKENS)
    # Check whether the specific match we found is a >positive_threshold gate.
    # If the threshold is a positive number or a named constant (not 0 / negative),
    # the match is a gate even when a sign check exists on the same line.
    is_zero_threshold = threshold_name in {"0", "0.0", "-0", "-0.0"}
    if has_sign_check and is_zero_threshold:
        return True, "sign-only check (criterion a)"

    # Criterion (b): threshold name is NOT a staleness gate.
    if threshold_name in _NON_GATE_THRESHOLD_NAMES:
        return True, f"non-gate threshold name {threshold_name!r} (criterion b)"

    # Not allowlisted — this is a freshness gate that should route through registry.
    return False, "freshness gate: not in allowlist"


def _scan_file(path: Path) -> list[FreshnessOffender]:
    """Scan a single Python file for ad-hoc freshness comparisons."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    file_lines = source.splitlines(keepends=True)
    offenders: list[FreshnessOffender] = []

    for lineno, line in enumerate(file_lines, start=1):
        for match in _COMPARE_RE.finditer(line):
            age_var = match.group(1)       # e.g. "age_seconds"
            threshold = match.group(2)     # e.g. "COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS"

            is_ok, reason = _is_allowlisted(line, threshold, file_lines, lineno)
            if not is_ok:
                rel_path = str(path.relative_to(_SRC_ROOT.parent))
                offenders.append(
                    FreshnessOffender(
                        path=rel_path,
                        lineno=lineno,
                        text=line.rstrip(),
                        reason=f"{age_var} > {threshold} — {reason}",
                    )
                )

    return offenders


def find_all_freshness_offenders() -> list[FreshnessOffender]:
    """Walk src/ and return all ad-hoc freshness gate sites not in the allowlist."""
    offenders: list[FreshnessOffender] = []
    for root, _dirs, files in os.walk(_SRC_ROOT):
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            offenders.extend(_scan_file(fpath))
    return sorted(offenders, key=lambda o: (o.path, o.lineno))


# ---------------------------------------------------------------------------
# Antibody test — XFAIL until T3 production migration is complete
# ---------------------------------------------------------------------------

def test_no_ad_hoc_age_seconds_hours_gates() -> None:
    """Assert zero ad-hoc age_seconds/age_hours/artifact_age_hours freshness gates remain.

    Scope: covers ONLY variables named ``age_seconds``, ``age_hours``, and
    ``artifact_age_hours`` compared with ``>`` against a threshold constant.
    Does NOT cover written_at_age, cached_age, staleness_h, timedelta-based
    gates, or freshness_gate.py budget loops (Phase-3 carryover — see module docstring).

    GREEN = all 10 T3 callsites migrated to FreshnessRegistry.evaluate().
    """
    offenders = find_all_freshness_offenders()

    if offenders:
        lines = [
            f"  {o.path}:{o.lineno}  {o.text.strip()!r}",
            f"    -> {o.reason}",
        ]
        detail = "\n".join(lines)
        pytest.fail(
            f"Found {len(offenders)} ad-hoc freshness gate(s) not routed through "
            f"FreshnessRegistry.evaluate():\n\n{detail}\n\n"
            "Each site must either:\n"
            "  (a) Be a sign-only check (>= 0 / < 0)\n"
            "  (b) Use a threshold name without AGE/STALE/FRESH/MAX_AGE tokens\n"
            "  (c) Carry `# allowlist:freshness-non-gate` annotation\n"
            "  OR be migrated to FreshnessRegistry.evaluate(source_id, age_seconds).\n"
            "See T3_FRESHNESS_REGISTRY_SCAFFOLD.md §3 migration plan."
        )


# ---------------------------------------------------------------------------
# Discovery helper test — always passes, reports count for observability
# ---------------------------------------------------------------------------

def test_freshness_offenders_discovery_count() -> None:
    """Non-gating test: report the count of ad-hoc freshness sites discovered.

    This test always passes — it exists so pytest output always shows the
    current offender count even before T3 migration.  Useful for tracking
    progress during the production pass.
    """
    offenders = find_all_freshness_offenders()
    # Print count to stdout (visible with pytest -s or in CI logs)
    print(
        f"\n[T3-freshness-antibody] discovered {len(offenders)} ad-hoc freshness gate(s):"
    )
    for o in offenders:
        print(f"  {o.path}:{o.lineno}  {o.reason}")
    # No assertion — this test is informational only.
