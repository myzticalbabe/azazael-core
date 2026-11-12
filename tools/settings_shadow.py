#!/usr/bin/env python3
"""settings_shadow.py -- HED-10: one knob, one owner.

HED-5 says settings live in `config/defaults.json` only, and every tool reads
them through `read_default(key, fallback)`. HED-8 says a new knob joins that file
rather than getting a private constant in a third tool. Neither rule is
observable by reading the config, because the failure is a mismatch between two
files, and a mismatch nobody checks drifts in silence.

This derives the mismatch from source instead of trusting either side. It walks
the tools with `ast`, collects every `read_default("key", fallback)` and
`setting("key", fallback)` call, flattens the config to dotted leaves, and
reports the three shapes the rules forbid:

  orphan_knob    the config declares a key no tool reads -- a knob that controls
                 nothing. This is HED-5's "silently-off default is
                 indistinguishable from a deleted gate", seen from the config.
  undeclared     a tool reads a key the config does not declare -- it runs on a
                 fallback nobody can see or change. This is HED-8's shadow.
  fallback_drift one key read with different fallbacks in different places, so
                 the effective default depends on which tool woke up first.
  bypass_reader  a module that hand-parses `config/defaults.json` with json.load
                 instead of read_default(), so its knobs are invisible to this
                 audit and its fallback can drift with no shared surface.
                 Measured 2026-11-05: src/CORE/data_retention.py reads
                 `retention.composite_eviction` that way.

No import, no execution: the audit is static by design, so it can run on a tree
whose tools do not import cleanly -- which is exactly when a reading is wanted.

THE VOCABULARY IS DATA (2026-11-05)
-----------------------------------
Which accessor names count as "reading the config" used to be a set literal in
this module, so a tool that invented a fourth name stayed invisible until
somebody edited the auditor -- a rule the config could not see and no gate could
check. The list now lives at `settings_shadow.readers`, is read through the
shared surface with a literal key (so this module's own knob is not an orphan to
itself), and `read_default` is always in it because that name is the surface
rather than a choice. Wrappers are still discovered from source: a function that
passes its own parameter straight to a reader is that reader's alias.

Exit: 0 when clean or advisory, 1 when clean is required (--strict) and anything
is open, 2 when the audit could not be performed.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "defaults.json"
DEFAULT_READERS = {"read_default", "setting", "get_default"}
#: The live vocabulary, replaced from `settings_shadow.readers` in main() before
#: any module is scanned, so the names a tool is judged against are the ones the
#: config declares.
READERS = set(DEFAULT_READERS)


def _surface_readers(fallback):
    """The declared vocabulary, read through the shared surface with a literal key.

    This module parses the config itself (the audit is static on purpose), so its
    own knob would be an `orphan_knob` to itself: the scan finds a read only when
    a module calls a reader with a literal string. This call IS that read. A tree
    without `defaults_profile` on the path degrades to the `--config` document.
    """
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from defaults_profile import read_default           # noqa: PLC0415
    except Exception:
        return fallback
    try:
        return read_default("settings_shadow.readers", fallback)
    except Exception:
        return fallback


def reader_vocabulary(config: dict) -> tuple:
    """(names, source) for the reader vocabulary, with `read_default` always in.

    Union rather than replace: `read_default` is the shared surface every other
    name wraps, so an overlay that lists three aliases and forgets the real
    accessor cannot silently blind the audit. Names are stripped and blanks
    dropped, because a trailing space in a JSON list is a typo that would hide a
    reader without reporting anything.
    """
    declared = _surface_readers(None)
    source = "config/defaults.json"
    if declared is None:
        node = config.get("settings_shadow")
        declared = node.get("readers") if isinstance(node, dict) else None
        source = "the --config document"
    if not isinstance(declared, list):
        return set(DEFAULT_READERS), "builtin (no settings_shadow.readers declared)"
    names = {str(name).strip() for name in declared if str(name).strip()}
    return {"read_default"} | names, source


def flatten(node, prefix="") -> list:
    """Flatten a nested config dict into dotted leaves, keeping each raw value."""
    leaves = []
    if isinstance(node, dict):
        for key, value in node.items():
            dotted = "%s.%s" % (prefix, key) if prefix else str(key)
            leaves.extend(flatten(value, dotted))
        if not node and prefix:
            leaves.append((prefix, {}))
        return leaves
    leaves.append((prefix, node))
    return leaves


def _literal(node):
    """Return (is_literal, text) for an AST node, without importing anything."""
    if isinstance(node, ast.Constant):
        return True, node.value
    try:
        return True, ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return False, ast.unparse(node)


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def local_wrappers(tree: ast.AST) -> set:
    """Function names in this module that wrap the shared reader.

    Every tool here reads settings through its own thin wrapper -- `setting()` in
    deep_dive, `_profile()` in golden_arrow -- so a scan that knows only
    `read_default` reports the wrapper's call sites as knobs nobody reads. That
    false orphan was measured on 2026-11-05: six `golden_arrow.*` keys flagged
    dead while `_profile("golden_arrow.timeout_s", ...)` read them on line 362.

    A caller of the reader is not a wrapper: `main()` passes a literal key and is
    an entry point. A wrapper passes one of its OWN PARAMETERS through, so the
    test is whether the reader's first argument is a name bound by the enclosing
    function. Measured 2026-11-05: the looser rule listed `main` as a reader in
    two modules and would have treated `main("some.key")` as a setting read.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in node.args.args + node.args.kwonlyargs}
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call) or _call_name(inner) not in READERS:
                continue
            if not inner.args:
                continue
            first = inner.args[0]
            if isinstance(first, ast.Name) and first.id in params:
                names.add(node.name)
                break
    return names


