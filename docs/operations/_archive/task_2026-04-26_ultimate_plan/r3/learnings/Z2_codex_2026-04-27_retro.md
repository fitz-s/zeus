# Z2 Retro — V2 adapter and envelope

Date: 2026-04-27
Agent: codex
Phase: Z2

## What changed

- Added `src/venue/polymarket_v2_adapter.py` as the V2 SDK boundary.
- Added `src/contracts/venue_submission_envelope.py` as the provenance contract.
- Converted `src/data/polymarket_client.py` into a compatibility wrapper for
  live placement/cancel/query paths, with V2 adapter preflight before submit.
- Removed V1 `py-clob-client` from runtime requirements and pinned
  `py-clob-client-v2==1.0.0`.
- Reworked neg-risk antibodies so `neg_risk` is allowed only as V2 venue
  provenance, not strategy/settlement logic.
- Repaired package YAML and topology routing so the accumulated R3 Z0/Z1/Z2
  diff is closeout-verifiable.

## Critic findings that changed the implementation

1. Compatibility code is live code.
   The first Z2 implementation still let injected `_clob_client` test doubles
   call `create_order/post_order` directly. That preserved a V1-shaped live
   bypass. The final implementation removes that bypass and updates tests to
   mock the V2 adapter seam instead.

2. Preflight must be centralized, not assumed by caller discipline.
   Entry called `v2_preflight()`, but exits reached `place_limit_order()` via a
   different path. The compatibility wrapper now calls adapter `preflight()`
   before every submit and returns a rejected dict when the operator/Q1 gate is
   absent.

   The post-close critic found one more version of the same mistake: direct
   public `PolymarketV2Adapter.submit()` / `submit_limit_order()` calls could
   bypass Q1 when the caller skipped the wrapper. The final contract enforces
   preflight inside those adapter side-effect methods too, and tests assert the
   fake SDK records zero calls when Q1 evidence is absent.

3. ACK requires an order id and non-rejected venue response.
   Entry previously could append `SUBMIT_ACKED` with a missing order id. The
   final code rejects both missing order ids and `success=false` before ACK, and
   tests assert no `SUBMIT_ACKED` event is emitted.

4. Provenance hashes must be over final submit fields.
   A post-hoc SELL mutation made the envelope hash disagree with the actual
   side/size. The compatibility path now builds a final-field envelope directly.

5. Snapshot freshness needs time semantics.
   The adapter now rejects stale timestamped snapshots via
   `captured_at + freshness_window_seconds`; U1 will replace the temporary
   compatibility placeholder with certified executable snapshots.

6. YAML is code for this packet.
   The verifier caught 19 malformed slice-card YAML files. Closeout now parses
   all R3 package YAML before claiming the package is reusable by future agents.

## Rules added for future phases

- Treat compatibility shims as live-money surfaces; test them directly.
- Never let a mock-only convenience path bypass the new live boundary.
- Every submit-facing wrapper must perform or enforce the same fail-closed gate
  as the new adapter.
- Every public adapter side-effect method must enforce the gate itself; caller
  discipline is not a safety boundary.
- Full package closeout must verify both the current phase subset and the
  accumulated uncommitted package diff.

## Remaining non-goals / risks

- Q1-zeus-egress remains open, so real V2 preflight is expected to fail closed
  until the operator probe exists.
- Compatibility envelopes use `legacy:<token_id>` placeholders and are not
  U1-certified executable market snapshots.
- V2 cancel/redeem support remains conservative because the captured wheel
  evidence did not verify a direct cancel method or redeem semantics.

## Post-close result

Post-close third-party critic Confucius approved Z2 after verifying public
`PolymarketV2Adapter.submit()` and `submit_limit_order()` enforce Q1 preflight
before SDK contact. Verifier Wegener passed the closeout after receipt metadata
was expanded to cover both directory-level `git status` paths and expanded file
paths, with focused tests at `100 passed, 4 skipped` and topology closeout green.

Runtime lesson: package receipts must cover the exact diff shape used by the
reviewer, not just the expanded file listing used by the parent shell.
