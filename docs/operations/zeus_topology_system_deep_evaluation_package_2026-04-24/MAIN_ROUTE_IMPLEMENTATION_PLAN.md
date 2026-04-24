# Main-Route Implementation Plan — Topology System Reform

**Author:** synthesis from `AGENTS.md`, this evaluation package
(`00`, `01`, `03`, `10`, `12`, `13`, `14`, `15`, `16`, `17`,
`repair_blueprints/p0..p5`), and direct repo inspection on
branch `data-improve`.

**Position relative to existing package files:** this is the *executive
execution route*. The repair blueprints in `repair_blueprints/` describe
*what* each phase changes; the per-phase small plans in `phase_plans/`
describe *the ordered atomic work* with concrete repo anchors. This file
ties them together as one route, with sequencing, gates between phases, and
risk control.

---

## 0. Problem framing (re-stated, not re-derived)

The topology system is **structurally sound but cognitively underfed**
(see `00_executive_ruling.md`). Three observable failure modes drive
the route:

1. **Lane conflation** — `scripts/topology_doctor.py:1084-1123` (`run_navigation`)
   aggregates 9 broad lanes and treats any `severity == "error"` as a
   navigation blocker. Unrelated docs/source/history/reference drift can
   block a focused source task.
2. **Thin issue shape** — `scripts/topology_doctor.py:71-76` defines
   `TopologyIssue` as `(code, path, message, severity)` only. There is
   no `lane`, `scope`, `owner_manifest`, `repair_kind`, `blocking_modes`,
   `companion_of`, or `confidence`. Issues cannot be machine-routed,
   grouped by repair, or scoped per mode.
3. **Cognition extraction debt** — dense topology knowledge lives in
   `tests/test_topology_doctor.py` (3,724 lines) and in compressed
   manifests, not in module/system books. Online-only agents must read
   tests or graph blobs to recover system meaning. Module books exist
   (`docs/reference/modules/topology_system.md` 123 lines, etc.) but are
   acknowledged thin in the package.

These three failures interact: thin issues prevent scope-aware lanes;
hidden cognition prevents normalizing manifests; absent module books
prevent removing test-encoded laws.

## 1. Route invariants (must hold across every phase)

Drawn from `AGENTS.md`, `14_not_now.md`, and the package decision register
(`13_decision_register.md`).

- **I-1 No runtime/source behavior changes.** Nothing under `src/**` is
  edited as part of this route. Topology reform is governance/infra only.
- **I-2 Topology stays an index over authority, not authority itself.**
  Graph output, compiled topology, repair drafts, and module books are
  derived/reference; they never displace canonical DB / chain / manifest
  truth.
- **I-3 No archive default-read.** Archive evidence is cited per claim
  (`[Archive evidence]`), never auto-loaded.
- **I-4 No new parallel registries.** Ownership is normalized into existing
  manifests; new YAML rows require a written owner before they land.
- **I-5 Test laws are promoted, not deleted.** Removing a hidden law from
  a test requires the law's prose to land in a module/system book first.
- **I-6 Backward-compatible JSON contracts.** Existing keys
  (`code`, `path`, `message`, `severity`) are preserved; new fields are
  additive.
- **I-7 Planning-lock respected.** Any phase that touches
  `architecture/**`, `src/state/**` write paths, `src/control/**`,
  `src/supervisor_api/**`, `.github/workflows/**`, or > 4 files must run
  `topology_doctor.py --planning-lock` and attach plan evidence
  (this very document and the matching `phase_plans/phase_p*_plan.md`
  qualify).
- **I-8 Map maintenance closeout per packet.** Every phase ends with
  `topology_doctor.py --map-maintenance --map-maintenance-mode closeout`
  on its changed file set.

These invariants are non-negotiable. A phase that cannot satisfy them
must stop and re-plan, per `17_apply_order.md` "Hard stops".

## 2. Route shape

```
Preflight  →  P0  →  Review0  →  P1  →  P2  ┐
                                            ├→  P3  →  P4  →  P5  →  Closeout
                                  (P2 in parallel with P3 prep)
```

- **Preflight** establishes baseline, invariant snapshot, and digest.
- **P0** lands lane policy (no behavior reform downstream is safe before
  this; it is the foundation).
- **Review0** is a critic gate that must pass before P1 starts.
- **P1** lands the typed issue model; downstream phases cite `owner_manifest`,
  `blocking_modes`, etc. that P1 introduces.
- **P2** rehydrates module/system books. It can begin in parallel with
  P3 design, but must not contradict P1 ownership shape.
- **P3** normalizes manifest ownership; depends on P1 (`owner_manifest`
  field) and on P2 (where ownership prose lives).
