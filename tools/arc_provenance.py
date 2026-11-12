"""Re-examine every ARC pin's evidence against bytes that exist right now.

A wing's checker grades whether a pin's *text* answers a demand. It cannot grade
whether the evidence behind that text can still be looked at: a ledger that
records ``verified: read at source, HTTP 200`` and retains nothing is a claim
with no artifact, and one that records a digest is only as good as the bytes
that digest names. Measured 2026-11-05 before this tool existed: 0 of 39 wing
pins carried a capture with re-hashable bytes, and nothing in the tree could
tell an unbacked tier-A pin from a backed one.

This tool answers one question per pin: **can the evidence be re-examined from
this tree, and does it still say what the ledger says it says?**

Four readings are taken, in this order, and the first that applies decides:

1. **Capture.** The evidence prose names a file and/or a ``sha256``. The file is
   located (repo-relative, then under the wing's ``project_root``, then by
   basename), re-hashed from disk, and compared. A recorded digest is never
   trusted -- it is recomputed, because a ledger that quotes its own digest can
   be edited into agreement with itself.
2. **Source.** ``source`` names a path inside this tree (a canon document, an
   instrument). The path must exist, and the pin's own quoted text must be
   findable in it.
3. **Instrument.** A tier-A pin with no bytes may still be re-runnable: the
   command it names is checked for the existence of the instrument it points at.
   ``--run-instruments`` re-runs it and records the outcome.
4. **Nothing.** A tier-A pin with no capture, no in-tree source and no
   instrument is ``UNBACKED``: coverage is missing, and the honest verdict is
   ``INCOMPLETE``, not a defect and never a pass.

Verdict and exit contract, shared with the wings and the container:

    READY       0   every tier-A pin's evidence re-examines clean
    GAPS        1   a declared capture, source quote or instrument does not hold
    INCOMPLETE  1   no defect, but a tier-A pin has no re-examinable evidence
    BLIND       2   a ledger could not be read -- never a pass

Precedence BLIND > GAPS > INCOMPLETE > READY.

What this tool does not do: it does not grade whether a pin is *sufficient*, and
it does not turn a tier-B or tier-C pin's weakness into a defect. Those ranks
declare that the pin rests on something other than a fetch; they are recorded
and counted, never promoted.

Usage:

    python tools/arc_provenance.py
    python tools/arc_provenance.py --json logs/arc_provenance.json
    python tools/arc_provenance.py --wing cain
    python tools/arc_provenance.py --run-instruments
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
ARC = ROOT / "ARC"

VERDICT_READY = "READY"
VERDICT_GAPS = "GAPS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_BLIND = "BLIND"

EXIT_READY = 0
EXIT_BAD = 1
EXIT_BLIND = 2

WING_EXIT: Dict[str, int] = {
    VERDICT_READY: EXIT_READY,
    VERDICT_GAPS: EXIT_BAD,
    VERDICT_INCOMPLETE: EXIT_BAD,
    VERDICT_BLIND: EXIT_BLIND,
}

# Per-item states. ``BACKED`` states are the only ones that mean "the bytes were
# re-hashed in this run"; ``INSTRUMENT`` means "re-runnable, not re-run here".
STATE_BACKED = "BACKED"
STATE_BACKED_SOURCE = "BACKED-SOURCE"
STATE_INSTRUMENT = "INSTRUMENT"
STATE_RERUN_OK = "RERUN-OK"
STATE_UNBACKED = "UNBACKED"
STATE_NOT_REQUIRED = "NOT-REQUIRED"
STATE_DEFECT = "DEFECT"

BACKED_STATES = frozenset({STATE_BACKED, STATE_BACKED_SOURCE, STATE_RERUN_OK})

# Directories walked by the basename index. Skipped by name, not by guesswork:
# each one holds either a copy of another tree (.build, build/lib), a toolchain
# (.venvs, node_modules) or generated output that is not evidence.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venvs",
        ".build",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".tox",
        ".eggs",
    }
)

# A capture larger than this is not hashed: it is not a page or a packet, and a
# multi-gigabyte read inside a gate makes the gate unreliable rather than strong.
MAX_CAPTURE_BYTES = 96 * 1024 * 1024

# Readings the instrument could not take. Collected rather than dropped: "the
# bytes would not read" and "the file is not there" are different findings, and
# collapsing the first into the second is how a locked or permission-denied
# capture reads as a missing one. Printed with the report, in the JSON, so a run
# that skipped something says what it skipped.
DIAGNOSTICS: List[Tuple[str, str]] = []
DIAGNOSTICS_CAP = 40


def note(kind: str, detail: str) -> None:
    """Record a reading that could not be taken, once per (kind, detail)."""
    entry = (kind, detail)
    if entry in DIAGNOSTICS or len(DIAGNOSTICS) >= DIAGNOSTICS_CAP:
        return
    DIAGNOSTICS.append(entry)

# Evidence-bearing fields. ``does_not_prove``, ``cite``, ``text`` and ``asserts``
# are excluded on purpose: they discuss the evidence rather than locate it, and
# a filename mentioned in prose about a limitation is not a claim of capture.
EVIDENCE_KEYS = (
    "verified",
    "mark_evidence_status",
    "mark_evidence_url",
    "evidence",
    "capture",
    "capture_path",
    "packet",
    "bytes",
    "sha256",
    "sha256sum",
    "provenance",
)

DIGEST_RE = re.compile(r"(?i)sha-?256\s*[:=]?\s*([0-9a-f]{16,64})")
# A filename with an extension, not preceded by a word character or path
# separator, so a URL's own path is not harvested as a local capture name.
FILE_RE = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.(?:html?|jsonl?|pdf|txt|md|csv|xml|log|ya?ml|zip|gz))"
)
INSTRUMENT_RE = re.compile(r"(?<![\w/.-])((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.py)")
DISCLAIMER_RE = re.compile(
    r"(?i)\bno command (?:in the tree )?(?:reproduces|counts|produces|recomputes|derives)"
)
URL_RE = re.compile(r"(?i)^https?://")

# A quoted sentence shorter than this is not evidence of anything; it matches
# by coincidence in almost any document.
MIN_QUOTE_CHARS = 40
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+|\n+")


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> Optional[str]:
    """The digest of the bytes on disk now, or None when they cannot be read."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        note("unreadable", "%s: %s" % (path, exc))
        return None
    return digest.hexdigest()


