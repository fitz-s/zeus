# P0 Patch Blueprint

## Intent

Execute the smallest safe rewrite that makes the online repo tell the truth about itself.
No source behavior.
No graph rebuild.
No archive promotion.

## Exact target state

### `AGENTS.md`

Rewrite around these sections:

1. What Zeus is
   - a runtime machine
   - a workspace change-control machine
2. Read order
   - root AGENTS
   - workspace_map
   - scoped AGENTS
   - relevant manifests
   - current_state
   - active packet docs if active
   - derived context engines as needed
3. Authority vs context vs history
   - authority = machine manifests + tests + active packet control
   - context = topology_doctor digests, CRG, rationale, lore
   - history = archive registry + archive bundles
4. Hard rules
   - graph non-authority
   - archives non-default-read
   - no runtime behavior widening

### `workspace_map.md`

Turn the file into a visibility matrix with at least these classes:

- tracked visible text
- tracked visible binary/derived context
- ignored local historical cold storage
- runtime-local scratch/control
- generated evidence / reports

The file should explicitly say that `.code-review-graph/graph.db` is tracked derived context and `docs/archives/**` is historical cold storage.

### `docs/README.md`

Remove any wording equivalent to “active subdirectories plus archives.”
Replace with:

- active tracked docs mesh
- visible historical interface (`docs/archive_registry.md`)
- hidden archive bodies outside default read path

### `docs/AGENTS.md`

Remove direct routing to `archives/AGENTS.md` as a live visible peer.
Route historical needs to `docs/archive_registry.md`.
Keep only live tracked docs surfaces in the default registry.

### `docs/archive_registry.md`

Create a new file with:

- purpose and non-authority warning
- when to use archives
- what kinds of archive categories exist
- how to label archive-derived claims (`[Archive evidence]`)
- promotion guardrails
- contamination warning (secrets / binaries / junk files)

### `docs/operations/AGENTS.md`

Tighten language so `current_state.md` is clearly a live control pointer and completed packet material belongs in archive after closeout.

### `docs/operations/current_state.md`

Slim to:

- current branch/program
- active packet
- status / freeze point
- next packet
- required evidence pointers
- blockers / dependencies
- explicit note that runtime-local details live elsewhere

Remove:

- runtime PID inventories
- hidden archive path lists
- large historical packet summaries
- anything that reads like a narrative diary

### `architecture/topology.yaml`

P0 should do the minimum necessary machine update:

- register `docs/archive_registry.md`
- stop describing hidden archives as part of the active visible docs mesh where possible without schema expansion
- keep graph classification intact

If schema makes a full visibility refactor too broad for P0, leave deeper topology semantics to P1.

## Implementation order

1. Create/update packet docs.
2. Rewrite root `AGENTS.md`.
3. Rewrite `workspace_map.md`.
4. Rewrite `docs/README.md` and `docs/AGENTS.md`.
5. Create `docs/archive_registry.md`.
6. Slim `docs/operations/current_state.md` and update `docs/operations/AGENTS.md`.
7. Update `architecture/topology.yaml`.
8. Run P0 verification commands.
9. Stage only allowed files.

## Acceptance criteria

- Online reader no longer thinks archives are visible peer docs.
- Online reader now sees graph/context engines as first-class derived context.
- current_state is visibly thinner.
- topology knows about the visible archive interface.
- no source/script/test behavior changed.
