"""P1/P2 hardening tests for the topology admission kernel.

Covers:
  * P1 fix — `reference artifact extraction` no longer self-conflicts when the
    caller wants to edit `scripts/topology_doctor.py`.
  * P1 fix — `modify topology kernel` is a real route for editing the
    admission kernel itself.
  * P2 fix — input normalization in `_reconcile_admission` (empty strings,
    `./` prefixes, duplicates, whitespace, `None` entries).
  * P2 fix — typed `match_policy` migration on `change settlement rounding`
    and `edit replay fidelity`, including `negative_phrases` veto.
  * P2 fix — glob compilation cache and equality-fast-path in `_matches_any`.
"""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Lock the P1/P2 kernel-hardening invariants so future profile edits
#          cannot regress the documented contracts.
# Reuse: When adding new admission rules, add cases here, not in
#        test_digest_admission_policy.py (that file is for the P0 contract).

from __future__ import annotations

import pytest

try:
    from scripts.topology_doctor_digest import (
        _compile_glob,
        _glob_match,
        _matches_any,
        _normalize_paths,
    )
    _DIGEST_AVAILABLE = True
except ModuleNotFoundError:
    # topology_doctor_digest.py deleted in Phase 3 (ULTIMATE_DESIGN §9.1).
    # Tests that depend on its private helpers are skipped post-deletion.
    _DIGEST_AVAILABLE = False
    _compile_glob = _glob_match = _matches_any = _normalize_paths = None  # type: ignore[assignment]

from scripts.topology_doctor import build_digest, run_navigation

_skip_if_no_digest = pytest.mark.skipif(
    not _DIGEST_AVAILABLE,
    reason="topology_doctor_digest.py deleted in Phase 3 (ULTIMATE_DESIGN §9.1)",
)


# ---------------------------------------------------------------------------
# P1 fix: reference-artifact-extraction self-conflict resolved
# ---------------------------------------------------------------------------

def test_reference_artifact_extraction_admits_topology_doctor():
    """Pre-fix: scripts/** forbidden_files blocked the explicitly allowed
    scripts/topology_doctor.py via route_contract_conflict. Post-fix: the
    file is admitted cleanly."""
    digest = build_digest(
        "reference artifact extraction work",
        ["scripts/topology_doctor.py"],
    )
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/topology_doctor.py" in digest["admission"]["admitted_files"]
    assert digest["admission"]["forbidden_hits"] == []


# ---------------------------------------------------------------------------
# P1 fix: modify-topology-kernel route exists and authorizes kernel edits
# ---------------------------------------------------------------------------

def test_modify_topology_kernel_admits_admission_files():
    digest = build_digest(
        "modify topology kernel admission policy",
        [
            "scripts/topology_doctor.py",
            "scripts/topology_doctor_digest.py",
            "architecture/topology.yaml",
            "tests/test_digest_admission_policy.py",
        ],
    )
    assert digest["admission"]["status"] == "admitted"
    assert digest["admission"]["profile_id"] == "modify topology kernel"


def test_modify_topology_kernel_blocks_unrelated_writes():
    """The new kernel profile MUST NOT silently authorize random src files."""
    digest = build_digest(
        "modify topology kernel admission policy",
        ["scripts/topology_doctor.py", "src/main.py"],
    )
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert "scripts/topology_doctor.py" in digest["admission"]["admitted_files"]
    assert "src/main.py" in digest["admission"]["out_of_scope_files"]


# ---------------------------------------------------------------------------
# P2 fix: input normalization
# ---------------------------------------------------------------------------

@_skip_if_no_digest
class TestNormalizePaths:
    def test_drops_empty_strings(self):
        assert _normalize_paths(["", "src/foo.py", ""]) == ["src/foo.py"]

    def test_drops_none(self):
        assert _normalize_paths([None, "src/foo.py", None]) == ["src/foo.py"]

    def test_strips_whitespace(self):
        assert _normalize_paths(["  src/foo.py  "]) == ["src/foo.py"]

    def test_strips_leading_dot_slash(self):
        assert _normalize_paths(["./src/foo.py"]) == ["src/foo.py"]
        # Multiple "./" prefixes also stripped.
        assert _normalize_paths(["././src/foo.py"]) == ["src/foo.py"]

    def test_dedups_preserving_order(self):
        assert _normalize_paths(
            ["b", "a", "b", "a", "c"]
        ) == ["b", "a", "c"]

    def test_dedups_after_normalization(self):
        # "src/foo.py" and "./src/foo.py" must collapse to one entry.
        assert _normalize_paths(["src/foo.py", "./src/foo.py"]) == ["src/foo.py"]

    def test_empty_input(self):
        assert _normalize_paths(None) == []
        assert _normalize_paths([]) == []

    def test_does_not_resolve_dotdot(self):
        """Security: do NOT call os.path.normpath; ../ resolution would let a
        caller bypass forbidden globs by reaching outside the repo root."""
        assert _normalize_paths(["../foo.py"]) == ["../foo.py"]