# Markdown decoration is formatting, not wording. The canon quotes its own
# operative sentence inside a blockquote with emphasis (docs/ESQUIRE_CANON
# 2026-11-03.md:69-70), and a pin that quotes the sentence correctly failed to
# match until these characters were dropped -- the instrument was right that the
# text differed and wrong about why.
QUOTE_DECORATION_RE = re.compile(r"[*_`>#]+")


def normalize(text: str) -> str:
    """Collapse whitespace so a quote is compared as text, not as formatting."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_quote(text: str) -> str:
    """Compare wording only: markdown decoration and blockquote markers removed."""
    return normalize(QUOTE_DECORATION_RE.sub("", text or ""))


def ledger_items(ledger: Path) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every pin in a wing ledger, or a reason it could not be read."""
    try:
        raw = ledger.read_text(encoding="utf-8")
    except OSError as exc:
        return [], "ledger unreadable at %s: %s" % (ledger, exc)
    try:
        loaded = json.loads(raw)
    except ValueError as exc:
        return [], "ledger is not JSON at %s: %s" % (ledger, exc)
    if isinstance(loaded, list):
        items = loaded
    elif isinstance(loaded, dict):
        items = loaded.get("pins") or loaded.get("marks") or []
    else:
        return [], "ledger at %s holds %s, not pins" % (ledger, type(loaded).__name__)
    if not isinstance(items, list):
        return [], "ledger at %s holds a non-list 'pins' field" % ledger
    return [item for item in items if isinstance(item, dict)], None


def item_id(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("surface") or "?")


def item_tier(item: Dict[str, Any]) -> str:
    """The tier, at the top level or inside ``mechanism`` (the cain shape)."""
    tier = item.get("tier")
    if not tier:
        mechanism = item.get("mechanism")
        if isinstance(mechanism, dict):
            tier = mechanism.get("tier")
    return str(tier or "").strip().upper()


def evidence_strings(item: Dict[str, Any]) -> List[str]:
    """Strings that locate evidence, from the item and its ``mechanism``."""
    out: List[str] = []
    for key in EVIDENCE_KEYS:
        value = item.get(key)
        if isinstance(value, str):
            out.append(value)
    mechanism = item.get("mechanism")
    if isinstance(mechanism, dict):
        for key in EVIDENCE_KEYS:
            value = mechanism.get(key)
            if isinstance(value, str):
                out.append(value)
    return out


