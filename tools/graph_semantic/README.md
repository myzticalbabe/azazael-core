# Graph Semantic Extraction — handoff & runbook

How the Azazael knowledge graph gets a **documentation layer**: one node for every
project doc, deduped concept hubs extracted from every doc's headings, and a
hand-authored rationale layer on the docs that carry design intent.

Written 2026-10-03 after the first full pass. Read this before re-running or
extending the extraction.

## Why this exists

`graphify` builds the **AST/code** layer automatically and free. Its **semantic**
layer (docs, papers, rationale) is normally produced by an LLM subagent. There is
no `GEMINI_API_KEY` / `GOOGLE_API_KEY` on this host, so `graphify` cannot do that
pass — **the agent IS the LLM here.** These scripts are the pipeline that turns
that into a repeatable job instead of a one-off.

Before this pass: 3,014 of 3,021 docs had no node at all, and the ones that did
were stranded islands. After: **1,527 / 1,527 project docs have a node**, and the
whole graph is *more* connected than before (126 → 23 connected components).

## The trap that shapes the design

**Adding docs naively makes fragmentation worse.** A node with no edges is its own
connected component. Adding 1,425 doc nodes without edges would have added ~1,425
components. So every layer here is designed to attach:

- doc → its directory bucket (guarantees every doc has ≥ 1 edge)
- directory bucket → a **spine node** in the semantic layer (which is itself
  anchored into the code graph) — this is what pulls the doc tree onto the main
  component instead of leaving it as a satellite
- doc → doc (markdown links)
- doc → code node (backticked path references that resolve to a real AST file)
- every LLM-authored concept → a real AST node id (label-resolved)

Measured effect of the doc layer: components 133 → 119, isolated nodes 43.

## Run order

Everything runs from the **repo root** (paths are relative to it):

```bash
python tools/graph_semantic/ast_index.py     # 1. label -> AST node id index (anchors)
python tools/graph_semantic/doc_scope.py     # 2. enumerate docs + exclusions
python tools/graph_semantic/doc_digest.py    # 3. compact digest for LLM reading
#    -> the agent reads graphify-out/_digest_p1..p4.txt and AUTHORS nodes/edges
python tools/graph_semantic/semantic_layer.py  # 4. emit the authored fragment
python tools/graph_semantic/doc_layer.py       # 5. doc node per file + buckets + xrefs
python tools/graph_semantic/concept_layer.py   # 6. deduped concept hubs from headings
python tools/graph_semantic/coverage_layer.py  # 7. skills, lexicon, islands, groups
python tools/graph_semantic/rationale_tranche2.py  # 8. authored rationale, large docs
python tools/graph_semantic/rationale_tranche3.py  # 9. authored rationale, remaining docs
python tools/graph_semantic/enhanced_semantic.py   # 10. entity mentions from every doc body
python tools/graph_semantic/bridge_layer.py        # 11. bridge the last stranded components
python tools/graph_semantic/build_graph.py         # 12. merge + cluster + label + export
graphify cluster-only . --no-viz --no-label         # 13. regenerate GRAPH_REPORT.md
python tools/graph_semantic/god_nodes.py --top 20 --patch-report  # 14. fix the ranking
```

Or run steps 1–11 with `python tools/graph_semantic/run_extraction.py`, and steps
1–13 with `python tools/graph_semantic/run_extraction.py --report` (which runs
`cluster-only` and re-applies the God Nodes patch for you, in that order).

Supporting checks that do not rebuild anything:

```bash
python tools/graph_semantic/audit_anchors.py       # ANCHORS -> real AST nodes?
python tools/graph_semantic/graphify_patch.py       # is the god-node fix applied?
python tools/graph_semantic/graphify_patch.py --emit-patch  # write the upstream diff
```

Step 4 is the part a human/LLM does. Steps 1–3 feed it; steps 5–8 are mechanical.

Prerequisite: `graph.json` must already exist (`graphify extract .`), because
steps 1 and 5 read it for the AST index and the code-node bridges.

## Layer by layer

### `ast_index.py`
Builds `label -> [ast_node_id]` from every `file_type == "code"` AST node, plus a
tail-only index. This is what lets a semantic node say "this concept is about
`@register_worm`" and land on the **real** node instead of minting a ghost.

