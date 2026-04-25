"""Regression tests guarding against return of legacy substring/echo bugs.

Each test here corresponds to a specific historical false-positive uncovered
in §15 of docs/reference/Zeus_Apr25_review.md. The intent is that any future
refactor of the resolver/reconciler must keep these red lines green.
"""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Pin specific false-positives so they cannot return.
# Reuse: When a new false-positive is found in production, add a case here.

from __future__ import annotations

from scripts.topology_doctor import build_digest


# ---------------------------------------------------------------------------
# Historical bug 1: "improve source code quality" matched "modify data
# ingestion" because the legacy substring matcher saw the bare token "source".
# ---------------------------------------------------------------------------

def test_regression_source_quality_does_not_match_data_ingestion():
    digest = build_digest("improve source code quality", ["src/foo.py"])
    assert digest["profile"] != "modify data ingestion"
    # And the caller's file is NOT silently admitted via generic echo.
    assert "src/foo.py" not in digest["admission"]["admitted_files"]


# ---------------------------------------------------------------------------
# Historical bug 2: any task with the bare word "test" matched test-suite
# profiles, allowing tests/ files to be admitted under unrelated work.
# ---------------------------------------------------------------------------

def test_regression_bare_test_word_does_not_admit_tests_dir():
    digest = build_digest("test the new behavior end-to-end", ["tests/test_x.py"])
    # Even if a profile resolves, tests/test_x.py must not be admitted
    # without the profile actually listing it.
    if "tests/test_x.py" in digest["admission"]["admitted_files"]:
        # Only acceptable if the resolved profile genuinely lists it.
        profile_id = digest["admission"]["profile_id"]
        assert profile_id != "generic"


# ---------------------------------------------------------------------------
# Historical bug 3: "cleanup signal handling" matched the signal-pipeline
# profile via bare "signal" and admitted any src/signal/*.py path.
# ---------------------------------------------------------------------------

def test_regression_signal_word_does_not_blanket_admit_signal_dir():
    digest = build_digest("cleanup signal handling boilerplate", ["src/foo.py"])
    # src/foo.py is not in any signal profile's allowed_files; must not be admitted.
    assert "src/foo.py" not in digest["admission"]["admitted_files"]


# ---------------------------------------------------------------------------
# Historical bug 4: caller-echo. The legacy generic fallback mirrored the
# caller's --files into allowed_files, granting implicit write authority.
# ---------------------------------------------------------------------------

def test_regression_generic_fallback_does_not_mirror_caller_files():
    """When task and file evidence resolve no profile, generic fallback
    must NOT silently echo the caller's --files into admitted_files."""
    digest = build_digest(
        "vague task description that matches no profile",
        ["docs/notes/random.md", "README.md"],  # paths not bound to any profile
    )
    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert "docs/notes/random.md" not in digest["admission"]["admitted_files"]
    assert "README.md" not in digest["admission"]["admitted_files"]


# ---------------------------------------------------------------------------
# Historical bug 5: forbidden patterns under the generic profile were skipped
# because legacy code returned early on profile=generic.
# ---------------------------------------------------------------------------

def test_regression_generic_profile_still_enforces_forbidden_patterns():
    digest = build_digest("misc cleanup", ["state/zeus_trades.db"])
    assert digest["admission"]["status"] == "blocked"
    assert "state/zeus_trades.db" in digest["admission"]["forbidden_hits"]


# ---------------------------------------------------------------------------
# Historical bug 6: partial profile match silently expanded the profile to
# accept any extra caller path (i.e. the union of profile.allowed_files and
# the caller's --files was authorized).
# ---------------------------------------------------------------------------

def test_regression_partial_match_does_not_expand_profile_allowlist():
    digest = build_digest(
        "change settlement rounding",
        ["src/contracts/settlement_semantics.py", "src/main.py"],
    )
    # Only the in-profile path is admitted. src/main.py is NOT in the
    # settlement profile and must be flagged out_of_scope.
    assert "src/contracts/settlement_semantics.py" in digest["admission"]["admitted_files"]
    assert "src/main.py" not in digest["admission"]["admitted_files"]
    assert "src/main.py" in digest["admission"]["out_of_scope_files"]
    assert digest["admission"]["status"] == "scope_expansion_required"


# ---------------------------------------------------------------------------
# Historical bug 7: route_context.allowed_files was treated as authoritative
# write authorization by downstream tooling. New contract: it's legacy_advisory.
# ---------------------------------------------------------------------------

def test_regression_admission_envelope_is_authoritative_not_allowed_files():
    digest = build_digest(
        "change settlement rounding",
        ["src/state/lifecycle_manager.py"],  # out of scope
    )
    # Legacy field still exists for backward compat...
    assert "allowed_files" in digest
    # ...but the admission envelope refuses authorization.
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert digest["admission"]["admitted_files"] == []
