# Plan: Authority Read-Side Closure + Generation-Naming Eradication
> Created: 2026-05-28 | Status: AWAITING SEQUENCE CONFIRMATION

## Goal
Two structural programs (audits 2026-05-28), one worktree, ROI-ranked.

- **Program A — Economics-Authority Split** (5 findings)
  - F1 chain-rescue stops mutating entry economics (P1, highest ROI)
  - F2 event timestamps typed/explicit (P1)
  - F3 deterministic fill_authority backfill (P1/P2)
  - F4 canonical phase from events, not runtime strings (P2 invasive)
  - F5 trade_decisions legacy-mirror demotion (P2)
- **Program B — Generation-Naming Eradication** (7 PRs)
  - PR-B1 denylist static tests (xfail, non-breaking law definition)
  - PR-B2 SCHEMA_VERSION → schema fingerprint; delete schema_version cols/CHECK widening
  - PR-B3 `_v2`/`_v1`/`vnext` table canonicalization
  - PR-B4 script/module file renames + deletes
  - PR-B5 domain field renames (`signal_version`→`signal_id` etc.)
  - PR-B6 event row `event_version` removal
  - PR-B7 registry / docs `legacy_archived` cleanup

## Context
- Worktree base: origin/main = b360211d99 (#349). Includes #352 (chain refactor) — F1-F5 build on that.
- Note: local main has 7395ba735d (ghost-table register hotfix) not in origin/main. Likely unpushed live hotfix — confirm before merging program work.
- Program B PR-B2 (SCHEMA_VERSION removal) requires coordinated live-DB migration; daemons halt on schema mismatch.
- F4 phase-derivation is the most invasive single change.

## Recommended Sequence (await operator confirm)

| Wave | PR | Scope | Risk |
|------|-----|-------|------|
| 0 | PR-B1 | Denylist tests as xfail. Defines law without breaking. | none |
| 1 | PR-A1 | F1 chain economics split + new authority-specific fields | medium (semantic) |
| 2 | PR-A2 | F2 typed timestamps + F3 authority backfill | medium |
| 3 | PR-A3 | F4 canonical phase from events | high (invasive) |
| 4 | PR-B2..B7 | Naming sweep + SCHEMA_VERSION cutover | **high — live DB** |
| 5 | PR-A4 | F5 trade_decisions demotion | low (post-sweep) |

Rationale: starts with the non-breaking law (PR-B1 xfail), then deep semantic changes against the current naming, then mechanical sweep last because (a) it locks names and (b) live-DB cutover is its own coordinated event.

## Alternate Sequences (if operator prefers)
- **B-first**: PR-B1..B7 → A1..A5. Pro: economics work uses clean names from start. Con: 7-PR mechanical churn before any semantic improvement.
- **A-first**: A1..A5 → B1..B7. Pro: highest-ROI semantic wins land first. Con: economics PRs add new `*_version` fields that B5 then renames.

## Open Decisions for Operator
1. **Sequence**: recommended / B-first / A-first / interleaved-other
2. **PR-B2 cutover scheduling**: tie to a planned daemon-drain window, OR keep `SCHEMA_VERSION` alongside fingerprint until later?
3. **7395 hotfix on local main**: push to origin/main before any of this lands?
4. **One-PR-per-wave or single-mega-PR per program**: each wave is 300-800 LOC; single PRs preserve coherence but stack reviewer load. Recommend per-wave.

## Tasks (will be created after sequence confirmed)
- [ ] PR-B1 denylist tests (8 static tests; see audit doc §4 tests 1-8)
- [ ] PR-A1 F1 chain economics split (`portfolio.py`, `chain_reconciliation.py`, `lifecycle_events.py`, `db.py`, `harvester.py`)
- [ ] PR-A2 F2 timestamps + F3 backfill
- [ ] PR-A3 F4 phase-from-events
- [ ] PR-B2..B7 generation sweep (per-PR detail in §5 of source audit)
- [ ] PR-A4 F5 trade_decisions demotion

## Risks / Hard No-Gos (from audit §7)
- No compatibility aliases for old names (alias = rot survives)
- No "temporary migration scripts" in final main (violates denylist)
- No rename `_v2`→base while keeping old base as ghost (pick one)
- No `schema_epoch` replacement that is still a counter — fingerprint only
- No `event_version` "just in case" — delete if no live branch reads it

## Acceptance (Program B done when)
```
git grep -i version             -> 0 repo-owned current-source hits
git grep -E '_v[0-9]+|v[0-9]+_' -> 0
git grep -i legacy              -> 0 (or excluded archive only)
fresh sqlite_master scan        -> 0 forbidden names
table registry                  -> 0 legacy_archived / *_new / *_old
dataclass fields                -> 0 *_version
event rows                      -> no event_version column
schema currency                 -> fingerprint only, no counter
```