def source_string(item: Dict[str, Any]) -> str:
    source = item.get("source")
    if not source:
        mechanism = item.get("mechanism")
        if isinstance(mechanism, dict):
            source = mechanism.get("source")
    return source if isinstance(source, str) else ""


def command_strings(item: Dict[str, Any]) -> List[str]:
    return [
        value
        for value in (item.get("command"), item.get("commands"), item.get("reproduce"))
        if isinstance(value, str) and value.strip()
    ]


def quote_text(item: Dict[str, Any]) -> str:
    """The pin's own words: what a source check looks for in the cited file.

    ``text`` and ``quote`` are quotations of the cited document. ``asserts`` is
    deliberately excluded: it states a measurement the pin draws *from* its
    instrument, so looking for it verbatim in the source is a category error --
    the first version of this check did exactly that and reported four tier-B
    eliza pins as "quote not found" for sentences they never claimed to quote.
    """
    for key in ("text", "quote"):
        value = item.get(key)
        if isinstance(value, str) and len(value.strip()) >= MIN_QUOTE_CHARS:
            return value
    return ""


def source_path(root: Path, source: str) -> Optional[Path]:
    """The in-tree file a ``source`` string names, when it names one.

    Handles the three shapes in the ledgers: a bare path, a path with a line or
    section suffix (``README.md:26``, ``docs/x.md#part-vii``) and a path followed
    by a parenthetical (``tools/x.py (GATE_METADATA, G4)``).
    """
    if not source or URL_RE.match(source.strip()):
        return None
    candidate = source.strip().split("#", 1)[0].split(" (", 1)[0]
    candidate = re.sub(r":\d+(-\d+)?$", "", candidate).strip()
    if not re.search(r"\.[A-Za-z0-9]{1,5}$", candidate):
        return None
    path = (root / candidate).resolve()
    if not path.is_file():
        return None
    try:
        path.relative_to(root.resolve())
    except ValueError:
        note("out-of-tree", "%s resolves outside %s" % (candidate, root))
        return None
    return path


