"""bibl_map.py — a hyperlinked map of the codebase.

PURPOSE
-------
Answer two questions for any entity in the tree: **what is it** and **where is
it**, plus **what does it connect to**. Derived recursively from the source, so
it is regenerated rather than curated into staleness.

WHY THIS IS NOT WORMZ
---------------------
WORMZ.py attempted this and produced a corrupt 30 MB INDEX.md. The defects
this module exists to not repeat:

  1. `WORMZ._parse_markdown` used ``r"`([^`]+)`"``. A negated character class
     matches newlines, so a single "term" could swallow an entire fenced code
     block; ``WORMZ._write_alphabetic_file`` then wrote it with
     ``f"## {term}"``, emitting foreign documents as headings. Terms here are
     single-line by construction (``_clean``), and the writer escapes.
  2. WORMZ stored one ``file_path`` per term, so a name appearing in 40 files
     kept only the last one. That cannot answer "where is everything", which
     is the entire point. Here a node carries ``locations``: every hit.
  3. WORMZ checksummed the symbol NAME, so ``crawl_delta`` detected renames
     and nothing else -- an edited body never re-indexed. Here the checksum is
     over the source segment, so any real change is visible.
  4. WORMZ merged code symbols and prose into one table, drowning ~3,000 code
     entries under 418,452 documentation ones. Split here into two graphs.
  5. Nothing verified the output. ``verify`` here regenerates and re-parses.

LLM ENRICHMENT
--------------
The surviving Kali snapshot showed the intended format, and its `Definition:`
lines are written prose -- not extractable from a docstring. So extraction is
deterministic and enrichment is not:

    crawl    -> skeleton.json        (symbol, kind, locations, signature)
    enrich   -> enrichments.json     (definitions, categories, see-also)
    render   -> INDEX.md + A-F/G-L/M-R/S-Z.md

Regenerating the crawl never destroys the enrichment, and enrichment never
masks a stale crawl -- the renderer reports which nodes have no definition.
That split is the only way to be both recursive and fresh.

CLI
---
    python bibl_map.py crawl  [--root PATH] [--out DIR]
    python bibl_map.py render [--root PATH] [--out DIR]
    python bibl_map.py verify [--out DIR]
    python bibl_map.py stats  [--out DIR]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Canonical root resolver. `_default_root()` below used to fall back to
#: `Path.home() / "azazael"`, which names a DIFFERENT, stale checkout from the tree
#: this module ships in -- so with AZAZAEL_ROOT unset the CLI crawled and rendered
#: the wrong tree. `get_project_root_safe()` is shared with every other module that
#: made the same choice, so the decision lives in one place.
try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("bibl_map:core-paths-import", exc)

    def _get_project_root_safe() -> Path:
        # CORE unreachable: derive from this file (src/ORCHESTRATION/BIBL/ -> root).
        return Path(__file__).resolve().parents[3]

# Range files, matching the layout the Kali snapshot used.
RANGES = (("A", "F", "A-F"), ("G", "L", "G-L"),
          ("M", "R", "M-R"), ("S", "Z", "S-Z"))

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", ".venv-3.14t",
    "var", "archive", "archived", "output", "logs", "state", "backups",
    "backup", "graphify-out", "docs", "dist", "build", "site-packages",
    "data", "BIBL", ".nox", ".deps-jiter", ".pytest_cache", ".mypy_cache",
    "htmlcov", ".ruff_cache", "__pypackages__", "eggs", ".eggs",
    ".claude", ".kilo", "nuitka_build_backup", "tmp",
}
# Directories that are legitimately part of the tree, even though they match a
# skip name. Ranking real code matters more than a tidy list.
FORCE_INCLUDE = ("src", "scripts", "tests", "tools")

MAX_MD_BYTES = 2_000_000
CODE_EXTS = {".py"}
DOC_EXTS = {".md"}


# ══════════════════════════════════════════════════════════════════════════
#  Node
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Location:
    file: str
    line: int = 0

    def __str__(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Node:
    """One entity in the map."""
    name: str                      # display form (cleaned, single-line)
    kind: str                      # class | function | coroutine | method | module | document
    graph: str                     # code | doc
    raw_name: str = ""             # identity form, never cleaned
    bases: list[str] = field(default_factory=list)   # for a class
    owner: str = ""                # enclosing class, for a method
    locations: list[Location] = field(default_factory=list)
    signature: str = ""
    docstring: str = ""
    checksum: str = ""             # over the SOURCE SEGMENT, not the name
    # filled from the enrichment overlay, never invented by the crawler
    category: str = ""
    definition: str = ""
    see_also: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.raw_name:
            self.raw_name = self.name

    @property
    def id(self) -> str:
        return "%s:%s:%s" % (self.graph, self.kind, self.raw_name)


def _clean(value: str, limit: int = 200) -> str:
    """Collapse a value to a single line and strip markdown metacharacters.

    The single-line guarantee is the fix for WORMZ's newline-spanning regex;
    the escaping is the fix for writing terms into a heading slot.
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("\\", "/")
    for ch in ("#", "*", "`", "|", "[", "]", "<", ">", "_", "{", "}"):
        text = text.replace(ch, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()[:16]


def _anchor(name: str) -> str:
    """GitHub-style anchor for a heading."""
    a = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[\s]+", "-", a)


def _first_line(docstring: str, limit: int = 300) -> str:
    if not docstring:
        return ""
    lines = [ln.strip() for ln in docstring.strip().splitlines()]
    for ln in lines:
        if ln:
            return _clean(ln, limit)
    return ""


# ══════════════════════════════════════════════════════════════════════════
#  Crawl -- deterministic
# ══════════════════════════════════════════════════════════════════════════

def _iter_files(root: Path, exts: set[str], max_bytes: int) -> Iterable[Path]:
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS or d in FORCE_INCLUDE]
        for name in names:
            p = Path(dirpath) / name
            if p.suffix.lower() not in exts:
                continue
            try:
                size = p.stat().st_size
            except OSError as exc:
                note_failure("bibl_map:L177", exc)
                continue
            if 0 < size <= max_bytes:
                yield p


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases[:2]]
        return "class %s(%s)" % (node.name, ", ".join(bases)) if bases \
            else "class %s" % node.name
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [a.arg for a in node.args.args][:8]
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return "%s %s(%s)" % (prefix, node.name, ", ".join(args))
    return ""


