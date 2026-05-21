# Authority Chain

## Resolving authority for a Phase 3+ planning question

Apply IN ORDER. First doc that contains a verbatim answer wins. Do not skip ahead.

| Order | Doc | Scope | Authority kind | Verifier check |
|---|---|---|---|---|
| 1 | `docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md` §M (line 1097-1106) | Phase ENUM for what `Phases 2-7` cover (Day0Nowcast / MarketAnalysisVNext / Shoulder / candidate stubs / EvidenceLadder / FDR family-ID spread_bucket / Settlement social→type-gate / Math §15.4 correlation matrix) | OPERATOR-DELIVERED LOCKED | `git show origin/main:docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md \| sed -n '1097,1106p'` |
| 2 | `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/AUTHORITY_GPT_ROUND_1_DOSSIER.md` | Substantive INTENT for what each §M slot means (object models, field lists, payoff geometry, Kelly haircuts, evidence ladder tiers). 95KB operator-pasted GPT Pro Round 1 analysis. | SESSION-LOCAL DOSSIER | `wc -l` should be ~1500 lines; §0 starts with "Executive Tribunal Verdict"; §7 covers Shoulder; §8 covers MarketAnalysisVNext; §9 covers Evidence Ladder. |
| 3 | `architecture/*.yaml` + `architecture/*.md` | Cross-cutting invariants (INV-37 cross-DB ATTACH, settlement era, table ownership, source rationale, antibody specs, lifecycle grammar). | LIVING REPO TRUTH | `ls architecture/` shows ~60 files; key: `settlement_dual_source_truth_2026_05_07.yaml`, `db_table_ownership.yaml`, `invariants.yaml`, `antibody_specs.yaml`, `source_rationale.yaml`. |
| 4 | `docs/reference/zeus_math_spec.md` | Mathematical authority (calibration eps §14.9, FDR §14.6 n_eff, bin topology §14.10, correlation matrix shrinkage §15.4). | MATH AUTHORITY | Section anchors `§14.6`, `§14.9`, `§14.10`, `§15.4` exist; cross-reference v4 §0 defects table. |
| 5 | Current code on `origin/main` via `git show origin/main:<path>` | Implementation reality. Always wins over docs when they disagree. | RUNTIME GROUND TRUTH | Always fetch fresh; line numbers rot fast. |
| 6 | Operator-cited external sources (Polymarket docs / ECMWF / NOAA / WSJ) | Microstructure + meteorology reality. Cited verbatim with URL in dossier §1.1. | EXTERNAL GROUND TRUTH | URLs in dossier footnotes [1]-[12]. |

## NOT authority (do not cite these as plan basis)

| Doc | Reason | Correct use |
|---|---|---|
| `docs/artifacts/Zeus_*_review_*.md` (May 2/3, Apr 25/26) | Multi-week-old review-class artifacts. Operator did not cite them in session `4beb2fa4`. | Background reading only. May inform exploration but never cite as authority without operator confirmation. |
| `docs/operations/task_2026-05-19_strategy_vnext_phase1/MAINLINE_AUTOPILOT_PLAN.md` | Phase 1 autopilot brief. §10 "Phase 2 scope preview" is 1-line per item, not a per-phase plan. | Use for Phase 1 closure context only. Don't extrapolate Phase 3+ from it. |
| `~/.claude/projects/.../*.jsonl` | Session transcripts. Volatile, lossy through compaction. | Drift audits only (e.g. find original operator quotes). |
| Earlier-session memory files | Past lessons, not current authority. | Cite for procedural discipline (e.g. "use silent self-poll"), not for scope. |

## Supersession rule

If two docs in the authority chain disagree:
- Lower-numbered (= higher priority) wins.
- Exception: row 5 (`origin/main` code) ALWAYS wins over docs when they disagree on implementation state. The doc is then the staler artifact; update the doc to match code or annotate the divergence.

## Provenance discipline (mandatory per file produced in mainline work)

Every new `.py` script + test file MUST carry header:

```python
# Created: YYYY-MM-DD
# Last reused or audited: YYYY-MM-DD
# Authority basis: <auth chain row N + doc + section anchor>
```

Provenance audit verdict goes into the commit message: `CURRENT_REUSABLE` / `STALE_REWRITE` / `DEAD_DELETE` / `QUARANTINED`.

## Authority for "what is on `origin/main` right now"

This is volatile; do not memorize. Always fetch fresh:

```bash
git tag --list 'phase*'                # what's tagged
git log --oneline origin/main -30      # what's merged
git show origin/main:<path>            # what file says
```

The current snapshot lives in `02_MAIN_STATE_INVENTORY.md` — that doc decays the moment a new PR merges; treat it as a checkpoint, not a contract. Verify against `git` before locking a plan.
