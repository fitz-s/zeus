# Created: 2026-08-02
# Last reused/audited: 2026-08-02
# Authority basis: .gitleaks.toml scoping contract
#                  docs/reference/security_false_positives.md [REVIEW-SAFE] index
#                  .github/workflows/secrets-scan.yml (the gate this config arms)
"""Blast-radius contract for the gitleaks allowlist.

An allowlist entry's ``regexes`` and ``paths`` combine as OR by default. An
entry carrying both does not mean "this literal, in this file"; it means "this
literal anywhere, OR anything in this file". The path arm exempts the whole
file, so a credential committed there later is never reported. Five entries
were written that way and were whole-file exemptions until 2026-08-02.

``condition = "AND"`` fixes this, but only alongside ``targetRules``. On its
own it parses without error and changes nothing, which is why it first measured
as inert. Verified at gitleaks 8.30.1 by fixture:

    targetRules + condition + paths + regexes -> true AND
    condition   +             paths + regexes -> silently OR
    paths + regexes                           -> OR
    regexes only                              -> literal cleared repo-wide

These tests pin the three properties true AND is supposed to give:

  - a DIFFERENT credential in a cleared file must still be reported
    (dropping ``targetRules``/``condition`` fails here);
  - the SAME literal OUTSIDE its cleared paths must still be reported
    (degrading an entry to regex-only fails here);
  - each cleared literal must stay suppressed at its own site, so hardening
    does not re-open the false-positive loop the REVIEW-SAFE index exists to end.

The scan runs against fixture files, not the repository: the question is what
the CONFIG permits, not what the tree happens to contain today.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".gitleaks.toml"

# High-entropy value in a shape gitleaks' default generic-api-key rule reports.
# Not a real credential: generated for this fixture and used nowhere else.
UNRELATED_SECRET = 'aws_secret_key = "j7Kd93MzQpL2vXbN8sTyR4wEuC6gHaF1oPzZmVdK"'

pytestmark = pytest.mark.skipif(
    shutil.which("gitleaks") is None,
    reason="gitleaks binary not installed; CI installs a SHA-pinned build",
)


def _scan(tmp_path: Path, rel: str, content: str) -> int:
    """Write one fixture file and return the number of findings."""
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    subprocess.run(
        [
            "gitleaks", "detect",
            "--no-git",
            "--source", str(tmp_path),
            "--config", str(CONFIG),
            "--no-banner",
            "--redact",
            "--report-format", "json",
            "--report-path", str(report),
        ],
        capture_output=True,
        check=False,
    )
    if not report.exists() or not report.read_text(encoding="utf-8").strip():
        return 0
    return len(json.loads(report.read_text(encoding="utf-8")))


# Files named by a REVIEW-SAFE ruling. Each was whole-file exempt before
# 2026-08-02; an unrelated credential in any of them must now be reported.
PREVIOUSLY_EXEMPT_PATHS = [
    "src/data/observation_client.py",
    "src/data/daily_obs_append.py",
    "src/data/wu_hourly_client.py",
    "docs/reference/security_false_positives.md",
    "docs/operations/notes.md",
    "src/strategy/candidates/c1_joint_tail_bayes.py",
    "src/strategy/candidates/c2_opening_stale_fok.py",
    "src/decision/family_decision_engine.py",
]


@pytest.mark.parametrize("rel", PREVIOUSLY_EXEMPT_PATHS)
def test_cleared_file_does_not_exempt_unrelated_credentials(tmp_path, rel):
    assert _scan(tmp_path, rel, UNRELATED_SECRET) == 1, (
        f"{rel} is whole-file exempt: a real credential committed there would "
        "pass the secrets-scan gate silently. An allowlist entry naming this "
        "file must clear a literal via `regexes`, never via `paths`."
    )


# A cleared literal, paired with a path NOT in that entry's `paths`. True AND
# means the clearance does not travel: the same value elsewhere is still a
# finding. A regex-only entry — the shape this config had before AND scoping
# was proven to work — clears the literal repo-wide and fails every case here.
LITERALS_OUTSIDE_THEIR_CLEARED_PATHS = [
    ("src/engine/unrelated_module.py",
     '_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"'),
    ("src/engine/unrelated_module.py",
     'semantic_key = "day0_daily_extrema_unconditioned_count"'),
    ("src/engine/unrelated_module.py",
     '_STRATEGY_KEY = "c1_joint_tail_bayes"'),
]


@pytest.mark.parametrize("rel,content", LITERALS_OUTSIDE_THEIR_CLEARED_PATHS)
def test_clearance_does_not_travel_outside_its_paths(tmp_path, rel, content):
    assert _scan(tmp_path, rel, content) == 1, (
        f"a cleared literal is suppressed in {rel}, which its allowlist entry "
        "does not list. The entry has lost its `targetRules` + "
        '`condition = "AND"` scoping and now clears the value repo-wide.'
    )


def test_sha256_shaped_credential_is_not_cleared_repo_wide(tmp_path):
    """The retired SCHEMA_PINNED_HASH entry allowlisted the regex [0-9a-f]{64}."""
    secret = 'api_key = "a3f5c9e2b7d14f8a06c3e5b9d2f7a14c8e0b5d3f9a2c7e4b1d8f0a5c3e9b7d2f"'
    assert _scan(tmp_path, "src/prod_config.py", secret) == 1, (
        "A 64-hex credential is cleared repo-wide. The schema pin file needs no "
        "allowlist at all — a bare digest has no `key =` assignment context and "
        "triggers no default rule."
    )


def test_guard_cell_clearance_does_not_launder_its_value(tmp_path):
    """DECISION_GUARD_CELL_KEYS clears an assignment shape, not a value.

    It uses regexTarget="match" so the cleared pattern is the whole
    ``*_guard_cell_key="..."`` construct. A different assignment in the same
    file must still be reported.
    """
    assert _scan(tmp_path, "src/decision/family_decision_engine.py", UNRELATED_SECRET) == 1


# Each cleared literal, at a site the ruling describes. All must stay silent.
CLEARED_LITERALS = [
    ("src/data/observation_client.py",
     '_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"'),
    ("src/control/live_health.py",
     'semantic_key = "day0_daily_extrema_unconditioned_count"'),
    ("docs/reference/security_false_positives.md",
     'token_id": "abc123def456"'),
    ("src/strategy/candidates/c1_joint_tail_bayes.py",
     '_STRATEGY_KEY = "c1_joint_tail_bayes"'),
    ("src/strategy/candidates/c2_opening_stale_fok.py",
     '_STRATEGY_KEY = "c2_opening_stale_fok"'),
    ("docs/operations/notes.md",
     "outcome_label=NO and outcome_label=YES"),
    ("src/decision/family_decision_engine.py",
     'q_lcb_guard_cell_key="day0_observed_boundary"'),
    ("src/decision/family_decision_engine.py",
     'selection_guard_cell_key="day0_observed_boundary"'),
    ("tests/state/_schema_pinned_hash.txt",
     "c058dfab84bb471f8d43225c18d339d567a805906fcb685e2cf4eba90d9a8e00"),
    ("tests/test_synthetic_fixture.py", UNRELATED_SECRET),
]


@pytest.mark.parametrize("rel,content", CLEARED_LITERALS)
def test_operator_cleared_literals_stay_suppressed(tmp_path, rel, content):
    assert _scan(tmp_path, rel, content) == 0, (
        f"{rel} regressed to reporting an operator-cleared value. Re-raising a "
        "[REVIEW-SAFE] item is a false-positive loop, not a finding."
    )
