#!/usr/bin/env python3
# coding: utf-8
"""YGGDRASIL census — the cleave ledger as a measurement, not a paragraph.

The 2026-10-30 analysis recorded 19 trees, 8 cleave-clean, 32 shim edges and 6
blocking cycles. Sixty days of file growth later the numbers were stale and
nothing noticed, because the measurement existed only as prose. This tool is
that measurement: run it, get the same answer, diff it against the last run.

Definitions, stated once so the numbers mean something:

* **tree** — a directory directly under ``src/`` holding at least one ``.py``.
  Loose modules at ``src/`` root are counted separately, never as trees.
* **module-level import** — an ``import``/``from`` statement in ``Module.body``
  (col_offset 0, i.e. executed at import time).
* **any-import edge** — any ``import``/``from`` anywhere in the file, including
  function bodies. This is the honest cleave metric: a function-local import
  still breaks when the tree is lifted out.
* **cycle** — a direct mutual import pair (``A -> B`` and ``B -> A``) at module
  level. Longer cycles are not reported (see ``limits`` in the JSON).
* **clean** — a tree that nothing outside it imports.

Usage::

    python tools/yggdrasil_census.py            # human report
    python tools/yggdrasil_census.py --json     # machine-readable ledger
    python tools/yggdrasil_census.py --json --out census.json
    python tools/yggdrasil_census.py --selftest # negative controls on the scanner

Exit codes: 0 = scanned, 2 = root missing, 3 = self-test failed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

SCHEMA = 2

#: The shard grammar the SHARD_01 artefact states: limb -> 24 shards -> sapling.
SHARDS_PER_LIMB = 24

#: Source languages counted per tree. The cleave grammar was written for Python
#: only, so a tree that is a Rust extension, a Go service or a TS front-end was
#: invisible to every number in the ledger -- it had 0 "modules" and therefore
#: 0 saplings, which reads as "no code" rather than "code this instrument cannot
#: see". Extension -> language; anything unmapped is reported as "other".
LANGUAGES: Dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".rs": "rust", ".go": "go", ".zig": "zig",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".cuh": "cuda", ".cu": "cuda",
    ".java": "java", ".kt": "kotlin", ".cs": "dotnet", ".jl": "julia",
    ".m": "objc", ".swift": "swift", ".rb": "ruby", ".sh": "shell",
    # Source formats the drive-wide walk found in third-party trees. A `.pyx` is
    # a compile unit like a `.py`, and `.mm`/.s/.asm are source, not data: calling
    # them unclassified would report the largest C-extension projects on this
    # disk as unmeasured.
    ".mm": "objc", ".s": "asm", ".asm": "asm", ".pyx": "cython",
    ".pxd": "cython", ".pxi": "cython", ".r": "r", ".vala": "vala",
    ".lua": "lua", ".vbs": "vbscript", ".hlsl": "hlsl", ".glsl": "glsl",
    ".wgsl": "wgsl", ".hip": "cuda", ".inc": "c", ".inl": "cpp",
    ".pl": "perl", ".pm": "perl", ".tcl": "tcl", ".awk": "awk",
    # The driver layer is not .sh here: 58 .bat + 18 .ps1 were uncounted, and
    # those files are exactly where python is invoked from outside python.
    ".bat": "batch", ".cmd": "batch", ".ps1": "powershell",
    ".html": "html", ".htm": "html",
}

#: Never walked by anything: VCS metadata and the interpreter's bytecode cache.
#: These are the ONLY things the inventory refuses to count -- every other
#: directory is walked and CLASSIFIED, because a directory that is skipped is a
#: directory whose files are not accounted for, and "not in the total" reads as
#: "does not exist" to every reader downstream.
ALWAYS_SKIP = {".git", "__pycache__", ".mypy_cache", ".pytest_cache"}

#: Walked (so the total reconciles with the disk) and classified `vendored`, but
#: never parsed: third-party packages, build output, downloaded driver bundles.
#: 145 MB of node_modules and 310 MB of cargo `target/` live here; counting them
#: as "files" is honest, pretending to graph them is not.
VENDOR_DIRS = {".venv", ".venvs", "venv", "node_modules", "target", "dist",
               "build", ".build", "site-packages", "vendor", "third_party",
               "third-party", "chromedriver-win64", "graphify-out", "externals",
               # Toolchain caches a user profile carries. They are third-party
               # copies of other people's trees, which is what `vendored` says.
               ".cargo", ".rustup", ".npm", ".conda", ".m2", ".gradle",
               ".nuget", ".dotnet", ".vscode", ".cursor", "Packages"}

#: Classified `generated`: tool output, not authored. Counted, never dropped --
#: `src/.hypothesis/constants` alone held 446 files.
GENERATED_DIRS = {".hypothesis", ".inference_cache", ".ruff_cache", ".nox",
                  ".tox", ".cache", "nuitka_build_backup", ".freebuff",
                  # Windows application state: settings, caches, unpacked
                  # payloads. Authored files do not live here, and "AppData is
                  # generated" is a statement about who writes it, not a way to
                  # avoid counting it -- the count is in `files.generated`.
                  "AppData"}

#: Classified `archived`: a dated snapshot of the tree, kept for provenance. The
#: ledger already declares that an archive is counted and never import-analysed;
#: this makes the same statement about the FILES. Measured 2026-10-31: 2,085 of
#: the 2,138 unplaced files were three ONNX-Runtime source snapshots under
#: `archived/2026-05-31/`, which is a vendored copy of someone else's tree inside
#: an archive of ours.
ARCHIVE_DIRS = {"archived", "archived_unified"}

#: What the language count and the graphs skip. Derived, so the three cannot
#: drift apart: a directory is skipped by the ANALYSIS exactly when it is vendored
#: or generated, and the INVENTORY still counts it.
SKIP_DIRS = ALWAYS_SKIP | VENDOR_DIRS | GENERATED_DIRS

#: Extension -> class for files that are NOT code. A file whose extension is in
#: neither LANGUAGES nor here is `unclassified`: named in the ledger with its
#: size, and failed by --selftest, because "not in any bucket" is not "not
#: code", it is unmeasured. Measured 2026-10-31: 1,236 of the 2,892 files under
#: src/tools/scripts were invisible to every number the ledger held -- the
#: periphery reported `files: 495` for a root whose language table explained 299.
NONCODE_CLASSES: Dict[str, str] = {
    ".md": "doc", ".rst": "doc", ".txt": "text",
    ".json": "data", ".jsonl": "data", ".csv": "data", ".tsv": "data",
    ".xml": "data", ".yaml": "data", ".yml": "data", ".db": "data",
    ".sqlite": "data", ".sql": "data", ".gz": "data", ".zip": "data",
    ".pkl": "data", ".npz": "data", ".npy": "data", ".onnx": "data",
    ".wasm": "data", ".typed": "data",
    ".ini": "config", ".cfg": "config", ".conf": "config", ".toml": "config",
    ".lock": "config", ".mod": "config", ".sum": "config", ".args": "config",
    ".env": "config", ".properties": "config", ".plist": "config",
    ".png": "media", ".jpg": "media", ".jpeg": "media", ".gif": "media",
    ".svg": "media", ".ico": "media", ".mp4": "media", ".webm": "media",
    ".ttf": "media", ".woff": "media", ".woff2": "media",
    ".exe": "binary", ".dll": "binary", ".so": "binary", ".pyd": "binary",
    ".dylib": "binary", ".o": "binary", ".a": "binary", ".lib": "binary",
    ".bin": "binary", ".whl": "binary",
    ".log": "log", ".err": "log",
    ".bak": "backup", ".backup": "backup", ".backup2": "backup",
    ".orig": "backup", ".patch": "backup", ".rej": "backup",
    ".diff": "backup",
    # Formats the 2026-10-31 inventory found outside every bucket. Each one is a
    # decision, not a default: a notebook is JSON, a CMake list is a build
    # recipe, a diagram source is a document.
    ".ipynb": "data", ".pb": "data", ".ort": "data", ".ort_partial": "data",
    ".partial": "data", ".lst": "data", ".dlc": "data", ".cff": "data",
    ".jsbundle": "data", ".pt": "data", ".pth": "data", ".safetensors": "data",
    ".prof": "data", ".binlog": "data", ".pyc": "binary",
    ".jar": "binary", ".dmp": "binary", ".chromedriver": "binary",
    ".cmake": "config", ".gradle": "config", ".scons": "config",
    ".in": "config", ".config": "config", ".sln": "config", ".csproj": "config",
    ".vcxproj": "config", ".props": "config", ".targets": "config",
    ".xaml": "config", ".xib": "config", ".storyboard": "config",
    ".pbxproj": "config", ".xcworkspacedata": "config", ".xcscheme": "config",
    ".xcsettings": "config", ".appxmanifest": "config", ".natvis": "config",
    ".wprp": "config", ".runsettings": "config", ".exclude": "config",
    ".idl": "config", ".fbs": "config", ".mmd": "doc", ".tex": "doc",
    ".pdf": "doc", ".css": "style", ".scss": "style",
    ".liquid": "template", ".tmpl": "template", ".template": "template",
    ".gexf": "data", ".graphml": "data", ".example": "config",
    ".kml": "data", ".gephi": "data",
    # Second pass, 2026-10-31, from the 87 extensions the drive-wide walk left
    # over. The datacode split follows the file's role, not its parser: a
    # linker script is a build recipe, a parquet column store is data.
    ".rd": "doc", ".rmd": "doc", ".dox": "doc", ".dot": "doc",
    ".links": "doc", ".doc-base": "doc", ".pptx": "doc", ".graffle": "doc",
    ".proto": "config", ".thrift": "config", ".flatbuffers": "config",
    ".tt": "template", ".ttinclude": "template", ".j2": "template",
    ".jinja": "template", ".dockerfile": "config", ".def": "config",
    ".lds": "config", ".rc": "config", ".pp": "config", ".build": "config",
    ".install": "config", ".wrap": "config", ".pc": "config", ".gemspec": "config",
    ".podspec": "config", ".proj": "config", ".pro": "config", ".ruleset": "config",
    ".entitlements": "config", ".xcprivacy": "config", ".wxs": "config",
    ".manifest": "config", ".repo": "config", ".tcc": "config",
    ".supp": "config", ".rproj": "config", ".ucrt": "config",
    ".jsonc": "data", ".parquet": "data", ".feather": "data", ".orc": "data",
    ".lance": "data", ".ckpt": "data", ".model": "data", ".map": "data",
    ".shp": "data", ".dbf": "data", ".asc": "data", ".tcc_data": "data",
    ".sqlite-shm": "data", ".sqlite-wal": "data", ".txn": "data",
    ".metadata": "data", ".options": "data", ".sample": "data",
    ".expected_out": "data", ".out": "log", ".pid": "log", ".local": "data",
    ".win": "data", ".runningtree": "data", ".runningtree-oct05": "data",
    ".onnx_adapter": "data", ".onnx_model_with_excluded_ops": "data",
    ".lnk": "binary", ".snk": "binary", ".eot": "media", ".bmp": "media",
    ".mp3": "media", ".obj": "binary", ".tmp": "backup", ".history": "log",
    # A `.dat` is a data blob (the registry hive included); `.log1`/`.log2` are
    # the second and third rolls of a log. Neither needs reading to be placed,
    # which matters: `NTUSER.DAT` is locked by the running hive and a classifier
    # that has to open a file to know what it is reports a live system file as
    # an unknown format.
    ".dat": "data", ".log1": "log", ".log2": "log", ".log0": "log",
    ".spec": "config", ".dblite": "data", ".const": "data", ".exp": "data",
    ".stderr": "log",
}

#: Variant suffixes a backup or a downloaded artefact can carry
#: (`.bak-20261029-freebuff`, `.bak_pretechniques`, `.pre_gate_backup`). A table
#: of exact suffixes cannot hold those, and reporting them as unclassified would
#: be a hole papered over rather than a decision.
NONCODE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    (".bak", "backup"), (".backup", "backup"), (".pre_gate", "backup"),
    (".orig", "backup"), (".diff", "backup"), (".fixbak", "backup"),
)

#: Suffixless files that are still a known class. `Dockerfile` is a config
#: artefact; `PKG-INFO` is metadata. Anything suffixless and NOT here is sniffed
#: for a shebang (a script is code whatever it is called) and otherwise
#: reported unclassified rather than guessed into a bucket.
NONCODE_NAMES: Dict[str, str] = {
    "dockerfile": "config", "makefile": "config", "procfile": "config",
    "pkg-info": "data", "requirements": "config", "notice": "doc",
    "license": "doc", "readme": "doc", "authors": "doc",
    "sha256sums": "data", "sha256sum": "data", "checksums": "data",
    # Suffixless files the drive-wide walk left over: build recipes (a Rakefile
    # runs, it is still a recipe) and the metadata files a source tree carries.
    "rakefile": "config", "gemfile": "config", "podfile": "config",
    "brewfile": "config", "justfile": "config", "codeowners": "config",
    "version_number": "data", "upstream_version": "data",
    "min_onnxruntime_version": "data", "format": "data", "compat": "data",
    "usage": "data", "from": "data", "checkpoint": "data",
    "nominal_checkpoint": "data", "hip_fatbin_insert": "data",
    "copyright": "doc", "changelog": "doc",
    # A file literally named `nul` is a Windows redirect accident (`> nul` in
    # cmd), three of which are committed in this repo and its mirrors. It is not
    # a document, not code, and not a format: an artefact of the shell.
    "nul": "artifact",
}

#: A tree is a STUB when nothing imports it, it holds at most this many Python
#: modules, and none of them define a function or class. Measured 2026-10-31:
#: `SHORTCUTS` (1 module, 1 line) and `RENDERING` (3 modules of prompt dicts)
#: both counted as cleave-ready trees purely because nothing imported them, which
#: is why the analysis' own caveat about stubs needs to be a rule and not a note.
STUB_MAX_PY = 5

#: Seams that cross the language boundary. A Python package with a maturin
#: pyproject, a Cargo.toml beside it, or ctypes/cffi use is coupled to native code
#: that the import graph cannot see, so its "clean cut" is not clean.
SEAM_MARKERS = ("maturin", "ctypes", "cffi", "pyo3", "wasmtime", "grpc")

#: Non-python reference syntax followed for cross-language edges. Each pattern
#: names a real join the AST graph cannot see.
_JS_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\brequire\(\s*|\bimport\(\s*)['"]([^'"\n]+)['"]""")
_HTML_SRC_RE = re.compile(r"""<script[^>]+src\s*=\s*['"]([^'"\n]+)['"]""", re.I)
_PY_CALL_RE = re.compile(r"""\bpython[0-9.]*(?:\.exe)?\s+['"]?([^'"\s|>;&]+\.py)""", re.I)

#: Hubs that make up the shared "mycelium". Token matched case-folded.
HUBS = ("audit", "inference", "paths", "pams", "worm_registry", "memorybus")

#: CORE is the trunk by measurement, not a candidate sapling.
TRUNK = "CORE"


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------

def _imported_roots(node: ast.AST) -> List[str]:
    """Absolute module roots named by one import statement.

    ``import a.b, c`` -> ["a", "c"]; ``from a.b import x`` -> ["a"].
    Relative imports (``from . import x``) name no root: skip them.
    """
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names if a.name]
    if isinstance(node, ast.ImportFrom):
        if getattr(node, "level", 0):
            return []
        return [(node.module or "").split(".")[0]]
    return []


def _walk_imports(tree: ast.AST, module_level_only: bool) -> Iterable[str]:
    if module_level_only:
        for stmt in tree.body:
            yield from _imported_roots(stmt)
    else:
        for node in ast.walk(tree):
            yield from _imported_roots(node)


# --------------------------------------------------------------------------
# non-python graphs: analyse the other languages, do not just count them
# --------------------------------------------------------------------------

#: Extensions a JS/TS relative import can resolve to, plus the directory-index
#: forms node and bundlers accept (`./panel` -> `./panel/index.ts`).
_JS_SUFFIXES = ("", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".json")
_JS_INDEX = ("index.js", "index.mjs", "index.ts", "index.tsx", "index.jsx")

#: `python -m pkg.mod` and the `py` launcher: the two forms a batch driver uses
#: to reach python without naming a .py file.
_PY_M_RE = re.compile(r"\bpython[0-9.]*(?:\.exe)?\s+-m\s+([\w.]+)")
_PY_LAUNCHER_RE = re.compile(r"(?:^|[&|(\s])py(?:\.exe)?(?:\s+-[0-9.]+)?\s+['\"]?([^\"'\s|>;&]+\.py)",
                            re.I | re.M)

#: PowerShell launches the interpreter through a VARIABLE -- `& $python311Path -m
#: venv $EnvDir` was the one python invocation its 15 files hold, and no literal
#: `python ...` pattern can see it. Without this the file reads as "scanned, 0
#: references", which is a different claim from "one reference, to an external
#: module". Group 1 = `-m module`, group 2 = a `.py` path.
_PS_INVOKE_RE = re.compile(
    r"^\s*&\s*\$[\w:]+\s+(?:-m\s+([\w.]+)|['\"]?([^\"'\s|>;&]+\.py))",
    re.I | re.M)

#: Batch/PowerShell prefixes that name the script's own directory. `%~dp0` is the
#: commonest way a .bat reaches a sibling .py, and stripping it is what turns
#: "target-not-found" into a real edge when the sibling is under src.
_SHELL_PREFIX_RE = re.compile(r"^(?:%~dp0|%CD%|%cd%|\$PSScriptRoot[\\/]|\.\\)", re.I)

#: A leading `%VAR%`, `$VAR` or `$env:VAR` names a ROOT, not a directory.
#: Measured 2026-10-31: of the 130 references this scanner could not place, 20
#: were exactly this shape -- `%Azazael_ROOT%/WORMZ/CONCURRENCY.py`,
#: `$env:Azazael_ROOT/scripts/azazael_cli.py`. Stripping the variable and
#: resolving the rest against the script's own directory threw the root away.
_VAR_RES = (
    re.compile(r"^\$env:([A-Za-z_]\w*)[\\/]?(.*)$", re.I),
    re.compile(r"^[%$]([A-Za-z_]\w*)[%$][\\/]?(.*)$"),
    re.compile(r"^\$([A-Za-z_]\w*)[\\/](.*)$"),
)

