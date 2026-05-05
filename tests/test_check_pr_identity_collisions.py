# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/PROPOSALS_2026-05-04.md P1 — coverage
#                  for scripts/check_pr_identity_collisions.py.
"""Unit tests for the identity-collision diff parser.

Pins the load-bearing parsing logic in
``added_classes_in_diff`` — the rest of the script is subprocess
glue that's covered by the GitHub Actions workflow itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path so the script-as-module import works.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from unittest.mock import patch

from check_pr_identity_collisions import (  # noqa: E402
    IDENTITY_FILE_PATTERNS,
    _file_matches_identity_scope,
    added_classes_in_diff,
    main,
)


# ---- _file_matches_identity_scope -----------------------------------------


def test_identity_scope_matches_calibration_forecast_file():
    assert _file_matches_identity_scope(
        "src/calibration/forecast_calibration_domain.py"
    )


def test_identity_scope_matches_types_dir():
    assert _file_matches_identity_scope("src/types/metric_identity.py")


def test_identity_scope_matches_strategy_profile():
    assert _file_matches_identity_scope("src/strategy/strategy_profile.py")
    assert _file_matches_identity_scope(
        "src/strategy/strategy_profile_registry.py"
    )


def test_identity_scope_excludes_test_files():
    """Tests can freely add ``class TestFoo`` without flagging."""
    assert not _file_matches_identity_scope("tests/test_anything.py")


def test_identity_scope_excludes_evaluator():
    """evaluator.py is hot but not identity-bearing — methods get added
    routinely, two PRs both adding helper functions there is normal.
    """
    assert not _file_matches_identity_scope("src/engine/evaluator.py")


# ---- added_classes_in_diff ------------------------------------------------


def test_added_class_in_identity_file_is_detected():
    diff = """\
diff --git a/src/types/foo.py b/src/types/foo.py
new file mode 100644
--- /dev/null
+++ b/src/types/foo.py
@@ -0,0 +1,3 @@
+class Foo:
+    pass
"""
    result = added_classes_in_diff(diff)
    assert ("src/types/foo.py", "Foo") in result


def test_added_class_outside_identity_scope_is_ignored():
    diff = """\
diff --git a/src/engine/helper.py b/src/engine/helper.py
--- a/src/engine/helper.py
+++ b/src/engine/helper.py
@@ -0,0 +1,2 @@
+class HelperLocal:
+    pass
"""
    result = added_classes_in_diff(diff)
    assert result == set()


def test_added_dataclass_with_decorator_is_detected():
    """``@dataclass``-decorated classes must still match — the bare
    ``class X:`` line appears in the diff regardless of decorator.
    """
    diff = """\
diff --git a/src/calibration/forecast_calibration_domain.py b/src/calibration/forecast_calibration_domain.py
--- a/src/calibration/forecast_calibration_domain.py
+++ b/src/calibration/forecast_calibration_domain.py
@@ -10,0 +11,5 @@
+@dataclass(frozen=True)
+class ForecastCalibrationDomain:
+    source_id: str
+    cycle_hour_utc: str
+    horizon_profile: str
"""
    result = added_classes_in_diff(diff)
    assert (
        "src/calibration/forecast_calibration_domain.py",
        "ForecastCalibrationDomain",
    ) in result


def test_modifications_inside_existing_class_are_ignored():
    """Adding methods to an existing class doesn't add a `class X:` line,
    so the parser correctly ignores it.  This is the false-positive
    avoidance for normal collaboration.
    """
    diff = """\
diff --git a/src/types/metric_identity.py b/src/types/metric_identity.py
--- a/src/types/metric_identity.py
+++ b/src/types/metric_identity.py
@@ -50,0 +51,3 @@
+    def new_method(self):
+        return 1
+
"""
    result = added_classes_in_diff(diff)
    assert result == set()


def test_inheriting_class_is_detected():
    """``class Foo(Bar):`` form must match — Pydantic models, NamedTuples,
    Protocol classes, etc. all rely on this.
    """
    diff = """\
diff --git a/src/contracts/foo.py b/src/contracts/foo.py
--- /dev/null
+++ b/src/contracts/foo.py
@@ -0,0 +1,3 @@
+from pydantic import BaseModel
+
+class FooModel(BaseModel):
+    x: int
"""
    result = added_classes_in_diff(diff)
    assert ("src/contracts/foo.py", "FooModel") in result


def test_two_prs_adding_same_class_intersect_correctly():
    """Simulate the PR #55 vs PR #56 scenario — both add
    ``class ForecastCalibrationDomain:`` in the same path.
    """
    diff_a = """\
+++ b/src/calibration/forecast_calibration_domain.py
@@ -0,0 +1,2 @@
+class ForecastCalibrationDomain:
+    pass
"""
    diff_b = """\
+++ b/src/calibration/forecast_calibration_domain.py
@@ -0,0 +1,2 @@
+class ForecastCalibrationDomain:
+    source_id: str
"""
    a = added_classes_in_diff(diff_a)
    b = added_classes_in_diff(diff_b)
    overlap = a & b
    assert overlap == {(
        "src/calibration/forecast_calibration_domain.py",
        "ForecastCalibrationDomain",
    )}


def test_underscore_prefixed_private_class_still_counts():
    """``_FooImpl`` still bears identity within the module — flag it."""
    diff = """\
