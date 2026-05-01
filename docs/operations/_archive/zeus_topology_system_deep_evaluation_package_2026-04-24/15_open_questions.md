# 15 — Open Questions

1. Should typed issue metadata live only in Python output, or should `topology_schema.yaml` define the JSON contract?
2. Should `module_manifest.yaml` gain explicit `maturity` and `canonical_owner_refs` fields?
3. Which module books should be committed as first-class docs versus generated appendices?
4. Should graph-derived textual sidecars be committed, generated on demand, or included only in context packs?
5. What is the maximum size budget for generated graph appendices?
6. Which current topology_doctor issue codes are allowed to become closeout-blocking?
7. How should deferrals be recorded in receipts without hiding global drift?
8. Should a dedicated `--global-health` lane replace strict-mode overload?
9. Should module book required headings be enforced only for system books or all module books?
10. What is the promotion bar for warning-first module manifest checks becoming blockers?
11. Which hidden laws in `tests/test_topology_doctor.py` should be moved into `topology_doctor_system.md` first?
12. Should repair drafts create YAML stubs in output JSON only, or also write patch files under a generated evidence folder?
13. Who owns conflict resolution when docs_registry and module_manifest disagree about a module book?
14. How should baseline commit and graph freshness be represented in context packs?
15. What is the right schedule for live repo-health tests after fixture split?