#: Words a driver puts before the interpreter's argument; not part of a path.
_BS = chr(92)

#: Declared resolution aliases. Inference is the LAST resort, not the first:
#: `WORMZ/` meant `CORE/WORMS/` before the tree moved, and a resolver that
#: rediscovers that on every run is guessing 25 times a run instead of once.
#: A declaration that matches no reference is reported as `stale_alias` -- the
#: import-linter rule that unmatched suppressions are an error, applied here so
#: the table cannot rot into decoration.
ALIAS_FILE = Path(__file__).resolve().parent / "resolution_aliases.json"


class Aliases:
    """roots (a variable that names a directory), prefixes, and file aliases."""

    def __init__(self, data: Dict[str, Any], source: str):
        self.source = source
        self.roots = {k.upper(): v for k, v in (data.get("roots") or {}).items()}
        self.prefixes = dict(data.get("prefixes") or {})
        self.files = dict(data.get("files") or {})
        self.used: Counter[str] = Counter()
        self.declared = ([("root", k) for k in self.roots]
                         + [("prefix", k) for k in self.prefixes]
                         + [("file", k) for k in self.files])

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Aliases":
        p = Path(path) if path else ALIAS_FILE
        if not p.is_file():
            return cls({}, "none")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("[census] unreadable aliases %s: %s" % (p, exc), file=sys.stderr)
            return cls({}, "unreadable")
        return cls(data, str(p))

    def rewrites(self, var: Optional[str], tail: str) -> List[Tuple[str, List[str]]]:
        r"""(candidate path, declarations it exercised).

        Chained on purpose: `%AZAZAEL_ROOT%\WORMZ\X.py` needs BOTH the root
        and the prefix, and a table that can only apply one of them would leave
        half these references unresolved while looking complete. The declaration
        list is what makes staleness detectable -- a rewrite that borrows another
        declaration must not make the borrowed one look unused.
        """
        forms: List[Tuple[str, List[str]]] = []
        if var and var.upper() in self.roots:
            key = "root:" + var.upper()
            root_dir = self.roots[var.upper()].strip("/")
            expanded = tail if root_dir in ("", ".") else root_dir + "/" + tail
            forms.append((expanded, [key]))
        forms.append((tail, []))

        out: List[Tuple[str, List[str]]] = []
        for form, keys in forms:
            if keys:
                out.append((form, keys))
            head, _, rest = form.partition("/")
            if head in self.prefixes and rest:
                out.append((self.prefixes[head].strip("/") + "/" + rest,
                            keys + ["prefix:" + head]))
            name = form.rsplit("/", 1)[-1]
            if name in self.files:
                out.append((self.files[name], keys + ["file:" + name]))
            if form in self.files and form != name:
                out.append((self.files[form], keys + ["file:" + form]))
        return out

    def stale(self) -> List[str]:
        return ["%s:%s" % (kind, key) for kind, key in self.declared
                if not self.used["%s:%s" % (kind, key)]]


def _exact_attempt(bases: List[Path], tail: str, suffixes: Tuple[str, ...],
                   index_names: Tuple[str, ...]) -> Optional[Path]:
    """The literal path, then each extension, then each directory index."""
    candidates = ("",) + tuple(s for s in suffixes if s)
    for b in bases:
        p = b / tail
        for suffix in candidates:
            cand = Path(str(p) + suffix)
            if cand.suffix and not cand.is_file():
                continue
            if cand.is_file():
                return cand
        for index_name in index_names:
            cand = p / index_name
            if cand.is_file():
                return cand
    return None


#: Trailing-component index over ``src``: `ml_platforms/harness.py` -> the
#: files whose path ends that way. Built once per tree and cached, because the
#: alternative to a tail match is not "no answer", it is a wrong answer.
_SUFFIX_INDEX: Dict[str, Dict[str, List[Path]]] = {}
_NAME_INDEX: Dict[str, Dict[str, List[Path]]] = {}


def _skip(path: Path, root: Optional[Path]) -> bool:
    """Is this path under a skipped DIRECTORY of `root`?

    Relative parts, never absolute: checking the whole absolute path made a temp
    fixture under `AppData/Local/Temp` invisible to the entire census once
    `AppData` became a generated directory, and a class that applies to a drive
    rather than to a position in the tree is a class that skips the tests.
    """
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
    return any(p in SKIP_DIRS for p in parts)


def _indices(src: Path) -> Tuple[Dict[str, List[Path]], Dict[str, List[Path]]]:
    key = str(src)
    if key not in _SUFFIX_INDEX:
        suffix: Dict[str, List[Path]] = defaultdict(list)
        by_name: Dict[str, List[Path]] = defaultdict(list)
        for f in src.rglob("*"):
            if not f.is_file() or _skip(f, src):
                continue
            parts = f.relative_to(src).as_posix().split("/")
            by_name[f.name.lower()].append(f)
            # 2..4 components: a one-component key is a bare filename, which is
            # a weaker claim and is indexed separately so it can be counted
            # separately instead of being blended into "resolved".
            for k in range(2, min(4, len(parts)) + 1):
                suffix["/".join(parts[-k:])].append(f)
        _SUFFIX_INDEX[key] = dict(suffix)
        _NAME_INDEX[key] = dict(by_name)
    return _SUFFIX_INDEX[key], _NAME_INDEX[key]


def _strip_var(ref: str) -> Tuple[Optional[str], str]:
    """('Azazael_ROOT', 'WORMZ/X.py') for a leading `%VAR%` / `$VAR` / `$env:VAR`."""
    for pattern in _VAR_RES:
        m = pattern.match(ref)
        if m:
            return m.group(1), m.group(2)
    return None, ref


def _under(path: Path, root: Optional[Path]) -> Optional[Path]:
    """The path relative to ``root``, or None.

    A containment TEST, not an error to handle: `is_relative_to` answers it
    without an exception, so there is no silent `except: pass` here to hide a
    real failure behind.
    """
    if root is None:
        return None
    p, r = path.resolve(), root.resolve()
    return p.relative_to(r) if p.is_relative_to(r) else None


def _kind(src: Path, path: Path, root: Optional[Path]) -> str:
    """How strong the landing is: inside the tree, inside the repo, or nowhere."""
    if _under(path, src) is not None:
        return "exact"
    if _under(path, root) is not None:
        return "periphery"
    return "external-path"


def resolve_ref(src: Path, base: Path, ref: str,
                root: Optional[Path] = None,
                suffixes: Tuple[str, ...] = _JS_SUFFIXES,
                index_names: Tuple[str, ...] = _JS_INDEX,
                allow_tail: bool = False,
                aliases: Optional["Aliases"] = None) -> Tuple[Optional[Path], str]:
    """Resolve one reference, and return HOW it resolved alongside the file.

    ``(path, how)`` where ``how`` is the evidence, weakest last:

    * ``exact``      -- the literal path / a suffix candidate / a dir index, under
                        the script's dir or ``src``.
    * ``alias``      -- a DECLARED rewrite (`WORMZ/` -> `CORE/WORMS/`). Stronger
                        than inference because a human wrote it down; a
                        declaration that matches nothing is reported stale.
    * ``variable``   -- a `%VAR%`/`$VAR` prefix was removed and the remainder
                        resolved (the variable named a root).
    * ``periphery``  -- a real file under the repo root but outside ``src``: the
                        driver layer, which is production code the shards do not
                        own. 4 of them on 2026-10-31.
    * ``tail``       -- a UNIQUE match on the trailing components. This is the
                        flattened-layout repair: 78 drivers name
                        `scripts/validate_docs.py` / `ml_platforms/harness.py`
                        from a layout where those dirs sat at the root; they now
                        live under `src/ORCHESTRATION/`. Only accepted when the
                        tail identifies exactly one file, and only when the
                        caller says the syntax tolerates it.
    * ``nearest``    -- a bare name that matched more than one file, narrowed to
                        the one candidate under the referring script's own
                        directory (`HYDRA/run_hydra.bat` ->
                        `HYDRA/ml_platforms/harness.py`). Directory agreement,
                        arrived at from the referring end; still unique-or-nothing.
    * ``basename``   -- a UNIQUE filename with no directory agreement at all
                        (`WORMZ/CONCURRENCY.py` -> `CORE/WORMS/CONCURRENCY.py`).
                        Real coupling, weak evidence: it is counted, labelled,
                        and never allowed to re-tier a tree.

    Failure reasons: ``external`` (a URL), ``external-path`` (a real file
    outside the repo -- the path is returned so the caller can name it),
    ``ambiguous`` (a tail matched more than one file), ``outside-root`` (an
    absolute or `../` reference), ``target-not-found``.

    A resolver that guesses manufactures edges, so every guess above is (a)
    required to be unique and (b) reported with its own label.
    """
    raw = ref.strip().strip("'\"")
    if not raw or raw.startswith(("http://", "https://", "//", "data:", "#")):
        return None, "external"
    norm = raw.replace(_BS, "/")
    # `/C:/x` comes from a git-bash path; a bare leading `/` names the repo root.
    if re.match(r"^/[A-Za-z]:/", norm):
        norm = norm[1:]
    elif norm.startswith("/") and root is not None:
        norm = norm.lstrip("/")

    stem = _SHELL_PREFIX_RE.sub("", norm)
    var, tail = _strip_var(stem)
    if var is None:
        tail = stem

    was_absolute = Path(tail).is_absolute()
    if was_absolute:
        bases = [Path(tail).parent]
        tail = Path(tail).name
    elif var is not None:
        # The variable names a root, so the remainder starts at the root.
        bases = ([root] if root is not None else []) + [src, base]
    else:
        bases = [base, src] + ([root] if root is not None else [])

    # The literal path is ALWAYS tried first. Passing `suffixes=(".py",)` for a
    # shell call used to test only `f.py.py`, so `python ../FFF/f.py` resolved to
    # nothing and was reported as outside-src -- a resolver bug wearing the
    # costume of a finding.
    hit = _exact_attempt(bases, tail, suffixes, index_names)
    if hit is not None:
        return hit, ("variable" if var is not None else _kind(src, hit, root))

    # Declared aliases, before any inference. `WORMZ/CONCURRENCY.py` is a fact
    # about this repo, not something to rediscover per run.
    if aliases is not None:
        alias_bases = [src] + ([root] if root is not None else []) + [base]
        for rewritten, keys in aliases.rewrites(var, tail):
            hit = _exact_attempt(alias_bases, rewritten, suffixes, index_names)
            if hit is not None:
                for key in keys:
                    aliases.used[key] += 1
                return hit, "alias"

    if allow_tail and not was_absolute:
        suffix_index, name_index = _indices(src)
        # `python -m TOOLS.debug_runner` names no extension, so the tail has to
        # be tried with each candidate suffix too, or a real module reads as
        # missing purely because its ref has no `.py` in it.
        variants = [tail] + [tail + s for s in suffixes if s]
        for variant in variants:
            parts = [q for q in variant.split("/") if q not in ("", ".")]
            if not parts:
                continue
            for k in range(min(4, len(parts)), 1, -1):
                hits = suffix_index.get("/".join(parts[-k:])) or []
                if len(hits) == 1:
                    return hits[0], "tail"
                if len(hits) > 1:
                    near = _nearest(hits, base)
                    if near is not None:
                        return near, "nearest"
                    return None, "ambiguous"
            hits = name_index.get(parts[-1].lower()) or []
            if len(hits) == 1:
                return hits[0], "basename"
            if len(hits) > 1:
                near = _nearest(hits, base)
                if near is not None:
                    return near, "nearest"
                return None, "ambiguous"

    if was_absolute or tail.startswith("../"):
        return None, "outside-root"
    # A `%VAR%` root that expanded correctly and still found nothing is a
    # MISSING FILE, not an unresolvable variable: `$AZAZAEL_ROOT/BIBL/bibl.py`
    # expands to a real directory and names a file that does not exist there.
    return None, "target-not-found"


#: Paths a resolver reached that are not under src. Counted, not swallowed: a
#: rising number means the graph is being asked about a tree it does not own.
_OUTSIDE_SRC: Counter[str] = Counter()


def _tree_of(src: Path, path: Optional[Path], root: Optional[Path] = None) -> Optional[str]:
    """Which tree owns this file.

    A file under ``src`` belongs to its top-level directory. A file under the
    repo root but outside ``src`` belongs to a PERIPHERY tree, named with a
    leading `@` so it can never be mistaken for a shard: `@scripts`,
    `@tools`. Drivers live there, and a coupling to them is real coupling that
    the cleave does not carry.
    """
    if path is None:
        return None
    rel = _under(path, src)
    if rel is not None:
        return rel.parts[0] if len(rel.parts) > 1 else None
    rel = _under(path, root)
    if rel is not None:
        return "@" + rel.parts[0] if len(rel.parts) > 1 else "@"
    _OUTSIDE_SRC[str(path)] += 1
    return None


def _rel_posix(src: Path, path: Path, root: Optional[Path] = None) -> str:
    rel = _under(path, src)
    if rel is not None:
        return rel.as_posix()
    rel = _under(path, root)
    if rel is not None:
        return "@" + rel.as_posix()
    return str(path)


def _read_capped(path: Path, cap: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:cap]
    except OSError as exc:
        print("[census] unreadable %s: %s" % (path, exc), file=sys.stderr)
        return ""


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def classify_file(path: Path, root: Optional[Path] = None) -> Tuple[str, str]:
    """``(kind, label)`` for one file: code / noncode / generated / unclassified.

    One classifier, used by the module shard plan and by the file inventory, so
    the two cannot disagree about what counts as code. ``label`` is a language
    for code, a class for noncode, and -- for the last return -- the reason the
    file was not placed, which is what makes an unplaced file nameable.

    The suffixless case is not a corner: ``scripts/hooks/pre-commit`` is a bash
    program and ``module_inventory`` could not see it, so the hook that gates
    every commit was outside every shard. A shebang settles it; a dot-file is
    config; anything else is reported, not guessed.
    """
    # Directory classes apply to the path WITHIN the walked root. Matching the
    # absolute path made every file under `C:/Users/clint/AppData/...` generated --
    # including this test's own temp directory -- because `AppData` was an
    # ancestor. A class is a property of where a file sits in the tree, not of
    # which drive it happens to be on.
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
    for part in parts:
        if part in ARCHIVE_DIRS:
            return "archived", "archived"
        if part in VENDOR_DIRS:
            return "vendored", "vendored"
        # Nuitka names its output `cli.build/` and `cli.onefile-build/`, so the
        # fixed-name set above cannot see it: the SUFFIX is the signal. Same for
        # `*.egg-info` and `*.dist`.
        if (part in GENERATED_DIRS or part.endswith(".egg-info")
                or part.endswith(".build") or part.endswith("-build")
                or part.endswith(".dist")):
            return "generated", "generated"
    ext = path.suffix.lower()
    if ext in LANGUAGES:
        return "code", LANGUAGES[ext]
    if ext in NONCODE_CLASSES:
        return "noncode", NONCODE_CLASSES[ext]
    for prefix, klass in NONCODE_PREFIXES:
        if ext.startswith(prefix):
            return "noncode", klass
    # `Dockerfile.ubuntu`, `Dockerfile.alpine`: the suffix is a VARIANT, not a
    # format, so the stem decides and the suffix is kept as a label.
    stem = path.name.lower().split(".", 1)[0]
    if stem in NONCODE_NAMES:
        return "noncode", NONCODE_NAMES[stem]
    stem = path.name.lower()
    if stem in NONCODE_NAMES:
        return "noncode", NONCODE_NAMES[stem]
    if ext == "":
        try:
            head = path.open("rb").read(128)
        except OSError:
            return "unreadable", "<io-error>"
        if head.startswith(b"#!"):
            first = head.split(b"\n", 1)[0].decode("utf-8", "replace").lower()
            if "python" in first:
                return "code", "python"
            # `#!/usr/bin/env sh` has a space before `sh`, not a slash: the first
            # pass looked only for `/sh` and reported six shell scripts as an
            # unknown shebang.
            if any(s in first for s in ("bash", "zsh", "dash", "/sh", " sh",
                                        "env sh")):
                return "code", "shell"
            if "ruby" in first:
                return "code", "ruby"
            if "perl" in first:
                return "code", "perl"
            if "tclsh" in first:
                return "code", "tcl"
            if "mawk" in first or "gawk" in first or "awk" in first:
                return "code", "awk"
            if "make" in first:
                # `#!/usr/bin/make -f` is a make recipe wearing a shebang.
                return "noncode", "config"
            if "pwsh" in first or "powershell" in first:
                return "code", "powershell"
            if "node" in first:
                return "code", "javascript"
            return "unclassified", "shebang:%s" % first.strip()
        if path.name.startswith("."):
            return "noncode", "config"
        # `LICENSE-APACHE`, `README.rst`'s suffixless cousins: a licence file is
        # a document whatever qualifier it carries.
        for prefix in ("license", "licence", "readme", "changelog", "notice",
                       "authors", "copyright"):
            if stem.startswith(prefix):
                return "noncode", "doc"
        # No suffix, no shebang, no known name: sniff it like any other
        # undeclared format. Guessing a FORMAT would be wrong; saying whether the
        # bytes are text or binary is a reading, and a reading is not a guess.
        return ("noncode", "binary:unrecognised" if b"\x00" in head
                else "text:unrecognised")
    # A declared table cannot name every format a full user profile holds (the
    # 2026-10-31 walk found 1,290 undeclared suffixes: apt indexes, fonts,
    # terminal descriptions, kernel modules). Reporting them as `unclassified`
    # would be honest but useless -- 166k files and no information. So the
    # suffix is left UNDECLARED and the file is classified by what it is made
    # of, which is a weaker claim and is labelled as one: `text:unrecognised`
    # or `binary:unrecognised`, with the suffix counted in
    # `undeclared_extensions`. `unclassified` stays reserved for a file with no
    # suffix at all, where even the shape is unknown.
    try:
        head = path.open("rb").read(4096)
    except OSError:
        # Not `unclassified`: we know the path, the size and that the OS holds it
        # (the live registry hive, OneDrive's marker). `unreadable` says exactly
        # that, and it is counted separately so the gap is a number, not a shrug.
        return "unreadable", "<io-error>"
    return ("noncode", "binary:unrecognised" if b"\x00" in head
            else "text:unrecognised")


