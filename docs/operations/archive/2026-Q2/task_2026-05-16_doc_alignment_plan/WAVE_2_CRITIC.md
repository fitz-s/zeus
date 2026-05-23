# WAVE 2 Critic — Archive Migration (3 commits)

**Scope**: commits `8070e15c8f` + `12cdbf2d49` + `44b12a372b`
**Branch**: `feat/doc-alignment-2026-05-16`
**Authority**: `PLAN.md §WAVE 2` + `ARCHIVAL_RULES.md`
**Critic**: sonnet, fresh context, 2026-05-16

---

## VERDICT: ACCEPT-WITH-RESERVATIONS

**Overall Assessment**: The 28-entry migration is structurally correct. All stubs point to existing archive targets, INDEX counts are consistent, git history is preserved via R100 rename records, and the deprecated forwarding doc is properly headed. Two findings survive self-audit: a pre-deletion check that was hardcoded rather than computed (MINOR — outcome correct but script is a latent lie), and three active architecture files that still cite migrated-content old paths but were not in the worker's declared scope of 5 ref fixes (MINOR — all are historical prose citations, not runtime-loaded paths).

---

## Pre-commitment Predictions vs Actuals

| # | Prediction | Outcome |
|---|---|---|
| 1 | Stub-link integrity: some stubs point to missing targets | MISS — all 28/28 OK |
| 2 | Broken back-ref completeness: active docs still cite old paths | PARTIAL HIT — 3 architecture files cite old paths for migrated entries, but these are historical prose, not operational refs |
| 3 | INDEX count integrity: mismatch | MISS — 29 files = 28 entries + INDEX.md; 30 pipe rows = header + separator + 28 data rows. All consistent |
| 4 | Bare-file vs dir handling inconsistency | MISS — `entry_type: file` vs `entry_type: directory` correct; restore_commands valid |
| 5 | Prior-deletion check hardcoded vs computed | HIT — confirmed hardcoded; plan §2.3 required git-log check |
| 6 | Git history not preserved | MISS — R100 rename records in commit 12cdbf2d49; file-level history follows |
| 7 | AGENTS.md ref correctness issues | MISS — all 3 updated refs use correct `<YYYY>-Q<N>` placeholder syntax |
| 8 | archive_registry.md deprecation incomplete | MISS — header, INDEX pointer, and ARCHIVAL_RULES pointer all present |
| 9 | AGENTS.md:202 annotation unclear | MISS — annotation reads clearly: "Batch 3 local-only cold storage" |
| 10 | Script left as permanent artifact without re-run guard | HIT — docstring says "One-off" but no `FileNotFoundError` guard; safe at current state since source dir is now gone |

---

## Probe Disposition Table

| Probe | Result | Evidence |
|---|---|---|
| P1: Stub-link integrity (5 sampled, all 28 checked) | PASS | All 28 `archived_to:` targets verified to exist on disk |
| P2: Broken back-ref completeness | PARTIAL PASS | 5 claimed fixes verified. 3 additional refs in architecture files to migrated content NOT fixed — but these are historical prose in YAML `rationale:` / `Snapshot:` / `supersedes:` fields, not operational pointers. See MINOR finding #1 |
| P3: INDEX count integrity | PASS | 29 filesystem entries = 28 archived + INDEX.md; 30 pipe rows = 1 header + 1 separator + 28 data rows |
| P4: Bare-file vs dir handling | PASS | `task_2026-04-13_topology_compiler_program.md.archived` has `entry_type: file`, correct `archived_to` (preserves `.md` extension), valid restore_command. Dir stubs have `entry_type: directory` |
| P5: Pre-deletion check | MINOR FAIL | `scripts/archive_migration_2026-05-16.py` hardcodes `"0 prior-deleted exclusions (all 28 present)"` as static string. Plan §2.3 required computing via `git log --diff-filter=D`. Outcome was correct (PROPOSALS was never in `docs/archives/packets/`; it was in `docs/operations/archive/`). The hardcoded check is a latent lie in the script |
| P6: Git history preservation | PASS | `git log --diff-filter=R --name-status` on commit `12cdbf2d49` shows R100 rename records for all file-level moves |
| P7: AGENTS.md ref correctness | PASS | Lines 52-53, 71-73, 230-234 of `docs/operations/AGENTS.md` use `docs/operations/archive/<YYYY>-Q<N>/` syntax correctly |
| P8: archive_registry.md deprecation | PASS | Lines 3-7: DEPRECATED header + INDEX redirect + ARCHIVAL_RULES pointer. Historical body retained correctly |
| P9: AGENTS.md:202 broken-ref annotation | PASS | `AGENTS.md:202` annotation reads "(Batch 3 local-only cold storage.)" — clear, accurate, unfixable since content is gitignored-local-only |
| P10: Script permanence | MINOR FLAG | `scripts/archive_migration_2026-05-16.py` has no guard for SOURCE_DIR missing; if re-run now, raises `FileNotFoundError`. Docstring says "One-off" but no runtime enforcement |