def readers_in(path: Path, declared: set | None = None) -> tuple:
    """(reads, wrappers) for one module.

    `reads` is every (key, fallback_text, line) the module takes through the
    shared reader or through a wrapper of it; `wrappers` is the local names that
    were discovered, so the audit can say which vocabulary each tool invented.

    `declared` is the config's own key set, and it exists because the first
    version kept only keys containing a dot. That quietly excluded every
    top-level knob: `ast_audit` is a bare key in `config/defaults.json`, so
    `setting("ast_audit", True)` was wired, correct and invisible -- reported as
    an orphan (measured 2026-11-05, after five other knobs were wired and this
    one alone stayed red). A key is now a setting read when it is dotted (nested)
    or when the config declares that exact name (top-level).
    """
    top_level = declared or set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        # A tool that will not parse has no readings to contribute, and saying so
        # is the difference between "no knob" and "could not look".
        return ([("__unparseable__",
                  "%s: %s" % (type(exc).__name__, str(exc)),
                  getattr(exc, "lineno", 0) or 0)], set())
    wrappers = local_wrappers(tree)
    targets = READERS | wrappers
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in targets or not node.args:
            continue
        ok, key = _literal(node.args[0])
        if not ok or not isinstance(key, str) or not key:
            continue
        if "." not in key and key not in top_level:
            continue
        fallback = "<none>"
        if len(node.args) > 1:
            _is_lit, fallback = _literal(node.args[1])
        elif node.keywords:
            for kw in node.keywords:
                if kw.arg in ("fallback", "default"):
                    _is_lit, fallback = _literal(kw.value)
        found.append((key, repr(fallback), node.lineno))
    return (found, wrappers)


def _mentions_config_literal(node: ast.AST) -> bool:
    """True when the subtree contains a string literal naming the config file."""
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                and inner.value.endswith("defaults.json")):
            return True
    return False