def file_inventory(roots: Iterable[Path],
                   unclassified_sample: int = 200) -> Dict[str, Any]:
    """Every file under every root, placed in exactly one bucket.

    The ledger used to answer "how many python modules" and nothing else, so
    1,236 of the 2,892 files under src/tools/scripts sat in no bucket at all and
    the total could not be reconciled with the disk. This reconciles it: for each
    root, ``total`` must equal ``code + noncode + generated + unclassified``, and
    ``unclassified`` must be empty for the claim to be "100% of files indexed".

    ``by_extension`` is the population the classifier was tested against, kept
    whole: a table that explains every file is checkable, a table that explains
    most of them is a summary pretending to be a census.
    """
    per_root: List[Dict[str, Any]] = []
    grand = Counter()
    by_ext: Counter = Counter()
    unclassified: List[Dict[str, Any]] = []
    unclassified_ext: Counter = Counter()
    undeclared_ext: Counter = Counter()
    unrecognised = 0
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            per_root.append({"root": str(root), "exists": False})
            continue
        c: Counter = Counter()
        langs: Counter = Counter()
        classes: Counter = Counter()
        for f in root.rglob("*"):
            try:
                rel = f.relative_to(root)
            except ValueError:
                continue
            if any(p in ALWAYS_SKIP for p in rel.parts) or not f.is_file():
                continue
            kind, label = classify_file(f, root)
            c[kind] += 1
            by_ext[f.suffix.lower() or "<none>"] += 1
            if label.endswith(":unrecognised"):
                # Counted, classed, and NAMED as undeclared: a weaker claim than
                # `declared`, and one the reader can see the size of.
                unrecognised += 1
                undeclared_ext[f.suffix.lower()] += 1
            if kind == "code":
                langs[label] += 1
            elif kind == "noncode":
                classes[label] += 1
            elif kind in ("unclassified", "unreadable"):
                unclassified_ext[label] += 1
                if len(unclassified) < unclassified_sample:
                    try:
                        size = f.stat().st_size
                    except OSError:
                        size = -1
                    unclassified.append({"root": str(root), "path": rel.as_posix(),
                                         "ext": f.suffix.lower() or "<none>",
                                         "reason": label, "bytes": size})
        total = sum(c.values())
        per_root.append({
            "root": str(root),
            "exists": True,
            "total": total,
            "code": c["code"],
            "noncode": c["noncode"],
            "generated": c["generated"],
            "vendored": c["vendored"],
            "archived": c["archived"],
            "unreadable": c["unreadable"],
            "unclassified": c["unclassified"],
            "adds_up": total == (c["code"] + c["noncode"] + c["generated"]
                                 + c["vendored"] + c["archived"]
                                 + c["unreadable"] + c["unclassified"]),
            "languages": dict(sorted(langs.items())),
            "noncode_by_class": dict(sorted(classes.items())),
        })
        grand.update(c)
    total = sum(grand.values())
    return {
        "roots": per_root,
        "total": total,
        "code": grand["code"],
        "noncode": grand["noncode"],
        "generated": grand["generated"],
        "vendored": grand["vendored"],
        "archived": grand["archived"],
        "unreadable": grand["unreadable"],
        "unclassified": grand["unclassified"],
        "adds_up": total == (grand["code"] + grand["noncode"] + grand["generated"]
                             + grand["vendored"] + grand["archived"]
                             + grand["unreadable"] + grand["unclassified"]),
        "every_file_indexed": grand["unclassified"] == 0 and total > 0,
        # `every_file_indexed` says no file is unplaced. `fully_declared` is the
        # stronger claim -- no file needed a content sniff either -- and the two
        # are reported apart so a fallback can never be read as a declaration.
        "unrecognised": unrecognised,
        "fully_declared": grand["unclassified"] == 0 and unrecognised == 0,
        "declared_share": (round(1 - unrecognised / total, 4) if total else None),
        "undeclared_extensions": dict(sorted(undeclared_ext.items(),
                                             key=lambda kv: (-kv[1], kv[0]))[:60]),
        "unclassified_by_extension": dict(sorted(unclassified_ext.items(),
                                                 key=lambda kv: (-kv[1], kv[0]))),
        "unclassified_rows": unclassified,
        "by_extension": dict(sorted(by_ext.items(), key=lambda kv: (-kv[1], kv[0]))),
        "note": ("every file under every root lands in exactly one bucket; "
                 "`code` is the population the per-language graphs and the shard "
                 "plan address, `generated` is tool output counted but not "
                 "authored, and `unclassified` must be empty -- a non-empty list "
                 "is a hole in this table, not a category"),
    }


def _graph_cycles(edges: Iterable[Tuple[str, str]]) -> List[List[str]]:
    """Direct mutual pairs. Same definition as the python cycle rule, so the two
    graphs are comparable rather than merely both non-empty."""
    pairs = set(edges)
    out = {tuple(sorted(p)) for p in pairs if (p[1], p[0]) in pairs and p[0] != p[1]}
    return [list(p) for p in sorted(out)]


# --------------------------------------------------------------------------
# edge recording: ONE place where a reference becomes an edge
# --------------------------------------------------------------------------

def _ambiguity_candidates(src: Path, ref: str, root: Optional[Path],
                          suffixes: Tuple[str, ...]) -> List[str]:
    """The files a refused reference could have meant.

    "ambiguous: 8" cannot be acted on; eight launchers each needing one of two
    files can be. Naming the candidates turns a refusal into a decision someone
    can make -- and a declaration (`resolution_aliases.json`) someone can record.
    """
    raw = ref.strip().strip("'\"")
    stem = _SHELL_PREFIX_RE.sub("", raw.replace(_BS, "/"))
    _, tail = _strip_var(stem)
    parts = [p for p in tail.split("/") if p not in ("", ".")]
    if not parts:
        return []
    suffix_index, name_index = _indices(src)
    for k in range(min(4, len(parts)), 1, -1):
        hits = suffix_index.get("/".join(parts[-k:]))
        if hits:
            return sorted(h.relative_to(src).as_posix() for h in hits[:4])
    hits = name_index.get(parts[-1].lower()) or []
    return sorted(h.relative_to(src).as_posix() for h in hits[:4])


def _new_acc() -> Dict[str, Any]:
    """Counters one graph accumulates. Shared so the three graphs cannot drift."""
    return {
        "edges": Counter(),
        "tree_pairs": Counter(),
        "tree_pairs_weak": Counter(),
        "periphery_pairs": Counter(),
        "tree_out": Counter(),
        "tree_in": Counter(),
        "pairs_by_language": Counter(),
        "resolved_by": Counter(),
        "unresolved": Counter(),
        "samples": [],
        "detail": [],
        "external_paths": Counter(),
    }


#: Set by census(keep_refs=True). `detail` is capped at 30 rows per graph because a
#: ledger should not carry every edge twice, but a question like "show me the 67
#: tail matches so I can name them" cannot be answered from a sample.
_KEEP_REFS = False


def _record_ref(acc: Dict[str, Any], src: Path, root: Optional[Path],
                here_file: str, here_tree: Optional[str], base: Path, ref: str,
                *, suffixes: Tuple[str, ...] = _JS_SUFFIXES,
                index_names: Tuple[str, ...] = _JS_INDEX,
                allow_tail: bool = False, line: Optional[int] = None,
                extra: Optional[Dict[str, Any]] = None,
                aliases: Optional["Aliases"] = None) -> str:
    """Resolve one reference and record it, or record WHY it did not resolve.

    Returns ``how`` so the caller can tag further detail (a shell graph wants to
    count the file it could not find) without re-deriving the outcome.

    Three outcomes, never two: an edge, a named failure, or a real path outside
    the repo. The last is not an edge (there is no tree to point at) but it is
    not a failure either -- calling a launcher that runs a Desktop script
    "broken" would be wrong, and dropping it silently is how the old ledger got
    a clean number.
    """
    target, how = resolve_ref(src, base, ref, root, suffixes, index_names,
                             allow_tail, aliases)
    note = dict(extra or {})
    if _KEEP_REFS:
        row = {"from": here_file, "ref": ref, "result": how}
        if line is not None:
            row["line"] = line
        acc.setdefault("refs", []).append(row)
    if target is None and how == "ambiguous":
        note["candidates"] = _ambiguity_candidates(src, ref, root, suffixes)
    if target is None:
        acc["unresolved"][how] += 1
        if len(acc["samples"]) < 20:
            acc["samples"].append({"from": here_file, "ref": ref, "reason": how})
        if len(acc["detail"]) < 30:
            row = {"from": here_file, "ref": ref, "result": how}
            if line is not None:
                row["line"] = line
            row.update(note)
            acc["detail"].append(row)
        return how
    if how == "external-path":
        acc["external_paths"][_rel_posix(src, target, root)] += 1
        return how
    there_file = _rel_posix(src, target, root)
    there_tree = _tree_of(src, target, root)
    if _KEEP_REFS and acc.get("refs"):
        acc["refs"][-1]["to"] = there_file
        acc["refs"][-1]["result"] = how
    acc["edges"][(here_file, there_file)] += 1
    acc["resolved_by"][how] += 1
    here_lang = LANGUAGES.get(Path(here_file).suffix.lower(), "other")
    there_lang = LANGUAGES.get(target.suffix.lower(), "other")
    acc["pairs_by_language"]["%s->%s" % (here_lang, there_lang)] += 1
    weak = how == "basename"
    if here_tree and there_tree and here_tree != there_tree:
        key = (here_tree, there_tree)
        if here_tree.startswith("@") or there_tree.startswith("@"):
            # A `@`-tree is a periphery directory (`@scripts`), not a limb. It is
            # kept out of tree_pairs so nothing downstream renders it as a tree.
            acc["periphery_pairs"][key] += 1
        else:
            acc["tree_pairs_weak" if weak else "tree_pairs"][key] += 1
    # A basename guess must not inflate "this tree holds a real module graph".
    if not weak:
        if here_tree:
            acc["tree_out"][here_tree] += 1
        if there_tree:
            acc["tree_in"][there_tree] += 1
    if len(acc["detail"]) < 30:
        row = {"from": here_file, "to": there_file, "ref": ref, "via": how}
        if line is not None:
            row["line"] = line
        row.update(note)
        acc["detail"].append(row)
    return how


def _nearest(hits: List[Path], base: Path) -> Optional[Path]:
    """The one candidate under the referring script's own directory, if unique.

    A bare `harness.py` next to a launcher means the copy in that launcher's own
    tree, so a shared directory prefix is directory agreement -- the same thing
    `tail` establishes, arrived at from the other end. Returns None when zero or
    more than one candidate is under `base`, so the caller still reports
    `ambiguous`: this narrows a tie, it does not pick a favourite.
    """
    # `abspath`, not `resolve`: resolve() follows links and can raise on a path
    # that no longer exists, and swallowing that would turn "cannot narrow" into
    # a silent default. Both sides are made absolute, so `src` may be relative.
    here = Path(os.path.abspath(base))
    under = [h for h in hits
             if here == Path(os.path.abspath(h)) or here in Path(os.path.abspath(h)).parents]
    return under[0] if len(under) == 1 else None


def _graph_common(acc: Dict[str, Any], language: str, n_files: int,
                  note: str) -> Dict[str, Any]:
    return {
        "language": language,
        "files": n_files,
        "statements": sum(acc["edges"].values()),
        "file_pairs": len(acc["edges"]),
        "tree_pairs": [{"from": a, "to": b, "refs": n}
                       for (a, b), n in sorted(acc["tree_pairs"].items())],
        "tree_pairs_weak": [{"from": a, "to": b, "refs": n}
                            for (a, b), n in sorted(acc["tree_pairs_weak"].items())],
        "periphery_pairs": [{"from": a, "to": b, "refs": n}
                            for (a, b), n in sorted(acc["periphery_pairs"].items())],
        "per_tree": {t: {"out": acc["tree_out"][t], "in": acc["tree_in"][t]}
                     for t in sorted(set(acc["tree_out"]) | set(acc["tree_in"]))},
        "pairs_by_language": dict(sorted(acc["pairs_by_language"].items())),
        "resolved_by": dict(sorted(acc["resolved_by"].items())),
        "weak_statements": acc["resolved_by"].get("basename", 0),
        "unresolved": dict(sorted(acc["unresolved"].items())),
        "unresolved_samples": acc["samples"],
        "external_paths": dict(acc["external_paths"].most_common(10)),
        "detail": acc["detail"],
        "note": note,
        # Private: popped by census() before serialisation. Contracts need the
        # file-level pairs, and a summary that keeps only counts cannot answer
        # "which shard does this edge enter".
        "_edges": [(a, b, n) for (a, b), n in acc["edges"].items()],
        # Private, and only when the caller asked: every reference with its
        # outcome, so a question about the population is answerable.
        **(({"refs": acc.get("refs") or []}) if _KEEP_REFS else {}),
    }


# --------------------------------------------------------------------------
# the python file graph: the instrument shard independence needs
# --------------------------------------------------------------------------

def _py_import_targets(node: ast.AST) -> Iterable[Tuple[str, Tuple[str, ...], int]]:
    """(dotted module, imported names, relative level) for one import node."""
    if isinstance(node, ast.Import):
        for a in node.names:
            yield a.name, (), 0
    elif isinstance(node, ast.ImportFrom):
        names = tuple(a.name for a in node.names if a.name and a.name != "*")
        yield (node.module or ""), names, getattr(node, "level", 0) or 0


def _py_module_index(src: Path, files: List[Path]) -> Dict[str, Path]:
    """Dotted-path key -> file, using the src-relative path as the package path.

    `CORE/WORMS/CONCURRENCY.py` is `CORE.WORMS.CONCURRENCY` here because src/ IS
    the package root -- the same assumption the tree-level graph already makes
    when it treats a top-level directory as a tree.
    """
    index: Dict[str, Path] = {}
    for f in files:
        rel = f.relative_to(src)
        if len(rel.parts) < 2:
            continue
        key = rel.with_suffix("").as_posix()
        index[key] = f
        if key.endswith("/__init__"):
            index[key[: -len("/__init__")]] = f
    return index


def _resolve_py_module(here_rel: str, module: str, names: Tuple[str, ...],
                       level: int, index: Dict[str, Path],
                       trees: Iterable[str]) -> Tuple[Optional[Path], str, str]:
    """(file, how, reason). Reason is set only when the file is None."""
    parts = here_rel.split("/")[:-1]
    if level:
        drop = level - 1
        base = parts[: len(parts) - drop] if drop else parts
        dotted = ".".join(base + ([module] if module else []))
        how = "relative"
    else:
        dotted = module
        how = "module"
    if not dotted:
        return None, how, "relative-needs-package" if level else "no-module"
    path = dotted.replace(".", "/")
    if path in index:
        return index[path], how, ""
    for n in names:
        cand = path + "/" + n
        if cand in index:
            return index[cand], how, ""
    top = dotted.split(".")[0]
    if top in set(trees):
        # `from CORE import paths` where CORE/paths.py does not exist: the name
        # is an attribute, not a module. Not a broken import -- a different fact.
        return None, how, "name-not-module"
    return None, how, "external" if not level else "relative-unresolved"


def record_python_file(acc: Dict[str, Any], src: Path, here_rel: str,
                       here_tree: Optional[str], tree: ast.AST,
                       index: Dict[str, Path], trees: Iterable[str]) -> None:
    """Follow every import in one file to a real module file, or count why not."""
    tree_set = set(trees)
    for node in ast.walk(tree):
        for module, names, level in _py_import_targets(node):
            target, how, reason = _resolve_py_module(here_rel, module, names,
                                                     level, index, tree_set)
            if target is None:
                acc["unresolved"][reason] += 1
                if len(acc["samples"]) < 20:
                    acc["samples"].append({"from": here_rel, "ref": module or ".",
                                           "reason": reason})
                continue
            there_rel = target.relative_to(src).as_posix()
            there_tree = there_rel.split("/")[0] if "/" in there_rel else None
            acc["edges"][(here_rel, there_rel)] += 1
            acc["resolved_by"][how] += 1
            acc["pairs_by_language"]["python->python"] += 1
            if here_tree and there_tree and here_tree != there_tree:
                acc["tree_pairs"][(here_tree, there_tree)] += 1
            if here_tree:
                acc["tree_out"][here_tree] += 1
            if there_tree:
                acc["tree_in"][there_tree] += 1
            if len(acc["detail"]) < 30:
                acc["detail"].append({"from": here_rel, "to": there_rel,
                                      "ref": module or ".", "via": how})



#: tree-sitter parsers, cached per grammar. The regex extractors cannot tell a
#: commented-out import from a live one, or `"require('./x')"` inside a string
#: from a real call -- measured on 2026-10-31 with a fixture that contains both.
_TS_PARSERS: Dict[str, Any] = {}


def _ts_parser(kind: str):
    if kind in _TS_PARSERS:
        return _TS_PARSERS[kind]
    parser = None
    try:
        import tree_sitter as ts
        if kind == "typescript":
            import tree_sitter_typescript as grammar
            language = ts.Language(grammar.language_typescript())
        else:
            import tree_sitter_javascript as grammar
            language = ts.Language(grammar.language())
        parser = ts.Parser(language)
    except (ImportError, AttributeError, TypeError, OSError) as exc:
        # A designed skip, said out loud: the regex extractor still runs, and the
        # graph reports which extractor produced its edges.
        print("[census] tree-sitter %s unavailable (%s); using regex"
              % (kind, type(exc).__name__), file=sys.stderr)
        parser = None
    _TS_PARSERS[kind] = parser
    return parser


def _first_string(node, blob: bytes) -> Optional[str]:
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "string":
            raw = blob[cur.start_byte:cur.end_byte].decode("utf-8", "replace")
            return raw.strip("'\"`")
        stack.extend(reversed(cur.children))
    return None


