# Orphan Artifact Cleanup Plan

Date: 2026-04-22
Branch: data-improve

## Objective

Remove stale root/local scratch artifacts and workbook duplicates that no
longer belong in active Zeus navigation surfaces.

## Scope

- Delete ignored root scratch files: `k_bugs.json`, `patch_schema.py`,
  `.git-commit-msg.tmp`, `.DS_Store`.
- Delete root duplicate `zeus_data_inventory.xlsx`.
- Delete stale ignored workbook copies:
  `docs/artifacts/zeus_data_inventory.xlsx`,
  `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx`,
  `docs/to-do-list/zeus_data_improve_bug_audit_100.xlsx`.
- Update active docs registries so agents no longer route to deleted workbook
  surfaces.

## Non-Goals

- Do not change Phase 10/P3 source code, state files, or graph DB.
- Do not delete text audit evidence that is still tracked and referenced.
- Do not rewrite current operations state beyond removing stale workbook
  pointers and registering this cleanup as packet evidence.