def _names_bound_to_config(tree: ast.AST) -> set:
    """Module names bound to a path that resolves to config/defaults.json.

    `data_retention.py` says `_DEFAULTS_CONFIG = (Path(__file__)... / "config" /
    "defaults.json")` and later `json.loads(_DEFAULTS_CONFIG.read_text(...))`.
    Matching that NAME is the difference between "this module reads the shared
    file" and "this module mentions the file in a docstring".
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _mentions_config_literal(value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _defines_reader(tree: ast.AST) -> bool:
    """True when this module IS the shared surface (it defines `read_default`).

    Only `read_default` exempts a module. The first version exempted any name in
    READERS, which let every tool that owns a thin wrapper (`setting` in
    deep_dive, `get_default` elsewhere) exempt itself and stopped the audit from
    looking at it: measured 2026-11-05, the surface list read three modules when
    exactly one of them opens the config on purpose.
    """
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "read_default" for node in ast.walk(tree))


def bypasses_in(path: Path) -> tuple:
    """(lines, is_surface) -- hand-parses of the shared config, per module.

    The first version of this detector fired on the co-occurrence of the string
    "defaults.json" and any `json.load`/`json.loads` call in the same file. It
    reported 17 bypasses on 2026-11-05, almost all of them false: `deep_dive.py`
    and `golden_arrow.py` `json.loads` their own REPORTS, and
    `defaults_profile.py` six times IS the reader. That is class-level noise, and
    a finding class an operator learns to skip is worse than no class at all --
    the real bypass (`data_retention.py`) would have been the row nobody read.

    So the test is now structural on both sides: the file must bind a NAME to a
    path literal ending `defaults.json`, and a `json.load(s)` argument must
    consume that name (or carry the literal inline). A module that defines the
    shared reader is reported as the surface, not as a bypass of it.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        # Unreadable modules are counted by readers_in; a second finding here
        # would double-report one defect.
        return ([], False)
    if _defines_reader(tree):
        # The surface has to open the file to be the surface.
        return ([], True)
    names = _names_bound_to_config(tree)
    if not names:
        return ([], False)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in ("load", "loads"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        consumes = any(isinstance(inner, ast.Name) and inner.id in names
                       for inner in ast.walk(arg))
        if consumes or _mentions_config_literal(arg):
            lines.append(node.lineno)
    return (lines, False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--root", action="append", default=[],
                    help="directory of modules to scan for readers; repeatable "
                         "(default: tools, src/CORE and src/eve_unleashed)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any finding is open")
    ap.add_argument("--json", default="", help="also write the findings here")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config)
    try:
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("settings_shadow: cannot read %s (%s). Nothing audited -- that is not "
              "a pass." % (cfg_path, exc))
        return 2
    declared = {key: value for key, value in flatten(config) if key}
    declared.pop("version", None)
    vocabulary, vocab_source = reader_vocabulary(config)
    READERS.clear()
    READERS.update(vocabulary)

    roots = [Path(r) for r in (args.root or [ROOT / "tools", ROOT / "src" / "CORE", ROOT / "src" / "eve_unleashed"])]
    scan_root = " + ".join(str(r) for r in roots)
    modules = sorted({p for root in roots for p in root.rglob("*.py")})
    if not modules:
        print("settings_shadow: no module under %s -- nothing audited, which is not "
              "a pass." % scan_root)
        return 2

    reads, unparseable, aliases, bypasses, surfaces = {}, [], {}, [], []
    for path in modules:
        found, wrappers = readers_in(path, set(declared))
        lines, is_surface = bypasses_in(path)
        if is_surface:
            surfaces.append(path.name)
        for line in lines:
            bypasses.append({
                "file": (str(path.relative_to(ROOT))
                         if str(path).startswith(str(ROOT)) else str(path)),
                "line": line})
        for name in wrappers:
            aliases.setdefault(name, []).append(path.name)
        for key, fallback, line in found:
            if key == "__unparseable__":
                unparseable.append({"file": str(path), "detail": fallback})
                continue
            reads.setdefault(key, []).append(
                {"file": str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT))
                 else str(path), "line": line, "fallback": fallback})

    findings = []
    for key in sorted(declared):
        if key not in reads:
            findings.append({"class": "orphan_knob", "key": key,
                             "detail": "declared %r but no module reads it"
                                       % declared[key]})
    for key in sorted(reads):
        if key not in declared:
            sites = ", ".join("%s:%d (%s)" % (r["file"], r["line"], r["fallback"])
                              for r in reads[key])
            findings.append({"class": "undeclared", "key": key,
                             "detail": "read at %s but absent from %s"
                                       % (sites, cfg_path.name)})
        fallbacks = {r["fallback"] for r in reads[key]}
        if len(fallbacks) > 1:
            findings.append({"class": "fallback_drift", "key": key,
                             "detail": "%d different fallbacks: %s"
                                       % (len(fallbacks),
                                          ", ".join(sorted(fallbacks)))})

    for row in bypasses:
        findings.append({
            "class": "bypass_reader", "key": Path(row["file"]).name,
            "detail": "hand-parses %s at %s:%d -- knobs it takes are invisible to "
                      "this audit and its fallback can drift silently"
                      % (cfg_path.name, row["file"], row["line"])})

    by_class = {}
    for row in findings:
        by_class[row["class"]] = by_class.get(row["class"], 0) + 1
    shared = sum(1 for key in reads if key in declared)

    print("settings_shadow: %d config leaf(s), %d key(s) read across %d module(s), "
          "%d shared, %d finding(s) [%s]  (scope: %s)"
          % (len(declared), len(reads), len(modules), shared, len(findings),
             ", ".join("%s=%d" % kv for kv in sorted(by_class.items())) or "none",
             scan_root))
    print("reader vocabulary: %s (%d name(s), from %s)"
          % (", ".join(sorted(READERS)), len(READERS), vocab_source))
    if aliases:
        print("local wrappers: %s -- one knob under several names is how a "
              "fallback drifts"
              % ", ".join("%s(%s)" % (name, "/".join(sorted(files)))
                          for name, files in sorted(aliases.items())))
    if surfaces:
        print("shared surface: %s -- exempt by definition (it IS the reader, so "
              "opening the config is its job)" % ", ".join(sorted(surfaces)))
    for row in findings:
        print("  - %-14s %-26s %s" % (row["class"], row["key"], row["detail"]))
    for row in unparseable:
        print("  - %-14s %-26s %s" % ("unreadable", Path(row["file"]).name,
                                      row["detail"]))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "config": str(cfg_path), "declared": len(declared),
            "read_keys": sorted(reads), "findings": findings,
            "unparseable": unparseable, "by_class": by_class,
            "bypasses": bypasses, "shared_surface": sorted(surfaces),
            "reader_aliases": {k: sorted(v) for k, v in aliases.items()},
            "reader_vocabulary": sorted(READERS), "vocabulary_source": vocab_source,
        }, indent=2) + "\n", encoding="utf-8")
        print("report: %s" % out)

    open_findings = len(findings) + len(unparseable)
    return 1 if (args.strict and open_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