def crawl_code(root: Path) -> tuple[dict[str, Node], dict[str, int]]:
    """Walk every Python file and collect class/function/coroutine nodes.

    One node per NAME, carrying every location it appears at -- this is the
    correction to WORMZ storing a single file_path per term.
    """
    nodes: dict[str, Node] = {}
    stats = {"files": 0, "parse_errors": 0, "symbols": 0}
    root = Path(root)

    for path in sorted(_iter_files(root, CODE_EXTS, max_bytes=4_000_000)):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError, RecursionError) as exc:
            note_failure("bibl_map:L210", exc)
            stats["parse_errors"] += 1
            continue
        stats["files"] += 1
        _walk_symbols(nodes, stats, rel, source, tree, [], [])
    return nodes, stats


def _walk_symbols(nodes: dict[str, Node], stats: dict, rel: str,
                  source: str, node: ast.AST, class_stack: list[str],
                  func_stack: list[str]) -> None:
    """Recursive descent that carries the class and function stacks.

    `ast.walk` is breadth-first and discards hierarchy, so a method's owning
    class is not recoverable from it -- the stack has to be threaded by hand.
    That relationship is what produces the 'see also' links, so it is captured
    here, while the tree is in hand, rather than reconstructed later from the
    flattened skeleton (where it no longer exists).

    The function stack does the same job for closures. A nested function has no
    entry of its own under its bare name: it is either absorbed into the
    enclosing symbol or lost. Two different `_do_execute` closures in two
    different files are genuinely different things, and a map that cannot tell
    them apart cannot answer "where is everything". Nested functions therefore
    get a QUALIFIED identity -- `enclosing.<locals>.inner` -- and remain
    addressable.
    """
    pushed = False
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                        ast.AsyncFunctionDef)):
        kind: str | None = None
        label = ""
        if isinstance(node, ast.ClassDef):
            kind, label = "class", node.name
        elif isinstance(node, ast.AsyncFunctionDef):
            kind, label = "coroutine", node.name
        elif isinstance(node, ast.FunctionDef):
            kind = "method" if class_stack and not func_stack else "function"
            label = node.name
        else:
            label = ""

        if kind and label and not (label.startswith("__")
                                   and label.endswith("__")):
            # Identity: qualified for nested defs, bare at top level.
            raw = label
            if func_stack:
                raw = ".".join(func_stack + [label]) + ".<locals>"
            seg = _source_segment(source, node) or label
            key = "%s:%s" % (kind, raw)
            existing = nodes.get(key)
            if existing is None:
                bases: list[str] = []
                if kind == "class" and isinstance(node, ast.ClassDef):
                    for b in node.bases:
                        try:
                            bases.append(_clean(ast.unparse(b), 80))
                        except (ValueError, TypeError) as exc:
                            note_failure("bibl_map:L267", exc)
                nodes[key] = Node(
                    name=_clean(label, 120), raw_name=raw,
                    kind=kind, graph="code",
                    bases=bases,
                    owner=(class_stack[-1] if class_stack else ""),
                    locations=[Location(rel, getattr(node, "lineno", 0))],
                    signature=_signature(node),
                    docstring=ast.get_docstring(node) or "",
                    checksum=_digest(seg),
                )
                stats["symbols"] += 1
            else:
                existing.locations.append(Location(rel, getattr(node, "lineno", 0)))
                # A name in several files is a different thing; checksum the
                # concatenation so any one change moves it.
                existing.checksum = _digest(existing.checksum + _digest(seg))
            pushed = kind in ("class", "function", "coroutine")

    if pushed and kind == "class":
        class_stack = class_stack + [label]
        func_stack = []
    elif pushed and kind in ("function", "coroutine"):
        func_stack = func_stack + [label]

    for child in ast.iter_child_nodes(node):
        _walk_symbols(nodes, stats, rel, source, child, class_stack, func_stack)


