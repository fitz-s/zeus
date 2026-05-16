# R-Letter Namespace Ruling

**Issued**: 2026-04-17 (post-compact) by team-lead
**Trigger**: critic-alice-2 flagged on onboarding that Phase 4.5 brief R-Q..R-U collides with testeng-emma final_dump §4 forward-draft R-Q..R-U.

## Decision

**Phase 4.5 wins R-Q..R-U** (landing NOW as failing tests in `tests/test_phase4_5_extractor.py`).
**testeng-emma's forward drafts shift to R-V..R-Z.**

## Authoritative mapping

### Phase 4.5 (this wave, about to land)

| Letter | Invariant | Owner |
|---|---|---|
| R-Q | GRIB extractor NEVER produces `unit="K"` or Kelvin-range member values | testeng-grace → exec-dan |
| R-R | Dynamic step horizon: west-coast day7 ⇒ step ≥ 204h | testeng-grace → exec-dan |
| R-S | Boundary-classified member ⇒ JSON `training_allowed=false` | testeng-grace → exec-dan |
| R-T | Causality slots always labeled (high default `"OK"`), never dropped | testeng-grace → exec-dan |
| R-U | `manifest_hash` stable across re-extractions (sorted-json canonical) | testeng-grace → exec-dan |

### Forward drafts (emma final_dump §4) — renamed

| Old | **New** | Invariant | Phase |
|---|---|---|---|
| R-Q | **R-V** | `Day0LowNowcastSignal(observed_low_so_far=X)` uses X, not silent zero | Phase 6 |
| R-R | **R-W** | `mn2t6` ingest: any boundary member ⇒ `training_allowed=false` | Phase 5 |
| R-S | **R-X** | `settlements_v2` live writer requires explicit `temperature_metric` | Phase 7 cutover |
| R-T | **R-Y** | `kelly_size(p_posterior, entry_price=X)` rejects bare-float once typed | Phase 9 |
| R-U | **R-Z** | `classify_chain_state()` returns `CHAIN_UNKNOWN` on stale reconcile | post-DT#4 activation |

## Rationale

1. Phase 4.5 R-letters are about to be committed as failing pytest (testeng-grace drafting). emma's drafts are prose forward-plans, not yet tests. Cost of renaming tests >> cost of renaming prose.
2. emma's letter choices were provisional (her dump explicitly says "already in scope" — never committed). Provisional loses to about-to-land.
3. R-V..R-Z still leaves 0 letters free. Future phases beyond Phase 9 will need R-AA, R-AB... or a letter-recycling policy. Not urgent; log for Phase 9+ planning.

## Namespace policy going forward

- R-letter claim is atomic: first-to-commit-test wins the letter.
- Before drafting new R-letters, grep `tests/` + final_dump files for the letter you want.
- A letter **once used in a landed test file is immutable**. It cannot be reassigned; only alias-renamed in comments.
- When a forward-draft R-letter in a final_dump gets bumped, update the final_dump's §4 with a rename note (or append to this ruling file).

## Propagation

- testeng-grace: **USE R-Q..R-U per handoff / phase4_plan.md §5**. Ignore emma's §4 labels when reading her final_dump.
- exec-dan: function signatures in brief unchanged. R-test labels unchanged.
- critic-alice: checklist absorbs this ruling.
- Future team-lead: Phase 5 opening brief must reference R-W (not R-R) for boundary quarantine, etc.

## Open item

- Appending `[RENAMED: see r_letter_namespace_ruling.md]` to testeng-emma final_dump §4 is doc hygiene. Not blocking; pick up at Phase 4.5 close.
