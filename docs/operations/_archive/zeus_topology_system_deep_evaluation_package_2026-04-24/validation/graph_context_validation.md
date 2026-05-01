# Graph Context Validation

## Status command

```bash
python scripts/topology_doctor.py --code-review-graph-status --changed-files <files> --json
```

Expected fields or concepts:

- graph path,
- tracked/untracked status,
- freshness status,
- missing/stale file hashes,
- changed-file coverage,
- warnings vs errors,
- authority status derived/not-authority.

## Context pack command

```bash
python scripts/topology_doctor.py context-pack --profile package_review --files <files> --json
```

Expected graph/context behavior:

- graph limitations included,
- stale/missing graph does not waive semantic boot,
- graph-derived impacted tests/files are labeled derived,
- missing graph is advisory unless graph evidence is requested.

## Official graph commands

```bash
code-review-graph status --repo <repo-root>
code-review-graph update --repo <repo-root>
code-review-graph watch --repo <repo-root>
code-review-graph daemon start --repo <repo-root>
```

Use official commands rather than custom refresh scripts.

## Acceptance criteria for P4

- Online-only context includes readable graph-derived summaries.
- Graph is never represented as authority.
- Stale graph is reported with remediation.
- Context packs still function without local graph access.