class Resolver:
    """Locate a file name in the tree, with one walk for the whole run.

    A bare name (``evidence_packet_r5.json``) is looked for by basename: the
    walk is skipped entirely when no bare name is ever asked for, which is the
    common case.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._by_name: Optional[Dict[str, List[Path]]] = None

    def _index(self) -> Dict[str, List[Path]]:
        if self._by_name is not None:
            return self._by_name
        index: Dict[str, List[Path]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                index.setdefault(filename, []).append(Path(dirpath) / filename)
        self._by_name = index
        return index

    def candidates(
        self, name: str, project_root: str = ""
    ) -> List[Path]:
        """Existing files this name could denote, most specific first."""
        found: List[Path] = []
        cleaned = name.strip()
        if not cleaned:
            return found
        if "/" in cleaned or "\\" in cleaned:
            for base in (self.root, self.root / project_root if project_root else None):
                if base is None:
                    continue
                path = (base / cleaned).resolve()
                if path.is_file():
                    found.append(path)
            return found
        by_name = self._index()
        exact = by_name.get(cleaned, [])
        if project_root:
            preferred = [p for p in exact if project_root in p.parts]
            others = [p for p in exact if project_root not in p.parts]
            found = sorted(preferred) + sorted(others)
        else:
            found = sorted(exact)
        return found


def wing_digest_index(wing_dir: Path, skip: Iterable[Path]) -> Dict[str, List[str]]:
    """digest -> repo-relative paths, for every file in the wing's own directory.

    Only used to resolve a digest that names no file. The ledger itself is
    skipped: a ledger cannot be its own evidence.
    """
    skip_paths = {p.resolve() for p in skip}
    index: Dict[str, List[str]] = {}
    if not wing_dir.is_dir():
        return index
    for dirpath, dirnames, filenames in os.walk(wing_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.resolve() in skip_paths:
                continue
            try:
                if path.stat().st_size > MAX_CAPTURE_BYTES:
                    continue
            except OSError as exc:
                note("unreadable", "%s: %s" % (path, exc))
                continue
            digest = sha256_file(path)
            if not digest:
                continue
            index.setdefault(digest, []).append(str(path))
    return index


def digest_matches(claimed: str, index: Dict[str, List[str]]) -> List[str]:
    """Paths in the index whose digest starts with ``claimed``."""
    claimed = claimed.lower()
    hits: List[str] = []
    for digest, paths in index.items():
        if digest.startswith(claimed):
            hits.extend(paths)
    return sorted(set(hits))


def quote_presence(source_text: str, quote: str) -> Tuple[int, int]:
    """(sentences of the quote found in the source, sentences worth checking)."""
    haystack = normalize_quote(source_text)
    sentences = [
        normalize_quote(part)
        for part in SENTENCE_SPLIT_RE.split(quote or "")
        if len(normalize_quote(part)) >= MIN_QUOTE_CHARS
    ]
    if not sentences:
        return 0, 0
    found = sum(1 for sentence in sentences if sentence in haystack)
    return found, len(sentences)


def _defect(state_detail: str, **extra: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "state": STATE_DEFECT,
        "detail": state_detail,
        "defect": True,
    }
    row.update(extra)
    return row


def grade_item(
    item: Dict[str, Any],
    *,
    root: Path,
    project_root: str,
    digest_index: Dict[str, List[str]],
    resolver: Resolver,
    run_instruments: bool = False,
    instrument_timeout: float = 120.0,
) -> Dict[str, Any]:
    """One pin's evidence, read against the tree as it is now."""
    tier = item_tier(item)
    tier_a = tier == "A"
    texts = evidence_strings(item)
    joined = " ".join(texts)

    names: List[str] = []
    for value in texts:
        if URL_RE.match(value.strip()):
            continue
        for match in FILE_RE.finditer(value):
            name = match.group(1)
            if name not in names:
                names.append(name)
    digests: List[str] = []
    for match in DIGEST_RE.finditer(joined):
        claimed = match.group(1).lower()
        if claimed not in digests:
            digests.append(claimed)

    # 1. CAPTURE — a named file, and a digest when one is recorded.
    if names:
        resolved: List[Tuple[str, Path]] = []
        for name in names:
            for path in resolver.candidates(name, project_root):
                resolved.append((name, path))
        if not resolved:
            if tier_a:
                return _defect(
                    "capture missing: evidence names %s, which is not in this tree"
                    % ", ".join(names[:3]),
                    names=names,
                )
            return {
                "state": STATE_NOT_REQUIRED,
                "detail": "tier %s names %s, not retained here"
                % (tier or "?", ", ".join(names[:2])),
            }
        mismatched: List[str] = []
        for name, path in resolved:
            actual = sha256_file(path)
            if actual is None:
                mismatched.append("%s unreadable" % name)
                continue
            if not digests:
                return {
                    "state": STATE_BACKED,
                    "detail": "bytes re-hashed now: %s sha256:%s (no digest was recorded)"
                    % (path.name, actual[:16]),
                    "path": str(path),
                    "sha256": actual,
                }
            for claimed in digests:
                if actual.startswith(claimed):
                    return {
                        "state": STATE_BACKED,
                        "detail": "%s sha256:%s matches the recorded digest"
                        % (path.name, claimed),
                        "path": str(path),
                        "sha256": actual,
                    }
            mismatched.append("%s sha256:%s" % (name, actual[:16]))
        if tier_a:
            return _defect(
                "digest mismatch: recorded %s does not match %s"
                % (", ".join(digests[:2]), "; ".join(mismatched[:2])),
                names=names,
            )
        return {
            "state": STATE_NOT_REQUIRED,
            "detail": "tier %s capture does not match its digest" % (tier or "?"),
        }

    # 2. DIGEST WITHOUT A NAME — resolvable only if the bytes are in the wing.
    if digests:
        for claimed in digests:
            hits = digest_matches(claimed, digest_index)
            if hits:
                return {
                    "state": STATE_BACKED,
                    "detail": "sha256:%s found in %s" % (claimed, hits[0]),
                    "path": hits[0],
                    "sha256": claimed,
                }
        if tier_a:
            return _defect(
                "digest unresolved: sha256:%s names no file and no bytes in ARC/"
                " re-hash to it" % digests[0],
                names=[],
            )

    # 3. IN-TREE SOURCE — the cited document is here, so the quote is checkable.
    #    Only tier A asserts a read at source, so only tier A is graded on it; a
    #    tier-B pin whose citation happens to live in this tree is recorded as
    #    present, never promoted to backed by that fact alone.
    source = source_string(item)
    src_path = source_path(root, source)
    if src_path is not None:
        rel = str(src_path.relative_to(root))
        quote = quote_text(item) if tier_a else ""
        if quote:
            try:
                source_text = src_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                if tier_a:
                    return _defect("source unreadable at %s: %s" % (source, exc))
                source_text = ""
            found, total = quote_presence(source_text, quote)
            if total and found == 0:
                if tier_a:
                    return _defect(
                        "quote not in source: none of %d quoted sentences is in %s"
                        % (total, rel),
                    )
                return {
                    "state": STATE_NOT_REQUIRED,
                    "detail": "tier %s: source present (%s), quote not verbatim there"
                    % (tier or "?", rel),
                }
            return {
                "state": STATE_BACKED_SOURCE,
                "detail": "%d of %d quoted sentences located in %s"
                % (found, total, rel),
                "path": str(src_path),
            }
        if tier_a:
            return {
                "state": STATE_BACKED_SOURCE,
                "detail": "source present in this tree: %s" % rel,
                "path": str(src_path),
            }
        return {
            "state": STATE_NOT_REQUIRED,
            "detail": "tier %s: source present in this tree: %s" % (tier or "?", rel),
            "path": str(src_path),
        }

    # 4. INSTRUMENT — re-runnable, whether or not it is re-run here.
    if tier_a:
        commands = command_strings(item)
        disclaimers = [c for c in commands if DISCLAIMER_RE.search(c)]
        if disclaimers:
            return _defect(
                "no instrument: tier A rests on %r, which states no command in the "
                "tree reproduces the figure" % disclaimers[0][:80]
            )
        runnable = [c for c in commands if not DISCLAIMER_RE.search(c)]
        if runnable:
            command = runnable[0].strip()
            instruments = [m.group(1) for m in INSTRUMENT_RE.finditer(command)]
            missing: List[str] = []
            located: List[Path] = []
            for name in instruments:
                hits = resolver.candidates(name, project_root)
                if hits:
                    located.append(hits[0])
                else:
                    missing.append(name)
            if missing:
                return _defect(
                    "instrument missing: %r names %s, which is not in this tree"
                    % (command[:60], ", ".join(missing))
                )
            if run_instruments:
                cwd = root
                if located and project_root:
                    parts = located[0].parts
                    if project_root in parts:
                        index = parts.index(project_root)
                        cwd = Path(*parts[: index + 1])
                return _run_instrument(command, cwd, instrument_timeout)
            return {
                "state": STATE_INSTRUMENT,
                "detail": "re-runnable: %r%s"
                % (
                    command[:80],
                    "" if not located else " (instrument %s exists)"
                    % located[0].name,
                ),
                "command": command,
            }

    # 5. NOTHING — tier A with no capture, no in-tree source, no instrument.
    if tier_a:
        return {
            "state": STATE_UNBACKED,
            "detail": "no retained bytes and no instrument: %r records a reading "
            "nothing in this tree can re-examine" % (texts[0][:80] if texts else "?"),
        }
    return {
        "state": STATE_NOT_REQUIRED,
        "detail": "tier %s, no retained bytes (recorded, not required)" % (tier or "?"),
    }


