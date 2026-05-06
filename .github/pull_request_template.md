## Summary
- 
- 

## Test plan
- [ ] regression baseline: N/M passing, delta 0
- [ ] 

## Packet / work log
<!-- link to active packet or work_log.md; omit if not applicable -->

---

## AI Review Scope

> AI reviewers (Claude Code Review / ultrareview, Copilot, Codex):
> **prioritize the surfaces marked below before traversing the file
> tree alphabetically.** Doctrine: `REVIEW.md`,
> `docs/review/code_review.md`, `docs/review/review_scope_map.md`.

### Primary change type (check all that apply)

- [ ] runtime execution (Tier 0 — `src/execution/**`, `src/venue/**`, kill switch, risk gates)
- [ ] external API / venue object identity (market_id, condition_id, token_id, YES/NO, fees, ticks)
- [ ] data extraction / forecast semantics (`src/data/**`, `src/ingest/**`, settlement semantics, dual-track integrity)
- [ ] probability / calibration / economics (`src/calibration/**`, `src/signal/**`, `src/risk_allocator/**`, executable-cost economics)
- [ ] persistence / replay / reporting (`src/state/**`, migrations, schema, derived JSON exports)
- [ ] tests only (no source change)
- [ ] docs / instructions only (`AGENTS.md`, `.agents/**`, `.claude/**`, `.github/**`, `architecture/**`, `docs/**`)
- [ ] generated / mechanical refactor (rename, codemod, formatter)

### High-risk paths changed (Tier 0 / Tier 1 — must be reviewed)

```
<list paths here, e.g. src/execution/executor.py, src/contracts/settlement_semantics.py>
```

### Paths to deprioritize (skip-list candidates)

```
<list paths the reviewer can spend less budget on, e.g. docs/archives/**, .claude/orchestrator/**>
```

### Large-PR flags

- [ ] This PR is a large refactor / batched change set.
- [ ] Recommend splitting review into semantic slices (specify):
  ```
  <slices, e.g. execution slice, contracts slice, state slice, docs slice>
  ```
- [ ] Coverage limit: reviewer is **not** expected to cover the following surfaces:
  ```
  <surfaces and reason; e.g. docs/archives/** (provenance), state/** (runtime artifacts)>
  ```

### Required tests / commands run locally

```
<commands, e.g. python -m pytest tests/contracts/, python3 scripts/topology_doctor.py --planning-lock --changed-files <files>>
```

### Known not-reviewed surfaces

<!-- Author honesty: paths the author themselves did not deeply review. Helps
     the reviewer allocate budget; not an excuse to merge unreviewed
     Tier 0 changes. -->

```
<paths>
```

### Explicit reviewer instruction

> AI reviewers: prioritize the checked surfaces above before file-order
> traversal. **Empty findings + partial coverage is not a clean pass** —
> report partial coverage explicitly. See `REVIEW.md` for severity model
> and coverage rules.
