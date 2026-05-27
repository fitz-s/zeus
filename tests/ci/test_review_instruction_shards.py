# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase G
#                  External review-upgrade spec (operator 2026-05-26)
"""
Phase G structural tests for Copilot review instruction shards.

Proves that the required instruction-file architecture stays in place:
  - all six required shards exist
  - each has a YAML frontmatter `applyTo`
  - each is under the 3600-char Copilot budget
  - hot surfaces (cycle_runtime.py + forecast/scanner/ingest) are routed
    into the correct shard via applyTo
  - shards contain the concrete review-precision phrases we ship in this PR
  - no shard depends on external HTTP/HTTPS links for substantive content
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTR = REPO_ROOT / ".github" / "instructions"
ROOT_INSTR = REPO_ROOT / ".github" / "copilot-instructions.md"

# 3600 must match scripts/ci/check_copilot_instruction_budget.py CHAR_BUDGET.
CHAR_BUDGET = 3600

# Required shards per Phase G spec.
REQUIRED_SHARDS = (
    "tier-scope.instructions.md",
    "zeus-execution-settlement.instructions.md",
    "zeus-forecast-source.instructions.md",
    "zeus-schema-state.instructions.md",
    "zeus-ci-tests.instructions.md",
    "docs-agent-review.instructions.md",
)

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_APPLY_TO = re.compile(r"^applyTo:\s*\"([^\"]+)\"\s*$", re.MULTILINE)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _apply_to_globs(path: Path) -> list[str]:
    text = _text(path)
    m = _FRONTMATTER.match(text)
    if not m:
        return []
    a = _APPLY_TO.search(m.group(1))
    if not a:
        return []
    return [g.strip() for g in a.group(1).split(",") if g.strip()]


# ---------------------------------------------------------------------------
# Existence + frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_SHARDS)
def test_required_shard_exists(name):
    assert (INSTR / name).exists(), f"missing required shard: {name}"


@pytest.mark.parametrize("name", REQUIRED_SHARDS)
def test_required_shard_has_apply_to(name):
    globs = _apply_to_globs(INSTR / name)
    assert globs, f"{name} missing applyTo frontmatter"


# ---------------------------------------------------------------------------
# Budget (matches check_copilot_instruction_budget.py)
# ---------------------------------------------------------------------------


def test_root_copilot_instructions_under_budget():
    # Use len(text) — char count — to match scripts/ci/check_copilot_instruction_budget.py.
    # Path.stat().st_size returns bytes, which diverges from char count on
    # multi-byte UTF-8 content (e.g. em-dash, NBSP) and can produce a test
    # verdict that disagrees with the actual budget enforcer.
    chars = len(_text(ROOT_INSTR))
    assert chars <= CHAR_BUDGET, f"{ROOT_INSTR} is {chars} chars > {CHAR_BUDGET}"


@pytest.mark.parametrize("shard", sorted(p.name for p in INSTR.glob("*.instructions.md")))
def test_every_shard_under_budget(shard):
    chars = len(_text(INSTR / shard))
    assert chars <= CHAR_BUDGET, f"{shard} is {chars} chars > {CHAR_BUDGET}"


# ---------------------------------------------------------------------------
# Hot-surface routing
# ---------------------------------------------------------------------------


def test_execution_shard_routes_cycle_runtime():
    globs = _apply_to_globs(INSTR / "zeus-execution-settlement.instructions.md")
    joined = ",".join(globs)
    assert "src/engine/cycle_runtime.py" in joined, (
        "execution shard must route src/engine/cycle_runtime.py for FC-03"
    )


def test_forecast_shard_routes_ingest_main():
    globs = _apply_to_globs(INSTR / "zeus-forecast-source.instructions.md")
    joined = ",".join(globs)
    assert "src/ingest_main.py" in joined, (
        "forecast shard must route src/ingest_main.py for FC-04/FC-05"
    )


# ---------------------------------------------------------------------------
# Required precision phrases (per Phase G spec)
# ---------------------------------------------------------------------------


def test_execution_shard_contains_fresh_at_submit_section():
    t = _text(INSTR / "zeus-execution-settlement.instructions.md")
    for phrase in ("Fresh-at-submit", "submit-time", "executable_snapshot_stale"):
        assert phrase in t, f"execution shard missing precision phrase: {phrase!r}"


def test_forecast_shard_covers_known_failure_chains():
    t = _text(INSTR / "zeus-forecast-source.instructions.md")
    for phrase in (
        "Latest snapshot",
        "source_run_coverage",
        "readiness_state",
        "full tag scan",
        "slug",
        "safe-fetch",
        "Settlement",
        "Day0",
    ):
        assert phrase in t, f"forecast shard missing phrase: {phrase!r}"


def test_test_shard_requires_runtime_relationship_target():
    t = _text(INSTR / "zeus-ci-tests.instructions.md")
    for phrase in ("runtime path", "relationship test", "one event per city", "time.sleep"):
        assert phrase in t, f"ci-tests shard missing phrase: {phrase!r}"


def test_docs_shard_separates_advisory_from_structural():
    t = _text(INSTR / "docs-agent-review.instructions.md")
    assert "Advisory vs structural" in t


def test_schema_shard_calls_out_owner_db():
    t = _text(INSTR / "zeus-schema-state.instructions.md")
    for phrase in ("owner DB", "Verification"):
        assert phrase in t, f"schema shard missing phrase: {phrase!r}"


def test_tier_scope_shard_has_root_target_rule():
    t = _text(INSTR / "tier-scope.instructions.md")
    assert "Root target rule" in t
    assert "relationship test" in t


def test_root_copilot_instructions_contains_finding_quality_contract():
    t = _text(ROOT_INSTR)
    assert "Finding quality contract" in t
    assert "root runtime path" in t.lower() or "root runtime" in t.lower()


# ---------------------------------------------------------------------------
# No external links as sole authority
# ---------------------------------------------------------------------------


def test_root_and_shards_do_not_rely_on_external_links_for_substantive_content():
    """Substantive review rules must be inline, not hidden behind external URLs.
    Citation links inside parens or quotes are OK; a shard with NOTHING but
    'see https://...' is what we're catching."""
    for path in [ROOT_INSTR, *INSTR.glob("*.instructions.md")]:
        text = _text(path)
        # Allow URLs to be present (some shards cite GitHub doc anchors) but
        # ensure body is not just a URL pointer.
        non_link_lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith(("http://", "https://"))
        ]
        # Any shard should have ≥10 substantive non-URL lines.
        assert len(non_link_lines) >= 10, (
            f"{path.name} has <10 substantive non-URL lines"
        )