def js_imports(path: Path, text: str) -> Tuple[List[Tuple[str, int]], str]:
    """(specifier, line) pairs for one js/ts file, and which extractor found them."""
    kind = "typescript" if path.suffix.lower() in (".ts", ".tsx") else "javascript"
    parser = _ts_parser(kind)
    if parser is None:
        return ([(m.group(1), _line_of(text, m.start()))
                 for m in _JS_IMPORT_RE.finditer(text)], "regex")
    blob = text.encode("utf-8", "replace")
    tree = parser.parse(blob)
    out: List[Tuple[str, int]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind_name = node.type
        if kind_name in ("import_statement", "export_statement"):
            ref = _first_string(node, blob)
            if ref:
                out.append((ref, node.start_point[0] + 1))
        elif kind_name == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                name = blob[fn.start_byte:fn.end_byte].decode("utf-8", "replace")
                if name in ("require", "import"):
                    args = node.child_by_field_name("arguments")
                    ref = _first_string(args, blob) if args is not None else None
                    if ref:
                        out.append((ref, node.start_point[0] + 1))
        stack.extend(node.children)
    return out, "tree-sitter"


def build_js_graph(src: Path, js_files: List[Path],
                   root: Optional[Path] = None,
                   aliases: Optional["Aliases"] = None) -> Dict[str, Any]:
    """The js/ts module graph: file -> file, resolved through import/require.

    Counting 62 javascript files and calling it coverage is what this replaces.
    An external package (`require('puppeteer')`) is a dependency, not an edge, and
    is counted separately; only a specifier that resolves to a file is an edge.

    A relative specifier is NEVER tail-matched: `./panel` names a file next to
    the importer, so a same-named file in another tree is not the same file.
    """
    acc = _new_acc()
    external: Counter[str] = Counter()
    extractors: Counter[str] = Counter()

    for path in js_files:
        text = _read_capped(path)
        if not text:
            continue
        here_file = _rel_posix(src, path, root)
        here_tree = _tree_of(src, path, root)
        refs, extractor = js_imports(path, text)
        extractors[extractor] += 1
        for ref, line in refs:
            if not ref.startswith("."):
                external[ref.split("/")[0]] += 1
                continue
            _record_ref(acc, src, root, here_file, here_tree, path.parent, ref,
                        line=line, aliases=aliases)

    out = _graph_common(
        acc, "javascript", len(js_files),
        "only a specifier resolving to a file is an edge; a bare package name "
        "is a dependency and is counted separately. A relative specifier is "
        "resolved against its importer only, never tail-matched")
    out["cycles"] = _graph_cycles(acc["edges"])
    out["external_packages"] = dict(external.most_common(12))
    out["external_statements"] = sum(external.values())
    out["extractor"] = dict(sorted(extractors.items()))
    return out


def build_shell_graph(src: Path, script_files: List[Path],
                      root: Optional[Path] = None,
                      aliases: Optional["Aliases"] = None) -> Dict[str, Any]:
    """The shell/batch -> python call graph.

    This is the join the cleave actually trips on: 58 .bat and 15 .ps1 drive the
    python tree, and none of them is an import. Four forms are followed --
    `python <file>`, `python -m <module>`, the `py` launcher, and any of those
    behind a `%VAR%`/`$VAR` root -- and each is resolved to a real file or
    counted by reason.

    `allow_tail` is ON here and OFF for js/html, deliberately. The launcher
    layer was written against a layout where `scripts/` and `ml_platforms/` sat
    at the repo root; they now live under `src/ORCHESTRATION/`. Matching the
    trailing components recovers those references exactly, and it is accepted
    only on a UNIQUE match with at least two components agreeing.
    """
    acc = _new_acc()
    forms: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    # Coverage per SCRIPT language, not per count: powershell was listed as
    # "counted, not analysed" while it was in fact scanned, and the only way to
    # show that is to say how many references the scan found in its 18 files.
    coverage: Dict[str, Dict[str, int]] = {}

    for path in script_files:
        text = _read_capped(path)
        if not text:
            continue
        here_file = _rel_posix(src, path, root)
        here_tree = _tree_of(src, path, root)
        base = path.parent
        lang = LANGUAGES.get(path.suffix.lower(), "other")
        cov = coverage.setdefault(lang, {"files": 0, "refs": 0})
        cov["files"] += 1
        found: List[Tuple[str, str, int]] = []
        for m in _PY_CALL_RE.finditer(text):
            found.append((m.group(1), "file", _line_of(text, m.start())))
        for m in _PY_LAUNCHER_RE.finditer(text):
            found.append((m.group(1), "launcher", _line_of(text, m.start())))
        for m in _PY_M_RE.finditer(text):
            found.append((m.group(1), "module", _line_of(text, m.start())))
        for m in _PS_INVOKE_RE.finditer(text):
            if m.group(1):
                found.append((m.group(1), "module", _line_of(text, m.start())))
            else:
                found.append((m.group(2), "file", _line_of(text, m.start())))
        cov["refs"] += len(found)
        for ref, form, line in found:
            forms[form] += 1
            raw = ref.strip().strip("'\"")
            if form == "module" and "." not in raw and "/" not in raw:
                # `python -m pip install ...` names an installed package, not a
                # shard: reporting it as a missing file once put "pip" at the
                # top of the broken-launcher list.
                acc["unresolved"]["external-module"] += 1
                if len(acc["samples"]) < 20:
                    acc["samples"].append({"from": here_file, "ref": ref,
                                           "reason": "external-module"})
                continue
            how = _record_ref(acc, src, root, here_file, here_tree, base,
                              ref.replace(".", "/") if form == "module" else ref,
                              suffixes=(".py",), index_names=("__init__.py",),
                              allow_tail=True, line=line, extra={"form": form},
                              aliases=aliases)
            if how == "target-not-found":
                # The useful form: WHICH file the driver wanted. A launcher
                # pointing at a path that exists nowhere in the repo is a broken
                # launcher, not a resolution gap.
                missing[raw.replace(_BS, "/")] += 1

    out = _graph_common(
        acc, "shell", len(script_files),
        "`python <file>`, `python -m <module>`, the `py` launcher and a "
        "`%VAR%`/`$VAR` root; a call that does not resolve is counted by reason. "
        "Unlike js/html a shell path may be tail-matched, because the launcher "
        "layer was written for a flat layout, and only a UNIQUE match counts")
    out["by_form"] = dict(sorted(forms.items()))
    out["coverage"] = dict(sorted(coverage.items()))
    out["targets"] = dict(Counter(t for (_, t) in acc["edges"]).most_common(12))
    out["missing_targets"] = dict(missing.most_common(15))
    out["missing_target_count"] = len(missing)
    return out


#: `mod name;` (a declaration, not an inline `mod name { }`) and `use crate::a::b`.
_RUST_MOD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", re.M)
_RUST_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+(?:crate|super|self)((?:::[A-Za-z_]\w*)+)", re.M)


def build_rust_graph(src: Path, rust_files: List[Path],
                     root: Optional[Path] = None,
                     aliases: Optional["Aliases"] = None) -> Dict[str, Any]:
    """The rust module graph: `mod`/`use crate::` between files.

    Three files and a 326-line tree is a small graph, and building it is still
    worth the twenty lines: without it the ledger says `rust: counted, not
    analysed`, which reads as "unknown" when the true finding is "both crates are
    standalone and every `use` names an external crate". A measured zero and an
    unmeasured count are not the same statement, and only one of them can be
    acted on.

    A module path is exact by construction (`crate::a::b` names `a/b.rs`), so
    `allow_tail` is OFF: rust does not need -- and must not get -- the
    flattened-layout repair that the launcher layer needed.
    """
    acc = _new_acc()
    external: Counter[str] = Counter()
    for path in rust_files:
        text = _read_capped(path)
        if not text:
            continue
        here_file = _rel_posix(src, path, root)
        here_tree = _tree_of(src, path, root)
        found: List[Tuple[str, int]] = []
        for m in _RUST_MOD_RE.finditer(text):
            found.append((m.group(1), _line_of(text, m.start())))
        for m in _RUST_USE_RE.finditer(text):
            # `use crate::a::b::Item` may name an ITEM, not a module, so try the
            # whole path and then each shorter prefix; the resolver only accepts
            # a file that exists, so a wrong guess resolves to nothing.
            parts = [p for p in m.group(1).split("::") if p]
            for k in range(len(parts), 0, -1):
                found.append(("/".join(parts[:k]), _line_of(text, m.start())))
        for m in re.finditer(r"^\s*(?:pub\s+)?use\s+([a-z_]\w*)::", text, re.M):
            if m.group(1) not in ("crate", "super", "self", "std", "core", "alloc"):
                external[m.group(1)] += 1
        for ref, line in found:
            _record_ref(acc, src, root, here_file, here_tree, path.parent, ref,
                        suffixes=(".rs",), index_names=("mod.rs",),
                        allow_tail=False, line=line, aliases=aliases)
    out = _graph_common(
        acc, "rust", len(rust_files),
        "`mod name;` and `use crate::a::b` resolved to a file; an external crate "
        "is a dependency and is counted separately. Module paths are exact, so "
        "nothing is tail-matched")
    out["external_packages"] = dict(external.most_common(12))
    out["external_statements"] = sum(external.values())
    out["cycles"] = _graph_cycles(acc["edges"])
    return out


#: Which graph INSTRUMENTS which counted language. This is the table the
#: ledger's `uninstrumented` is read from, and the reason it is a table: the
#: previous version exempted languages by name in an inline literal
#: ("python", "javascript", "typescript", ...) instead of by whether an extractor
#: existed, so `go` and `java` were counted and then explained away for not being
#: on the list. A language is instrumented iff a graph covers it.
GRAPH_LANGUAGES: Dict[str, Tuple[str, ...]] = {
    "python": ("python",),
    "javascript": ("javascript", "typescript"),
    "shell": ("shell", "batch", "powershell"),
    "html": ("html",),
    "rust": ("rust",),
    "go": ("go",),
    "java": ("java",),
}

#: Go: a single-import line and a parenthesised block, plus the quoted paths in
#: it. Java: one `import a.b.C;` per line. Both name a MODULE PATH, which is
#: absolute, so neither is tail-matched.
_GO_IMPORT_ONE = re.compile(r'^\s*import\s+(?:[A-Za-z_.]\w*\s+)?"([^"]+)"', re.M)
_GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\((.*?)^\s*\)", re.M | re.S)
_GO_QUOTED = re.compile(r'"([^"]+)"')
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.M)

#: First path segment of an import that names the STANDARD library, not a
#: project package. Both go files here are stdlib-only, and calling `fmt` an
#: "unresolved reference" would be a false finding of exactly the kind this
#: instrument exists to avoid.
_STDLIB_ROOTS = frozenset({
    "fmt", "os", "io", "sync", "time", "flag", "runtime", "strings", "bytes",
    "sort", "math", "strconv", "errors", "bufio", "context", "encoding",
    "path", "net", "http", "log", "regexp", "json", "testing", "crypto",
    "reflect", "unicode", "hash", "archive", "compress", "container",
    "database", "debug", "embed", "expvar", "go", "html", "image", "index",
    "internal", "mime", "plugin", "text", "unsafe", "java", "javax", "sun",
    "jdk",
})
# `com` and `org` are conspicuously NOT stdlib: every java import here is
# `org.gephi.*` or `org.openide.*`, so treating the prefix as standard would have
# counted 36 third-party imports as part of the language and reported a dependency
# as nothing at all.


def _imports_of(language: str, text: str) -> List[Tuple[str, int]]:
    """``(ref, line)`` for each import in a go or java file."""
    out: List[Tuple[str, int]] = []
    if language == "go":
        for m in _GO_IMPORT_BLOCK.finditer(text):
            for q in _GO_QUOTED.finditer(m.group(1)):
                out.append((q.group(1), _line_of(text, m.start(1) + q.start())))
        for m in _GO_IMPORT_ONE.finditer(text):
            # A block import also matches the one-line pattern on its header, so
            # the quoted path is added once; the block pass covers the rest.
            out.append((m.group(1), _line_of(text, m.start())))
    else:
        for m in _JAVA_IMPORT.finditer(text):
            out.append((m.group(1), _line_of(text, m.start())))
    return out


def build_import_graph(src: Path, files: List[Path], language: str,
                       suffixes: Tuple[str, ...], dot_to_slash: bool = False,
                       root: Optional[Path] = None,
                       aliases: Optional["Aliases"] = None) -> Dict[str, Any]:
    """Go and Java: an import names a path; resolve it or declare it external.

    Both languages were COUNTED and never analysed, which is the one state this
    ledger is not allowed to leave a file in: `go: 2` with no graph reads as
    "nothing to see" and the measured truth is "two stdlib-only programs, zero
    internal references" -- a different claim, and a checkable one.

    Resolution is exact only (a module path is absolute by construction). An
    import whose first segment is stdlib or a vendor root is a dependency and is
    counted as one; anything else that does not resolve is a real finding and is
    reported unresolved with its reason.
    """
    acc = _new_acc()
    external: Counter = Counter()
    stdlib: Counter = Counter()
    for path in files:
        text = _read_capped(path)
        if not text:
            continue
        here_file = _rel_posix(src, path, root)
        here_tree = _tree_of(src, path, root)
        for ref, line in _imports_of(language, text):
            look = ref.replace(".", "/") if dot_to_slash else ref
            target, _how = resolve_ref(src, path.parent, look, root, suffixes, (),
                                      False, aliases)
            if target is None and root is not None:
                # A go import path is relative to the module root, not the file.
                target, _how = resolve_ref(src, Path(root), look, root, suffixes,
                                           (), False, aliases)
            if target is not None:
                _record_ref(acc, src, root, here_file, here_tree, path.parent, look,
                            suffixes=suffixes, index_names=(), allow_tail=False,
                            line=line, aliases=aliases)
                continue
            head = look.split("/", 1)[0]
            if head in _STDLIB_ROOTS:
                stdlib[head] += 1
            else:
                external[look] += 1
    out = _graph_common(
        acc, language, len(files),
        "an import names a module path and is resolved exactly, against the "
        "file's directory and the module root; a stdlib import is counted as "
        "stdlib and a third-party one as a dependency, so `0 internal edges` is "
        "a measurement rather than an absence of analysis")
    out["external_packages"] = dict(external.most_common(12))
    out["external_statements"] = sum(external.values())
    out["stdlib_packages"] = dict(stdlib.most_common(12))
    out["stdlib_statements"] = sum(stdlib.values())
    out["cycles"] = _graph_cycles(acc["edges"])
    return out


def build_html_graph(src: Path, html_files: List[Path],
                     root: Optional[Path] = None,
                     aliases: Optional["Aliases"] = None) -> Dict[str, Any]:
    """html -> asset edges (`<script src>`).

    142 html files made html the second-largest language here and none of it was
    analysed: a template that loads a recorder script is a join like any other.
    """
    acc = _new_acc()
    external: Counter[str] = Counter()
    for path in html_files:
        text = _read_capped(path)
        if not text:
            continue
        here_file = _rel_posix(src, path, root)
        here_tree = _tree_of(src, path, root)
        for m in _HTML_SRC_RE.finditer(text):
            ref = m.group(1)
            if re.match(r"^(https?:)?//", ref) or ref.startswith("data:"):
                external[ref.split("/")[0][:40]] += 1
                continue
            _record_ref(acc, src, root, here_file, here_tree, path.parent, ref,
                        line=_line_of(text, m.start()), aliases=aliases)
    out = _graph_common(
        acc, "html", len(html_files),
        "<script src> only; inline <script> bodies are not parsed. A relative "
        "src is resolved against the template only, never tail-matched")
    out["external_assets"] = sum(external.values())
    return out



# --------------------------------------------------------------------------
# contracts: the promises the cleave makes, checked
# --------------------------------------------------------------------------

def transitive_cycles(nodes: Iterable[str],
                      edges: Iterable[Tuple[str, str]]) -> List[List[str]]:
    """Mutually-reachable groups of ANY length.

    `cycles_real` only ever found direct pairs (A<->B), and the ledger said so in
    its own limits. A->B->C->A is the same defect one lap longer, and it is what
    a 20-tree graph actually produces. Reachability closure at this size (<=30
    nodes) is exact, cheap, and needs no cleverness.
    """
    universe = sorted(set(nodes) | {a for a, _ in edges} | {b for _, b in edges})
    reach: Dict[str, Set[str]] = {n: set() for n in universe}
    for a, b in edges:
        if a in reach and b in reach:
            reach[a].add(b)
    changed = True
    while changed:
        changed = False
        for a in universe:
            new = set(reach[a])
            for b in tuple(reach[a]):
                new |= reach[b]
            if new != reach[a]:
                reach[a] = new
                changed = True
    groups: List[List[str]] = []
    seen: Set[str] = set()
    for a in universe:
        if a in seen:
            continue
        group = [b for b in universe
                 if b == a or (b in reach[a] and a in reach[b])]
        if len(group) > 1:
            groups.append(sorted(group))
            seen.update(group)
    return groups


