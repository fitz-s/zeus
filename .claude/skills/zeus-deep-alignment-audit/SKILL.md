---
name: zeus-deep-alignment-audit
description: Read-only deep alignment audit for Zeus that finds the issues code review can't catch — data provenance holes, math drift, statistical errors, cross-module invariant violations, silent failures, assumption drift. Invoke ONLY by explicit operator request ("run deep alignment audit", "/skill zeus-deep-alignment-audit", "do a comprehensive correctness audit on zeus"). Do NOT auto-trigger on routine review, debug, fix, plan, PR-comment, or any compound trigger phrase appearing in normal Zeus work — this skill is opt-in only. Self-evolving via sibling LEARNINGS.md: every run reads past learnings to prioritize, writes new heuristics / promotes new categories / demotes dead ones after each run. Not a fixed template.
model: inherit
---

# Zeus Deep Alignment Audit (read-only, self-evolving)

## What this is

A correctness audit that finds the failure modes code review cannot — data provenance breaks, math/statistics drift, cross-module semantic mismatches, silent failures, assumption decay. Full-repo scope with Tier-0 runtime-risk priority.

**Hard read-only**: no source edits, no git mutations except creating the report directory, no live commands, no PR opens.

## Why a skill (not a fixed template)

Zeus law evolves. The categories worth checking last quarter are not the categories worth checking next quarter. This skill seeds 8 starter categories but the brain is `LEARNINGS.md` next to this file — every run reads it, every run writes to it. Seed categories may be demoted; new categories may be promoted to first-class. Every 3rd run does a meta-audit and prunes/restructures.

A frozen 8-category template is anti-Fitz-methodology: it would patch symptoms forever and never become an immune system. This file is intentionally short — the protocol is small, the accumulated wisdom lives next door.

## Boot (mandatory, in order — do not begin audit work before completion)

1. Read this `SKILL.md` (seed protocol).
2. Read sibling `LEARNINGS.md` — evolved heuristics from prior runs. **Treat as higher-weight priors than seed categories below.** When seeds and learnings disagree, follow learnings.
3. Read sibling `AUDIT_HISTORY.md` — what past audits found and missed; note repeat-offender categories.
4. Read repo root: `AGENTS.md`, `~/.claude/CLAUDE.md` (Fitz methodology), `architecture/invariants.yaml`, `architecture/db_table_ownership.yaml`, `architecture/fatal_misreads*`, `docs/current_*_validity*`.
5. Read contracts: `src/contracts/settlement_semantics.py`, `execution_price.py`, `venue_submission_envelope.py`, `fx_classification.py`.
6. Run `python3 scripts/topology_doctor.py --task-boot-profiles` and `--navigation --task "deep alignment audit"`.
7. `git log --oneline --since="60 days ago" -- architecture/ src/contracts/` — locate assumption-drift hot zones.
8. `git ls-files | wc -l`, `git ls-files 'src/**/*.py' | wc -l`, `git ls-files 'tests/**/*.py' | wc -l` — quantify full scope so the Coverage Map can be honest.

## Worker dispatch (opus orchestrator + parallel haiku scouts)

Dispatch one haiku worker per **active** category (seed A–H + any promoted by `LEARNINGS.md`, minus any demoted). All workers in a single message, in parallel. Each worker:
- read-only, full-repo scope
- writes `/tmp/audit_cat_{ID}.md`
- returns evidence-only (file:line × ≥3 per finding); orchestrator does all aggregation
- brief ≤40 lines per dispatch-brief-concise feedback

Opus orchestrator never greps. Aggregation and root-cause merge only. Every opus turn ≈ 100× haiku cost.

## Seed categories (starting heuristics — defer to LEARNINGS.md when it disagrees)

These are the v0 categories. `LEARNINGS.md` may have added, removed, refined, or restructured them. Read learnings first; treat this list as the fallback when no learnings yet exist.

- **A. Data provenance holes** — `source`/`authority`/`data_version` missing fields, inherited timezones, fallback masquerading, multi-source semantic drift
- **B. Math drift** — probability normalization across the chain, α-fusion weight conservation, Platt clip bias, MC sampling independence, bootstrap CI nominal-vs-actual coverage, Kelly/utility/bankroll alignment, P_cal vs P_posterior path mixing
- **C. Statistical pitfalls** — selection bias in calibration training set, HIGH/LOW family independence leak, replay look-ahead leakage, Day0 future-observation contamination, stationarity-assumption decay
- **D. Time/calendar** — DST spring-forward/fall-back edges, local-calendar-day cross-family alignment, settlement-day vs valid-time vs publish-time vs ingest-time mixing
- **E. Settlement edges** — shoulder bin (`75°F+`) treated as bounded, `point`/`finite_range`/`open_shoulder` cardinality consistency, WU rounding path (real→sensor→METAR→display) consistency across harvester/evaluator
- **F. Cross-module invariants** — K1 DB split (INV-37 cross-DB transactions), DB-before-JSON direction (INV-17), authority chain shortcuts, `market_id` cross-DB consistency, `with conn:` nested inside SAVEPOINT
- **G. Silent failures** — `try/except: pass` and exception swallowing, scheduler (launchd/cron) failures without alarm, `assert`-only invariants bypassed by `python -O`
- **H. Assumption drift** — `invariants.yaml`/`current_*_validity*` last-update gap vs schema change dates, comment/code disagreement, stale `xfail`/`skip` with rotted reasons, lingering TODO/FIXME/HACK from past 60 days

