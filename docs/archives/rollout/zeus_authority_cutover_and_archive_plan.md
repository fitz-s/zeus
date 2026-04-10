File: docs/rollout/zeus_authority_cutover_and_archive_plan.md
Disposition: NEW
Authority basis: docs/governance/zeus_autonomous_delivery_constitution.md; docs/governance/zeus_runtime_delta_ledger.md; current repo authority drift; Session 2 rollout package.
Supersedes / harmonizes: scattered archive/cutover language in live plans and progress docs.
Why this file exists now: authority installation without an archive/cutover plan would create new shadow surfaces immediately.
Current-phase or long-lived: Current-phase only.

# Zeus Authority Cutover and Archive Plan

## 0. Purpose
This file controls the transition from mixed authority to single canonical repo-local authority.

## 1. Cutover principles
- install new authority before deleting old claims
- patch drift before archive when drift is still operationally dangerous
- preserve rationale, demote authority
- no silent cutover
- no present-tense claim beyond actual runtime convergence

## 2. Immediate actions

### Completed now
1. root/scoped `AGENTS.md` installed
2. `.claude/CLAUDE.md` patched to compatibility shim
3. delivery constitution and decision register published
4. boundary note and package map published

### Still pending in current phase
5. patch or stage demotion banners for:
   - `docs/specs/zeus_spec.md`
   - `docs/architecture/zeus_blueprint_v2.md`
   - `docs/architecture/zeus_design_philosophy.md`
   - `docs/plans/zeus_live_plan.md`
6. keep runtime delta ledger current as remaining current-phase packets close

## 3. Demotion wording rule
Every demoted doc should state:
- this file is historical rationale, not principal authority
- current authority order is in `architecture/self_check/authority_index.md`
- current delivery law is in `docs/governance/zeus_autonomous_delivery_constitution.md`

## 4. Transitional truths that must stay explicit
Keep explicit until replaced:
- `position_events` is real
- open-position truth is still mixed
- `status_summary` remains derived
- `control_plane` remains ingress-only
- `decision_log` remains legacy fallback only
- `strategy_tracker` remains derived / demoted only

## 5. Patch-now items
- remove default-bucket strategy fallback (`P-STATE-01`)
- remove implicit local-date fallback in observation context (`P-STATE-01`)

## 6. Do not cut over yet
Do not declare full runtime convergence until:
- `position_current` exists
- replay/parity is meaningful
- read paths are updated
- operator rollback path is rehearsed
- human gate approves timing

## 7. Archive-later items
Archive later, not now:
- stale historical queues
- old “highest authority” claims once banners are installed and stable
- redundant operator maps if they remain low-value after AGENTS adoption
- any advisory-only boundary or rollout wording that becomes obsolete after canonical cutover

## 8. Rollback doctrine
If cutover-doc package causes confusion:
- revert new authority docs as a coherent set only if necessary
- otherwise preserve new authority and patch the confusing surface
- do not restore old “highest authority” claims as a convenience fix
