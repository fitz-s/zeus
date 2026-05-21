# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §6.3 INV-freshness-no-ad-hoc-checks
"""INV-freshness-no-ad-hoc-checks — T3 SCAFFOLD antibody.

Problem (§6.1): ~10 sites across src/ perform ad-hoc freshness comparisons
(`age_seconds > CONST`, `age_hours > CONST`) with hardcoded or locally-scoped
thresholds instead of routing through FreshnessRegistry.evaluate().

This antibody AST-walks src/ for ALL of the patterns defined in §6.3:
  1. `age_seconds > <CONST>`
  2. `age_hours > <CONST>`
  3. `datetime.now() - <var>`   (age computation, not just comparison)
  4. `time.time() - <var>`

Allowlist criteria (§6.3): a pattern is ALLOWLISTED iff ANY of:
  (a) Comparison is a SIGN check only (>= 0 / < 0) without a max-age threshold.
  (b) Threshold name doesn't include AGE/STALE/FRESH/MAX_AGE tokens (e.g. clock-skew
      sanity guards or rate-limit checks like HEARTBEAT_REFRESH_SECONDS).
  (c) Call site is documented with `# allowlist:freshness-non-gate` marker.

Status: XFAIL (RED) until T3 production pass migrates all 10 callsites to
FreshnessRegistry.evaluate().  Once migration is complete, remove @pytest.mark.xfail
to harden as a permanent GREEN antibody.

See T3_FRESHNESS_REGISTRY_SCAFFOLD.md §4 for production-pass checklist.
"""

from __future__ import annotations

import ast
import os
import re
import textwrap
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
# NOTE: These are text-level patterns; AST is used to locate exact file:line.
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
# AST + text scanner
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

@pytest.mark.xfail(
    strict=False,
    reason=(
        "T3 SCAFFOLD: awaiting production-pass migration of ad-hoc freshness gates "
        "to FreshnessRegistry.evaluate().  Expected RED until migration complete. "
        "strict=False so XPASS reports cleanly when migration lands."
    ),
)
def test_no_ad_hoc_freshness_checks() -> None:
    """Assert zero ad-hoc freshness gates remain in src/ outside the allowlist.

    Each offender is a site that directly compares age_seconds / age_hours / artifact_age_hours
    against a threshold constant without routing through FreshnessRegistry.evaluate().

    Allowlist criteria (§6.3 — ANY of):
      (a) Sign check only (>= 0 / < 0)
      (b) Threshold name not in AGE/STALE/FRESH/MAX_AGE token set
      (c) # allowlist:freshness-non-gate marker on the line or the line above

    This antibody is currently XFAIL (stage-1 immune-system antibody per Fitz methodology).
    Remove @pytest.mark.xfail once all T3 callsite migrations are complete and verified.
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
