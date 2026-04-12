# CURRENT_STATE

> Role: single live control entry surface for the repo.
> For operating rules, see root `AGENTS.md`.
> For present-tense runtime blockers, read `docs/known_gaps.md`.
> Do **not** infer current status from historical design files in `docs/archives/`.

## Read order for active work
1. `AGENTS.md`
2. `docs/operations/current_state.md`
3. Active work packet (below)
4. `docs/known_gaps.md` for current runtime gap context

## Current active packet
- **Phase**: Phase 1 Live-Only Reorientation (plan: `docs/operations/phase1live_2026-04-11_plan.md`, execution: `.omx/context/phase1live_2026-04-11_execution_plan.md`)
- **Packet**: Replay / operator-semantics continuation on `data-improve` — active small-package Ralph loop. Recent accepted groups: risk/operator visibility (`b573444` → `f5025bc`) and replay reporting/provenance (`14371ef`, `6299700`).
- **Team**: zeus-phase1-live (team-lead opus + cassandra opus adversary + atlas/prometheus/titan sonnet executors + hermes sonnet tester + scribe haiku writer)
- **Branch**: `data-improve`
- **Freeze doc (archived reference)**: `.omx/context/p1-safetycap_2026-04-11_freeze.md` (367 lines, A1–A4 history)
- **Mainline order**: ~~P1~~ → ~~P2~~ → P3 → P4 → P5 → P6 → P7 → P8 → P9a → P9b (with simplify checkpoints S1/S2/S3 every 3 packets)
- **Next**: Continue replay/calibration hardening from `.omx/context/zeus_deferred_audit_backlog_20260412T174038Z.md`. Do not reopen P2 as pending; the auto-pause hook and operator-visible pause source are already implemented and tested on `data-improve`.
- **Retrospective items** (to capture at Step 12 P1 report): (1) Cassandra NOTE 8 — permanent execution plan §3 Step 6 protocol change requiring `git stash → pytest → unstash` baseline diff on any failures; (2) Hermes silence pattern investigation for P2 onboarding; (3) Cassandra NOTE 10 aspirational pytest antibody for "mock signature divergence" bug category; (4) 4 Lead error catalog with per-catch lessons; (5) Scribe bindings: NOTE Q3 (`capped_by_safety_cap` log field is operator-visible only, not decision input) and NOTE 4 (forbid `paper_*` counterparts to `live_*` config keys in P12 runbook)

## Last accepted packet
- GOV-ROOT-AUTHORITY-GUIDE (archived)
- GOV-FAST-ARCHIVE-SWEEP (archived to `docs/archives/governance_doc_restructuring/`)
- GOV-TOP-LAW-EXPANSION (archived to `docs/archives/governance_doc_restructuring/`)

## Note
Historical control ledgers and completed work packets are archived under `docs/archives/`. They are NOT active control authority. See `docs/operations/AGENTS.md` for packet lifecycle rules.

Current-state rule: `current_state.md` + `known_gaps.md` describe what is true **now**. `docs/authority/zeus_current_architecture.md` and `zeus_current_delivery.md` describe the active law that current work must obey.