def test_normalization_does_not_admit_extra_files():
    """A flood of empty strings must not cause the kernel to grant admission
    for anything (and must not crash)."""
    digest = build_digest(
        "change settlement rounding",
        ["", None, "", "src/contracts/settlement_semantics.py", "  "],
    )
    assert digest["admission"]["status"] == "admitted"
    assert digest["admission"]["admitted_files"] == [
        "src/contracts/settlement_semantics.py",
    ]


def test_normalization_strips_dot_slash_prefix_in_admission():
    """Caller submits './src/contracts/settlement_semantics.py' (as `git
    diff --name-only` would). Admission must still match the manifest."""
    digest = build_digest(
        "change settlement rounding",
        ["./src/contracts/settlement_semantics.py"],
    )
    assert digest["admission"]["status"] == "admitted"
    assert "src/contracts/settlement_semantics.py" in digest["admission"]["admitted_files"]


# ---------------------------------------------------------------------------
# P2 fix: typed match_policy with negative_phrases
# ---------------------------------------------------------------------------

def test_settlement_profile_matches_real_task():
    digest = build_digest(
        "change settlement rounding for HKO",
        ["src/contracts/settlement_semantics.py"],
    )
    assert digest["admission"]["profile_id"] == "change settlement rounding"
    assert digest["admission"]["status"] == "admitted"


def test_settlement_profile_vetoed_by_negative_phrase():
    """The new negative_phrases must veto false-positive matches."""
    # "shock" used to nearly substring-match "hko" on case-insensitive
    # checks; with weak_terms.single_terms_can_select=false plus
    # negative_phrases this no longer selects the settlement profile.
    digest = build_digest(
        "investigate market shock event handling",
        [],
    )
    assert digest["admission"]["profile_id"] != "change settlement rounding"


def test_replay_profile_does_not_steal_replay_attack_task():
    digest = build_digest(
        "investigate replay attack vector in CLOB auth",
        [],
    )
    assert digest["admission"]["profile_id"] != "edit replay fidelity"


def test_replay_profile_strong_phrase_still_admits():
    digest = build_digest(
        "audit replay fidelity for shadow-mode determinism",
        ["src/engine/replay.py"],
    )
    assert digest["admission"]["profile_id"] == "edit replay fidelity"
    assert digest["admission"]["status"] == "admitted"


# ---------------------------------------------------------------------------
# P2 fix: glob cache and equality fast-path
# ---------------------------------------------------------------------------

@_skip_if_no_digest
class TestGlobCache:
    def test_compile_glob_returns_same_object(self):
        # The lru_cache must produce the same compiled pattern object on
        # repeat lookups.
        a = _compile_glob("src/**")
        b = _compile_glob("src/**")
        assert a is b

    def test_glob_match_exact(self):
        assert _glob_match("src/foo.py", "src/*.py")
        assert not _glob_match("tests/foo.py", "src/*.py")

    def test_glob_match_recursive(self):
        # fnmatch.translate handles ** as zero-or-more chars including "/".
        assert _glob_match("src/a/b/c.py", "src/**")

    def test_matches_any_equality_fast_path(self):
        # Verbatim equality must succeed without consulting the glob cache.
        assert _matches_any("a.py", ["a.py", "b.py"])

    def test_matches_any_no_patterns(self):
        assert _matches_any("a.py", []) is False
        assert _matches_any("a.py", None) is False  # type: ignore[arg-type]

    def test_matches_any_glob(self):
        assert _matches_any("src/foo/bar.py", ["src/**"])
        assert _matches_any("docs/archives/old.md", ["docs/archives/**"])
        assert not _matches_any("src/foo.py", ["docs/**"])


def test_admission_handles_large_file_list():
    """Smoke test: 200 files must reconcile in well under a second now that
    the glob cache eliminates per-call re.compile."""
    import time
    files = [f"src/foo_{i}.py" for i in range(200)]
    start = time.perf_counter()
    digest = build_digest("change settlement rounding", files)
    elapsed = time.perf_counter() - start
    assert digest["admission"]["status"] == "scope_expansion_required"
    # Generous bound to absorb CI noise; pre-fix runs were ~hundreds of ms.
    assert elapsed < 1.0, f"admission too slow: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Forbidden-wins still trumps normalization quirks
# ---------------------------------------------------------------------------

def test_dot_slash_does_not_bypass_forbidden_glob():
    """Pre-normalization, a caller could try './state/zeus_trades.db' to slip
    past a fnmatch('state/*.db') check on some platforms. Post-normalization
    the './' is stripped *before* the forbidden test so the block holds."""
    digest = build_digest(
        "change settlement rounding",
        ["./state/zeus_trades.db"],
    )
    assert digest["admission"]["status"] == "blocked"
    assert "state/zeus_trades.db" in digest["admission"]["forbidden_hits"]