def _source_segment(source: str, node: ast.AST) -> str:
    try:
        seg = ast.get_source_segment(source, node)
    except (ValueError, TypeError) as exc:
        note_failure("bibl_map:L300", exc)
        seg = None
    return seg or ""


def crawl_docs(root: Path) -> tuple[dict[str, Node], dict[str, int]]:
    """Collect document headings. Headings are single-line by construction --
    the regex is anchored per line and cannot span a fence the way WORMZ's
    backtick pattern could."""
    nodes: dict[str, Node] = {}
    stats = {"files": 0, "symbols": 0}
    root = Path(root)
    heading = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

    for path in sorted(_iter_files(root, DOC_EXTS, MAX_MD_BYTES)):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            note_failure("bibl_map:L318", exc)
            continue
        stats["files"] += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            m = heading.match(line)
            if not m:
                continue
            title = _clean(m.group(2), 120)
            if not title:
                continue
            title_raw = m.group(2).strip()
            key = "heading:%s" % title_raw
            if key not in nodes:
                nodes[key] = Node(
                    name=title, raw_name=title_raw, kind="heading",
                    graph="doc",
                    locations=[Location(rel, lineno)],
                    signature="h%d" % len(m.group(1)),
                    checksum=_digest(line),
                )
                stats["symbols"] += 1
            else:
                nodes[key].locations.append(Location(rel, lineno))
    return nodes, stats


# ══════════════════════════════════════════════════════════════════════════
#  Enrichment overlay -- supplied, never invented
# ══════════════════════════════════════════════════════════════════════════