### `doc_scope.py`
Enumerates every `.md` under the repo and separates project docs from archive /
backup / vendor / generated content. Excludes by directory name:
`.git node_modules __pycache__ .venv venv .mypy_cache .pytest_cache graphify-out
dist build .agents skills_staging output graphs` plus archive classes
`archived archive archived_unified backup skills.win-backup target`.

Writes `graphify-out/_doc_scope.json` — the single source of truth for "which docs
count". Both `doc_layer.py` and `concept_layer.py` read it.

### `doc_digest.py`
Emits a compact projection of every doc — path, word count, title, headings, lead
paragraph — plus `_digest_p1..p4.txt`. **This is what the agent reads** rather than
dumping 700K words into context. Read the four parts, then author.

### `semantic_layer.py`  ← the authored layer
A literal table of `(id, label, file_type, source_file, rationale)` nodes,
`(source, target, relation, confidence, score)` edges, an `ANCHORS` map, and
`HYPEREDGES`. This is the LLM's actual product; it is hand-written, not generated.

Conventions that matter (from graphify's extraction spec):
- `file_type` ∈ {`code`, `document`, `paper`, `image`, `rationale`, `concept`}.
  Anything else is rejected.
- `confidence_score` is REQUIRED on every edge, drawn from the discrete set
  `1.0 / 0.95 / 0.85 / 0.75 / 0.65 / 0.55`; AMBIGUOUS uses 0.1–0.3. **Never 0.5.**
- `rationale` is an attribute ON the concept node — do not make a bare "why" node.
- Node id = full repo-relative path with extension dropped, every segment
  lowercased with non-alphanumerics → `_`, then the entity. Deterministic from the
  label alone. Never append chunk numbers.
- Max 3 hyperedges per chunk; use them only when the group adds information beyond
  pairwise edges.

### `doc_layer.py`
One `document` node per doc. Adds direction buckets and the four edge classes from
the design section above.

### `concept_layer.py`
Extracts H2–H4 headings from every doc, normalizes, drops boilerplate, and keeps
only headings that appear in **2..N docs** (N = `MAX_DOC_FREQ`, currently 60).
Shared headings become one concept node with many doc edges — real cross-document
hubs. Headings above N docs are template artefacts (the generated skill docs emit
`Related` / `Verify` / `Identity` in ~1,090 files each — those are noise, not
concepts).

### `coverage_layer.py`
Closes the gaps the first passes leave:

- **Skills corpus.** The 1,235 `skills/*.md` files carry YAML frontmatter
  (`name`, `description`, `metadata.category`). Each becomes a `concept` node with
  its description as rationale, grouped under a category hub (43 categories). This
  is the real semantic content of the skill corpus — not their section headings.
- **BIBL lexicon.** Every lexicon volume points at the shared `bibl_lexicon` node.
- **Duplicated tree.** `src/CODEX/DOCUMENTATION/*` is linked to its canonical
  `docs/*` twin (13 pairs), so a reader lands on one copy instead of two.
- **Polyglot islands.** `packages/azazael-core`, `gateway-go` and
  `native/worm_manifest` get concept nodes whose rationale is quoted from their own
  manifest, plus grounded edges to the subsystem they belong to.
- **Dimensionless files.** `.ps1`, `.sh`, `.java` and `__init__.py` have no
  supported AST language, so they are degree-0 by construction. They are grouped
  under suite/marker concept nodes so they carry identity.

### `rationale_tranche2.py`
The first authored pass covered the ~120 docs with design intent. This is the next
tranche: the large load-bearing docs it had not read (the task registry, the
research record, the two full-project audits, the session-insight and lesson
records, the worm audit, the project map, the cross-environment manifest, the
Rust tracker, the sync strategy, the worm proposals, the algorithm log). Each gets
a rationale and an edge to the subsystem it belongs to. Node id == the doc-layer
stem, so it upgrades the existing `document` node rather than duplicating it.

### `enhanced_semantic.py`
Reads **every** document's *body* for mentions of known graph entities (code
labels, concept and document titles) and adds a `mentions` edge per entity named.
This is what takes the pass from "every doc has an identity" to "every doc states
what it is about" — a reader can start at any document and reach its subsystems.
Two guards prevent the generic-hub defect: a label must be identifier-shaped (an
uppercase after the first character, an underscore, or a digit), and a label that
appears in more than `MAX_DOC_FREQ` (40) documents is corpus vocabulary
(`Environment`, `Category`) and is dropped. An edge exists only when the label
literally appears in the text — nothing is inferred from topic similarity.

