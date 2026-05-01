# M1 INV-29 amendment work record

Date: 2026-04-27
Branch: plan-pre5
Task: Close the M1 INV-29 governance gate with a narrow closed-law amendment
Changed files: see `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`
Summary: Incorporated the already-reviewed M1 command-side grammar expansion into `architecture/invariants.yaml` under amendment `R3-M1-INV-29-2026-04-27`, updated the operator decision register to close INV-29 for M1 grammar values only, and added tests/topology routing so future agents cannot treat the amendment as runtime semantics or live authorization.
Verification: Topology navigation PASS; focused tests 15 passed; M1 drift GREEN=15; map-maintenance PASS; planning-lock PASS; closeout PASS with nonblocking warnings only; pre-close critic Huygens PASS; pre-close verifier Laplace PASS.
Next: Run post-close third-party critic+verifier before unfreezing M2.

Post-close artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M1_inv29_post_close_2026-04-27.md`.

Post-close: critic Planck PASS; verifier Pasteur PASS. M2 may now freeze next, but M2 implementation requires its own boot/review and live/cutover gates remain closed.