def load_enrichment(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("enrichment overlay unreadable (%s); continuing without it" % e,
              file=sys.stderr)
        return {}


def apply_enrichment(nodes: dict[str, Node], overlay: dict[str, Any]) -> int:
    applied = 0
    for node in nodes.values():
        rec = overlay.get(node.id) or overlay.get(node.name)
        if not isinstance(rec, dict):
            continue
        if rec.get("definition"):
            node.definition = _clean(rec["definition"], 400)
        if rec.get("category"):
            node.category = _clean(rec["category"], 60)
        if isinstance(rec.get("see_also"), list):
            node.see_also = [_clean(s, 120) for s in rec["see_also"]][:12]
        applied += 1
    return applied


# ══════════════════════════════════════════════════════════════════════════
#  Derive -- deterministic enrichment
# ══════════════════════════════════════════════════════════════════════════

CATEGORY_BY_AREA = (
    ("/CORE/WORMS", "Worm"),
    ("/WORMS", "Worm"),
    ("/CORE", "Core"),
    ("/UTILITIES", "Utility"),
    ("/ORCHESTRATION", "Orchestration"),
    ("/HYDRA", "Hydra"),
    ("/CODEX", "Codex"),
    ("/META", "Meta"),
    ("/AOA", "AOA"),
    ("/tests/", "Test"),
    ("archived_unified", "Archive"),
    ("/packages/", "Package"),
    ("/scripts/", "Script"),
)


def _doc_category(node: Node) -> str:
    """Doc category from the document's own path, mirroring the code map."""
    if not node.locations:
        return "Document"
    path = "/" + node.locations[0].file.replace("\\", "/")
    for frag, cat in CATEGORY_BY_AREA:
        if frag in path:
            return cat
    return "Document"


def _category_for(node: Node) -> str:
    locs = node.locations
    if not locs:
        return "Code"
    path = "/" + locs[0].file.replace("\\", "/")
    if "test_" in Path(locs[0].file).name:
        return "Test"
    for frag, cat in CATEGORY_BY_AREA:
        if frag in path:
            return cat
    return "Code"


def _definition_from_code(node: Node) -> str:
    """What can be said about this node without inventing anything.

    A docstring's first line IS the code telling us what it is, so that is
    used verbatim. Failing that we state the structural fact -- what it is
    called, what shape it has, where it lives -- and stop. A structural
    sentence is honest; a plausible-sounding invention is not.
    """
    if node.docstring:
        first = _first_line(node.docstring, 280)
        if first:
            return first
    where = node.locations[0].file if node.locations else "unknown"
    n = len(node.locations)
    plural = " (also in %d other file%s)" % (n - 1, "" if n == 2 else "s") \
        if n > 1 else ""
    if node.kind in ("function", "coroutine", "method"):
        sig = _clean(node.signature, 120)
        return "%s defined in `%s`%s." % (
            sig or ("%s '%s'" % (node.kind, node.name)),
            Path(where).name, plural)
    return "%s '%s' defined in `%s`%s." % (
        node.kind, node.name, Path(where).name, plural)


def _relations(nodes: dict[str, Node]) -> dict[str, list[str]]:
    """Real links, computed from the code -- not invented.

    Two relationships captured during the crawl, while the AST tree was still
    in hand: a class and what it inherits from, and a method and the class it
    belongs to. Reconstructing these from the flattened skeleton afterwards is
    not possible -- the class body is gone by then.
    """
    rel: dict[str, list[str]] = {}
    for node in nodes.values():
        links: list[str] = []
        links.extend(node.bases)
        links.extend(node.owner for _ in [0] if node.owner)
        links = [x for x in dict.fromkeys(links) if x and x != node.raw_name]
        if links:
            rel[node.id] = links[:8]
    return rel


def _doc_definition(node: Node) -> str:
    """What a heading is, from the document it lives in.

    A heading carries no docstring, so the honest statement is structural:
    its level, its document, and every place that document is indexed. That is
    more useful than a blank, and unlike prose it cannot be wrong.
    """
    where = node.locations[0].file if node.locations else "unknown"
    level = (node.signature or "h?").lstrip("h")
    n = len(node.locations)
    docs = sorted({loc.file for loc in node.locations})
    if len(docs) == 1:
        plural = ""
    else:
        plural = "; also headed in %d other document(s)" % (len(docs) - 1)
    return ("Level %s heading in `%s`%s."
            % (level, Path(where).name, plural))


def derive_enrichment(nodes: dict[str, Node]) -> dict[str, Any]:
    """Produce a complete enrichment record without any LLM."""
    rel = _relations(nodes)
    out: dict[str, Any] = {}
    for node in nodes.values():
        if node.graph == "code":
            rec: dict[str, Any] = {
                "definition": _definition_from_code(node),
                "category": _category_for(node),
            }
            if node.id in rel:
                rec["see_also"] = rel[node.id]
        else:
            rec = {
                "definition": _doc_definition(node),
                "category": _doc_category(node),
            }
        out[node.id] = rec
    return out


# ══════════════════════════════════════════════════════════════════════════
#  Render -- safe writer
# ══════════════════════════════════════════════════════════════════════════

def _range_for(name: str) -> str | None:
    first = name[:1].upper()
    if not first.isalpha():
        return "0-9"
    for lo, hi, label in RANGES:
        if lo <= first <= hi:
            return label
    return None


def _definition_for(node: Node) -> str:
    """Prefer the LLM definition; fall back to the docstring, then say so."""
    if node.definition:
        return node.definition
    if node.docstring:
        return _first_line(node.docstring)
    return "(no definition -- enrichment pending)"


def render_node(node: Node) -> list[str]:
    """Emit one entry. Every interpolated value has been through _clean()."""
    out = [f"### {node.name}", ""]
    if node.category:
        out.append(f"**Category:** {node.category}")
        out.append("")
    out.append(f"**Definition:** {_definition_for(node)}")
    out.append("")
    if node.signature:
        out.append(f"**Signature:** `{_clean(node.signature, 200)}`")
        out.append("")
    if node.see_also:
        out.append("**See Also:**")
        for rel in node.see_also:
            target = rel.replace(" ", "-")
            rng = _range_for(rel) or "A-F"
            out.append(f"- [{_clean(rel, 100)}]({rng}.md#{_anchor(rel)})")
        out.append("")
    if node.locations:
        shown = node.locations[:8]
        out.append("**Where:**")
        for loc in shown:
            out.append(f"- `{loc.file}`:{loc.line}")
        if len(node.locations) > len(shown):
            out.append(f"- ... and {len(node.locations) - len(shown)} more")
        out.append("")
    out.append(f"*kind: `{node.kind}` | checksum: `{node.checksum}`*")
    out.append("")
    out.append("---")
    out.append("")
    return out


def render_all(nodes: dict[str, Node], out_dir: Path, root: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_range: dict[str, list[Node]] = {label: [] for _, _, label in RANGES}
    by_range["0-9"] = []
    for node in sorted(nodes.values(), key=lambda n: n.name.lower()):
        rng = _range_for(node.name) or "0-9"
        by_range.setdefault(rng, []).append(node)

    written = {}
    for label, group in by_range.items():
        head = [
            "---",
            f"title: Azazael Lexicon {label}",
            f"version: {time.strftime('%Y.%m.%d')}",
            f"last_updated: {time.strftime('%Y-%m-%d')}",
            "status: GENERATED",
            "---",
            "",
            f"# {label} Lexicon",
            "",
            "> [Back to Index](INDEX.md) | "
            + " | ".join(f"[{r}]({r}.md)" for _, _, r in RANGES),
            "",
            f"Entries: {len(group)}",
            "",
        ]
        body: list[str] = []
        for node in group:
            body.extend(render_node(node))
        path = out_dir / f"{label}.md"
        path.write_text("\n".join(head + body), encoding="utf-8")
        written[label] = len(group)

    # Master index
    code_n = sum(1 for n in nodes.values() if n.graph == "code")
    doc_n = len(nodes) - code_n
    defined = sum(1 for n in nodes.values() if n.definition)
    index = [
        "---",
        "title: BIBL - Hyperlinked Lexicon",
        f"version: {time.strftime('%Y.%m.%d')}",
        f"last_updated: {time.strftime('%Y-%m-%d')}",
        "status: GENERATED",
        "---",
        "",
        "# BIBL - Hyperlinked Lexicon",
        "",
        f"> Generated by bibl_map.py on {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"> Total nodes: {len(nodes)}  (code {code_n} / doc {doc_n})",
        f"> LLM-enriched: {defined}/{len(nodes)}",
        "",
        "## Ranges",
        "",
    ]
    for _, _, label in RANGES:
        index.append(f"- [{label}]({label}.md) — {written.get(label, 0)} entries")
    index.append(f"- [0-9](0-9.md) — {written.get('0-9', 0)} entries")
    index.append("")
    (out_dir / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    return {"ranges": written, "total": len(nodes),
            "code": code_n, "doc": doc_n, "enriched": defined}


# ══════════════════════════════════════════════════════════════════════════
#  Verify -- the check that would have caught all of this
# ══════════════════════════════════════════════════════════════════════════

def verify(out_dir: Path, expected: int) -> dict:
    """Re-parse what was written and confirm nothing was lost or mangled."""
    problems: list[str] = []
    emitted = 0
    for label in [r[2] for r in RANGES] + ["0-9"]:
        path = out_dir / f"{label}.md"
        if not path.exists():
            problems.append(f"{label}.md missing")
            continue
        text = path.read_text(encoding="utf-8")
        emitted += len(re.findall(r"^### ", text, re.MULTILINE))
        # A term must never carry a newline into a heading.
        for m in re.finditer(r"^### (.+)$", text, re.MULTILINE):
            if not m.group(1).strip() or m.group(1).startswith("#"):
                problems.append(f"{label}.md: malformed heading {m.group(1)[:40]!r}")
        if re.search(r"^-\s*\*\*\s*$", text, re.MULTILINE):
            problems.append(f"{label}.md: truncated bullet emitted")

    if expected and emitted != expected:
        problems.append(f"entry count mismatch: wrote {expected}, parsed {emitted}")
    return {"emitted": emitted, "expected": expected,
            "ok": not problems, "problems": problems}


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def _default_root() -> Path:
    env = os.environ.get("AZAZAEL_ROOT")
    if env:
        return Path(env)
    return _get_project_root_safe()


def cmd_crawl(args) -> int:
    root = Path(args.root)
    out = Path(args.out)
    t0 = time.time()
    code, cstats = crawl_code(root)
    doc, dstats = crawl_docs(root)
    nodes = {**code, **doc}
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(root),
        "stats": {"code": cstats, "doc": dstats, "total": len(nodes)},
        "nodes": [asdict(n) for n in nodes.values()],
    }
    (out / "skeleton.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print("crawled %d code + %d doc = %d nodes in %.1fs"
          % (len(code), len(doc), len(nodes), time.time() - t0))
    print("  parse errors: %d   files: %d code / %d doc"
          % (cstats["parse_errors"], cstats["files"], dstats["files"]))
    print("  skeleton: %s" % (out / "skeleton.json"))
    return 0


def _load_nodes(path: Path) -> dict[str, Node]:
    """Reload the skeleton keyed by the crawler's identity, not the display
    name -- see the collision note in crawl_code()."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[str, Node] = {}
    for rec in payload.get("nodes", []):
        node = Node(
            name=rec["name"], kind=rec["kind"], graph=rec["graph"],
            raw_name=rec.get("raw_name", ""),
            bases=list(rec.get("bases", [])),
            owner=rec.get("owner", ""),
            locations=[Location(**l) for l in rec.get("locations", [])],
            signature=rec.get("signature", ""),
            docstring=rec.get("docstring", ""),
            checksum=rec.get("checksum", ""),
        )
        nodes[node.id] = node
    return nodes


def cmd_render(args) -> int:
    out = Path(args.out)
    skel = out / "skeleton.json"
    if not skel.exists():
        print("no skeleton.json -- run `crawl` first", file=sys.stderr)
        return 1
    payload = json.loads(skel.read_text(encoding="utf-8"))
    crawled = len(payload.get("nodes", []))
    nodes = _load_nodes(skel)
    overlay = merge_enrichment(
        load_enrichment(out / "enrichments.derived.json"),
        load_enrichment(out / "enrichments.json"),
    )
    applied = apply_enrichment(nodes, overlay)
    info = render_all(nodes, out / "lexicon", Path(args.root))
    # Verify against the CRAWL total, not len(nodes). Comparing against a
    # post-load count lets a loss between stages pass silently -- which is
    # exactly how 224 nodes went missing the first time.
    v = verify(out / "lexicon", crawled)
    print("crawled %d -> loaded %d -> rendered %d"
          % (crawled, len(nodes), info["total"]))
    print("  (%d code / %d doc), %d enriched"
          % (info["code"], info["doc"], applied))
    print("  ranges: %s" % info["ranges"])
    print("verify: %s (%d emitted)" % ("OK" if v["ok"] else "PROBLEMS", v["emitted"]))
    for p in v["problems"][:10]:
        print("   - %s" % p)
    return 0 if v["ok"] else 1


def cmd_verify(args) -> int:
    out = Path(args.out)
    skel = out / "skeleton.json"
    expected = 0
    if skel.exists():
        expected = len(_load_nodes(skel))
    v = verify(out / "lexicon", expected)
    print("expected %d, emitted %d" % (v["expected"], v["emitted"]))
    for p in v["problems"]:
        print("   - %s" % p)
    print("RESULT:", "PASS" if v["ok"] else "FAIL")
    return 0 if v["ok"] else 1


def cmd_stats(args) -> int:
    out = Path(args.out)
    skel = out / "skeleton.json"
    if not skel.exists():
        print("no skeleton.json", file=sys.stderr)
        return 1
    payload = json.loads(skel.read_text(encoding="utf-8"))
    print("generated: %s" % payload.get("generated"))
    print("stats: %s" % json.dumps(payload.get("stats", {}), indent=1))
    nodes = _load_nodes(skel)
    multi = [n for n in nodes.values() if len(n.locations) > 1]
    print("nodes in >1 location: %d" % len(multi))
    return 0


def cmd_derive(args) -> int:
    """Write a complete enrichment record from the code itself.

    Writes enrichments.derived.json rather than enrichments.json, so an
    LLM-authored overlay can sit on top without either side clobbering the
    other. Merge with: merge_enrichment(derived, llm).
    """
    out = Path(args.out)
    skel = out / "skeleton.json"
    if not skel.exists():
        print("no skeleton.json -- run `crawl` first", file=sys.stderr)
        return 1
    nodes = _load_nodes(skel)
    rec = derive_enrichment(nodes)
    path = out / "enrichments.derived.json"
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    with_links = sum(1 for v in rec.values() if v.get("see_also"))
    print("derived %d code nodes -> %s" % (len(rec), path.name))
    print("  with see_also links: %d" % with_links)
    return 0


def merge_enrichment(derived: dict, llm: dict) -> dict:
    """LLM entries win; derived fills the gaps. Neither destroys the other."""
    out = dict(derived)
    for key, rec in llm.items():
        if not isinstance(rec, dict):
            continue
        merged = dict(out.get(key, {}))
        for field in ("definition", "category"):
            if rec.get(field):
                merged[field] = rec[field]
        if isinstance(rec.get("see_also"), list) and rec["see_also"]:
            merged["see_also"] = rec["see_also"]
        out[key] = merged
    return out


def cmd_merge(args) -> int:
    out = Path(args.out)
    derived = load_enrichment(out / "enrichments.derived.json")
    llm = load_enrichment(out / "enrichments.json")
    merged = merge_enrichment(derived, llm)
    (out / "enrichments.merged.json").write_text(
        json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    print("merged %d derived + %d llm = %d"
          % (len(derived), len(llm), len(merged)))
    # An overlay entry that matches no node is an invented fact. Report it
    # rather than letting it inflate the count.
    known = set(derived)
    orphans = [k for k in llm if k not in known]
    if orphans:
        print("WARNING: %d llm entr(y|ies) match no crawled node -- these are "
              "inventions, not enrichments:" % len(orphans))
        for k in orphans[:20]:
            print("   - %s" % k)
    else:
        print("no orphan entries: every llm entry matches a real node")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="bibl_map",
                                description=(__doc__ or "").split("\n")[1])
    p.add_argument("--root", default=str(_default_root()))
    p.add_argument("--out", default=str(Path(__file__).resolve().parent))
    sub = p.add_subparsers(dest="cmd")
    for name in ("crawl", "derive", "merge", "render", "verify", "stats"):
        sub.add_parser(name)
    args = p.parse_args()
    fn = {"crawl": cmd_crawl, "derive": cmd_derive, "merge": cmd_merge,
          "render": cmd_render, "verify": cmd_verify,
          "stats": cmd_stats}.get(args.cmd or "")
    if not fn:
        p.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