---

## Critical Findings

None.

---

## Major Findings

None.

---

## Minor Findings

1. **Pre-deletion check hardcoded in migration script** (`scripts/archive_migration_2026-05-16.py:68`).
   The plan §2.3 required: "Pre-check each bare-file entry for prior deletion... exclude from migration; capture in migration log as 'already-deleted, no migration needed'." The script instead writes `"## Pre-deletion check: 0 prior-deleted exclusions (all 28 present)"` as a static string before enumerating the directory. In this run the outcome was correct (PROPOSALS was never in `docs/archives/packets/`). But the script is a historical artifact that claims to have performed a check it didn't run.
   Fix (if cleanup is desired, <10 LOC): replace static string with `lines.append(f"## Pre-deletion check: {len(entries)} entries found; all present on disk (git log --diff-filter=D not applicable — source dir was gitignored after 98fe1c1fc4)")` to accurately describe the limitation rather than claiming a check was run.

2. **Three architecture files still cite old paths for migrated content**.
   `architecture/preflight_overrides_2026-04-28.yaml:194` references `docs/archives/packets/task_2026-05-02_hk_paris_release/work_log.md` (migrated to `docs/operations/archive/2026-Q2/`). `architecture/paris_station_resolution_2026-05-01.yaml:19` references `docs/archives/packets/task_2026-05-01_paris_station_resolution/` (also migrated). Worker declared scope was 5 refs in `docs/operations/AGENTS.md`, `current_source_validity.md`, `current_state.md` — these architecture files were outside that scope. Both are YAML `rationale:` / `Snapshot:` prose fields, not runtime-loaded paths. Functionally harmless. Recommend either annotating them in a follow-up or explicitly deferring to WAVE 7.
   Fix: 2 one-line path updates. Or add a `# NOTE: migrated to docs/operations/archive/2026-Q2/` comment beside each.

3. **Migration script has no re-run guard** (`scripts/archive_migration_2026-05-16.py`).
   After successful execution, `docs/archives/packets/` no longer exists. If the script is re-run (e.g., by a future agent scanning `scripts/`), `SOURCE_DIR.iterdir()` will raise `FileNotFoundError`. The `One-off` docstring is the only warning.
   Fix (<5 LOC): add `if not SOURCE_DIR.exists(): sys.exit("SOURCE_DIR does not exist; migration already complete or not applicable")` at top of `main()`.

4. **`last_modified_before_archive` field set to migration date (2026-05-16) for all stubs**.
   ARCHIVAL_RULES stub schema (`ARCHIVAL_RULES.md:97`) shows `last_modified_before_archive: <ISO date>` intended to capture the packet's actual last-modified date before archive. All 28 stubs show today's date. The migration script uses `TODAY` for this field rather than inspecting git history per entry.
   Impact: minor fidelity loss; the field is informational only and not checked by any runtime. Future restore decisions relying on this date will see incorrect data.
   Fix: compute per-entry via `git log -1 --format="%as" docs/archives/packets/<name>` in the script.

