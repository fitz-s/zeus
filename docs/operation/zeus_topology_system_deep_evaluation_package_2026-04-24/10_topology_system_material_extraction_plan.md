# 10 — Topology System Material Extraction Plan

## Ruling

This is the highest-leverage work after lane policy. Zeus must extract durable knowledge from hidden or compressed sources into the correct surface: authority, reference cognition, machine manifest data, or derived evidence.

## Extraction matrix

| Material class | Source of truth today | Why hidden | Where promote | Authority/reference? | Machine data, dense text, or both |
|---|---|---|---|---|---|
| Lane policy semantics | topology_doctor.py, closeout helper, uploaded plan | Mode/blocking decisions are implicit in code/tests | topology_doctor_system.md; issue model schema | reference + implementation | dense module text + typed issue data |
| Issue code ontology | tests/test_topology_doctor.py, helper modules | Issue codes lack owner/repair metadata | topology_doctor_system.md and repair-draft manifest/table | reference then machine data | both |
| Manifest ownership | architecture/AGENTS.md, map_maintenance.yaml, helper modules | Ownership inferred by validators/companion rules | manifests_system.md; optional ownership section in topology_schema | reference + machine schema | both |
| Docs archive/default-read law | docs checks, registry tests, topology docs_subroots | Mostly in docs checks/tests | docs_system.md and docs_registry ownership notes | reference + docs_registry | both |
| Current_state receipt binding | docs checks, current_state, current_delivery | Operationally enforced but thinly explained | closeout_and_receipts_system.md | reference | dense text |
| Test law gates and live/fixture split | test_topology.yaml, tests | Tests encode why gates exist | tests.md/topology_doctor_system.md | reference + test_topology | both |
| Script lifecycle safety | script_manifest.yaml, script checks | Manifest huge; semantics compressed | manifests_system.md and scripts.md | reference + script_manifest | both |
| Graph derived routes | graph.db, graph helper, graph protocol, graph book | Binary graph not readable online | code_review_graph.md + generated sidecars | reference/derived context | dense generated text |
| Module manifest field meanings | module_manifest.yaml, context_pack helper | Fields are present/placeholder but not fully explained | manifests_system.md + module book template | reference + machine schema | both |
| Repair draft classes | uploaded plan, issue code patterns | No current durable repair generator | topology_doctor_system.md + repair_draft schema | reference + future machine output | both |
| Context-pack route health | context_pack helper/tests | Output behavior not fully book-documented | topology_system.md/context_pack section | reference | dense text |
| History lore extraction rules | history_lore.yaml, topology_system book | Lore contains rich lessons but is non-default | topology_system.md + docs_system.md | reference | dense text |

## Extraction rules

### 1. Tests verify; books explain

If a test is the only place a rule is understandable, promote the explanation to a module/system book and keep the test as verification.

### 2. Manifests classify; books rehydrate

If YAML contains a dense registry but not the why/false-assumption/repair story, keep YAML as machine data and add prose to the book.

### 3. Graph discovers; books/context packs summarize

Graph-derived facts are useful but non-authoritative. Extract small structural summaries with limitations and freshness metadata.

### 4. Archives are evidence; extracted lessons can become durable

Do not make archives default-read. Promote only current durable lessons and cite/provenance them in the relevant book.

### 5. Packet plans are not durable law

The uploaded plan’s concepts should become durable only after explicit module-book/schema/implementation packets.

## Candidate promoted materials

### From tests

Promote:

- compiled topology output contract,
- graph protocol tests,
- archive default-read behavior,
- current_state receipt binding,
- docs registry parent coverage behavior,
- module book required headings,
- module manifest required fields,
- freshness metadata rules,
- reverse antibody quarantine.

### From topology_doctor code

Promote:

- lane roles,
- helper module responsibilities,
- issue code families,
- closeout scoping algorithm,
- graph warning/error boundaries,
- context-pack route health semantics,
- docs/source/test/script ownership models.

### From `docs/operations/current_state.md`

Promote:

- current_state role as live control pointer,
- receipt-bound source semantics,
- active package vs active execution packet distinction,
- current fact companion rule,
- next action surface conventions.

Do not promote active packet status into reference.

### From module_manifest

Promote:

- field definitions,
- placeholder/maturity semantics,
- graph_appendix_status meaning,
- archive_extraction_status meaning,
- high-risk file meaning,
- law/current-fact dependency link semantics.

### From docs_registry

Promote:

- doc_class/freshness/truth_profile meanings,
- default_read contract,
- direct_reference_allowed behavior,
- parent coverage rules,
- volatile metrics/current tense placement.

### From history_lore

Promote:

- recurring failure modes and false assumptions,
- named antibodies with current relevance,
- repair routes that recur across packets.

Leave dated narrative in lore.

### From graph-derived structure

Promote as derived appendices:

- high-impact nodes,
- changed-file impacted tests,
- module bridge files,
- topology_doctor helper centrality,
- graph coverage/freshness status.

## Acceptance criteria

A future online-only agent can answer these without reading tests or archives:

- What does each manifest own?
- Why does navigation not block on unrelated global drift?
- What makes closeout fail?
- What does graph prove and not prove?
- What companion updates follow a source/test/script/docs/module-book change?
- How should a missing source rationale/test topology/script manifest/docs registry entry be repaired?
- Which system book should be read for topology_doctor lane semantics?
