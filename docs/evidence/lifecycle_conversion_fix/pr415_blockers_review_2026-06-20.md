# PR415 blocker re-review resolution — B3 / B5 / B6 — 2026-06-20

Created: 2026-06-20
Authority basis: round-2 adversarial consult re-review of commit a91bfe438c + local verification
(ChatGPT advises; Claude Code verifies). Branch live/lifecycle-conversion-fix-20260620.

The re-review returned NO-GO on B3 (residual) and B5 (WAL durability); B6 GO. Disposition below.

## B6 — GO (no change)
`_check_armed_live_no_submit_receipts` fails closed (→ RED) on an unreadable receipt query only
under armed-live; not-armed-live short-circuits before the query; happy path stays GREEN. Antibody
covers all branches. Closed.

## B3 — residual CLOSED (fix applied this commit)
Round-1 fixed the named taker-quality consume point (`_build_event_bound_taker_quality_proof`). The
re-review found a residual: `_proof_uses_qkernel_spine` treated a *syntactically-valid cert alone* as
qkernel authority, so an UNSTAMPED (legacy-selected) proof carrying a stray valid cert could source
its `payoff_q_lcb` from that cert in sizing/materialization (`_qkernel_execution_economics` →
`_robust_marginal_utility_stake_and_price`) without the spine being the selector.

Note: `_CandidateProof` carries no `candidate_id` field, so the consult's exact "candidate_id match"
is not the applicable lever here (the cert is already bound to the proof via bin_id + route_id + side
in `_qkernel_execution_economics`). The applicable, correct close is to require the SELECTION STAMP:
`_proof_uses_qkernel_spine` now returns True only for `q_source == "qkernel_spine"` or
`selection_authority_applied == "qkernel_spine"` — the cert-alone branch is removed. Verified safe:
`qkernel_spine_bridge.py:1372` sets `selection_authority_applied="qkernel_spine"` on EVERY
spine-selected proof, so all legitimate qkernel sizing still flows; only the unstamped-with-stray-cert
case fails closed (correct — legacy proofs size on legacy q, never on an unbound qkernel cert).
RED-on-revert tests added; 103 money_path/qkernel-routing/recapture tests green (no sizing regression).

## B5 — INV-37 COMPLIANT (re-review's WAL concern verified OUT OF INV-37 SCOPE)
The re-review NO-GO'd B5 on the grounds that a single ATTACHed connection + one `commit()` is not
crash-durable across `zeus-world.db` and `zeus_trades.db` under WAL. Verified locally:
- **INV-37's actual definition (repo evidence docs):** "ATTACH+SAVEPOINT cross-DB discipline; all
  writes on ONE connection; never independent connections." It is about eliminating the application-level
  split-commit window — NOT crash/power-loss durable cross-file atomicity. (refs:
  docs/evidence/.../presence_resolver_review_2026-06-16.md, fill_to_position_path_2026-06-16.md,
  planning_2026-06-14/P1_S3-kcut.md, and .claude/CLAUDE.md K1 DB split.)
- **`_connect` forces `PRAGMA journal_mode=WAL` (db.py:217) for every connection.** So WAL is a
  repo-wide concurrency choice, and EVERY existing canonical INV-37 writer
  (`get_trade_connection_with_world_required`, the fill-bridge writer, etc.) shares the exact same
  WAL+ATTACH crash semantics. The WAL multi-file crash caveat is therefore a pre-existing, repo-wide
  property — NOT a B5 regression and NOT an INV-37 violation.
- **The B5 fix resolves the round-1 violation** (it removed the two-independent-connections /
  two-separate-commits path at 3 sites) and matches the repo's canonical single-conn ATTACH pattern,
  with the feasibility insert schema-qualified to `trades.execution_feasibility_evidence` (the
  shadow-table footgun, which IS a real correctness fix).

**Disposition:** B5 is merge-acceptable as INV-37 compliance. The "all-or-nothing" in the new comments
means application-transaction atomicity (single commit, no Python split), which is true; cross-file
crash-durability under WAL is the repo-wide limitation noted here, deliberately OUT OF SCOPE for this
hotfix (closing it would require co-locating the pair in one DB, or a non-WAL journal for this txn, or
a durable outbox/recovery protocol — a repo-wide design change affecting all INV-37 writers, to be
tracked separately, not gated on this restart-readiness hotfix). The consult itself noted its verdict
flips under exactly this INV-37 reading.

## Validation
- B3 residual: `test_unstamped_proof_with_stray_cert_is_not_qkernel_authority` (RED-on-revert proven by
  stashing the adapter), `test_spine_stamped_proof_is_qkernel_authority` (no-regression). 103 green
  across money_path canary + qkernel routing + submit recapture.
- B6: 6/6 antibody tests green. B5: 11 INV-37 antibody tests + 56 broader INV-37/price-channel green.
- Governance (planning-lock + map-maintenance) pass.

## Open follow-up (not merge-blocking, tracked)
Repo-wide WAL cross-DB crash-durability for INV-37 authority pairs (affects all INV-37 writers, not
just B5). Options: co-locate the authority pair in one physical DB; enforce/prove a rollback-journal
for the exact cross-DB txn; or a durable outbox + startup reconciliation that treats half-pairs as
non-authoritative.
