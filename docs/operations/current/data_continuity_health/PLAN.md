# Data continuity health — Plan

Date: 2026-07-28
Branch: `fix/data-continuity-health-20260728`
Status: implemented; awaiting live landing verification

## Background

Live health has narrowed to two failures with different causes:

- Hong Kong HIGH 2026-07-28 has a current HKO provisional running extreme
  through 23:50 local and a matching observation-conditioned replacement
  posterior, but the held monitor rejects all provisional probability after
  local midnight until HKO publishes the finalized Daily Extract.
- Shanghai LOW 2026-07-28 is reported as posterior-starved after 12 hours even
  though its local target day is already complete. The watchdog claims to
  cover live-tradeable forecast families, but its SQL uses the UTC calendar
  date and therefore keeps completed Asia/Shanghai families in forecast
  freshness scope.

The first is a continuity gap between provisional probability evidence and
final settlement truth. The second is a false scope expansion in the health
watchdog. Neither is repaired by lengthening a freshness threshold or
relabeling stale/final evidence.

## Scope

See sibling `scope.yaml`.

Money-path location:

`source truth -> continuous probability -> held-position redecision -> monitoring`

SCOPE:

- Post-local-day held-position monitoring only, when a current posterior is
  identity-bound to an authorized provisional settlement-channel observation.
  Entry authority remains forbidden.
- Posterior-starvation health checks only for a city's target date while that
  local forecast day has not completed.

DRAIN:

- HKO's independent five-minute final poll replaces provisional probability
  with exact final-daily authority when the provider publishes.
- The next health pass recomputes each city's local calendar scope from the
  current UTC instant.

RESET:

- A final daily observation takes the exact settlement-simplex path.
- A missing, mismatched, expired, or non-authorized provisional bundle still
  fails closed.
- Unknown city/timezone identities remain in starvation scope rather than
  being silently excluded.

## Deliverables

- Permit identity-bound provisional Day0 replacement probability to remain
  monitor authority after local midnight for reduce-only held redecision.
- Route that post-local provisional state through its persisted coherent
  replacement simplex; an empty remaining-day member window is not a valid
  post-midnight probability carrier.
- Preserve the hard boundary: provisional observation is never entry or final
  settlement authority.
- Make posterior-starvation use each configured city's local calendar instead
  of UTC date.
- Defer a subsecond-future observation row to the next event-trigger scan
  instead of failing the whole reactor cycle when SQLite's second-resolution
  prefilter admits it.
- Retry one observation/posterior clock visibility mismatch in the held
  monitor before declaring probability unavailable.
- Add behavioral antibodies for post-day provisional continuity, continued
  entry rejection, east-of-UTC completed-day exclusion, exact subsecond causal
  deferral, and observation-clock visibility recovery.

## Verification

- Focused current-global Day0, monitor, and starvation tests.
- Python compile and `git diff --check`.
- Source-rationale/test-topology changed-surface checks.
- Planning-lock check against this plan.
- After landing: exact loaded SHA, Hong Kong monitor freshness, posterior
  starvation count, composite health, and canonical final/provisional source
  rows.

## Non-goals and safety

- No source substitution, fabricated HKO final row, settlement write, schema
  change, DB cleanup, retention, or `VACUUM`.
- No entry-authority expansion.
- No freshness-threshold widening and no stale-as-fresh fallback.
- No change to probability mathematics; the already materialized,
  observation-conditioned replacement distribution remains the probability
  carrier until final truth supersedes it.

## Implementation evidence

- Focused current-global Day0, monitor, and posterior-starvation suite:
  424 passed; 27 pre-existing NumPy warnings.
- Narrow continuity/entry-boundary suite: 17 passed.
- Python compile and `git diff --check`: passed.
- Structural no-override gates: 0 findings.
- Source-rationale delta gate: 0 findings.
- Planning-evidence compatibility gate: 0 findings.
- Repo-wide source topology still reports unrelated pre-existing registry
  drift; changed-surface structural gates are clean and this packet does not
  repair unrelated topology inventory.
