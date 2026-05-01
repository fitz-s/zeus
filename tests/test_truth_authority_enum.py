# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P1-3 +
#                  repo_review_2026-05-01 SYNTHESIS K-C +
#                  INV-23 (DEGRADED_PROJECTION must be a distinct
#                  non-VERIFIED label) +
#                  P1_3_TRUTH_AUTHORITY_AUDIT.md (architect, 2026-05-01)
"""Relationship tests for the closed `TruthAuthority` StrEnum.

The audit at `docs/operations/repo_review_2026-05-01/
P1_3_TRUTH_AUTHORITY_AUDIT.md` recommended Option (a) MINIMAL: define the
StrEnum, migrate the canonical producer (`_TRUTH_AUTHORITY_MAP` in
`src/state/portfolio.py:65-69`), add ONE relationship test. This file is
that relationship test.

Three layered antibodies:

1. **Member contract**: the enum exposes exactly the 4 documented members.
   Adding a 5th here is a deliberate type edit that triggers this test
   PLUS the audit doc to be re-validated.
2. **Producer-side closure**: every value emitted by
   `_TRUTH_AUTHORITY_MAP` round-trips through the enum constructor.
   Catches any future producer that hand-codes a string outside the enum.
3. **Wire compatibility**: the JSON serialization, set-membership, and
   string-equality contracts that downstream readers (and the file paths
   under `src/state/truth_files.py`) implicitly assume continue to hold.
"""
from __future__ import annotations

import json

import pytest

from src.types.truth_authority import TruthAuthority


# Frozen baseline of the canonical 4-member set as of 2026-05-01.
# To extend: add the new member to TruthAuthority, update this set, AND
# update P1_3_TRUTH_AUTHORITY_AUDIT.md to re-classify per-grammar
# (the audit found 6 distinct authority namespaces; new members must
# clearly belong to grammar A — truth-file authority).
_EXPECTED_MEMBERS = frozenset({
    "VERIFIED",
    "UNVERIFIED",
    "QUARANTINED",
    "DEGRADED_PROJECTION",
})


def test_truth_authority_member_set_is_exactly_the_2026_05_01_baseline():
    """Lock the 4-member surface. Adding a 5th member without updating
    this baseline (and re-reading the audit) fails."""
    actual = {a.value for a in TruthAuthority}
    assert actual == _EXPECTED_MEMBERS, (
        "TruthAuthority drift: enum members do not match the 2026-05-01 "
        f"baseline.\n"
        f"  added (in enum, not in baseline): {sorted(actual - _EXPECTED_MEMBERS)}\n"
        f"  removed (in baseline, not in enum): {sorted(_EXPECTED_MEMBERS - actual)}\n"
        "If you genuinely added a new authority member, update both this "
        "test's _EXPECTED_MEMBERS AND re-classify per the 6-grammar table "
        "in docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md "
        "to confirm it belongs to grammar A (truth-file authority) and not "
        "to one of the 5 sibling grammars (PortfolioState, ScanAuthority, "
        "AuthorityTier, DepthProofSource, forecast authority_tier, "
        "entry_economics_authority)."
    )


def test_truth_authority_map_values_are_all_enum_members():
    """Producer-side closure: `_TRUTH_AUTHORITY_MAP` is the canonical
    grammar-B → grammar-A translator. Every value it emits must round-trip
    through the enum constructor. If a future agent hand-codes a string
    here that bypasses the enum, this test fails immediately."""
    from src.state.portfolio import _TRUTH_AUTHORITY_MAP

    for grammar_b_key, value in _TRUTH_AUTHORITY_MAP.items():
        # StrEnum members ARE str instances — coerce-and-compare locks both:
        # (a) bare string: TruthAuthority(str_value) succeeds.
        # (b) enum member: round-trip TruthAuthority(member.value) is identity.
        assert TruthAuthority(str(value)) is TruthAuthority(value.value if isinstance(value, TruthAuthority) else value), (
            f"P1-3 producer drift: _TRUTH_AUTHORITY_MAP[{grammar_b_key!r}] = "
            f"{value!r} is not a closed TruthAuthority member. Use one of "
            f"the four canonical members instead of a hand-coded string."
        )

    # Also lock the specific mapping shape per INV-23 — degraded MUST NOT
    # collapse into VERIFIED, and canonical_db MUST be VERIFIED.
    assert _TRUTH_AUTHORITY_MAP["canonical_db"] == TruthAuthority.VERIFIED
    assert _TRUTH_AUTHORITY_MAP["degraded"] == TruthAuthority.DEGRADED_PROJECTION
    assert _TRUTH_AUTHORITY_MAP["degraded"] != TruthAuthority.VERIFIED, (
        "INV-23 violation: degraded must not collapse into VERIFIED."
    )
    assert _TRUTH_AUTHORITY_MAP["unverified"] == TruthAuthority.UNVERIFIED


def test_truth_authority_strenum_wire_compat_json_roundtrip():
    """Wire-compat antibody #1: JSON serialization. Every consumer of
    truth-stamp JSON (status_summary writers, positions.json) treats the
    field as a plain string. StrEnum members serialize as bare strings
    with no class qualifier, so the wire format is unchanged.
    """
    for member in TruthAuthority:
        encoded = json.dumps({"authority": member})
        decoded = json.loads(encoded)
        assert decoded == {"authority": member.value}, (
            f"JSON serialization drift: {member!r} did not round-trip as a "
            f"bare string. Expected {member.value!r}, got {decoded!r}."
        )
        # And the opposite direction — a JSON-loaded plain string must
        # be coercible back to the enum.
        assert TruthAuthority(decoded["authority"]) is member