It runs token-based (one cheap tokeniser pass, then dict lookups), not one giant
alternation regex — that took the layer from tens of seconds to ~1.3 s on this
corpus. Measured tuning: raising `GS_MENTION_MAX_DF` from 40 to 60 to 90 adds no
coverage (the cap is a plateau, not the constraint), and case-insensitive matching
is *worse* — it merges `Hydra`/`HYDRA`/`hydra` into one document frequency and
pushes real entities (hydra 86, directmemory 49) over the cap. Coverage is bounded
by whether a document names a distinctive identifier at all; 883 documents only
ever match their own title. Safe result: **324 documents** with a genuine
cross-document mention.

### Edge relations this pipeline adds

| relation | writer | meaning |
|---|---|---|
| `references` | semantic, doc, coverage, bridge | points at a specific node |
| `conceptually_related_to` | semantic, coverage | same topic, weaker than a reference |
| `rationale_for` | semantic, tranche 2/3 | the *why* behind a concept |
| `mentions` | enhanced_semantic | this document's body literally names this entity |

`mentions` is deliberately the weakest claim in the graph: it encodes "the text
contains this label", not "the author intended this link". Weight is mention count
(capped at 5).

### `bridge_layer.py`
After every other layer, a few real clusters are still their own connected
components because the AST pass gives a self-contained file an island and nothing
links it to a subsystem. This layer adds exactly one *grounded* edge per stranded
component to a spine node: noxfile's typecheck session to the type gate it
invokes, the inference cascade to the endpoints it routes between, `cluster_route`
to the Tier-2 cluster concern, `graph_learn` to the ML harness, the animation and
vega assets to the CODEX video pipeline, the forensic audits to the error-lesson
audit, and the mesh-resilience primitives to the mesh architecture audit.
Components are discovered at run time; a component that disappears stops being
bridged.

### `audit_anchors.py`
Checks the claim "every authored node is anchored to a real AST symbol": anchor
keys that exist in `ANCHORS` but not in `NODES` (silently skipped by `emit()`),
labels that resolve to no AST node (contribute zero edges), and authored ids that
**graphify rewrote to a source-file-stem id** on merge (edges survive, but the
on-disk id differs from the authored one). Exit 1 on a hard problem.

### `graphify_patch.py`
`god_nodes.py` patches the *report*, which `cluster-only` regenerates. This module
fixes the ranking at the source: `god_nodes_fixed()` is a drop-in replacement for
`graphify.analyze.god_nodes` that also excludes document / injected-concept hubs,
`install()` monkeypatches it for the current process, and `--emit-patch` writes a
unified diff against the installed `graphify/analyze.py`
(`tools/graph_semantic/patches/graphify_analyze_god_nodes.patch`) for upstream.

### `god_nodes.py`
The raw `graphify` god-node ranking is dominated by documentation reference hubs:
`CATCHALL_BACKLOG.md` (583 edges) outranked `DirectMemory` (197) as the "#1 core
abstraction". This tool adds the three filters graphify's own ranking lacks —
`file_type in {document, concept}`, stdlib names, and test-local helpers — and can
patch the `## God Nodes` section of `GRAPH_REPORT.md` in place.

### `build_graph.py`
Feeds every fragment to `graphify.build.build_merge` in **one** call, drops
self-loop edges (they carry no information in an undirected graph), clusters,
labels (hub labels + a few hand labels), attaches hyperedges, exports with
`to_json(..., force=True)`, and prints a BEFORE/AFTER component count so the
connectivity effect is measured, not assumed.

## Gotchas that cost time

1. **`build_merge` replaces per `source_file` AND per tier.** A source present in a
   new chunk has its existing nodes/edges dropped for the tier the chunk contains.
   To change a node, edit it in `semantic_layer.py` and re-run the whole build — do
   not patch `graph.json` directly, or the next rebuild silently reverts you.
2. **Label collisions silently delete nodes.** `deduplicate_by_label` merges a doc
   node into a code node when labels match. `docs/*/X_API.md` files are titled
   `X.py`, i.e. exactly a code filename. `doc_layer.py` handles this by appending
   `(documentation)`; keep that guard if you add docs.
3. **Non-ASCII filenames collide on id.** `BIBL/Α.md`, `Γ.md`, `Î.md` all normalize
   into the same stem. `stem()` disambiguates colliding stems with a short SHA-1 of
   the real path — stable, but note this deviates from the spec's "no suffix" rule
   because the alternative is losing the file entirely.
