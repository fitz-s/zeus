# GPT-5.6 Pro review request — single live semantics

Status: READY FOR OPERATOR SUBMISSION
Target: https://github.com/fitz-s/zeus/pull/445
Exact head: resolve the current head of PR #445 at submission time; do not review
an earlier cached SHA.
Base: `live`

## Review instruction

Review the complete PR diff from `live` to that exact head. Treat the PR as
a live-money architecture deletion, not as a naming cleanup. Report findings
first, ordered by severity, with exact file and line anchors. Do not approve from
test results alone.

The intended end state is one executable live mechanism. Offline replay,
historical evidence, read-only audit, health measurement, and backtest may exist
only when their types and write boundaries make them incapable of authorizing or
perturbing a live decision. A disabled, observe-only, veto-only, rehearsal,
promotion, canary, dry-run, or alternate persistence route inside live semantics
is forbidden. The two retired vocabulary families can be reconstructed as
`sha` + `dow` and `diag` + `nostic`; search case-insensitively for their prefixes,
suffixes, abbreviations, schemas, flags, environment variables, enum values,
comments, tests, deployment recipes, and semantic equivalents.

Answer these questions independently:

1. Over-deletion: did the PR remove any real safety or money-path requirement?
   In particular, trace signed-order identity before POST, submit-time truth
   refresh, freshness fail-closed behavior, source validity, settlement
   semantics, price bounds, risk behavior, command journaling, idempotency,
   reconciliation, Day0 invalidation, and held-position re-decision.
2. Incomplete extinction: can any active source, config, schema, persisted row,
   workflow, launch recipe, test seam, or operator instruction still select or
   infer a second live behavior? Look for equivalent semantics even when the
   retired words are absent.
3. Migration safety: is the cutover script target-bound, resumable, idempotent,
   transactional where needed, and fail-closed before deleting protected
   attribution? Can a partial run, retry, or concurrent writer corrupt or erase
   unresolved evidence?
4. Structural integrity: do deleted files leave imports, registry rows, schema
   ownership, workflow references, docs routes, or generated topology dangling?
   Are the remaining exclusions in the relapse checker narrow enough to prevent
   concept laundering through a renamed module?
5. Capital objective: did the PR accidentally reduce valid opportunity capture
   or duplicate uncertainty/risk haircuts? Inspect exact current family wealth
   composition, native holdings, inflight commitments, BUY/NO symmetry,
   uncertainty counting, price/fee/depth inputs, termination, and expected
   delta-log-wealth. Identify removable gate stacking or latency only when doing
   so preserves freshness, settlement, risk, identity, and venue constraints.

For every finding, classify it as one of:

- `OVER_DELETION`: required behavior was erased or weakened.
- `RESIDUAL_ALTERNATE`: forbidden alternate-live behavior remains.
- `CAPITAL_DRAG`: redundant constraint or duplicated haircut reduces expected
  capital growth without buying a distinct safety invariant.
- `PROOF_GAP`: the implementation may be correct but the PR cannot prove it.

For each finding provide severity, runtime path, concrete failure scenario,
smallest correct repair, and the test that would fail before the repair and pass
after it. Explicitly say which major surfaces you traced and which you did not.
An empty finding list with partial coverage is not a clean verdict.

Do not propose a new mode, readiness tier, feature flag, second authority,
parallel persistence path, or submit-disabled rehearsal as a remedy. Optimize
capital only after preserving the single-live invariant and all independent
live-money safety contracts.