- **P4** adds graph/context-pack textual extraction; depends on P1
  (typed `authority_status`, `graph_freshness` fields) and P2
  (`code_review_graph.md` body).
- **P5** resegments tests once lane policy and issue shape are stable
  enough to fixture; running it earlier would re-fixture against a
  moving target.
- **Closeout** runs the full validation matrix and writes the lore commit
  trail.

This ordering is identical to `12_packetized_execution_plan.md` and
`17_apply_order.md`. The main-route file makes the *gates between
phases* explicit so each phase can ship as an independent packet
without breaking the chain.

## 3. Dependencies and gate matrix

| Phase | Hard prerequisites | Soft (recommended) | Gate to next phase |
|------:|--------------------|--------------------|--------------------|
| Preflight | `git status` clean; baseline commit recorded | digest run | digest captured to packet folder |
| P0 | Preflight gate passed | none | nav digest separates `direct_blockers` vs `repo_health_warnings`; closeout fixture for unrelated drift passes |
| Review0 | P0 merged | none | critic prompt `prompts/codex_review_after_p0.md` returns no semantic regressions |
| P1 | P0 + Review0 | OQ-1 decided (where typed schema lives) | legacy `code/path/message/severity` consumers pass; new fields appear in JSON for at least docs/source/test/script families |
| P2 | P0 (lane scoping) | P1 underway (so books cite typed fields correctly); OQ-3 decided | `--docs` lane green; new books registered in `docs_registry.yaml` and `module_manifest.yaml` |
| P3 | P1 (`owner_manifest` field) + P2 (`manifests_system.md` ownership matrix prose) | OQ-2 decided | ownership validator green; no two manifests claim canonical ownership of the same fact type/path |
| P4 | P1 + P2 (`code_review_graph.md` body) | OQ-4, OQ-5 decided | graph status lane stays advisory; context-pack JSON includes graph appendix with `authority_status: derived_not_authority` |
| P5 | P0 + P1 stable | none (do not block on P3/P4) | `pytest -m "not live_topology"` deterministic across two runs; `pytest -m live_topology` reports live debt without hiding it |
| Closeout | All above | OQ-7 decided (deferral recording) | full validation matrix in `validation/validation_matrix.md` green; lore commit trail intact |

Open Questions (`OQ-#`) reference `15_open_questions.md`.

## 4. Cross-cutting risk controls

- **Risk: P1 schema drift.** Mitigated by writing `topology_schema.yaml`
  contract before code (resolves OQ-1) and by keeping fallback
  factory `legacy_issue()` returning the old shape (per
  `repair_blueprints/p1_issue_model_repair.md` rollback).
- **Risk: P2 books duplicate manifests.** Mitigated by P2 books being
  *reference-only* (no machine claims) and by P3 ownership matrix
  preceding any prose that asserts canonical ownership.
- **Risk: P5 tests fixture against moving lane policy.** Mitigated by
  ordering: P5 follows P0 + P1 + P3 stabilization, and the marker split
  is added in one step before refactoring fixture data.
- **Risk: P4 graph treated as authority.** Mitigated by every emitted
  graph fact carrying `authority_status: derived_not_authority`, and
  by `--code-review-graph-status` remaining advisory unless task
  profile demands graph evidence.
- **Risk: scope creep into `src/**`.** Each phase's allowed/forbidden
  file list is enforced by `topology_doctor --closeout --changed-files`
  per packet; `src/**` belongs to `forbidden_files` for every phase
  except where explicitly listed (none of P0–P5 list `src/**`).

## 5. Surfaces by phase (machine view)

| Phase | Allowed (write) | Forbidden (must not touch) |
|------:|------------------|---------------------------|
| P0 | `scripts/topology_doctor.py`, `scripts/topology_doctor_cli.py`, `scripts/topology_doctor_closeout.py`, narrow tests | `src/**`, `architecture/*manifest*.yaml` (broad), `.code-review-graph/graph.db`, archives |
| P1 | `topology_doctor*.py` issue dataclass + factories + renderers, tests; optionally `architecture/topology_schema.yaml` (schema only, OQ-1) | `src/**`, broad manifest content, runtime |
| P2 | `docs/reference/modules/{topology_system,code_review_graph,docs_system,manifests_system,topology_doctor_system,closeout_and_receipts_system}.md`, `docs/reference/AGENTS.md`, `docs/reference/modules/AGENTS.md`, `architecture/docs_registry.yaml` (registrations only), `architecture/module_manifest.yaml` (registrations only) | `src/**`, scripts behavior, archives as default-read, graph authority |
| P3 | `architecture/topology_schema.yaml`, selected `architecture/*manifest*.yaml`, `topology_doctor*.py` ownership validator + tests, `docs/reference/modules/manifests_system.md` | `src/**`, graph.db, broad docs cleanup outside ownership surfaces |
| P4 | `scripts/topology_doctor_code_review_graph.py`, `scripts/topology_doctor_context_pack.py`, `docs/reference/modules/{code_review_graph,topology_system}.md`, optional approved generated sidecar | graph.db staging, custom graph refresh, runtime, graph promoted to authority |
| P5 | `tests/test_topology_doctor.py`, `pytest.ini`/`pyproject` markers, fixture data/helpers, `validation/*.md` notes | `src/**`, manifest edits to make live tests pass |