4. **Validate before merging.** `graphify.semantic_cleanup.validate_semantic_fragment`
   returns a (possibly empty) error list. Run it; 0 errors is the bar.
5. **`build_from_json` vs `build_merge` disagree on component count** for the same
   data. Always compare before/after with the *same* function, or you will chase a
   phantom regression.
6. **`--exclude-hubs N` is a degree percentile below which nodes are KEPT**, not
   "exclude the top N%". It made fragmentation dramatically worse on this graph.
7. **`--resolution` below 1.0 gives fewer/larger communities**; 0.2 changed 3,378 →
   3,368 on the older graph, i.e. no effect. Do not reach for clustering knobs to
   fix a fragmentation problem — it is a documentation/edge problem.
8. **`cluster-only` overwrites community labels** from hubs. Hand labels set in
   `build_graph.py` survive until the next `cluster-only`; re-apply if they matter.
9. **`graphify-out/` is gitignored.** `graph.json` and `GRAPH_REPORT.md` are
   generated. That is exactly why this directory exists in `tools/` — so the
   authored content and the method are reproducible and reviewable.

## Adding one authored node (worked example)

To add a hand-written node for a document, add a row to `NODES` in
`semantic_layer.py` and (optionally) an `ANCHORS` entry so it lands on real code:

```python
# in NODES
("docs_backup_restore_doc", "Backup & restore runbook", "document", "docs/BACKUP_RESTORE.md",
 "Two-person rule on restores; the restore is rehearsed quarterly because an "
 "unrehearsed backup is a hypothesis, not a recovery path."),

# in ANCHORS (label is matched case/punctuation-insensitively against AST labels)
"docs_backup_restore_doc": ["BackupManager"],
```

Then re-run `python tools/graph_semantic/run_extraction.py --report` and confirm
with `python tools/graph_semantic/audit_anchors.py` that the anchor resolved. Two
rules to keep: the node id is `{repo_relative_path_stem}` lowercased with
non-alphanumerics → `_` (do not append a chunk number), and if graphify rewrites
your id to a source-file stem that is fine — the audit tool reports it.

## Honest limits

- The authored rationale layer now covers ~140 high-value docs (`semantic_layer.py`
  plus `rationale_tranche2.py`). The remaining docs get an identity, a bucket, xrefs,
  their shared headings, and the entities they name — not hand-written rationale.
  Extending coverage means reading more docs and adding rows.
- `enhanced_semantic.py` reaches 324 docs with a distinctive entity mention. The
  rest name no distinctive identifier (883 match only their own title), so they get
  no `mentions` edge; that is the intended behaviour, not a gap.
- Concept hubs from headings are as good as the headings. Several top hubs
  ("Architecture", "Enforcement") are generic; the high-value concepts are the named
  entities in the authored layer.
- Self-loop edges are removed in `build_graph.py`, so the exported graph is loop-free.
- The final graph has 4 components and **1** isolated node (at the doc-layer
  intermediate stage it was 119 components / 43 isolated nodes). The three
  non-giant components are deliberately-leaf and listed in `EXPLAIN` in
  `bridge_layer.py` (two 2-node test fixtures and a stray top-level test file).

## Verify

```bash
# 100% doc coverage — must print missing=None
python - <<'PY'
import json
from pathlib import Path
g = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
scope = json.loads(Path("graphify-out/_doc_scope.json").read_text(encoding="utf-8"))
have = {n.get("source_file") for n in g["nodes"]}
print("missing:", [p for p in scope["included"] if p not in have])
PY

graphify diagnose multigraph --graph graphify-out/graph.json   # want 0 collapsed/dup
```

Last run (2026-10-03, clean-fragment rebuild): **35,889 nodes · 65,900 edges ·
2,198 communities · 12 hyperedges · 4 connected components (giant 35,884 +
2 + 2 + 1 deliberate leaves) · 1 isolated node · 0 self-loops · 1,528/1,528 project
docs · 2,259 nodes carrying a rationale · 2,205 document→entity `mentions` edges.**

Re-running `run_extraction.py` end-to-end reproduces these numbers exactly (the
runner forces `PYTHONIOENCODING=utf-8` on every step; `doc_digest.py` also
reconfigures its own stdout, because doc titles contain non-ASCII and a cp1252
console otherwise aborts the pipeline).
