# Z0 retrospective — codex — 2026-04-27

## Objective function applied

Maximize R3 upgrade progress while preserving live-money safety, evidence
quality, reversibility, and future-agent learning. For Z0, that meant correcting
the source-of-truth packet without touching live execution code.

## What changed

- Reframed the old CLOB V2 packet from "generic low-risk SDK swap" into
  `V2_ACTIVE_P0` evidence bounded by R3 phase cards.
- Rewrote `v2_system_impact_report.md` around eight falsified-premise
  disclaimers rather than preserving misleading legacy assertions.
- Added a packet-local live-money contract instead of creating
  `docs/architecture/`, because topology showed that path would create an
  unclassified parallel authority surface.
- Added Z0 plan-lock tests and registered them in `architecture/test_topology.yaml`.

## Rules that mattered

- Topology doctor outranks intuition: the Z0 card requested a path that was
  unsafe in this workspace, so the implementation adapted rather than forcing
  the card literally.
- Pre-close critic+verifier are mandatory before marking a phase complete.
- Dirty derived artifacts are not cleanup targets unless they are in the
  phase scope; `.code-review-graph/graph.db` was explicitly excluded and left
  untouched.

## Rules to carry forward

- Treat old packet prose as evidence, not execution authority, once R3 has a
  phase card for the same area.
- For every future phase, add executable tests that lock the exact regression
  the phase is meant to prevent before doing broader refactors.
- If a phase card names an unclassified authority path, stop and route the
  artifact to an already-governed packet-local path, then record the protocol
  evolution.

## Verification evidence

- Changed-file topology excluding `.code-review-graph/graph.db`:
  navigation OK, planning-lock OK, map-maintenance OK.
- Exact Z0 pytest nodes: `4 passed, 1 skipped`.
- `r3_drift_check.py --phase Z0`: `GREEN=20 YELLOW=0 RED=0`.
- `git diff --check`: clean.
- Pre-close critic: APPROVE.
- Pre-close verifier: APPROVE.

## Open risk

Z1 must not infer runtime heartbeat cadence, transitional order statuses, or
pUSD FX accounting from the corrected Z0 prose. Those remain evidence-gated by
Q-HB, source-cited status tests, and Q-FX-1 respectively.
