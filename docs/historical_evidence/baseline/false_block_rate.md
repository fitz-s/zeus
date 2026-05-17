# False Block Rate Baseline

Created: 2026-05-06
Last reused or audited: 2026-05-06
Authority basis: ULTIMATE_DESIGN sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.A

## Method

`git log --since="60 days ago" --grep="\[skip-invariant\]" --oneline`

## Window counts

| Window | Count | Notes |
|---|---|---|
| 60-day | 161 | All commits fall 2026-05-02 to 2026-05-06 |
| 30-day | 161 | Same — repo skip-invariant history is <10 days old |
| Oldest skip-invariant commit | 2026-05-02 | Confirmed via `git log --date=short` |

**Effective window: 5 calendar days (2026-05-02 through 2026-05-06)**
**Rate: 161 / 5 = 32.2 skip-invariant commits per day**

Note: The IMPLEMENTATION_PLAN briefing §1 references a 60-day baseline of
159 commits ≈ 2.6/day. That figure was measured on the date the plan was
authored against a longer branch history. As of 2026-05-06, git history with
`[skip-invariant]` only spans 5 days; 61% of those commits fall on
2026-05-02 (initial hook setup day). The plan baseline (2.6/day) is from the
operator's prior measurement and is the accepted target reference.

## Classification (46 commits manually reviewed, full 30d list)

| Category | Count | Criteria |
|---|---|---|
| Legitimate | 27 | Docs-only, pre-existing test failures explicitly noted, chore/data-version, hook/infra not touching src/ |
| Bypass | 19 | src/ feat/fix/refactor commits where `[skip-invariant]` circumvented topology_doctor gate |
| Unclassified (older, not shown in cmd output) | 115 | Falls outside manual review; rate assumed proportional |

Of the 46 classified commits: bypass rate = 19/46 = **41%**

## First 3 bypass examples

1. `1d9a367d` — fix(state,engine): T2I-A narrow ATTACH except (cycle_runner+db.py)
2. `4fc327fa` — fix(autochain): accept "complete" status string
3. `ee94539f` — feat(engine,harvester): T2G wire connect_or_degrade + enqueue_redeem_command

## Interpretation

The high bypass rate confirms the topology-doctor friction problem documented
in IMPLEMENTATION_PLAN §1: agents encounter legitimate write-path tasks,
hit the topology_doctor gate, and use `[skip-invariant]` to proceed rather
than spending time resolving the block. This is the primary driver for the
redesign.