## Method constraints (judging criteria for the final report)

- **K ≤ 5 root structural gaps**. N raw findings unmerged = report unacceptable (Fitz: K << N). Findings must converge into ≤5 root design failures.
- **Three independent evidence per SEV-1/2**: code line + test/test-absence + data/log artifact. Single observation never produces SEV-1.
- **Antibody per SEV-1/2**: design a test/type/structural change that makes the entire category permanently impossible. **Describe only, do not implement.**
- **Tier routing**: haiku for grep/enumerate, opus for semantic aggregation only.
- **Citation freshness**: every file:line in the report re-greped within ≤10 minutes before writing. Zeus line numbers rot fast.
- **Code > docs**: docs disagreeing with code is itself a SEV finding, never silently trusted.
- **Three confidence tiers, strict**: HIGH-CONFIDENCE / MEDIUM-SUSPICION / NEEDS-HUMAN-INPUT, no blurring.
- **Honest coverage**: empty findings on a category that wasn't scanned ≠ clean.

## Deliverable

Single markdown report at `docs/operations/task_{YYYY-MM-DD}_deep_alignment_audit/REPORT.md` (suffix `_v2`, `_v3` … if path exists same day).

Required sections:
1. **Executive Summary** — K (≤5) root gaps, one line + risk-magnitude estimate each
2. **Findings** — sorted by SEV × category, each with ≥3 evidence cites + read-only repro command
3. **Unverified Suspicions** — seeds for next run
4. **Antibody Recommendations** — design-only, one per SEV-1/2
5. **Audit Coverage Map** — what each worker scanned, what was skipped, ratio of unaudited files vs Boot step 8 totals
6. **PR-ification suggestions** — which SEV-1/2 cluster into a single fix-PR vs need separate PRs (Codex adversarial happens at PR open, not here)

## Closeout (mandatory — this is what makes the skill self-evolving)

Before declaring done, update sibling files. Skipping this defeats the entire skill design.

### Update `LEARNINGS.md`:
- **Per-category yield update**: which active categories found SEV-1/2 this run? Which found nothing for 3+ consecutive runs? Mark Yield as HIGH / MEDIUM / LOW / DEAD. Dead categories get demoted with a dated note; don't delete the row.
- **New categories proposed**: any finding that didn't fit any active category gets a proposed new category with name + 3-bullet definition. Marked `PROPOSED — needs 1 more run to validate`. Promote to active after 2nd appearance.
- **High-signal probes captured verbatim**: when a worker's probe was low-false-positive and found real issues, record the exact probe phrasing so future runs reuse it.
- **Anti-heuristics recorded**: probes that were noisy / wasted budget. Recorded so future runs skip.
- **Antibody status updates**: if a past-run antibody recommendation actually shipped (check git log or operator confirmation), mark it DEPLOYED. Categories with all antibodies deployed can be archived.

### Append to `AUDIT_HISTORY.md`:
- Row: date, commit SHA, K root gaps, SEV-1/2/3 counts per category, coverage ratio, link to REPORT.md
- Retrospective paragraph: what was surprising this run? What pattern recurred from prior runs? What did the audit miss that a later incident revealed (back-filled in subsequent runs)?

### Every 3rd audit — meta-audit step:
- Re-read entire `LEARNINGS.md`. If active category list > 12, prune lowest-yield. If pattern reveals a better taxonomy, restructure.
- Update SKILL.md's seed-category list (this file's section above) to match current top-N in LEARNINGS. Seeds should reflect ~6 months of audit reality, not the v0 list indefinitely. Record the SKILL.md edit in AUDIT_HISTORY with rationale.

## Forbidden shortcuts

- Skipping LEARNINGS.md read because "I already know the categories" — defeats the self-evolving design and reverts the skill to a frozen template.
- Implementing antibodies (this skill is strictly read-only).
- Opening a PR from this skill (PR + Codex adversarial is a separate downstream activity).
- Declaring done without updating both `LEARNINGS.md` and `AUDIT_HISTORY.md`.
- Treating seed A–H as authoritative when LEARNINGS contradicts.
- Co-tenant `git add -A`: never. Stage specific files only (memory `feedback_no_git_add_all_with_cotenant`).

## When to stop and surface to operator

- Audit reveals a SEV-1 affecting a position currently on-chain → STOP, surface immediately via push notification, do not finish report silently.
- Boot step 4 reveals `invariants.yaml` ≥ 90 days stale → STOP, audit premise is broken, ask operator if invariants should be refreshed first.
- Coverage Map cannot honestly claim ≥ 70% of Tier-0 surfaces scanned (worker died, timeout, hard stop) → mark report DRAFT in title, do not present as final.
- 3 consecutive runs find zero SEV-1/2 across all categories → STOP, surface to operator: either Zeus is genuinely clean (celebrate) or the audit itself has decayed and needs methodology refresh.

That's the protocol. SKILL.md is intentionally small. The brain is `LEARNINGS.md`.