def instrument_argv(command: str) -> List[str]:
    """A declared instrument as an argument vector, never a shell string.

    The command comes from a ledger, and a ledger is data: running it through a
    shell would let a pin's text reach a command interpreter. Splitting also
    pins the interpreter -- a ledger that says ``python`` gets the interpreter
    running this tool, not whatever ``python`` means on PATH.
    """
    argv = shlex.split(command, posix=True)
    if not argv:
        return []
    if Path(argv[0]).name.lower() in ("python", "python3", "python.exe", "python3.exe"):
        argv[0] = sys.executable
    return argv


def _run_instrument(command: str, cwd: Path, timeout: float) -> Dict[str, Any]:
    """Execute a declared instrument and record what it did."""
    started = time.monotonic()
    argv = instrument_argv(command)
    if not argv:
        return _defect("instrument %r names no executable" % command[:60])
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        kind = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "exec"
        return _defect(
            "instrument %s after %.0fs: %r (a hung instrument is not a pass)"
            % (kind, time.monotonic() - started, command[:60])
        )
    elapsed = round(time.monotonic() - started, 2)
    out_sha = hashlib.sha256((proc.stdout or "").encode("utf-8")).hexdigest()
    if proc.returncode != 0:
        return _defect(
            "instrument exited %d in %.2fs (%r): %s"
            % (
                proc.returncode,
                elapsed,
                command[:60],
                (proc.stdout or proc.stderr or "").strip().replace("\n", " ")[:160],
            )
        )
    return {
        "state": STATE_RERUN_OK,
        "detail": "re-ran in %.2fs, exit 0, stdout sha256:%s" % (elapsed, out_sha[:16]),
        "command": command,
        "stdout_sha256": out_sha,
        "seconds": elapsed,
    }


