# PR332 Remaining Live Safety Fixes

Created: 2026-05-27

## Scope

This plan covers the remaining deploy-safety fixes that can land in PR332 without
turning it into full-live scaleout:

- fail closed when EDLI stage readiness cannot inspect pending reconcile or live
  cap reservations;
- make tiny-live cap reservation atomic across concurrent event attempts;
- keep durable venue-submit outbox, Day0 hard facts, and full order lifecycle as
  explicitly blocked follow-up scope unless implemented by separate packages.

## Non-goals

- No venue submit enablement.
- No live canary promotion.
- No `edli_live` scaleout.

## Verification

- Focused stage readiness and live-cap tests.
- Schema/table ownership coherence if a cap slot table is added.
- Money-path EDLI invariant slice.