## 6. Validation strategy

The package supplies `validation/validation_matrix.md`,
`validation/topology_doctor_regression_commands.md`,
`validation/live_repo_health_commands.md`,
`validation/graph_context_validation.md`, and
`validation/fixture_vs_live_test_split.md`. The route uses them as follows:

- **After every phase**: rerun the regression commands relevant to that
  phase and add a row to `validation/validation_matrix.md` (date,
  commit, command, ok/fail, notes).
- **Before next phase starts**: confirm the previous phase's gate row
  is green. A red row blocks the next phase's start.
- **Closeout**: run the full matrix; live health may have unrelated
  drift entries — those are captured advisory-only in the matrix and
  do not block, per D11.

Per-phase regression command sets are listed in each phase plan under
"§5 Verification".

## 7. Decisions deferred to phase entry (Open Questions resolution route)

These OQs from `15_open_questions.md` are not blocking the route, but
each phase that needs them must record the decision in the matching
phase plan before code lands.

- **OQ-1** (typed-issue schema location): decide at P1 entry.
  Default: keep schema in Python dataclass + a thin
  `architecture/topology_schema.yaml` issue contract section.
- **OQ-2** (`module_manifest` `maturity` and `canonical_owner_refs`
  fields): decide at P3 entry. Default: add `maturity` (existing
  practice elsewhere) but defer `canonical_owner_refs` to P3 itself.
- **OQ-3** (committed vs generated module books): decide at P2 entry.
  Default: commit as first-class docs; generated appendices only in P4.
- **OQ-4 / OQ-5** (graph sidecar storage and budget): decide at P4 entry.
  Default: included in context packs only, hard-capped at e.g. 2 KB
  per appendix unless approved otherwise.
- **OQ-6** (which codes may become closeout-blocking): decide
  iteratively across P1/P3; default: only codes with
  `owner_manifest != "unknown"` and `maturity == "stable"`.
- **OQ-7** (deferral recording in receipts): decide at Closeout entry.
- **OQ-13** (docs_registry vs module_manifest conflict resolution):
  resolved structurally by P3 ownership matrix.

## 8. Definition of done (whole route)

The route is complete when **all** of the following hold:

1. `topology_doctor.py --navigation --task '...' --files ...` returns a
   route digest with separate `direct_blockers`, `route_context`,
   `repo_health_warnings`, and `global_health_counts`, and unrelated
   drift never appears in `direct_blockers`.
2. `TopologyIssue` exposes the typed fields listed in
   `repair_blueprints/p1_issue_model_repair.md` and JSON output retains
   the four legacy keys.
3. The six module/system books listed in §5 P2 exist, are registered in
   `architecture/docs_registry.yaml` and `architecture/module_manifest.yaml`,
   and are explicitly marked reference-only.
4. `architecture/topology_schema.yaml` (or equivalent) declares a
   canonical owner per fact type, and no two manifests claim canonical
   ownership of the same fact.
5. `topology_doctor.py --code-review-graph-status --json` and
   context-pack output emit a graph appendix with
   `authority_status: derived_not_authority` and freshness/limitation
   metadata; graph remains advisory in non-graph-required tasks.
6. `pytest -q tests/test_topology_doctor.py -m "not live_topology"` is
   deterministic across two consecutive runs on the baseline commit and
   on `HEAD`; `live_topology` runs separately and is allowed to report
   non-blocking drift.
7. `validation/validation_matrix.md` contains an unbroken green row for
   each phase's exit gate, and the lore commit message log matches the
   per-phase strings in `12_packetized_execution_plan.md`.

Anything else (broader manifest cleanup, archive promotions, repair-draft
auto-application, runtime/source changes) is explicitly **out of route**
per `14_not_now.md`.

## 9. How to use this file

1. Read this file once for the shape.
2. For each phase, read the matching `phase_plans/phase_p<N>_plan.md`
   for the executable checklist.
3. Refer back to `repair_blueprints/p<N>_*.md` for the conceptual
   blueprint and to `prompts/codex_p<N>_*.md` for the Codex execution
   prompt.
4. Update `validation/validation_matrix.md` after every phase.

This file does not describe runtime/source behavior. It does not change
any law. It is a planning artifact and should be cited as such, not
treated as authority.
