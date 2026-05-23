# WAVE 0 CLOSURE — SCOUT 0A/0B/0C Consolidated Findings

**Author:** executor (Wave 0 closure dispatch), 2026-05-16  
**Branch:** `feat/ref-authority-docs-2026-05-17`  
**Plan authority:** PLAN.md v3 (commit `e19b7ca922`)  
**Status:** WAVE 0 CLOSED — findings locked, WAVE 1 queued  

---

## Findings Summary

| Tier | Docs | Claims | Drifts | WAVE | Est. effort |
|------|------|--------|--------|------|-------------|
| 0A   | 8    | 491    | 1      | WAVE 1 | <30 min |
| 0B   | 10   | ~330   | 40     | WAVE 2 | 5–6 hr |
| 0C   | 4    | 446    | TBD per-claim | WAVE 3 (start) | 4–5 hr |
| 1    | 39   | TBD    | TBD    | WAVE 3 (continue) | 5–7 hr |

---

## SCOUT 0A (TIER 0A — Governance-locked YAMLs)

**Scope:** 8 docs, 491 claims examined.  
**Drifts found:** 1  

| File | Drift |
|------|-------|
| `architecture/world_schema_version.yaml` | Bump-on-migration comment cites `architecture/world_schema_manifest.yaml` — retired by commit `7fe2af7bee` (2026-05-14). File never to be re-created; successor authority is `architecture/db_table_ownership.yaml`. |

**invariants.yaml status:** properly governance-locked; no drifts.  
All other 6 TIER 0A docs: no drifts found.

---

## SCOUT 0B (TIER 0B — Runtime-coupled YAMLs)

**Scope:** 10 docs, ~330 claims examined.  
**Drifts found:** 40  

| File | Drifts |
|------|--------|
| `architecture/source_rationale.yaml` | 16 |
| `architecture/script_manifest.yaml` | 12 |
| `architecture/test_topology.yaml` | 5 |
| `architecture/topology_v_next_binding.yaml` | 5 |
| `config/reality_contracts/data.yaml` | 2 |

**SCOPE INFLATION RISK:** `source_rationale.yaml` + `script_manifest.yaml` together = 28 of 40 drifts (70%). These two files drive most WAVE 2 work. Budget accordingly; consider partial-fix ordering (low-drift files first to prove §8.5 discipline before high-density files).

**Loader-test command (SCOUT 0B determined, V3-A deferred):**  
`python -m maintenance_worker.cli.entry --config <maintenance_worker_config.json> status`  
Note: `--config` must appear **before** subcommand; `maintenance_worker_config.json` path TBD at WAVE 2 execution (zero results from `find . -name maintenance_worker_config.json` at time of scout).

---

## SCOUT 0C (TIER 0C + TIER 1)

### TIER 0C — Lifecycle-tracked markdown docs

**Scope:** 4 docs examined.  
**Status:** ALL 4 STALE (last touched 2026-04-23/24, 3+ weeks past 3-day lifecycle).  
**Claims:** 446 total — require full per-claim audit (no shortcuts; §8.5 Rule 2 essence-over-bloat applies).  

WAVE 3 will handle these. Per-claim audit method: grep-verify each cited path/symbol/value against current codebase before any edit.

### TIER 1 — AGENTS.md files

**Scope reconciliation:**
- 46 total AGENTS.md across repo
- 4 deferred (out of scope this phase)
- 3 confirmed CURRENT via PR #124 touch: `architecture/AGENTS.md`, `docs/operations/AGENTS.md`, `src/ingest/AGENTS.md` → **dropped from WAVE 3 scope**
- **Final WAVE 3 in-scope count: 39**

---

## Decision Deferrals (operator decides at WAVE 0 close)

**D1 — PR-split strategy:**  
Plan proposed 3 PRs (governance/runtime/authority). TIER 0A is essentially empty after WAVE 1 (1 trivial fix). Options:
- Keep 3-PR split (governance PR = just this 1 fix, establishes §8.5 discipline baseline)
- Condense: fold TIER 0A fix into the TIER 0B runtime PR (reduces PR overhead)

**D2 — WAVE 3 TIER 0C chunking:**  
4 docs, 446 claims, all STALE. Options:
- All 4 in one PR (PR-C) — simpler review
- Chunked by doc (4 PRs) — smaller review surface per PR, allows parallelism

Operator decision requested before WAVE 3 dispatch.

---

## WAVE 1 Status

**Scope:** 1 fix — `architecture/world_schema_version.yaml` dead-ref repair.  
**Status:** COMPLETE (see commit in this branch).  

§8.5 compliance:
- Rule 1 SURGICAL: YES — 1-line swap, no rewrite
- Rule 2 ESSENCE: YES — comment now cites the actual live authority (`db_table_ownership.yaml`)
- Rule 3 ATOMIC: YES — single file, single commit
- Rule 4 PROVENANCE: YES — commit body records OLD/WHY/NEW
- Rule 5 STOP CONDITION: YES — all 4 checks pass (grep-verify, claims sourced, no orphan refs, no net-new lines)
