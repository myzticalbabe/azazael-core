# Upstream submission: exclude document / injected-concept hubs from `god_nodes`

**Target:** `graphify/analyze.py` → `god_nodes()`
**Patch:** `graphify_analyze_god_nodes.patch` (same directory; regenerate with
`python tools/graph_semantic/graphify_patch.py --emit-patch`)
**Tracking:** PMB goal *"Submit the graphify god-node patch upstream"* (open).

## Problem

`god_nodes` excludes file-level hub nodes, concept nodes (no file extension) and
JSON keys, but **not** `document` nodes. On any corpus where documents become
first-class graph nodes, a document that lists per-line file detail has the highest
degree and is reported as the "#1 core abstraction".

Measured on the Azazael graph (2026-10-03):

| node | file_type | degree |
|---|---|---|
| `Catch-All Refactoring Backlog` (CATCHALL_BACKLOG.md) | document | 583 |
| `DirectMemory` | code | 197 |

A backlog document outranks every real abstraction. The same defect class as the
stdlib god nodes (already filtered) and the AST `indirect_call` false hub.

## Fix

Insert a helper and add one exclusion clause:

```python
def _is_doc_or_injected_hub(G, node_id: str) -> bool:
    """Document / injected-concept nodes that outrank real code when ranked by degree."""
    attrs = G.nodes[node_id]
    if str(attrs.get("file_type") or "").lower() in {"document", "concept", "rationale"}:
        return True
    label = str(attrs.get("label") or "")
    if label.lower() in _BUILTIN_NOISE_LABELS:
        return True
    return len(label.rstrip("()")) <= 2


# in god_nodes(), after the existing exclusion:
        if _is_doc_or_injected_hub(G, node_id):
            continue
```

Backwards-compatible: nodes without a `file_type` (or with `code`/`paper`/`image`)
are unaffected.

## Local verification (done)

- `python tools/graph_semantic/graphify_patch.py --emit-patch` writes the diff
  against the installed source and it is byte-consistent with `god_nodes_fixed()`
  in `graphify_patch.py`.
- `tests/test_graphify_god_nodes_patch.py` (4 tests): the document node with the
  highest degree is excluded, a real code node is kept, `install()` monkeypatches
  `graphify.analyze.god_nodes`, and the emitted source still compiles and is
  idempotent.

## Submission checklist

- [ ] `--emit-patch` regenerated against the current installed graphify and the
      diff applied cleanly to a checkout of graphify at that version.
- [ ] The four tests pass in graphify's own suite (ported to its test layout).
- [ ] A note added to graphify's changelog referencing the corpus that motivated it.
- [ ] PR opened; PR URL recorded back on the PMB goal.

(Until it lands upstream, `god_nodes.py --patch-report` and
`graphify_patch.install()` keep this repository correct.)