def _shard_of(plan: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """module path -> shard id, and shard id -> language."""
    member: Dict[str, str] = {}
    language: Dict[str, str] = {}
    for lang, blk in (plan.get("languages") or {}).items():
        for shard in blk.get("detail") or []:
            language[shard["id"]] = lang
            for mod in shard.get("modules") or []:
                member[mod] = shard["id"]
    return member, language


def shard_liftability(plan: Dict[str, Any],
                      edge_sets: Dict[str, Iterable[Tuple[str, str, int]]],
                      ) -> Dict[str, Any]:
    """For each shard: can it be lifted out today?

    A shard lifts out iff no file OUTSIDE it references a file INSIDE it. That is
    the whole promise, and it is checkable from the file-level graphs -- which is
    why the python file graph had to exist before this could be an answer rather
    than an assertion.
    """
    member, language = _shard_of(plan)
    # Keyed by (file, graph): a .bat that calls into a python shard blocks that
    # shard exactly as a python importer does, but the two need different
    # remedies, so the graph travels with the blocker instead of being dropped.
    inbound: Dict[str, Counter] = defaultdict(Counter)
    unplaced = Counter()
    for graph, edges in edge_sets.items():
        for a, b, n in edges:
            sa, sb = member.get(a), member.get(b)
            if sb is None:
                continue
            if sa is None:
                unplaced[graph] += n
                continue
            if sa != sb:
                inbound[sb][(a, graph)] += n
    blocked = []
    for shard, refs in inbound.items():
        if not refs:
            continue
        blocked.append({"shard": shard, "language": language.get(shard, "?"),
                        "modules": sum(1 for m, s in member.items() if s == shard),
                        "external_refs": sum(refs.values()),
                        "blocked_by_graph": dict(sorted(
                            Counter(g for (_a, g) in refs).items())),
                        "blockers": [{"from": a, "graph": g, "refs": v}
                                     for (a, g), v in refs.most_common(5)]})
    blocked.sort(key=lambda r: -r["external_refs"])
    total = len({s for s in member.values()})
    return {
        "shards": total,
        "liftable": total - len(blocked),
        "blocked": len(blocked),
        "blocked_detail": blocked[:15],
        "files_outside_the_plan": dict(unplaced),
        "note": ("a shard is liftable when no file outside it references a file "
                 "inside it, in ANY instrumented language -- a .bat driver that "
                 "calls into a python shard blocks it just as an importer does. "
                 "Each blocker names its graph so the two remedies stay distinct"),
    }


def affected_trees(led: Dict[str, Any], target: str,
                   limit: int = 40) -> Dict[str, Any]:
    """Everything that must be re-verified when `target` changes.

    A file gets a FILE-precise answer in every instrumented language (this is
    what the python file graph was built for); a limb name gets the limb-level
    answer, which is the shard unit. The result says which granularity it used
    instead of implying precision it does not have, and names the graph each
    hop came from -- a .bat and an importer need different remedies.
    """
    raw = target.replace(_BS, "/").strip("/")
    limbs = sorted({p.split("/")[0] for p in _ledger_paths(led)})
    tree = raw.split("/")[0] if "/" in raw else raw
    looks_like_file = "." in raw.rsplit("/", 1)[-1]
    file_edges = _ledger_file_edges(led)

    if looks_like_file and file_edges:
        return _affected_files(raw, file_edges, led.get("_files") or (), limit=limit)
    if tree not in limbs and raw in limbs:
        tree = raw

    reverse: Dict[str, Counter] = defaultdict(Counter)
    for g in [led.get("python_graph") or {}] + list((led.get("graphs") or {}).values()):
        for tp in g.get("tree_pairs") or []:
            if tp["from"] != tp["to"]:
                reverse[tp["to"]][tp["from"]] += tp["refs"]
    rows: List[Dict[str, Any]] = [{"node": tree, "depth": 0, "refs": 0,
                                   "graph": "-"}]
    seen = {tree}
    frontier = [tree]
    depth = 0
    while frontier:
        depth += 1
        nxt: List[str] = []
        for node in frontier:
            for dep, refs in reverse[node].most_common():
                if dep in seen:
                    continue
                seen.add(dep)
                rows.append({"node": dep, "depth": depth, "refs": refs,
                             "graph": "limb"})
                nxt.append(dep)
        frontier = nxt
    return {"granularity": "limb", "target": raw, "rows": rows,
            "affected_limbs": [r["node"] for r in rows[1:]],
            "affected_files": None}


def _ledger_file_edges(led: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, int]]]:
    """File-level edges per language, from whichever copy the ledger holds."""
    out: Dict[str, List[Tuple[str, str, int]]] = {}
    stored = led.get("_file_edges") or {}
    for gname, edges in stored.items():
        out.setdefault(gname, [(a, b, n) for a, b, n in edges])
    pg = (led.get("python_graph") or {}).get("_edges") or []
    if pg:
        out["python"] = [(a, b, n) for a, b, n in pg]
    return out


def _ledger_paths(led: Dict[str, Any]) -> List[str]:
    """Every file path any graph mentions, so a limb name can be recognised."""
    paths: List[str] = []
    for edges in _ledger_file_edges(led).values():
        for a, b, _n in edges:
            paths.append(a)
            paths.append(b)
    for g in (led.get("graphs") or {}).values():
        for tp in g.get("tree_pairs") or []:
            paths.append(tp["from"] + "/x")
    return paths


def _affected_files(target: str,
                    file_edges: Dict[str, List[Tuple[str, str, int]]],
                    ledger_files: Optional[Iterable[str]] = None,
                    limit: int = 40) -> Dict[str, Any]:
    """Reverse BFS over file edges, all languages, with per-hop evidence."""
    reverse: Dict[str, Counter] = defaultdict(Counter)
    known: set = set(ledger_files or ())
    for gname, edges in file_edges.items():
        for a, b, n in edges:
            known.add(a)
            known.add(b)
            if a != b:
                reverse[b][(a, gname)] += n
    if target not in known:
        # A suffix is enough to name a file unambiguously -- and it has to work
        # in both directions, because edges are stored relative to `src` while
        # a caller types `src/CORE/x.py`. Ambiguity is refused, never guessed:
        # "affected" licenses re-verification.
        def _suffix(p: str, t: str) -> bool:
            return p == t or p.endswith("/" + t) or t.endswith("/" + p)
        hits = sorted(p for p in known if _suffix(p, target))
        if len(hits) != 1:
            return {"granularity": "file", "target": target, "rows": [],
                    "affected_limbs": [], "affected_files": [],
                    "error": ("not a file in any graph" if not hits
                              else "ambiguous: %d matches" % len(hits)),
                    "candidates": hits[:10]}
        target = hits[0]

    rows: List[Dict[str, Any]] = [{"node": target, "depth": 0, "refs": 0,
                                   "graph": "-"}]
    seen = {target}
    frontier = [target]
    depth = 0
    truncated = False
    while frontier:
        depth += 1
        nxt: List[str] = []
        for node in frontier:
            for (dep, gname), refs in reverse[node].most_common():
                if dep in seen:
                    continue
                seen.add(dep)
                if len(rows) > limit:
                    truncated = True
                    continue
                rows.append({"node": dep, "depth": depth, "refs": refs,
                             "graph": gname})
                nxt.append(dep)
        frontier = nxt
    return {"granularity": "file", "target": target, "rows": rows,
            "affected_files": [r["node"] for r in rows[1:]],
            "affected_limbs": sorted({r["node"].split("/")[0] for r in rows[1:]}),
            "truncated": truncated}