---

## What's Missing

- **Evidence trail TSV** (`ARCHIVAL_RULES.md:103-104` step 5): "Add a row to `02_daily_maintenance_agent/evidence_trail/<date>/archived.tsv`." No TSV was written. This step was implicitly replaced by `migration_log.txt` which the plan adopted as the audit trail. The plan §2 does not explicitly call out this ARCHIVAL_RULES step as superseded. If the maintenance_worker's evidence ingestion depends on this TSV schema, future dry-runs may not see the 2026-05-16 archive as evidence of executed maintenance. Recommend: explicit note in PLAN.md §WAVE 2 gate that ARCHIVAL_RULES §step-5 (TSV) is superseded by migration_log.txt for this migration.

- **`architecture/history_lore.yaml` refs to migrated content** (lore card `sources:` fields at lines 2062, 2090, 2218, etc.): these cite `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/` which was never git-tracked (local-only). These are not broken — they're archaeologically correct. Not actionable but worth noting for future lore card maintenance.

---

## Ambiguity Risks

None surfaced. The `<YYYY>-Q<N>` placeholder in `docs/operations/AGENTS.md` updated refs is intentional (agents should compute the quarter from the packet date, not hardcode a quarter). This is correct behavior for a procedural instruction.

---

## Multi-Perspective Notes

**Executor**: A future maintenance_worker implementing the archival procedure will find `docs/operations/AGENTS.md` correctly updated. The stub schema is machine-readable (YAML front-matter). The `restore_command` is copy-pasteable. The INDEX.md is human-scannable. The main gap is `last_modified_before_archive` accuracy — an executor using this to assess age will see incorrect dates.

**Stakeholder**: The migration achieves its stated goal: 28 entries moved from gitignored cold-storage alias (`docs/archives/packets/`) to git-tracked quarterly archive (`docs/operations/archive/2026-Q2/`). The deprecated forwarding doc is present. Nav docs updated. No stub points to a missing target.

**Skeptic**: The plan claimed "46-entry archive backlog" but only 28 were migrated. The gap of 18 is explained by the gitignore split: `docs/archives/` has been gitignored since commit `98fe1c1fc4` (2026-04-12), so only 28 of the historical entries were tracked. The remaining 18 (including task_2026-04-28 through task_2026-04-30 content) are local-only cold storage on the developer's machine and cannot be migrated via `git mv`. This is a legitimate structural limitation, not a worker error. The plan itself notes "0 prior-deleted exclusions" as the correct figure.

---

## Verdict Justification

ACCEPT-WITH-RESERVATIONS. The four MINOR findings are the complete finding set after self-audit. None block WAVE 2 gate; none introduce broken runtime paths. The pre-deletion check hardcoding (Minor #1) is a documentation flaw in a one-shot script that will never run again. The architecture file refs (Minor #2) were outside declared scope and are non-operational. The re-run guard (Minor #3) protects against accidental re-execution but source dir no longer exists anyway. The `last_modified_before_archive` inaccuracy (Minor #4) is informational only.

WAVE 2 gate condition from PLAN.md: "actual migration count matches INDEX; no broken back-refs; critic CLEAR_PASS." Count matches (28 = 28). No broken back-refs in operational docs (architecture historical prose refs are out of scope). Gate is met.

Realist check: all four findings are stable at MINOR. None involve runtime paths, data integrity, or security. Detection time for any of these issues would be measured in minutes if someone tried to use the affected fields operationally.

Review operated in THOROUGH mode throughout. No CRITICAL or MAJOR findings triggered escalation to ADVERSARIAL mode.

**Recommended actions before WAVE 3**:
1. (Optional) Add the re-run guard to `scripts/archive_migration_2026-05-16.py` (Minor #3) — 3 LOC, zero risk.
2. (Optional) Annotate or defer Minor #2 (architecture file refs) explicitly in PLAN.md §WAVE 7.
3. (Optional) Update `last_modified_before_archive` fields if accurate archival dates matter downstream.

None of these block WAVE 3 execution.
