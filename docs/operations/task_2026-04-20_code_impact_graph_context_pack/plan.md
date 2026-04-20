# Code Impact Graph Context Pack Plan

Date: 2026-04-20
Branch: data-improve

## Objective

Deepen the Code Review Graph integration by adding a derived code-impact
appendix to topology context packs.

## Scope

- Add `code_impact_graph` to `package_review` and `debug` context packs.
- Use the local graph only after `--code-review-graph-status` health checks.
- Mark graph output as `derived_code_impact_not_authority`.
- Keep stale, missing, or non-applicable graph output visible but unusable.

## Non-Goals

- Do not make graph output blocking authority.
- Do not expose source-writing CRG refactor tools.
- Do not modify Phase 10A/Dual-Track runtime code or raw oracle snapshots.