def census(src: Path, archive: Optional[Path] = None,
           extras: Optional[List[Path]] = None,
           aliases: Optional["Aliases"] = None,
           keep_edges: bool = False,
           keep_refs: Optional[bool] = None) -> Dict[str, Any]:
    """Measure one ``src`` tree (and optionally an archive) into a dict.

    `extras` are roots OUTSIDE ``src`` that still ship production code --
    ``tools/``, ``scripts/``, a crate directory. They are counted by language and
    scanned for seams only: they are not trees of the cleave, but a ledger that
    says "0 rust" while the repo holds a Cargo crate is measuring the wrong
    directory and reporting the answer as a fact.
    """
    global _KEEP_REFS
    if keep_refs is not None:
        _KEEP_REFS = bool(keep_refs)
    src = Path(src)
    if not src.is_dir():
        raise FileNotFoundError(f"not a directory: {src}")

    files = sorted(p for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    trees = sorted({p.relative_to(src).parts[0] for p in files
                    if len(p.relative_to(src).parts) > 1
                    and (src / p.relative_to(src).parts[0]).is_dir()})
    tree_set = set(trees)

    per_tree: Dict[str, Dict[str, Any]] = {
        t: {"py": 0, "lines": 0, "exec_units": 0, "langs": {}, "seams": [],
            "parse_fail": 0}
        for t in trees
    }
    loose: Dict[str, Any] = {"py": 0, "lines": 0, "langs": {}}
    parse_failures: List[Dict[str, Any]] = []

    # The python FILE graph. `edges_any`/`edges_mod` can only ever say "CORE
    # imports WORMS"; a shard is 8 modules, so the shard question needs the file.
    py_acc = _new_acc()
    py_index = _py_module_index(src, files)

    edges_mod: Counter[Tuple[str, str]] = Counter()
    edges_any: Counter[Tuple[str, str]] = Counter()

    for f in files:
        rel = f.relative_to(src)
        here = rel.parts[0] if len(rel.parts) > 1 else None
        bucket = per_tree[here] if here in tree_set else loose
        bucket["py"] += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            parse_failures.append({"file": str(f), "error": f"read: {exc}"})
            if isinstance(bucket, dict) and "parse_fail" in bucket:
                bucket["parse_fail"] += 1
            continue
        bucket["lines"] += text.count("\n")
        bucket.setdefault("langs", {})["python"] = (
            bucket.setdefault("langs", {}).get("python", 0) + 1)
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            parse_failures.append({"file": str(f), "line": exc.lineno,
                                   "error": "SyntaxError"})
            if isinstance(bucket, dict) and "parse_fail" in bucket:
                bucket["parse_fail"] += 1
            continue
        # Executable behaviour, not line count: a tree of dict literals is data.
        exec_units = sum(1 for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                           ast.ClassDef)))
        if isinstance(bucket, dict) and "exec_units" in bucket:
            bucket["exec_units"] += exec_units
        record_python_file(py_acc, src, rel.as_posix(), here, tree, py_index,
                           tree_set)
        if here not in tree_set:
            continue
        for root in _walk_imports(tree, module_level_only=False):
            if root in tree_set and root != here:
                edges_any[(here, root)] += 1
        for root in _walk_imports(tree, module_level_only=True):
            if root in tree_set and root != here:
                edges_mod[(here, root)] += 1

    # --- other languages + cross-language seams ----------------------------
    # Python was the only language this ledger could see. Every other source
    # file was invisible: a Rust extension or a TS front-end contributed 0
    # modules, hence 0 shards and 0 saplings, which reads as "empty" instead of
    # "not counted". Counted here per tree, by extension, with lines by byte
    # scan (no parser needed for a line count).
    lang_totals: Dict[str, int] = {}
    lang_lines: Dict[str, int] = {}
    lang_files: List[Tuple[Path, str]] = []
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        if _skip(f, src):
            continue
        ext = f.suffix.lower()
        if ext == ".py" or ext not in LANGUAGES:
            continue
        rel = f.relative_to(src)
        bucket = per_tree.get(rel.parts[0]) if len(rel.parts) > 1 else None
        lang = LANGUAGES[ext]
        lang_totals[lang] = lang_totals.get(lang, 0) + 1
        try:
            with f.open("rb") as fh:
                n = fh.read(4_000_000).count(b"\n")
        except OSError:
            n = 0
        lang_lines[lang] = lang_lines.get(lang, 0) + n
        if isinstance(bucket, dict):
            bucket.setdefault("langs", {})[lang] = bucket["langs"].get(lang, 0) + 1
        lang_files.append((f, lang))

    # --- per-language graphs, not per-language counts -----------------------
    # A count says "62 javascript modules". These say which module imports which,
    # which js module is imported by another tree, and which batch driver calls
    # which python file -- the joins that break when a shard is lifted out.
    js_files = [f for f, lg in lang_files if lg in ("javascript", "typescript")]
    shell_files = [f for f, lg in lang_files if lg in ("shell", "batch", "powershell")]
    html_files = [f for f, lg in lang_files if lg == "html"]
    rust_files = [f for f, lg in lang_files if lg == "rust"]
    go_files = [f for f, lg in lang_files if lg == "go"]
    java_files = [f for f, lg in lang_files if lg == "java"]
    # The repo root is a resolution base, not a convenience: a driver at
    # `src/X/y.bat` saying `python scripts/validate_docs.py` means the repo
    # root, and 4 of these references land on real files there. `root=None`
    # keeps every reference inside src, which is what a standalone fixture wants.
    repo_root = src.parent if src.parent != src else None
    plan = None
    if aliases is None:
        aliases = Aliases.load()
    graphs = {
        "javascript": build_js_graph(src, js_files, repo_root, aliases),
        "shell": build_shell_graph(src, shell_files, repo_root, aliases),
        "html": build_html_graph(src, html_files, repo_root, aliases),
        "rust": build_rust_graph(src, rust_files, repo_root, aliases),
    }
    # A language with no file in src/ gets no graph row rather than a zero row:
    # "0 go files" and "go not instrumented" are different statements, and the
    # second one is what `languages.uninstrumented` exists to carry.
    if go_files:
        graphs["go"] = build_import_graph(src, go_files, "go", (".go",),
                                          root=repo_root, aliases=aliases)
    if java_files:
        graphs["java"] = build_import_graph(src, java_files, "java", (".java",),
                                            dot_to_slash=True, root=repo_root,
                                            aliases=aliases)
    # Every language a graph covers is INSTRUMENTED, derived from the table that
    # built the graphs above -- not from an inline exemption list.
    covered_languages = {lg for gname in graphs
                         for lg in GRAPH_LANGUAGES.get(gname, ())}
    covered_languages.add("python")

    # Per-tree non-python coupling, so a tree's verdict can carry it. Counts ALL
    # edges whose endpoints are known, not only cross-tree ones: an intra-tree
    # edge still means the limb holds a real module graph and is not a flat pile
    # of scripts, which is what `0 edges` was hiding for CODEX's 61 js files.
    for gname, g in graphs.items():
        for tree, counts in (g.get("per_tree") or {}).items():
            if tree not in per_tree:
                continue
            rec = per_tree[tree].setdefault("nonpython", {})
            if counts.get("out"):
                out = rec.setdefault("out", {"total": 0, "by_language": {}})
                out["total"] += counts["out"]
                out["by_language"][gname] = out["by_language"].get(gname, 0) + counts["out"]
            if counts.get("in"):
                inn = rec.setdefault("in", {"total": 0, "by_language": {}})
                inn["total"] += counts["in"]
                inn["by_language"][gname] = inn["by_language"].get(gname, 0) + counts["in"]
        for tp in g.get("tree_pairs") or []:
            src_t, dst_t, n = tp["from"], tp["to"], tp["refs"]
            if src_t in per_tree:
                per_tree[src_t].setdefault("nonpython", {}).setdefault(
                    "cross_out", {})[dst_t] = n
            if dst_t in per_tree:
                per_tree[dst_t].setdefault("nonpython", {}).setdefault(
                    "cross_in", {})[src_t] = n

    # `_edges` is popped here: contracts need the file-level pairs, the ledger
    # does not, and a 3,000-row edge list in the JSON is not a deliverable.
    edge_sets: Dict[str, List[Tuple[str, str, int]]] = {
        "python": [(a, b, n) for (a, b), n in py_acc["edges"].items()]}
    for gname, g in graphs.items():
        edge_sets[gname] = g.pop("_edges", [])
    # Every reference with its outcome, per graph -- only when asked for. `detail`
    # is a sample by design; "name the 67 tail matches" needs the population.
    ref_rows: Dict[str, List[Dict[str, Any]]] = {}
    if _KEEP_REFS:
        ref_rows["python"] = list(py_acc.get("refs") or [])
        for gname, g in graphs.items():
            ref_rows[gname] = list(g.pop("refs", []) or [])
    python_graph = _graph_common(
        py_acc, "python", len(files),
        "file -> file, resolved against the src-relative package path that the "
        "tree-level graph already assumes; `from X import y` is followed to "
        "X/y.py, and a name that is not a module is counted separately rather "
        "than called a broken import")
    python_graph["cycles"] = _graph_cycles(py_acc["edges"])
    if plan is None:
        plan = shard_plan(src)
    contracts = {
        "cycles_transitive": transitive_cycles(tree_set, [k for k in edges_mod]),
        "direct_cycle_pairs": len({(a, b) for (a, b) in edges_mod
                                    if (b, a) in edges_mod}),
        "liftability": shard_liftability(plan, edge_sets),
    }

    # --- the cross-language summary, derived from the graphs above ----------
    # This used to be a second, independent walk with its own resolver. Two
    # resolvers meant two answers to one question (258 vs 123 unresolved on the
    # same corpus), so the summary is now a projection of the graphs and there is
    # exactly one place a reference is resolved.
    cross_pair: Counter[str] = Counter()
    cross_edges: Counter[Tuple[str, str]] = Counter()
    cross_edges_weak: Counter[Tuple[str, str]] = Counter()
    cross_inbound: Dict[str, Counter] = defaultdict(Counter)
    cross_resolved_by: Counter[str] = Counter()
    cross_unresolved: Counter[str] = Counter()
    cross_detail: List[Dict[str, Any]] = []
    cross_samples: List[Dict[str, Any]] = []
    cross_external_paths: Counter[str] = Counter()
    cross_periphery: Counter[Tuple[str, str]] = Counter()
    cross_refs = 0
    cross_weak_refs = 0
    for gname, g in graphs.items():
        cross_refs += int(g.get("statements") or 0)
        cross_weak_refs += int(g.get("weak_statements") or 0)
        cross_pair.update(g.get("pairs_by_language") or {})
        for tp in g.get("tree_pairs") or []:
            cross_edges[(tp["from"], tp["to"])] += tp["refs"]
            if tp["from"] != tp["to"]:
                # STRONG edges only: a unique-basename guess must not re-tier a
                # tree, or tier A-cross becomes a rumour.
                cross_inbound[tp["to"]][tp["from"]] += tp["refs"]
        for tp in g.get("tree_pairs_weak") or []:
            cross_edges_weak[(tp["from"], tp["to"])] += tp["refs"]
        for tp in g.get("periphery_pairs") or []:
            cross_periphery[(tp["from"], tp["to"])] += tp["refs"]
        cross_resolved_by.update(g.get("resolved_by") or {})
        cross_unresolved.update(g.get("unresolved") or {})
        cross_external_paths.update(g.get("external_paths") or {})
        for row in (g.get("unresolved_samples") or [])[:20]:
            if len(cross_samples) < 20:
                item = dict(row)
                item["graph"] = gname
                cross_samples.append(item)
        for row in g.get("detail") or []:
            if len(cross_detail) < 40:
                item = dict(row)
                item["graph"] = gname
                cross_detail.append(item)

    # Seams: native or string-level coupling the AST graph cannot follow.
    for f in src.rglob("*"):
        if not f.is_file() or _skip(f, src):
            continue
        rel = f.relative_to(src)
        if len(rel.parts) < 2:
            continue
        bucket = per_tree.get(rel.parts[0])
        if bucket is None:
            continue
        name = f.name.lower()
        if name == "cargo.toml":
            bucket["seams"].append(f"cargo:{rel.as_posix()}")
        elif name == "build.rs":
            bucket["seams"].append(f"build.rs:{rel.as_posix()}")
        elif f.suffix.lower() in (".pyd", ".so", ".dll", ".dylib"):
            bucket["seams"].append(f"native:{rel.as_posix()}")
        elif name in ("pyproject.toml", "setup.py"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            hit = [m for m in SEAM_MARKERS if m in text]
            if hit:
                bucket["seams"].append(f"{rel.as_posix()}:{','.join(hit)}")

    # --- periphery: production code that is not under src/ -----------------
    periphery: Dict[str, Any] = {}
    for extra in (extras or []):
        ep = Path(extra)
        if not ep.is_dir():
            periphery[str(ep)] = {"exists": False}
            continue
        lang: Dict[str, int] = {}
        seams: List[str] = []
        n_files = 0
        by_kind: Counter = Counter()
        unplaced: List[Dict[str, Any]] = []
        go_java: Dict[str, List[Path]] = {"go": [], "java": []}
        for f in ep.rglob("*"):
            try:
                relp = f.relative_to(ep)
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in relp.parts) or not f.is_file():
                continue  # relp is already root-relative
            n_files += 1
            # Same classifier as the file inventory: a periphery root cannot
            # report `files: 495` with 196 of them in no bucket, which is what
            # `tools/` did -- 299 explained by the language table, 495 on disk.
            kind, label = classify_file(f, ep)
            by_kind[kind] += 1
            if kind == "code":
                lang[label] = lang.get(label, 0) + 1
                if label in go_java:
                    go_java[label].append(f)
            elif kind == "unclassified":
                unplaced.append({"path": relp.as_posix(),
                                 "ext": f.suffix.lower() or "<none>",
                                 "reason": label, "bytes": f.stat().st_size})
            name = f.name.lower()
            if name == "cargo.toml" or name == "build.rs":
                seams.append(f"{name}:{relp.as_posix()}")
            elif f.suffix.lower() in (".pyd", ".so", ".dll", ".dylib"):
                seams.append(f"native:{relp.as_posix()}")
        # Go and Java are indexed here too: both were counted and never analysed,
        # and the measured answer (stdlib-only / vendor-only, zero internal
        # references) is what makes `0 edges` a finding instead of a gap.
        pg: Dict[str, Any] = {}
        # NOT `files`: that name holds the python module list for the rest of
        # this function, and rebinding it here fed a .java path into the python
        # walk three hundred lines later. A shadowed loop variable is a bug that
        # only appears in the branch that runs.
        for glang, glang_files in go_java.items():
            if not glang_files:
                continue
            g = build_import_graph(ep, glang_files, glang,
                                   (".go",) if glang == "go" else (".java",),
                                   dot_to_slash=(glang == "java"), root=ep,
                                   aliases=aliases)
            g.pop("_edges", None)
            pg[glang] = {k: g[k] for k in ("files", "statements", "resolved_by",
                                          "external_packages", "external_statements",
                                          "stdlib_packages", "stdlib_statements",
                                          "unresolved")}
        # A periphery language is instrumented when a graph was built for it HERE.
        # Reading `covered_languages` alone reported go/java as uninstrumented
        # while their graphs sat in the same record -- the exact defect the table
        # was introduced to remove, one level down.
        periph_covered = set(covered_languages) | set(pg)
        periphery[str(ep)] = {"exists": True, "files": n_files,
                              "code": by_kind["code"],
                              "noncode": by_kind["noncode"],
                              "generated": by_kind["generated"],
                              "unclassified_count": by_kind["unclassified"],
                              "unclassified": unplaced[:40],
                              "languages": dict(sorted(lang.items())),
                              "graphs": pg,
                              "uninstrumented": dict(sorted(
                                  (lg, n) for lg, n in lang.items()
                                  if lg not in periph_covered)),
                              "seams": seams[:40], "seam_count": len(seams)}

    inbound_any: Dict[str, Dict[str, int]] = defaultdict(dict)
    inbound_mod: Dict[str, Dict[str, int]] = defaultdict(dict)
    for (a, b), n in edges_any.items():
        inbound_any[b][a] = n
    for (a, b), n in edges_mod.items():
        inbound_mod[b][a] = n

    # --- tiers -------------------------------------------------------------
    def _is_stub(t: str) -> bool:
        # An unparseable module is NOT a stub: its `exec_units` is 0 only because
        # the parser never got to look. The first run of this rule classified
        # the fixture's `BAD` (one broken file) as a stub, which is the same
        # class of error as reading a parse failure as an empty file.
        return (not inbound_any.get(t)
                and per_tree[t]["py"] > 0
                and per_tree[t]["parse_fail"] == 0
                and per_tree[t]["py"] <= STUB_MAX_PY
                and per_tree[t]["exec_units"] == 0)

    def _other_langs(t: str) -> Dict[str, int]:
        return {k: v for k, v in (per_tree[t].get("langs") or {}).items()
                if k != "python"}

    stub_trees = [t for t in trees if _is_stub(t)]
    clean = [t for t in trees if not inbound_any.get(t) and not _is_stub(t)]
    # A clean PYTHON cut of a tree that is mostly another language is not a clean
    # cut of the tree. Measured 2026-10-31: CODEX is 87 python + 61 javascript
    # modules, and the ledger called it cleave-today because it only ever
    # imported-analyzed the python half.
    tier_a = [t for t in clean if not _other_langs(t)]
    tier_a_unaudited = [t for t in clean if _other_langs(t)]
    tier_b = [t for t in trees if inbound_any.get(t)]
    # A clean python cut that another LANGUAGE references is still a cut whose
    # join breaks: report it beside tier A rather than inside it, because a
    # shell wrapper is not an import and must not be miscounted as one.
    tier_a_cross = [t for t in tier_a if cross_inbound.get(t)]
    cycles = sorted({tuple(sorted((a, b))) for (a, b) in edges_mod
                     if (b, a) in edges_mod})
    cycle_blocked = sorted({t for pair in cycles for t in pair})

    # --- mycelium ----------------------------------------------------------
    hub_sites: Dict[str, Dict[str, int]] = {}
    hub_tokens: Dict[str, Set[str]] = {h: set() for h in HUBS}
    tree_text: Dict[str, str] = {t: "" for t in trees}
    for f in files:
        rel = f.relative_to(src)
        if len(rel.parts) < 2 or rel.parts[0] not in tree_set:
            continue
        try:
            tree_text[rel.parts[0]] += f.read_text(
                encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
    for hub in HUBS:
        files_hit = sites = 0
        for t, text in tree_text.items():
            n = text.count(hub)
            if n:
                files_hit += 1
                sites += n
                hub_tokens[hub].add(t)
        hub_sites[hub] = {"trees": files_hit, "sites": sites}
    speaking = {t for ts in hub_tokens.values() for t in ts}

    # --- saplings ----------------------------------------------------------
    src_modules = sum(v["py"] for v in per_tree.values()) + loose["py"]
    archive_modules = 0
    archive_trees = 0
    if archive is not None and Path(archive).is_dir():
        arch = Path(archive)
        archive_modules = sum(1 for p in arch.rglob("*.py")
                              if "__pycache__" not in p.parts)
        archive_trees = sum(1 for d in arch.iterdir()
                            if d.is_dir() and any(d.rglob("*.py")))

    # --- the file ledger: nothing on disk is left in no bucket ---------------
    # Reconciliation is the point, so it is computed and stated: `code` for src
    # must equal the modules the shard plan addresses (python + other), and for
    # each periphery root it must equal the sum of its language counts. If those
    # disagree the inventory is describing a different directory than the graphs.
    files_block = file_inventory([src] + [Path(e) for e in (extras or [])])
    files_block["reconciliation"] = {
        "src_code": next((r.get("code") for r in files_block["roots"]
                          if r.get("root") == str(src)), None),
        "modules_src": src_modules,
        "other_modules": sum(lang_totals.values()),
        "matches_shard_population": (
            next((r.get("code") for r in files_block["roots"]
                  if r.get("root") == str(src)), -1)
            == src_modules + sum(lang_totals.values())),
        "periphery_matches": all(
            (r.get("code") == sum((periphery.get(r["root"], {}).get("languages") or {}).values()))
            for r in files_block["roots"][1:] if r.get("exists")),
    }

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(src),
        "definitions": {
            "tree": "directory directly under src/ holding >=1 .py",
            "any_import_edge": "import anywhere in the file (incl. function bodies)",
            "module_level_edge": "import executed at module import time",
            "clean": "no tree outside it imports it",
            "cycle": "direct mutual module-level import pair",
        },
        "totals": {
            "trees": len(trees),
            "modules_src": src_modules,
            "modules_archive": archive_modules,
            "archive_trees": archive_trees,
            "loose_modules": loose["py"],
            "lines_src": sum(v["lines"] for v in per_tree.values()) + loose["lines"],
            "parse_failures": len(parse_failures),
        },
        "trees": {
            t: {
                **per_tree[t],
                "inbound_any": inbound_any.get(t, {}),
                "inbound_module": inbound_mod.get(t, {}),
                "inbound_any_count": len(inbound_any.get(t, {})),
                "inbound_module_count": len(inbound_mod.get(t, {})),
                "is_trunk": t == TRUNK,
                "exec_units": per_tree[t]["exec_units"],
                "langs": per_tree[t]["langs"],
                "seams": sorted(set(per_tree[t]["seams"])),
                "is_stub": t in stub_trees,
                "nonpython": per_tree[t].get("nonpython") or {},
                "hubs": sorted(h for h, ts in hub_tokens.items() if t in ts),
            }
            for t in trees
        },
        "edges": {
            "module_level_pairs": len(edges_mod),
            "any_import_pairs": len(edges_any),
            "module_level_statements": sum(edges_mod.values()),
            "any_import_statements": sum(edges_any.values()),
        },
        "tiers": {
            "A_cleave_today": tier_a,
            "A_cross_language": [
                {"tree": t, "by": dict(sorted(cross_inbound[t].items()))}
                for t in tier_a_cross
            ],
            "A_mixed_unaudited": [
                {"tree": t, "py": per_tree[t]["py"],
                 "other": _other_langs(t)}
                for t in tier_a_unaudited
            ],
            "B_needs_shim": [
                {"tree": t, "inbound_any": len(inbound_any.get(t, {})),
                 "inbound_module": len(inbound_mod.get(t, {})),
                 "importers": sorted(inbound_any.get(t, {}))}
                for t in sorted(tier_b, key=lambda t: (-len(inbound_any[t]), t))
            ],
            "C_cycle_blocked": cycle_blocked,
            "D_stub": stub_trees,
        },
        "module_level_cycles": [list(c) for c in cycles],
        "cycles_real": len(cycles),
        "mycelium": {
            "hubs": hub_sites,
            "total_sites": sum(v["sites"] for v in hub_sites.values()),
            "trees_speaking": len(speaking),
            "trees_silent": sorted(set(trees) - speaking),
        },
        "saplings": {
            "shards_per_limb": SHARDS_PER_LIMB,
            "from_src": src_modules // SHARDS_PER_LIMB,
            "from_src_plus_archive": (src_modules + archive_modules) // SHARDS_PER_LIMB,
            "shard_slots_src": len(trees) * SHARDS_PER_LIMB,
            "remainder_src": src_modules % SHARDS_PER_LIMB,
            "by_language": {lang: n // SHARDS_PER_LIMB
                            for lang, n in sorted(lang_totals.items())},
            "pending_by_language": {lang: n % SHARDS_PER_LIMB
                                    for lang, n in sorted(lang_totals.items())},
        },
        "cross_language": {
            "pairs_by_language": dict(sorted(cross_pair.items())),
            "tree_pairs": [{"from": a, "to": b, "refs": n}
                           for (a, b), n in sorted(cross_edges.items())],
            "tree_pairs_weak": [{"from": a, "to": b, "refs": n}
                                for (a, b), n in sorted(cross_edges_weak.items())],
            # statements = every reference that became an edge. `tree_statements`
            # is the cross-TREE subset; summing tree pairs and calling that the
            # statement count is how the last version under-reported 43 edges.
            "statements": cross_refs,
            "tree_statements": sum(cross_edges.values()),
            "weak_statements": cross_weak_refs,
            "inbound": {t: dict(sorted(c.items())) for t, c in cross_inbound.items()},
            # HOW each reference landed. `exact` is a path, `tail` is a unique
            # trailing-component match (the flattened-layout repair), `basename`
            # is a filename with no directory agreement. A single "resolved"
            # count would hide exactly the distinction that matters.
            "resolved_by": dict(sorted(cross_resolved_by.items())),
            "outside_repo": dict(cross_external_paths.most_common(10)),
            # Coupling to code the shards do not own: a driver in src reaching a
            # real file under the repo root but outside src (`@scripts`).
            "periphery_pairs": [{"from": a, "to": b, "refs": n}
                                for (a, b), n in sorted(cross_periphery.items())],
            "detail": cross_detail,
            "unresolved": dict(sorted(cross_unresolved.items())),
            "unresolved_samples": cross_samples,
            "note": ("derived from the three graphs, not resolved a second time: "
                     "shell/batch -> python by `python <file>`/`-m`/`py`, js/ts by "
                     "import/require, html by <script src>. `resolved_by` names the "
                     "evidence for every edge; `unresolved` and `outside_repo` account "
                     "for every reference that did not become one, so the totals add "
                     "up to the references looked at"),
        },
        "graphs": graphs,
        "python_graph": python_graph,
        "contracts": contracts,
        **(({"_refs": ref_rows} if _KEEP_REFS else {})),
        **({"_file_edges": edge_sets,
            # The inventory, not only the edge endpoints: a file nobody imports is
            # still a file a question can be asked about, and answering "not in
            # any graph" for one would be a false negative, not a refusal.
            "_files": sorted({_rel_posix(src, p, None)
                              for p in (list(files) + list(js_files)
                                        + list(shell_files) + list(html_files))})
            } if keep_edges else {}),
        "aliases": {"source": aliases.source,
                    "declarations": len(aliases.declared),
                    "used": dict(sorted(aliases.used.items())),
                    "stale_alias": aliases.stale()},
        "periphery": periphery,
        "files": files_block,
        "languages": {
            "modules": dict(sorted(lang_totals.items())),
            "lines": dict(sorted(lang_lines.items())),
            "python_modules": src_modules,
            "other_modules": sum(lang_totals.values()),
            # `instrumented` is read off the graphs that actually exist and the
            # languages those graphs cover, not written by hand: the old literal
            # said ["python"] while three more languages had graphs, and the
            # exemption list beside it named languages instead of asking whether
            # an extractor existed -- so `go` and `java` were counted and then
            # explained away for not being on it.
            "instrumented": sorted(covered_languages),
            "uninstrumented": dict(sorted(
                (lg, n) for lg, n in lang_totals.items()
                if lg not in covered_languages)),
            "note": ("a language is instrumented iff a graph covers it "
                     "(GRAPH_LANGUAGES); `uninstrumented` names what is COUNTED "
                     "but not resolved, and is empty when every code file in "
                     "src/ lands in a graph. Inline <script> bodies are not "
                     "parsed, and a bare js/ts/html specifier is a package "
                     "dependency, never an edge"),
        },
        "parse_failure_detail": parse_failures[:20],
        "limits": [
            "static scan: importlib/__import__ and string-based plugin loading "
            "(the worm registry) are invisible, so runtime coupling is higher",
            "cycle detection reports direct mutual pairs per language; the "
            "transitive closure in `contracts.cycles_transitive` is tree-level only",
            "archive is counted, never import-analysed",
            "dependencies are package-level for js/ts and html: a bare specifier is "
            "counted as a package dependency, never an edge",
        ],
    }


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def render(led: Dict[str, Any]) -> str:
    t = led["totals"]
    out: List[str] = []
    add = out.append
    add(f"YGGDRASIL census — {led['root']}")
    add(f"generated {led['generated_at']}")
    add("")
    add(f"trees {t['trees']}  modules {t['modules_src']}  lines {t['lines_src']:,}"
        f"  parse failures {t['parse_failures']}"
        + (f"  archive modules {t['modules_archive']}" if t["modules_archive"] else ""))
    add("")
    add(f"TIER A — cleave today, python-audited ({len(led['tiers']['A_cleave_today'])})")
    add("  " + ", ".join(led["tiers"]["A_cleave_today"]))
    mixed = led["tiers"].get("A_mixed_unaudited") or []
    add("")
    add(f"TIER A-MIXED — python-clean, other languages UNAUDITED ({len(mixed)})")
    if not mixed:
        add("  — none")
    for row in mixed:
        other = ", ".join(f"{k} {v}" for k, v in row["other"].items())
        add(f"  {row['tree']:<16}{row['py']:>4} py + {other} — cut verdict rests on the python half")
    add("")
    cross = led.get("cross_language") or {}
    xa = led["tiers"].get("A_cross_language") or []
    add(f"TIER A-CROSS — python-clean but referenced from another language ({len(xa)})")
    if not xa:
        add("  — none")
    for row in xa:
        by = ", ".join(f"{k} {v} ref(s)" for k, v in row["by"].items())
        add(f"  {row['tree']:<16}{by}")
    add("")
    add("LANGUAGE GRAPHS — resolved file by file, not counted")
    for key, g in (led.get("graphs") or {}).items():
        ext = g.get("external_statements", g.get("external_assets", 0))
        cycles = len(g.get("cycles") or [])
        add(f"  {key:<11}{g['files']:>4} file(s) {g['statements']:>5} edge(s) "
            f"{g['file_pairs']:>4} pair(s)  {len(g.get('tree_pairs') or []):>2} tree pair(s)"
            f"  {cycles} cycle(s)  {ext} external"
            + (f"  weak {g['weak_statements']}" if g.get("weak_statements") else "")
            + (f"  forms {g['by_form']}" if g.get("by_form") else ""))
        unres = g.get("unresolved") or {}
        if unres:
            add("    unresolved: " + ", ".join(f"{k} {v}" for k, v in unres.items()))
    add("")
    add(f"CROSS-LANGUAGE — {cross.get('statements', 0)} reference(s) resolved, "
        f"{cross.get('tree_statements', 0)} of them crossing a tree boundary "
        f"({len(cross.get('tree_pairs') or [])} tree pair(s))")
    for pair, n in (cross.get("pairs_by_language") or {}).items():
        add(f"  {pair:<24}{n:>5}")
    # The evidence, not just the total: a number that does not say how it was
    # reached cannot be checked, and `tail`/`basename` are the two classes a
    # reader needs to see separately.
    by = cross.get("resolved_by") or {}
    if by:
        add("  resolved by: " + ", ".join(f"{k} {v}" for k, v in by.items())
            + f"  ({cross.get('weak_statements', 0)} by filename alone)")
    outside = cross.get("outside_repo") or {}
    if outside:
        add(f"  outside the repo, not an edge: {sum(outside.values())}")
    peri = cross.get("periphery_pairs") or []
    if peri:
        add("  to periphery code: "
            + ", ".join(f"{p['from']}->{p['to']} {p['refs']}" for p in peri))
    unres = cross.get("unresolved") or {}
    if unres:
        add("  unresolved (universe for the count above): "
            + ", ".join(f"{k} {v}" for k, v in unres.items()))
    pg = led.get("python_graph") or {}
    if pg:
        add(f"PYTHON FILE GRAPH — {pg['files']} file(s), {pg['statements']} import(s), "
            f"{pg['file_pairs']} file pair(s), {len(pg.get('tree_pairs') or [])} tree pair(s), "
            f"{len(pg.get('cycles') or [])} cycle(s)")
        unres = pg.get("unresolved") or {}
        if unres:
            add("  not followed (universe): "
                + ", ".join(f"{k} {v}" for k, v in unres.items()))
    con = led.get("contracts") or {}
    if con:
        lf = con.get("liftability") or {}
        add("CONTRACTS — what the cleave promises, checked")
        add(f"  cycles of any length     {len(con.get('cycles_transitive') or [])} "
            f"(direct pairs {con.get('direct_cycle_pairs', 0)})")
        add(f"  shards liftable today    {lf.get('liftable', 0)}/{lf.get('shards', 0)} "
            f"({lf.get('blocked', 0)} blocked by a reference from outside)")
        for row in (lf.get("blocked_detail") or [])[:5]:
            who = ", ".join(f"{b['from']} x{b['refs']} [{b.get('graph', '?')}]"
                            for b in row["blockers"][:2])
            add(f"    {row['shard']:<20}{row['external_refs']:>5} ref(s)  {who}")
    al = led.get("aliases") or {}
    if al.get("declarations"):
        stale = al.get("stale_alias") or []
        add(f"ALIASES — {al['declarations']} declared, "
            + ("all exercised" if not stale else "STALE: " + ", ".join(stale)))
    add(f"TIER B — needs a shim ({len(led['tiers']['B_needs_shim'])} trees, "
        f"{led['edges']['any_import_pairs']} tree-pairs, "
        f"{led['edges']['any_import_statements']} import statements)")
    for row in led["tiers"]["B_needs_shim"]:
        trunk = "  (trunk)" if row["tree"] == TRUNK else ""
        add(f"  {row['tree']:<16}{row['inbound_any']:>3} in   "
            f"{row['inbound_module']:>3} at module level{trunk}")
    add("")
    add(f"TIER C — cycle-blocked ({led['cycles_real']})"
        + (": " + ", ".join(" <-> ".join(c) for c in led["module_level_cycles"])
           if led["module_level_cycles"] else " — none"))
    add("")
    stub = led["tiers"]["D_stub"]
    add(f"TIER D — stub, not a cleave ({len(stub)})"
        + (" (nothing imports it, <=%d modules, no function or class)" % STUB_MAX_PY
           if stub else " — none"))
    for name in stub:
        row = led["trees"][name]
        add(f"  {name:<16}{row['py']:>3} modules  {row['lines']:>6} lines  "
            f"exec units {row['exec_units']}")
    add("")
    myc = led["mycelium"]
    add(f"MYCELIUM — {myc['trees_speaking']}/{t['trees']} trees speak >=1 hub, "
        f"{myc['total_sites']} sites; silent: "
        + (", ".join(myc["trees_silent"]) or "none"))
    for hub, v in myc["hubs"].items():
        add(f"  {hub:<15}{v['trees']:>4} trees {v['sites']:>7} sites")
    add("")
    s = led["saplings"]
    add(f"SAPLINGS — {s['from_src']} from src alone, "
        f"{s['from_src_plus_archive']} with the archive "
        f"({s['shards_per_limb']} shards/limb, remainder {s['remainder_src']})")
    langs = led["languages"]["modules"]
    if langs:
        add("")
        add("LANGUAGES — counted, not import-analysed")
        lines = led["languages"]["lines"]
        for lang, n in sorted(langs.items(), key=lambda kv: -kv[1]):
            add(f"  {lang:<12}{n:>6} modules {lines.get(lang, 0):>9,} lines "
                f"{n // s['shards_per_limb']:>3} saplings")
        add(f"  python       {s['from_src']:>6} saplings across {led['totals']['trees']} trees "
            f"(import-analysed)")
    peri = led.get("periphery") or {}
    if peri:
        add("")
        add("PERIPHERY — production code outside src/ (counted, never import-analysed)")
        for path, rec in peri.items():
            if not rec.get("exists"):
                add(f"  {path} — missing")
                continue
            langs = ", ".join(f"{k} {v}" for k, v in (rec["languages"] or {}).items()) or "none"
            add(f"  {path} — {rec['files']:,} files; {langs}; "
                f"{rec['seam_count']} seam marker(s)")
            for s in rec["seams"][:4]:
                add(f"      {s}")
    seams = [(t, r["seams"]) for t, r in led["trees"].items() if r.get("seams")]
    if seams:
        add("")
        add(f"CROSS-LANGUAGE SEAMS — {len(seams)} tree(s) coupled outside the AST graph")
        for t, ss in sorted(seams):
            add(f"  {t:<16}{len(ss):>3} marker(s)  e.g. {ss[0][:70]}")
    if t["parse_failures"]:
        add("")
        add("PARSE FAILURES")
        for row in led["parse_failure_detail"]:
            add(f"  {row['file']}:{row.get('line')} {row['error']}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# shard plan: the same limb -> 24 shards grammar, per language, as data
# --------------------------------------------------------------------------

def module_inventory(src: Path) -> Dict[str, List[str]]:
    """Every source file under ``src`` by language. Python first if present."""
    src = Path(src)
    out: Dict[str, List[str]] = {}
    for f in src.rglob("*"):
        if not f.is_file() or _skip(f, src):
            continue
        # The classifier, not a suffix lookup: a script with a shebang and no
        # suffix is code, and leaving it out of the shard plan is how a file ends
        # up counted by the inventory and addressed by nothing.
        kind, lang = classify_file(f, src)
        if kind != "code":
            continue
        out.setdefault(lang, []).append(f.relative_to(src).as_posix())
    return {k: sorted(v) for k, v in sorted(out.items())}


def shard_plan(src: Path, shard_size: int = SHARDS_PER_LIMB) -> Dict[str, Any]:
    """Assign every module of every language to exactly one shard.

    The cleave grammar (limb -> 24 shards -> sapling) was written for Python,
    where a shard is a directory of importable modules. Counting JS/TS/shell
    modules produces a *number*, not a shard: nothing says which files belong
    to which. This assigns them, ordered by tree and path so a shard stays
    inside one limb wherever the arithmetic allows, and reports the shards that
    still straddle a tree -- a straddling shard cannot be lifted out cleanly in
    any language, python included.
    """
    inv = module_inventory(src)
    langs: Dict[str, Any] = {}
    for lang, mods in inv.items():
        # Two allocations, measured together. `continuous` is the naive global
        # 24-chunk: cheap, but a chunk that spans two trees is a shard nobody
        # can lift out. `limb_aligned` chunks inside each tree, which removes
        # straddling at the cost of underfilled shards. Both numbers are
        # reported so the choice is a decision, not an assumption.
        keyed = sorted(mods, key=lambda p: (p.split("/")[0], p))
        cont = [keyed[i:i + shard_size] for i in range(0, len(keyed), shard_size)]
        cont_straddle = sum(1 for c in cont if len({p.split("/")[0] for p in c}) > 1)

        by_tree: Dict[str, List[str]] = {}
        for p in keyed:
            by_tree.setdefault(p.split("/")[0], []).append(p)
        shards: List[Dict[str, Any]] = []
        for tree, members in by_tree.items():
            for j in range(0, len(members), shard_size):
                chunk = members[j:j + shard_size]
                shards.append({"id": f"{lang[:3]}-{tree.lower()}-{j // shard_size:02d}",
                               "tree": tree, "trees": [tree], "straddles": False,
                               "size": len(chunk), "modules": chunk})
        langs[lang] = {
            "modules": len(keyed),
            "shards": len(shards),
            "saplings": len(shards) // shard_size,
            "pending": len(keyed) % shard_size,
            "instrumented": any(lang in cover for cover in GRAPH_LANGUAGES.values()),
            "graph": next((g for g, cover in GRAPH_LANGUAGES.items() if lang in cover),
                          None),
            "straddling_shards": sum(1 for s in shards if s["straddles"]),
            "underfilled": sum(1 for s in shards if s["size"] < shard_size // 2),
            "wasted_slots": len(shards) * shard_size - len(keyed),
            "continuous": {"shards": len(cont), "straddling": cont_straddle},
            "detail": shards,
        }
    return {"schema": "azazael.shard_plan/2",
            "root": str(src), "shard_size": shard_size,
            "mode": "limb_aligned (a shard never spans two trees)",
            "languages": langs,
            "totals": {"modules": sum(v["modules"] for v in langs.values()),
                       "shards": sum(v["shards"] for v in langs.values()),
                       "straddling_shards": sum(v["straddling_shards"]
                                                 for v in langs.values()),
                       "continuous_straddling": sum(v["continuous"]["straddling"]
                                                     for v in langs.values()),
                       "wasted_slots": sum(v["wasted_slots"]
                                           for v in langs.values())}}


# --------------------------------------------------------------------------
# self-test: a scanner that has never been proven wrong is not an oracle
# --------------------------------------------------------------------------

def selftest() -> int:
    """Negative controls: known-shaped fixtures the scanner MUST classify right."""
    import tempfile

    failures: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "src"
        (root / "AAA").mkdir(parents=True)
        (root / "BBB").mkdir()
        (root / "CCC").mkdir()
        (root / "DDD").mkdir()
        # AAA imports BBB at module level; BBB imports AAA inside a function.
        (root / "AAA" / "a.py").write_text(
            "import BBB.b\nfrom CCC import c\nprint('audit')\n", encoding="utf-8")
        (root / "BBB" / "b.py").write_text(
            "def f():\n    from AAA import a\n    return a\n", encoding="utf-8")
        (root / "CCC" / "c.py").write_text("c = 1\n", encoding="utf-8")
        (root / "DDD" / "d.py").write_text("import BBB.b\n", encoding="utf-8")
        (root / "loose.py").write_text("x = 1\n", encoding="utf-8")
        (root / "BAD").mkdir()
        (root / "BAD" / "broken.py").write_text("def (:\n", encoding="utf-8")
        # EEE: python-clean but carries a TS module the ledger cannot analyse.
        (root / "EEE").mkdir()
        (root / "EEE" / "e.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / "EEE" / "x.ts").write_text("export const x = 1;\n", encoding="utf-8")
        # FFF is python-clean, but a shell driver runs one of its files: the
        # join is real and the AST graph cannot see it.
        (root / "FFF").mkdir()
        (root / "FFF" / "f.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / "GGG").mkdir()
        (root / "GGG" / "run.sh").write_text("#!/bin/sh\npython ../FFF/f.py\n",
                                             encoding="utf-8")
        (root / "GGG" / "mod.sh").write_text("#!/bin/sh\npython -m FFF.f\n",
                                             encoding="utf-8")
        # A js graph with one real edge, one cross-tree import and one external
        # package: the three outcomes must not be collapsed into "imports: 3".
        (root / "III").mkdir()
        (root / "III" / "widget.js").write_text("export const w = 1;\n", encoding="utf-8")
        (root / "III" / "use.js").write_text(
            "import { w } from './widget.js';\nexport default w;\n", encoding="utf-8")
        (root / "III" / "tool.js").write_text(
            "const p = require('puppeteer');\nmodule.exports = p;\n", encoding="utf-8")
        (root / "HHH").mkdir()
        (root / "HHH" / "app.js").write_text(
            "const { w } = require('../III/widget.js');\n", encoding="utf-8")

        # The launcher layer, in the shape measured 2026-10-31: a driver written
        # for a FLAT layout (`scripts/x.py` at the root) after those dirs moved a
        # level down. A unique trailing-component match must recover it -- and a
        # js relative import in the same shape must NOT be recovered, because
        # `./mirror.js` names a file next to the importer, not any file by that name.
        (root / "CCC" / "scripts").mkdir()
        (root / "CCC" / "scripts" / "flat.py").write_text("print(1)\n",
                                                         encoding="utf-8")
        (root / "KKK").mkdir()
        (root / "KKK" / "mirror.js").write_text("export const m = 1;\n",
                                                 encoding="utf-8")
        (root / "III" / "shot.js").write_text(
            "import { m } from './mirror.js';\n", encoding="utf-8")
        # MMM is clean and referenced only by a BASENAME form (`WORMZ\m.py`):
        # real coupling, weak evidence, so it must not be re-tiered as A-CROSS.
        (root / "MMM").mkdir()
        (root / "MMM" / "m.py").write_text("def m():\n    return 1\n", encoding="utf-8")
        (root / "LLL").mkdir()
        (root / "LLL" / "flat.bat").write_text("python scripts\\flat.py\r\n",
                                                encoding="utf-8")
        (root / "LLL" / "weak.bat").write_text("python WORMZ\\m.py\r\n",
                                                encoding="utf-8")

        led = census(root)
        tiers = led["tiers"]
        b_trees = [r["tree"] for r in tiers["B_needs_shim"]]

        # Nobody imports BAD (unparseable) or DDD; AAA and CCC are imported,
        # CCC at module level by AAA and AAA by BBB from inside a function.
        # BAD is unparseable and DDD is a data stub: neither is a cleave, and
        # BAD must be excluded from the stub tier too (unknown != empty).
        if tiers["A_cleave_today"] != ["BAD", "FFF", "MMM"]:
            failures.append(f"tier A = {tiers['A_cleave_today']!r}, "
                            f"expected ['BAD', 'FFF', 'MMM']")
        # The exact error the 2026-10-30 analysis made: a function-local
        # back-import must NOT register as a cycle.
        if led["cycles_real"] != 0:
            failures.append(f"function-local back-import counted as a cycle: "
                            f"{led['module_level_cycles']!r}")
        # ...but it MUST keep AAA out of tier A, because the edge still breaks
        # when the tree is lifted out.
        if "AAA" not in b_trees:
            failures.append(f"function-local importer AAA missing from tier B: {b_trees!r}")
        if "CCC" not in b_trees:
            failures.append("CCC (module-level import by AAA) missing from tier B")
        if led["trees"]["AAA"]["inbound_module_count"] != 0:
            failures.append("AAA counted as module-level inbound, expected 0")
        if led["totals"]["loose_modules"] != 1:
            failures.append(f"loose modules = {led['totals']['loose_modules']}, expected 1")
        if led["totals"]["parse_failures"] != 1:
            failures.append(f"parse failures = {led['totals']['parse_failures']}, expected 1")
        if led["mycelium"]["trees_silent"] != ["BAD", "BBB", "CCC", "DDD", "EEE",
                                               "FFF", "MMM"]:
            failures.append(f"silent trees = {led['mycelium']['trees_silent']!r}")
        cross = led.get("cross_language") or {}
        # TWO now, not one: `python ../FFF/f.py` and `python -m FFF.f` both name
        # FFF/f.py, and the old cross walk never looked at the `-m` form at all
        # (only _PY_CALL_RE, which requires a .py in the text).
        if cross.get("pairs_by_language", {}).get("shell->python") != 2:
            failures.append(f"cross-language shell->python = "
                            f"{cross.get('pairs_by_language')!r}, expected 2 "
                            f"(file form + -m form)")
        # The flat-layout repair: `python scripts\flat.py` from LLL finds
        # CCC/scripts/flat.py by a UNIQUE two-component tail, and says so.
        if (cross.get("resolved_by") or {}).get("tail") != 1:
            failures.append("tail resolution = %r, expected 1 (flat-layout driver)"
                            % (cross.get("resolved_by"),))
        # ...while the same shape in js must stay unresolved: a relative import
        # is a sibling reference, not a filename to be hunted for elsewhere.
        js_u = (led.get("graphs") or {}).get("javascript") or {}
        if (js_u.get("unresolved") or {}).get("target-not-found") != 1:
            failures.append("js relative import was tail-matched: %r"
                            % (js_u.get("unresolved"),))
        # A basename-only match is counted, labelled, and kept OUT of tiering.
        if (cross.get("resolved_by") or {}).get("basename") != 1:
            failures.append("basename match = %r, expected 1"
                            % (cross.get("resolved_by"),))
        if "MMM" in [r["tree"] for r in led["tiers"].get("A_cross_language") or []]:
            failures.append("a basename-only reference re-tiered MMM as A-CROSS")
        across = [r["tree"] for r in led["tiers"].get("A_cross_language") or []]
        if across != ["FFF"]:
            failures.append(f"A-CROSS = {across!r}, expected ['FFF']")
        if "FFF" not in led["tiers"]["A_cleave_today"]:
            failures.append("a shell reference must not evict FFF from tier A")
        g = led.get("graphs") or {}
        js = g.get("javascript") or {}
        # Two internal edges (use->widget, HHH/app->widget). tool->puppeteer is a
        # dependency, so counting it here would be the old bug in a new function.
        if js.get("statements") != 2:
            failures.append("js graph statements = %r, expected 2 internal edges"
                            % js.get("statements"))
        if js.get("external_packages", {}).get("puppeteer") != 1:
            failures.append("js external package not isolated: %r"
                            % (js.get("external_packages"),))
        if {"from": "HHH", "to": "III", "refs": 1} not in (js.get("tree_pairs") or []):
            failures.append("js cross-tree edge HHH->III missing: %r"
                            % (js.get("tree_pairs"),))
        sh = g.get("shell") or {}
        # Three file-form calls (run.sh, flat.bat, weak.bat) and one -m form.
        if sh.get("by_form") != {"file": 3, "module": 1}:
            failures.append("shell call forms = %r, expected {'file': 3, 'module': 1}"
                            % (sh.get("by_form"),))
        if sh.get("statements") != 4:
            failures.append("shell graph statements = %r, expected 4" % sh.get("statements"))
        plan = shard_plan(root)
        for lang, blk in plan["languages"].items():
            flat = [m for s in blk["detail"] for m in s["modules"]]
            if len(flat) != len(set(flat)) or len(flat) != blk["modules"]:
                failures.append(f"shard plan {lang}: modules duplicated or dropped")
            if any(len(s["modules"]) > plan["shard_size"] for s in blk["detail"]):
                failures.append(f"shard plan {lang}: shard over shard_size")
        if led["saplings"]["from_src"] != 0:
            failures.append(f"saplings below one shard should be 0: "
                            f"{led['saplings']['from_src']}")
        # A tree of dict literals that nothing imports must NOT be offered as a
        # cleave candidate: without this control, adding 3 files of prompt data
        # silently grew Tier A from 9 to 10 (measured 2026-10-31).
        if led["tiers"]["D_stub"] != ["DDD"]:
            failures.append(f"stub tier = {led['tiers']['D_stub']!r}, expected ['DDD'] "
                            f"(1 module, no defs, nothing imports it)")
        if "DDD" in led["tiers"]["A_cleave_today"]:
            failures.append("stub tree DDD leaked into tier A")
        # A tree with an unanalysed TS module must be reported as mixed, never
        # certified as a clean python cut.
        mixed = [r["tree"] for r in led["tiers"].get("A_mixed_unaudited") or []]
        if mixed != ["EEE"]:
            failures.append(f"mixed tier = {mixed!r}, expected ['EEE']")
        if "EEE" in led["tiers"]["A_cleave_today"]:
            failures.append("mixed tree EEE leaked into tier A")

        # --- 24: a DECLARED alias upgrades a weak edge, and only an unused
        # declaration is stale. Without this control the alias table can rot:
        # `weak.bat` says `python WORMZ\m.py`, which is a basename guess until
        # someone declares that WORMZ means MMM.
        aliased = Aliases({"prefixes": {"WORMZ": "MMM", "NOSUCH": "CCC"}},
                          "selftest")
        led_alias = census(root, aliases=aliased)
        alias_pairs = {(r["from"], r["to"]): r["refs"]
                       for r in (led_alias.get("cross_language") or {}).get("tree_pairs") or []}
        if not alias_pairs.get(("LLL", "MMM")):
            failures.append("declared alias WORMZ->MMM produced no strong edge "
                            "(pairs: %r)" % (sorted(alias_pairs),))
        aliases_blk = led_alias.get("aliases") or {}
        if "prefix:NOSUCH" not in (aliases_blk.get("stale_alias") or []):
            failures.append("unused alias NOSUCH not reported stale: %r"
                            % (aliases_blk.get("stale_alias"),))
        if "prefix:WORMZ" in (aliases_blk.get("stale_alias") or []):
            failures.append("a USED alias was reported stale -- the table would "
                            "look dead while it is doing the resolving")

        # --- 25/26: `--affected` is file-precise, and refuses an unknown file
        # rather than answering with the whole repo. DDD/d.py imports BBB/b.py
        # which imports AAA/a.py, so AAA/a.py must reach DDD at depth 2.
        led_files = census(root, keep_edges=True)
        aff = affected_trees(led_files, "AAA/a.py")
        if aff.get("granularity") != "file":
            failures.append(f"affected granularity = {aff.get('granularity')!r}, "
                            f"expected 'file'")
        depth_of = {r["node"]: r["depth"] for r in aff.get("rows") or []}
        if depth_of.get("BBB/b.py") != 1 or depth_of.get("DDD/d.py") != 2:
            failures.append(f"affected depths wrong: {depth_of!r}")
        missing = affected_trees(led_files, "no_such_file.py")
        if not missing.get("error") or missing.get("rows"):
            failures.append("unknown file was answered instead of refused: %r"
                            % (missing,))

        # --- 27: a blocker names its graph. GGG's two shell drivers call into
        # FFF, so FFF's shard is blocked BY A SHELL EDGE -- the reason a python
        # import graph alone was never a liftability answer.
        lf = (led.get("contracts") or {}).get("liftability") or {}
        shell_blocked = [r for r in lf.get("blocked_detail") or []
                         if any(b["from"].startswith("GGG/") for b in r["blockers"])]
        if not shell_blocked:
            failures.append("no shard reported as blocked by a shell driver: %r"
                            % ([r["shard"] for r in lf.get("blocked_detail") or []],))
        elif not all(b.get("graph") == "shell" for b in shell_blocked[0]["blockers"]
                     if b["from"].startswith("GGG/")):
            failures.append("shell blockers do not name their graph: %r"
                            % (shell_blocked[0],))

        # --- 28/29/30: a SECOND root, so these fixtures cannot perturb the
        # counts the controls above assert. Rust modules, a PowerShell launch
        # through a variable, and a bare name that only its own tree explains.
        r2 = Path(tmp) / "r2" / "src"
        (r2 / "RR" / "src").mkdir(parents=True)
        (r2 / "RR" / "src" / "lib.rs").write_text(
            "mod util;\nuse rayon::prelude::*;\n", encoding="utf-8")
        (r2 / "RR" / "src" / "util.rs").write_text("pub fn u() {}\n", encoding="utf-8")
        (r2 / "PP").mkdir(parents=True)
        (r2 / "PP" / "helper.py").write_text("x = 1\n", encoding="utf-8")
        (r2 / "PP" / "run.ps1").write_text(
            "$py = 'python.exe'\n& $py -m venv $Env\n& $py .\\helper.py\n",
            encoding="utf-8")
        (r2 / "AA" / "ml_platforms").mkdir(parents=True)
        (r2 / "BB" / "ml_platforms").mkdir(parents=True)
        (r2 / "AA" / "ml_platforms" / "harness.py").write_text("a = 1\n", encoding="utf-8")
        (r2 / "BB" / "ml_platforms" / "harness.py").write_text("b = 1\n", encoding="utf-8")
        # `python harness.py` names no directory, and BOTH trees hold one: only
        # the copy under the driver's own tree is the driver's copy.
        (r2 / "AA" / "run.bat").write_text("python harness.py\r\n", encoding="utf-8")

        led2 = census(r2)
        rs = (led2.get("graphs") or {}).get("rust") or {}
        if rs.get("files") != 2 or rs.get("statements") != 1:
            failures.append("rust graph = files %r statements %r, expected 2/1 "
                            "(mod util; in lib.rs)" % (rs.get("files"), rs.get("statements")))
        if rs.get("external_packages", {}).get("rayon") != 1:
            failures.append("rust external crate not isolated: %r"
                            % (rs.get("external_packages"),))
        sh2 = (led2.get("graphs") or {}).get("shell") or {}
        ps = (sh2.get("coverage") or {}).get("powershell") or {}
        if ps.get("files") != 1 or ps.get("refs") != 2:
            failures.append("powershell coverage = %r, expected 1 file / 2 refs "
                            "(both launches go through a variable)" % (ps,))
        if "rust" not in (led2.get("languages") or {}).get("instrumented", []):
            failures.append("rust still listed as uninstrumented after building its graph")
        xl2 = (led2.get("cross_language") or {}).get("resolved_by") or {}
        if xl2.get("nearest") != 1:
            failures.append("nearest tie-break = %r, expected 1 (AA/run.bat -> "
                            "AA/harness.py, not BB's)" % (xl2,))
        near_edge = [(r["from"], r["to"]) for r in (led2.get("cross_language") or {})
                     .get("detail") or [] if r.get("resolved") == "nearest"]
        if not near_edge and not any(r.get("via") == "nearest"
                                     for r in (sh2.get("detail") or [])):
            failures.append("nearest resolution not visible in any detail row: %r"
                            % (sh2.get("detail") or [],))

    # --- the file inventory: one bucket per file, and no bucket invented -----
    # The negative control is the point. A classifier that calls everything
    # "unclassified" would pass a "find the unknown file" test, and one that
    # calls everything "data" would pass a "zero unclassified" test. Both are
    # asserted here, in opposite directions, on the same fixture.
    with tempfile.TemporaryDirectory() as tmp2:
        r = Path(tmp2)
        (r / "node_modules").mkdir()
        (r / "node_modules" / "vendored.js").write_text("import 'x';\n")
        (r / "archived" / "old").mkdir(parents=True)
        (r / "archived" / "old" / "old.py").write_text("x = 1\n")
        (r / ".hypothesis").mkdir()
        (r / ".hypothesis" / "abc123").write_text("1")
        (r / "tool.build" / "mod").mkdir(parents=True)
        (r / "tool.build" / "mod" / "x.obj").write_text("1")
        (r / "hook").write_text("#!/usr/bin/env bash\necho hi\n")
        (r / "notes.md").write_text("# n\n")
        (r / "Dockerfile.alpine").write_text("FROM alpine\n")
        (r / "odd.zzz").write_text("?\n")
        (r / "mystery").write_text("no suffix, no shebang\n")
        # ...and the one shape that stays genuinely unknown: a script whose
        # interpreter nobody has declared. This is the control that keeps
        # `unclassified` falsifiable instead of an empty column.
        (r / "weird").write_text("#!/opt/bin/mystery-lang\nrun\n")
        (r / "mod.py").write_text("y = 2\n")
        expect = [
            ("mod.py", ("code", "python")),
            ("notes.md", ("noncode", "doc")),
            # A shebang script with NO suffix is code. `scripts/hooks/pre-commit`
            # is exactly this shape and was outside every shard.
            ("hook", ("code", "shell")),
            ("Dockerfile.alpine", ("noncode", "config")),
            ("node_modules/vendored.js", ("vendored", "vendored")),
            ("archived/old/old.py", ("archived", "archived")),
            (".hypothesis/abc123", ("generated", "generated")),
            ("tool.build/mod/x.obj", ("generated", "generated")),
            # An undeclared SUFFIX is sniffed and labelled as undeclared...
            ("odd.zzz", ("noncode", "text:unrecognised")),
            # A file with no suffix and no shebang is sniffed, not guessed.
            ("mystery", ("noncode", "text:unrecognised")),
            # An unknown INTERPRETER is the shape that stays unknown.
            ("weird", ("unclassified", "shebang:#!/opt/bin/mystery-lang")),
        ]
        for rel, want in expect:
            got = classify_file(r / rel, r)
            if got != want:
                failures.append(f"classify {rel} = {got!r}, expected {want!r}")
        inv = file_inventory([r])
        if not inv["adds_up"]:
            failures.append("file inventory does not add up: %r" % (inv,))
        if inv["unclassified"] != 1:
            failures.append("file inventory unclassified = %r, expected exactly the "
                            "one suffixless mystery" % (inv["unclassified"],))
        if inv["every_file_indexed"]:
            failures.append("one unclassified file was still reported as 100% indexed")
        if inv["unrecognised"] != 2:
            failures.append("unrecognised = %r, expected 2 (.zzz suffix and the "
                            "suffixless text file)" % (inv["unrecognised"],))
        if inv["unreadable"] != 0:
            failures.append("readable fixture reported unreadable files: %r"
                            % (inv["unreadable"],))
        if inv["undeclared_extensions"].get(".zzz") != 1:
            failures.append("undeclared extension not named: %r"
                            % (inv["undeclared_extensions"],))
        if inv["total"] != sum(1 for _ in r.rglob("*")
                               if _.is_file()):
            failures.append("file inventory total %r != files on disk %r"
                            % (inv["total"], sum(1 for _ in r.rglob("*") if _.is_file())))

    if failures:
        print("SELFTEST FAILED", file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 3
    print("SELFTEST OK — 42 controls (file classification x10, no false "
          "unreadable, undeclared suffix "
          "sniffed and NAMED, suffixless file left unplaced, inventory adds up, "
          "unclassified counted not absorbed, 100%-claim refused with one unknown, "
          "inventory total equals disk, tier list, cycle filter, tier B membership, "
          "loose modules, parse failure, hub silence, stub tier, no stub in tier A, "
          "mixed-language tier, no mixed tree in tier A, cross-language edge, "
          "A-CROSS membership, shell does not evict a tree, shard plan partitions, "
          "js edge count, js external isolation, js cross-tree edge, shell call "
          "forms, shell statement count, tail resolution of a flat-layout driver, "
          "js relative import is never tail-matched, basename match counted, "
          "basename match does not re-tier a tree, declared alias upgrades a weak "
          "edge, unused alias is stale, affected is file-precise, affected refuses "
          "an unknown file, shell blocker names its graph, rust mod graph, rust "
          "external crate isolated, powershell launch through a variable counted)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None,
                    help="src directory to scan (default: ../src next to this tool)")
    ap.add_argument("--archive", default=None,
                    help="archive directory to count (default: ../archived_unified)")
    ap.add_argument("--extra-root", action="append", default=None,
                    help="root outside src/ to count by language (repeatable, e.g. tools scripts)")
    ap.add_argument("--json", action="store_true", help="emit the ledger as JSON")
    ap.add_argument("--out", default=None, help="write JSON to this path as well")
    ap.add_argument("--selftest", action="store_true", help="run scanner controls")
    ap.add_argument("--plan", default=None,
                    help="write the per-language shard plan JSON to this path")
    ap.add_argument("--aliases", default=None,
                    help="resolution alias table (default: tools/resolution_aliases.json)")
    ap.add_argument("--affected", default=None, metavar="PATH_OR_TREE",
                    help="print everything that must be re-verified when this changes")
    ap.add_argument("--contracts", action="store_true",
                    help="print the checked promises: cycles of any length, shard liftability")
    ap.add_argument("--refs", nargs="?", const="", default=None, metavar="RESULT",
                    help="print EVERY reference with its outcome (optionally only "
                         "one result, e.g. --refs tail); the ledger keeps a 30-row sample")
    ap.add_argument("--files", action="store_true",
                    help="print ONLY the file inventory for --root/--extra-root: every "
                         "file in one bucket, unclassified named. Runs under any python3 "
                         "(no aliases, no graphs), so each environment can be measured "
                         "where it lives instead of over a network share")
    ap.add_argument("--top", action="append", default=None, metavar="DIR",
                    help="with --files: walk this WHOLE directory (a repo root) instead "
                         "of src + extras; repeatable")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    here = Path(__file__).resolve().parent
    root = Path(args.root) if args.root else here.parent / "src"
    archive = Path(args.archive) if args.archive else here.parent / "archived_unified"

    if args.files:
        walk = [Path(p) for p in (args.top or [])] or (
            [root] + [Path(p) for p in (args.extra_root or [])])
        inv = file_inventory(walk)
        inv["schema"] = "azazael.file_inventory/1"
        inv["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        inv["host_root"] = str(here.parent)
        print(json.dumps(inv, indent=2))
        return 0 if inv["every_file_indexed"] else 1

    try:
        extras = [Path(p) for p in (args.extra_root or [])]
        if not args.extra_root:
            # Default: the two places production code actually lives outside src/.
            extras = [root.parent / "tools", root.parent / "scripts"]
        aliases = Aliases.load(Path(args.aliases)) if args.aliases else None
        # File-level edges are kept only when a question needs them: a 1,500-row
        # edge list in every ledger is payload, not a deliverable.
        ledger = census(root, archive, extras, aliases,
                        keep_edges=bool(args.affected),
                        keep_refs=bool(args.refs is not None))
    except FileNotFoundError as exc:
        print(f"yggdrasil_census: {exc}", file=sys.stderr)
        return 2

    private = {k for k in ledger if k.startswith("_")}
    text = (json.dumps({k: v for k, v in ledger.items() if k not in private},
                       indent=2, sort_keys=False) if args.json else render(ledger))
    print(text)

    if args.refs is not None:
        want = args.refs.strip().lower()
        rows = [(g, r) for g, rs in (ledger.get("_refs") or {}).items()
                for r in rs if not want or r["result"].lower() == want]
        print("\nREFS — %d reference(s)%s" % (len(rows),
                                          (" with result %r" % want) if want else ""),
              file=sys.stderr)
        for gname, r in sorted(rows, key=lambda gr: (gr[1]["result"], gr[1]["from"])):
            dest = r.get("to") or "-"
            print("  %-18s %-11s %-46s %s" % (r["from"][:46], r["result"],
                                            r["ref"][:60], dest), file=sys.stderr)

    if args.affected:
        target_arg = args.affected.replace(chr(92), "/").strip("/")
        res = affected_trees(ledger, target_arg)
        rows = res["rows"]
        print(f"\nAFFECTED — {res['target']} ({res['granularity']}-precise)",
              file=sys.stderr)
        if res.get("error"):
            print(f"  {res['error']}", file=sys.stderr)
            for cand in res.get("candidates") or []:
                print(f"    {cand}", file=sys.stderr)
        for row in rows[1:]:
            print(f"  depth {row['depth']}  {row['node']:<52}"
                  f"{row['graph']:>8}{row['refs']:>4} ref(s)", file=sys.stderr)
        if res.get("affected_limbs"):
            print("  limbs to re-verify: " + ", ".join(res["affected_limbs"]),
                  file=sys.stderr)
        elif not res.get("error"):
            print("  nothing references it", file=sys.stderr)
        if res.get("truncated"):
            print("  (list truncated)", file=sys.stderr)

    if args.contracts:
        con = ledger.get("contracts") or {}
        lf = con.get("liftability") or {}
        print(f"\nCONTRACTS", file=sys.stderr)
        print(f"  cycles of any length  {len(con.get('cycles_transitive') or [])} "
              f"(direct pairs {con.get('direct_cycle_pairs', 0)})", file=sys.stderr)
        for group in (con.get("cycles_transitive") or [])[:5]:
            print("    " + " -> ".join(group) + " -> " + group[0], file=sys.stderr)
        print(f"  shards liftable       {lf.get('liftable', 0)}/{lf.get('shards', 0)} "
              f"({lf.get('blocked', 0)} blocked)", file=sys.stderr)
        for row in (lf.get("blocked_detail") or [])[:8]:
            who = ", ".join(f"{b['from']} x{b['refs']} [{b.get('graph', '?')}]"
                            for b in row["blockers"][:2])
            print(f"    {row['shard']:<20}{row['external_refs']:>5} ref(s)  {who}",
                  file=sys.stderr)
    if args.plan:
        plan = shard_plan(root)
        Path(args.plan).write_text(json.dumps(plan, indent=1), encoding="utf-8")
        lb = plan["languages"]
        print("\nSHARD PLAN — " + ", ".join(
            f"{k} {v['modules']} mod / {v['shards']} shards / "
            f"{v['underfilled']} underfilled / cont-straddle "
            f"{v['continuous']['straddling']}" for k, v in lb.items()),
              file=sys.stderr)
        print(f"  totals: {plan['totals']}", file=sys.stderr)
        print(f"wrote {args.plan}", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
