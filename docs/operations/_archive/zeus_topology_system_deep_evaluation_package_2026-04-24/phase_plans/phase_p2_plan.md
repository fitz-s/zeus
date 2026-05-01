# Phase Plan — P2: Module/System Book Rehydration

**Companion to:** `../repair_blueprints/p2_module_book_rehydration.md`,
`../prompts/codex_p2_expand_topology_books.md`,
`../module_books_expansion/*.md` (the drafts to adapt),
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §3 (P2 row).

## 1. Goal restated

Promote hidden topology cognition out of tests, packet plans, history
lore, and source comments into dense reference-only module/system
books. Today three relevant books exist but are thin:

- `docs/reference/modules/topology_system.md` — 123 lines
- `docs/reference/modules/code_review_graph.md` — 133 lines
- `docs/reference/modules/docs_system.md` — 126 lines

Three system books are missing entirely:

- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`

After P2, all six exist, are dense, are registered in
`architecture/docs_registry.yaml` and `architecture/module_manifest.yaml`,
and are explicitly labeled reference-only / non-authority.

## 2. Anchor points

| What | Where |
|------|-------|
| Drafts to adapt | `../module_books_expansion/topology_system_expanded.md`, `code_review_graph_expanded.md`, `docs_system_expanded.md`, `manifests_system_expanded.md`, `topology_doctor_system_expanded.md`, `closeout_and_receipts_system_expanded.md` |
| Existing books to extend | `docs/reference/modules/topology_system.md`, `code_review_graph.md`, `docs_system.md` |
| Books to create | `docs/reference/modules/manifests_system.md`, `topology_doctor_system.md`, `closeout_and_receipts_system.md` |
| Docs registry | `architecture/docs_registry.yaml` |
| Module manifest | `architecture/module_manifest.yaml` |
| Scoped routers | `docs/reference/AGENTS.md`, `docs/reference/modules/AGENTS.md` |

## 3. Pre-decisions (resolves OQ-3 and partially OQ-9)

- **OQ-3 decision**: commit all six books as first-class
  `docs/reference/modules/*.md`. Generated graph appendices are deferred
  to P4 and live in context-pack output, not committed text.
- **OQ-9 partial**: required headings enforced for these six *system*
  books only in this phase. Module books for non-system surfaces stay
  warning-first.
- **Authority labeling**: every new/expanded book opens with
  `> Status: reference, not authority. See <authority pointer>.` This
  satisfies I-2.
- **No archive default-read**: any archive-derived claim carries
  `[Archive evidence]` inline. No archive is added to default-read sets
  in `architecture/docs_registry.yaml`.

## 4. Required heading set (for the six system books)

Per §10 of `repair_blueprints/p2_module_book_rehydration.md` and
`module_books_expansion/topology_system_expanded.md` shape:

1. `# <book title>`
2. `> Status` line (reference / authority pointer / freshness class)
3. `## Purpose`
4. `## Authority anchors` (links to the manifests/laws this book explains)
5. `## How it works`
6. `## Hidden obligations` (laws currently enforced only by tests/code)
7. `## Failure modes` (with `[Archive evidence]` citations where used)
8. `## Repair routes` (cite `repair_kind` values from P1 where applicable)
9. `## Cross-links` (to other module books, no broken paths)

The drift guard test added in step 6 below enforces this set on the
six system books only.

## 5. Ordered atomic todos

1. **Read all six expansion drafts** in
   `module_books_expansion/`. Identify reusable prose vs needs-rewrite.
2. **Adapt and write each book** in this order (so cross-links resolve):
   1. `topology_doctor_system.md` (foundation — referenced by the others)
   2. `manifests_system.md` (ownership matrix prose lands here for P3)
   3. `closeout_and_receipts_system.md` (cites topology_doctor_system)
   4. Extend `topology_system.md` (cite the three above)
   5. Extend `docs_system.md` (cite manifests_system)
   6. Extend `code_review_graph.md` (sets up P4)
3. **Insert the ownership matrix into `manifests_system.md`** using the
   data from `16_machine_readable_summary.json` `manifest_ownership`
   field. P3 will *enforce* this matrix; P2 only writes the prose.
4. **Register every new book** in:
   - `architecture/docs_registry.yaml` with
     `doc_class: reference`,
     `truth_profile: explanation`,
     `freshness_class: stable`,
     `default_read: false`,
     `parent: docs/reference/modules/`.
   - `architecture/module_manifest.yaml` with the corresponding module
     ID and a pointer to the book file.
5. **Update scoped routers**:
   - `docs/reference/modules/AGENTS.md` lists the six system books
     with one-line descriptions.
   - `docs/reference/AGENTS.md` cross-links to
     `modules/AGENTS.md` (no other change).
6. **Add drift guard test** in `tests/test_topology_doctor.py`:
   `test_system_books_have_required_headings()` — checks the nine
   headings (§4) on exactly the six system books.
7. **Run docs lane and module-book check.**
8. **Validation matrix row** for P2.

## 6. Verification

```bash
python3 scripts/topology_doctor.py --docs --json
python3 scripts/topology_doctor.py --module-books --json
python3 scripts/topology_doctor.py --module-manifest --json
python3 scripts/topology_doctor.py context-pack --profile package_review --files docs/reference/modules/topology_system.md docs/reference/modules/manifests_system.md --json
pytest -q tests/test_topology_doctor.py -k "module or docs or system_book"
```

## 7. Definition of done

- All six books exist, follow the required heading set, open with the
  Status line, and contain no broken cross-links.
- All six are registered in `docs_registry.yaml` and
  `module_manifest.yaml` with `default_read: false`.
- `--docs` and `--module-books` lanes are green.
- The drift guard test passes.
- No existing book was deleted or had its authority status changed.
- Validation matrix row green.

## 8. Rollback

Atomic revert of:

1. New book files,
2. Registry/manifest registrations,
3. Router updates,
4. Drift guard test.

The expansion drafts under `module_books_expansion/` stay; they are
package evidence, not committed source.

## 9. Critic focus

- Did any book make a *machine* claim that belongs in a manifest?
  Books are reference-only.
- Did the ownership matrix prose accidentally pre-empt P3's
  enforcement decisions? It must describe, not enforce.
- Are archive citations marked `[Archive evidence]`?
- Did `default_read` become `true` anywhere? It must not.

## 10. Risks specific to P2

- **R-P2-1**: Books duplicate manifest content. Mitigation: cite
  manifest paths and quote field names; never re-list every row.
- **R-P2-2**: Required heading set is too rigid for the existing
  three books. Mitigation: extend rather than rewrite the existing
  bodies; add missing headings as new sections rather than restructuring.
- **R-P2-3**: AGENTS files balloon. Mitigation: scoped routers add
  one link line per book, not summaries.

## 11. Lore commit message

`Topology P2: rehydrate topology cognition into reference-only module/system books`
