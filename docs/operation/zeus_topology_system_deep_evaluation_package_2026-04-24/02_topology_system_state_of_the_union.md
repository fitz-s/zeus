# 02 — Topology System State of the Union

## What is working

Zeus has a substantial topology system. It already contains:

- root and scoped operating routers,
- architecture manifests for invariants, negative constraints, topology, docs registry, map maintenance, context budget, script manifest, source rationale, test topology, artifact lifecycle, and graph protocol,
- module manifest and module books,
- topology doctor CLI lanes and subcommands,
- context-pack generation,
- graph status and derived code-impact paths,
- closeout compilation,
- tests that exercise strict, docs, source, test, script, graph, context-pack, compiled-topology, and current-state behavior.

This is not a toy registry.

## What is not working

The system has become too compressed in the places where agents need cognition. A machine registry can answer “what file owns this category?” but it cannot, by itself, explain Zeus’s hidden obligations, false assumptions, failure modes, and decision logic to an online-only model.

The result is a split:

- **Machine layer:** rich, dense, numerous, increasingly normalized.
- **Human/agent cognition layer:** present but thinner than needed, with major knowledge still living in tests, packet plans, code comments, graph blobs, and history lore.

## Current strengths

### Authority boundaries are mostly correct

Topology and graph are repeatedly labeled as derived routing/context, not runtime/source truth. This is healthy.

### Manifest coverage is broad

The repo has enough manifest categories to encode docs, scripts, tests, source rationale, map maintenance, context budget, and graph protocol.

### Topology doctor has real capability

It checks far more than a simple linter: docs classification, source rationale, test law gates, script safety, map companions, current-state receipts, graph status, context pack route health, and compiled topology.

### Graph protocol is conceptually safe

The graph is structural context, semantic boot comes first, and stale/missing graph state is mostly warning-first.

## Current weaknesses

### Navigation is too global

Route discovery should not be a broad strict health gate.

### Closeout is not yet semantic enough

Closeout can scope by changed files, but it does not yet use a fully typed issue/blocking policy or strong companion expansion model.

### Tests are overburdened

The topology doctor test file acts as regression suite, hidden law book, live health check, and fixture model at once.

### Module books are too thin relative to manifest density

Some module books exist, but topology/system-level books are not yet dense enough to replace packet-plan archaeology.

### Repair routes are underdeveloped

A topology issue should tell an agent: owner manifest, exact repair class, companion surfaces, verification, and whether it blocks current mode. Current issues do not.

## Bottom line

The topology system has the right skeleton. It needs denser cognition surfaces, typed issues, scoped gates, and explicit ownership contracts.