def test_truth_authority_strenum_wire_compat_set_and_equality():
    """Wire-compat antibody #2: equality and set membership against bare
    strings. Existing consumers (e.g., `harvester_truth_writer.py:150`'s
    `!= "VERIFIED"`, `db.py:2453`'s `not in {"VERIFIED","UNVERIFIED",
    "QUARANTINED"}`) compare against bare strings. StrEnum equality MUST
    match bare-string equality in both directions or every existing
    consumer breaks silently.
    """
    assert TruthAuthority.VERIFIED == "VERIFIED"
    assert "VERIFIED" == TruthAuthority.VERIFIED
    assert TruthAuthority.VERIFIED in {"VERIFIED", "UNVERIFIED", "QUARANTINED"}
    assert "VERIFIED" in {TruthAuthority.VERIFIED, TruthAuthority.UNVERIFIED}
    # f-string interpolation must produce the bare value, not the
    # qualified `TruthAuthority.VERIFIED` repr.
    assert f"{TruthAuthority.VERIFIED}" == "VERIFIED"


def test_truth_authority_rejects_unknown_string():
    """StrEnum constructor MUST refuse strings outside the closed set —
    that's the primary structural antibody. A future producer typing
    `"VERIFED"` (typo) or `"DEGRADED"` (wrong grammar) is caught at
    construction time."""
    for bad in ("", "VERIFED", "DEGRADED", "verified", "DEGRADED_PROJECTON"):
        with pytest.raises(ValueError):
            TruthAuthority(bad)


# ---------------------------------------------------------------------------
# Predicate helpers — exhaustive truth tables
# ---------------------------------------------------------------------------


def test_is_authoritative_truth_table():
    """`is_authoritative` must return True for VERIFIED only, False for
    every other member. This locks the binary semantic so a future agent
    can't quietly "fix" DEGRADED_PROJECTION to authoritative=True."""
    from src.types.truth_authority import is_authoritative

    expected = {
        TruthAuthority.VERIFIED: True,
        TruthAuthority.UNVERIFIED: False,
        TruthAuthority.QUARANTINED: False,
        TruthAuthority.DEGRADED_PROJECTION: False,
    }
    assert set(expected.keys()) == set(TruthAuthority), (
        "Truth table must enumerate every TruthAuthority member; if a new "
        "member was added, decide explicitly whether it's authoritative."
    )
    for member, want in expected.items():
        got = is_authoritative(member)
        assert got is want, (
            f"is_authoritative({member!r}) returned {got}, expected {want}. "
            "If you intentionally changed the semantic, also update every "
            "consumer of `truth['authority']` that gates on VERIFIED-only."
        )


def test_is_authoritative_refuses_bare_string():
    """The predicate is type-only by design — bare strings must construct
    a TruthAuthority first so the closed-enum guard catches typos before
    the predicate runs."""
    from src.types.truth_authority import is_authoritative

    with pytest.raises(TypeError, match=r"requires a TruthAuthority instance"):
        is_authoritative("VERIFIED")  # type: ignore[arg-type]


def test_requires_human_review_truth_table():
    """`requires_human_review` must return True for QUARANTINED and
    DEGRADED_PROJECTION (the two not-yet-canonical-but-not-fail-closed
    states), False for VERIFIED and UNVERIFIED. This locks the
    operator-paging surface."""
    from src.types.truth_authority import requires_human_review

    expected = {
        TruthAuthority.VERIFIED: False,
        TruthAuthority.UNVERIFIED: False,  # fail-closed default; not yet flagged
        TruthAuthority.QUARANTINED: True,
        TruthAuthority.DEGRADED_PROJECTION: True,
    }
    assert set(expected.keys()) == set(TruthAuthority)
    for member, want in expected.items():
        got = requires_human_review(member)
        assert got is want, (
            f"requires_human_review({member!r}) returned {got}, expected "
            f"{want}. If a new member was added, decide whether it pages a "
            "human before merging."
        )


def test_requires_human_review_refuses_bare_string():
    from src.types.truth_authority import requires_human_review

    with pytest.raises(TypeError, match=r"requires a TruthAuthority instance"):
        requires_human_review("DEGRADED_PROJECTION")  # type: ignore[arg-type]


def test_predicates_partition_the_enum_consistently():
    """Cross-predicate sanity: is_authoritative and requires_human_review
    must NOT both be True for any member (a row can't be simultaneously
    canonical AND need human review). UNVERIFIED is the only member where
    both are False — the fail-closed initial state.
    """
    from src.types.truth_authority import is_authoritative, requires_human_review

    for member in TruthAuthority:
        auth = is_authoritative(member)
        review = requires_human_review(member)
        assert not (auth and review), (
            f"Predicate conflict on {member!r}: is_authoritative={auth}, "
            f"requires_human_review={review}. A canonical row cannot also "
            "be one that needs human attention; reconcile the two helpers."
        )