def grade_wing(
    root: Path,
    wing: Dict[str, str],
    *,
    run_instruments: bool = False,
    instrument_timeout: float = 120.0,
) -> Dict[str, Any]:
    """Every pin of one wing, graded, with the wing's own verdict."""
    name = wing["wing"]
    started = time.monotonic()
    ledger = root / wing["ledger"]
    project_root = str(wing.get("project_root", "") or "")

    def blind(reason: str) -> Dict[str, Any]:
        return {
            "wing": name,
            "verdict": VERDICT_BLIND,
            "exit": EXIT_BLIND,
            "reason": reason,
            "errors": [reason],
            "pins": None,
            "items": [],
            "backed": 0,
            "instruments": 0,
            "defects": 0,
            "coverage_gaps": 0,
            "not_required": 0,
            "seconds": round(time.monotonic() - started, 2),
        }

    items, error = ledger_items(ledger)
    if error:
        return blind(error)

    wing_dir = ledger.parent
    digest_index = wing_digest_index(wing_dir, skip=[ledger])
    resolver = Resolver(root)

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    backed = instruments = defects = coverage = not_required = 0
    for item in items:
        row = grade_item(
            item,
            root=root,
            project_root=project_root,
            digest_index=digest_index,
            resolver=resolver,
            run_instruments=run_instruments,
            instrument_timeout=instrument_timeout,
        )
        row["id"] = item_id(item)
        row["tier"] = item_tier(item)
        rows.append(row)
        state = row["state"]
        if state in BACKED_STATES:
            backed += 1
        elif state == STATE_INSTRUMENT:
            instruments += 1
        elif state == STATE_UNBACKED:
            coverage += 1
            errors.append("%s (%s) UNBACKED: %s" % (row["id"], row["tier"], row["detail"]))
        elif state == STATE_NOT_REQUIRED:
            not_required += 1
        else:
            defects += 1
            errors.append("%s (%s) DEFECT: %s" % (row["id"], row["tier"], row["detail"]))

    if defects:
        verdict, code = VERDICT_GAPS, EXIT_BAD
    elif coverage:
        verdict, code = VERDICT_INCOMPLETE, EXIT_BAD
    else:
        verdict, code = VERDICT_READY, EXIT_READY

    return {
        "wing": name,
        "verdict": verdict,
        "exit": code,
        "reason": None,
        "errors": errors,
        "pins": len(rows),
        "items": rows,
        "backed": backed,
        "instruments": instruments,
        "defects": defects,
        "coverage_gaps": coverage,
        "not_required": not_required,
        "seconds": round(time.monotonic() - started, 2),
    }


def combine(rows: Sequence[Dict[str, Any]]) -> Tuple[str, int]:
    """The provenance verdict: BLIND > GAPS > INCOMPLETE > READY."""
    states = {row.get("verdict") for row in rows}
    if VERDICT_BLIND in states:
        return VERDICT_BLIND, EXIT_BLIND
    if VERDICT_GAPS in states:
        return VERDICT_GAPS, EXIT_BAD
    if VERDICT_INCOMPLETE in states:
        return VERDICT_INCOMPLETE, EXIT_BAD
    return VERDICT_READY, EXIT_READY