+++ b/src/types/private.py
@@ -0,0 +1,2 @@
+class _FooImpl:
+    pass
"""
    assert ("src/types/private.py", "_FooImpl") in added_classes_in_diff(diff)


def test_empty_diff_returns_empty_set():
    assert added_classes_in_diff("") == set()


def test_class_in_test_file_outside_scope():
    """The most common false-positive source: tests adding helper classes."""
    diff = """\
+++ b/tests/test_foo.py
@@ -0,0 +1,2 @@
+class TestHelper:
+    pass
"""
    assert added_classes_in_diff(diff) == set()


def test_identity_patterns_compile():
    """Sanity: every pattern in IDENTITY_FILE_PATTERNS is a valid regex."""
    import re
    for pat in IDENTITY_FILE_PATTERNS:
        re.compile(pat)


# ---- stacked-PR skip semantics --------------------------------------------
#
# Tests for the predicate at scripts/check_pr_identity_collisions.py:191
# that decides which other PRs are stacked (skip) vs. sibling (check).
# Stubs _pr_base, _list_other_open_prs, and _pr_diff so no gh CLI needed.


_IDENTITY_DIFF = """\
diff --git a/src/types/thing.py b/src/types/thing.py
new file mode 100644
--- /dev/null
+++ b/src/types/thing.py
@@ -0,0 +1,2 @@
+class ThingIdentity:
+    pass
"""


class TestStackedPrSkipSemantics:
    """Unit tests for the stacked-PR skip predicate.

    The predicate in main():
        if pr.get("headRefOid") == this_base:
            continue   # stacked: skip
    Stacked  = other PR's headRefOid IS our baseRefOid  → SKIP (expected inheritance)
    Sibling  = other PR's headRefOid is different, but same base as ours → NOT skipped
    Unrelated = different head AND different base → NOT skipped (evaluated normally)
    """

    BASE_OID = "aaa000"
    OTHER_HEAD_STACKED = BASE_OID          # stacked: other.head == this.base
    OTHER_HEAD_SIBLING = "bbb111"          # sibling: different head
    OTHER_HEAD_UNRELATED = "ccc222"        # unrelated

    def _run_main(self, this_pr_oid: str, others: list[dict], this_diff: str, other_diff: str) -> list[tuple]:
        """Run main() with all external calls stubbed; return collisions list."""
        collisions_captured: list = []

        def fake_format_warning(this_pr, collisions):
            collisions_captured.extend(collisions)
            return "WARNING\n"

        import check_pr_identity_collisions as mod
        with (
            patch.object(mod, "_pr_base", return_value=self.BASE_OID),
            patch.object(mod, "_list_other_open_prs", return_value=others),
            patch.object(mod, "_pr_diff", return_value=other_diff),
            patch.object(mod, "_format_warning", side_effect=fake_format_warning),
        ):
            # Patch _pr_diff for THIS pr to return our identity diff
            original_pr_diff = mod._pr_diff

            def selective_pr_diff(pr_number, repo):
                if pr_number == 99:
                    return this_diff
                return other_diff

            with patch.object(mod, "_pr_diff", side_effect=selective_pr_diff):
                main(["--this-pr", "99", "--repo", "owner/repo"])

        return collisions_captured

    def test_stacked_pr_is_skipped(self):
        """Stacked: other PR's headRefOid == this PR's baseRefOid → skip."""
        others = [{"number": 10, "headRefOid": self.OTHER_HEAD_STACKED,
                   "headRefName": "stacked-branch", "baseRefOid": "prev000"}]
        collisions = self._run_main(
            this_pr_oid=self.BASE_OID,
            others=others,
            this_diff=_IDENTITY_DIFF,
            other_diff=_IDENTITY_DIFF,
        )
        assert collisions == [], (
            "Stacked PR (other.headRefOid == this.baseRefOid) must be skipped; "
            f"got collisions: {collisions}"
        )

    def test_sibling_pr_is_not_skipped(self):
        """Sibling: same base commit, different head → primary collision scenario."""
        others = [{"number": 11, "headRefOid": self.OTHER_HEAD_SIBLING,
                   "headRefName": "sibling-branch", "baseRefOid": self.BASE_OID}]
        collisions = self._run_main(
            this_pr_oid=self.BASE_OID,
            others=others,
            this_diff=_IDENTITY_DIFF,
            other_diff=_IDENTITY_DIFF,
        )
        assert len(collisions) == 1, (
            "Sibling PR (same baseRefOid, different head) must NOT be skipped; "
            f"got: {collisions}"
        )
        pr_dict, overlap = collisions[0]
        assert pr_dict["number"] == 11
        assert ("src/types/thing.py", "ThingIdentity") in overlap

    def test_unrelated_pr_is_evaluated(self):
        """Unrelated: different head AND different base → evaluated normally."""
        others = [{"number": 12, "headRefOid": self.OTHER_HEAD_UNRELATED,
                   "headRefName": "unrelated-branch", "baseRefOid": "other-base"}]
        collisions = self._run_main(
            this_pr_oid=self.BASE_OID,
            others=others,
            this_diff=_IDENTITY_DIFF,
            other_diff=_IDENTITY_DIFF,
        )
        # Unrelated PR adds the same class → collision detected
        assert len(collisions) == 1, (
            "Unrelated PR must be evaluated; expected collision; "
            f"got: {collisions}"
        )
