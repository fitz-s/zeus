# Transcendent docs condensation — 2026-08-20

Goal: the three public evidence surfaces state current law only, verdict-first,
workflow-not-examples. Operator directive 2026-08-20: 浓缩 update; no worked
examples; condensed workflow that shows the thinking; do not degrade what is
already strong. Third-party audit (Featured-surface, 2026-08-19) supplies the
per-page fix specs; where it conflicts with executable law, code wins.

Chain position: documentation only. No runtime, schema, or money-path change.
Rollback: revert the single PR.

## Ground truth verified (file:line, 2026-08-20)

- Walk-forward EB de-bias LIVE: `src/forecast/bayes_precision_fusion.py:51-78`
  (`eb_bias`), single-application governed by `src/forecast/debias_authority.py`.
  Audit's "remove de-bias" over-corrected; keep it, drop only stale phrasing.
- Predictive spread is current-evidence (same-cycle causal ENS within-spread +
  provider between-spread + ENS-center displacement), fails closed on missing
  shape: `docs/authority/replacement_final_form_2026_06_09.md` §1d, 2026-08-19
  correction. README step "Spread" (walk-forward residual + floor) is STALE.
- Lapse-rate localization: absent from `src/forecast/` — STALE in README.
  Representativeness variance lives at `bayes_precision_fusion.py:242,338,364`.
- Action probability = posterior mean `q_json`; `q_lcb/q_ucb` are confidence
  evidence (Clopper-Pearson finite-member + Cantelli moment bounds), not the
  action objective: authority §1f, §4.
- Sizing: outcome-atom payoff, "robust expected Δlog-wealth over the joint
  outcome ATOMS" (`src/solve/solver.py:14`), routes+stakes against existing
  exposure; fractional-Kelly scalar formula in README is STALE.
- Chain reconciliation is per reconcile cycle (`src/state/chain_reconciliation.py`,
  cycle_runtime), not hourly.
- Reliability report pools every eligible frozen decision (incl. STALE_DECISION);
  generator's "only skill outcomes feed calibration" sentence contradicts its own
  decomposition — fix at the generator (audit B4).
- Capital-scale→standard-error sentence is statistically indefensible (audit B5).

## Changes

1. `README.md` — fix the four stale mechanisms above; delete the Worked-example
   section (operator: no examples); add one evidence-navigation line; recast
   "skill outcomes only" claims into frozen-certificate / attribution-explains /
   walk-forward law; "hourly sweep" → every cycle; keep the strong bones
   (opening, Three things 1-2, honest limits, reading table).
2. `scripts/gen_diagram.py` — refactor to `render(theme) -> str` + atomic
   `main()` writing both `docs/architecture-{light,dark}.svg`; replace stale
   copy (emp. Bayes / fractional Kelly / skill-outcomes-only gate / hourly
   reconcile); `__main__` guard; regenerate both SVGs.
3. `tests/scripts/test_gen_diagram.py` — NEW: committed SVGs byte-match
   `render()`; stale phrases absent; required phrases present; both themes.
4. `scripts/generate_calibration_report.py` — title; "Read this first" verdict
   branches (neg/pos/zero/unavailable) + scope block from the same in-memory
   decomp; provenance into `<details>` with one visible measurement-unit line;
   B4 attribution paragraph; B5 return-scope bullet; Data-through = max
   settlement timestamp; atomic temp-file writes (SVG first, report last).
5. `tests/scripts/test_generate_calibration_report.py` — extend: verdict-branch
   sign fixtures, Read-this-first-before-provenance ordering, forbidden phrases,
   counts consistency, missing-q excluded.
6. `docs/reference/calibration_report.md` + `calibration_reliability.svg` —
   regenerated read-only against live DBs.
7. `AI_ASSISTANCE.md` — authority/boundaries-first restructure; 22% history into
   a closing `<details>`; shared-ref incident de-exploited (class/impact/status,
   no command, no SHA, no workstation path); "not closed" honesty retained.
8. GitHub repo description: drop "live 24/7" → audit A1 text (at release).

## Tests / acceptance

- `python3 scripts/gen_diagram.py` idempotent; committed SVGs match render().
- `python3 -m pytest tests/scripts/test_gen_diagram.py tests/scripts/test_generate_calibration_report.py -q` green.
- `rg` finds no stale phrase in README/SVGs: "fractional Kelly", "skill outcomes
  only"/"only the skill outcomes", "hourly", "lapse rate", "lucky win teaches".
- Third-party consult gate on the full diff before PR.

## Outcome (2026-08-21)

Three gate rounds: FAIL 0.82 -> FAIL 0.93 -> **PASS 0.98**
(REQ-20260820-191822-1567bb thread). Two consult findings were REVERSED by
code verification before applying: walk-forward EB de-bias is live law
(kept), and the promoted selection-calibrator artifact is 96/96 v1 cells so
the served admission bound IS the one-sided Wilson lower bound (round-1 EB
wording reverted). GitHub repo description replaced per audit A1 (no
"live 24/7"). All 75 targeted tests green; report regenerated (902 settled,
587 scoreable, BSS -0.155, data through 2026-08-20).

Registered follow-ups (not claimed in the published pages):
- calibration report: per-decision-law-version cut of the scoreable corpus
  (needs a law-version identifier on certificates or a dated mapping).
- calibration report: market-family × date clustered intervals or block
  bootstrap alongside the position-level Wilson bars.

## Next action / rollback

Branch `claude/transcendent-docs` (5 commits on 9acb47042) is release-ready.
BLOCKED ON OPERATOR: pushing it publishes the 30 unpushed local-live commits
in its history; alternatives are pushing live first, or rebasing the docs
commits onto origin/live (074f887a16 — old README, conflicts resolved by
taking the new pages wholesale). Rollback: drop the branch; main tree
untouched.