def declared_wings() -> Tuple[Dict[str, str], ...]:
    """The wing table, taken from the container so it is declared once."""
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import arc_check  # noqa: PLC0415 -- single declaration of WINGS lives there

    return tuple(arc_check.WINGS)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-examine every ARC pin's evidence against bytes on disk."
    )
    parser.add_argument("--check", action="store_true", help="grade and exit (default)")
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    parser.add_argument(
        "--wing",
        action="append",
        default=None,
        metavar="NAME",
        help="grade one declared wing (repeatable; default: all)",
    )
    parser.add_argument(
        "--run-instruments",
        action="store_true",
        help="execute each declared tier-A command instead of only checking it exists",
    )
    parser.add_argument(
        "--instrument-timeout",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="wall bound per instrument under --run-instruments (default 120)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print the table and verdict only"
    )
    args = parser.parse_args(argv)

    if not ARC.is_dir():
        print("verdict: %s" % VERDICT_BLIND)
        print("reason: no ARC directory at %s" % ARC)
        return EXIT_BLIND

    wings = declared_wings()
    if args.wing:
        wanted = {w.lower() for w in args.wing}
        wings = tuple(w for w in wings if w["wing"].lower() in wanted)
        if not wings:
            print("verdict: %s" % VERDICT_BLIND)
            print("reason: no declared wing matches %s" % ", ".join(args.wing))
            return EXIT_BLIND

    graded = [
        grade_wing(
            ROOT,
            wing,
            run_instruments=args.run_instruments,
            instrument_timeout=args.instrument_timeout,
        )
        for wing in wings
    ]
    name, code = combine(graded)

    if args.json:
        out = Path(args.json)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "verdict": name,
                        "exit": code,
                        "wings": graded,
                        "pins": sum(w["pins"] or 0 for w in graded),
                        "backed": sum(w["backed"] for w in graded),
                        "instruments": sum(w["instruments"] for w in graded),
                        "defects": sum(w["defects"] for w in graded),
                        "coverage_gaps": sum(w["coverage_gaps"] for w in graded),
                        "run_instruments": bool(args.run_instruments),
                        "diagnostics": [
                            {"kind": kind, "detail": detail}
                            for kind, detail in DIAGNOSTICS
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            print("verdict: %s" % VERDICT_BLIND)
            print("reason: report could not be written to %s: %s" % (out, exc))
            return EXIT_BLIND
        print("wrote %s" % out)

    print(
        "%-10s %-12s %-5s %-6s %-7s %-6s %-5s %-5s %s"
        % ("wing", "verdict", "exit", "items", "backed", "instr", "defect", "uncov", "secs")
    )
    for wing in graded:
        print(
            "%-10s %-12s %-5d %-6s %-7d %-6d %-5d %-5d %.2f"
            % (
                wing["wing"],
                wing["verdict"],
                wing["exit"],
                "-" if wing["pins"] is None else wing["pins"],
                wing["backed"],
                wing["instruments"],
                wing["defects"],
                wing["coverage_gaps"],
                wing["seconds"],
            )
        )

    for wing in graded:
        if wing["verdict"] == VERDICT_BLIND:
            print("  [BLIND] %s: %s" % (wing["wing"], wing["reason"]))

    if not args.quiet:
        for wing in graded:
            for error in wing["errors"][:4]:
                print("  [%s] %s" % (wing["wing"], error))
            hidden = len(wing["errors"]) - 4
            if hidden > 0:
                print(
                    "  [%s] ... and %d more (run --wing %s for the full list)"
                    % (wing["wing"], hidden, wing["wing"])
                )

    if DIAGNOSTICS:
        kinds: Dict[str, int] = {}
        for kind, _detail in DIAGNOSTICS:
            kinds[kind] = kinds.get(kind, 0) + 1
        print(
            "diagnostics: %s (a reading not taken is reported, never passed)"
            % ", ".join("%s=%d" % (k, n) for k, n in sorted(kinds.items()))
        )
        if not args.quiet:
            for kind, detail in DIAGNOSTICS[:6]:
                print("  [%s] %s" % (kind, detail))
            hidden = len(DIAGNOSTICS) - 6
            if hidden > 0:
                print("  ... and %d more diagnostic(s)" % hidden)

    print("verdict: %s" % name)
    print(
        "provenance: %d wings, %d items, %d backed, %d instruments, %d defects, %d coverage gaps"
        % (
            len(graded),
            sum(w["pins"] or 0 for w in graded),
            sum(w["backed"] for w in graded),
            sum(w["instruments"] for w in graded),
            sum(w["defects"] for w in graded),
            sum(w["coverage_gaps"] for w in graded),
        )
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
