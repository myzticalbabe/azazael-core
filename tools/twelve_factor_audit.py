#!/usr/bin/env python
"""Twelve-Factor audit -- a measured 12-face ("dodecahedron") posture report.

WHY THIS EXISTS
---------------
A web search for an established "dodecahedron milestone" framework returns
nothing: there is no such artifact in software engineering. The nearest real
12-facet, 12-sided architecture framework is the Twelve-Factor App
(https://12factor.net/, Heroku, 2011). This tool audits THIS repository
against it, so the answer to "can we apply it?" is evidence rather than
opinion.

HONESTY CONTRACT
----------------
Every face is reported as one of:

    pass    -- a probe measured the property and it holds
    partial -- a probe measured it and it only partly holds
    gap     -- a probe measured it and it does not hold
    manual  -- NOT automatically measurable; this tool refuses to guess

`manual` is a first-class verdict on purpose. Twelve-Factor is a set of
runtime/operational properties; a static source tree cannot decide, for
example, whether a process is truly stateless at runtime. Reporting `manual`
instead of inventing a pass is the whole point of the contract -- see the
`azazael-stale-artifact-self-audit` skill on confidently-incomplete
artifacts.

Probes count real occurrences. If you change the heuristics, the counts in
the artifact change; nothing here is cached or pre-baked.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import hashlib

#: Directory names never worth scanning for these probes.
#:
#: `archive`, `nuitka_build_backup` and `inline_copy` matter: a first version of
#: this tool omitted them and reported 8 "hardcoded credential literals" where
#: the real answer was zero -- frozen historical snapshots and a vendored Jinja2
#: lexer (`token = 'block_begin'`) were being counted as application secrets.
#: Validating a probe before trusting its verdict is the point of
#: `azazael-stale-artifact-self-audit`.
_SKIP_DIRS = frozenset({
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__",
    "node_modules", "graphify-out", ".venv", "venv", "build", "dist",
    ".aider.tags.cache.v4", ".audit", "logs",
    "archive", "archived", "nuitka_build_backup", "inline_copy", "vendor",
    "_vendor", "site-packages", "snapshots", "cold",
    # Second measured pollution: `archived/2026-05-31/onnxruntime-*` is a
    # pristine upstream checkout, and it contributed AZURE_BLOB_SAS_TOKEN,
    # BROWSERSTACK_TOKEN and JAVA_PGP_KEY to a first draft of .env.example.
    # Those are upstream CI variables, not this project's configuration.
    "backup", "backups", "third_party", "external", "downloads", "models",
})

#: Any directory whose name starts with one of these is skipped too, which
#: catches versioned upstream checkouts (`onnxruntime-main`, `onnxruntime_git`).
_SKIP_DIR_PREFIXES = ("onnxruntime", "torch", "transformers")

#: Values that look like a credential but are placeholders, fixtures or the
#: literal name of a token type. Matched case-insensitively against the whole
#: assigned literal.
_PLACEHOLDER_VALUES = frozenset({
    "anonymous", "none", "null", "changeme", "change_me", "placeholder",
    "dummy", "redacted", "example", "test", "testing", "fake", "sample",
    "your_api_key_here", "todo", "todo-set-me", "unset", "not-set",
    "block_begin", "block_end", "variable_begin", "variable_end",
    # Output SENTINELS, not key material: src/SHARDS/polyglot.py defines
    # GOLDEN_TOKEN/WORK_TOKEN and every language snippet prints one so a caller
    # can prove the interpreter ran (polyglot's own contract: "the snippet
    # prints exactly AZAZAEL-GOLDEN-OK on stdout"). Measured 2026-11-02: one
    # definition each, six print sites per token across python/go/rust/js/
    # powershell/bash, no request header, no auth path -- the same five files
    # that carry GOLDEN_TOKEN print it and never send it.
    "azazael-work-ok",
    "azazael-golden-ok",
})

#: Variable-name prefixes that mark a value as a deliberate test fixture.
#: `src/CORE/enforcement_harness.py` sets `invalid_code_secret` on purpose --
#: it is the scanner's own negative test, not a leaked key.
_FIXTURE_PREFIXES = ("invalid_", "fake_", "dummy_", "example_", "sample_",
                     "mock_", "stub_", "test_")

#: Source roots that count as "the application". Tests are excluded from
#: config/logging heuristics: a test asserting on a FileHandler is not
#: production logging behaviour.
_PRIMARY_SRC = "src"

#: Directory names that mean "this is a test tree".
_TEST_DIRS = frozenset({"tests", "test"})


def _looks_like_test(path: Path) -> bool:
    """True when `path` is a test module or lives in a test directory.

    Deliberately structural rather than a substring scan over the whole path.
    A first version tested `"test_" in path.as_posix()`, which is wrong twice
    over: it skipped unrelated files whose names merely CONTAIN the substring
    (so a module like `greatest_hits.py` was invisible), and it skipped every
    file under a pytest `tmp_path`, because that directory is named after the
    running test.
    """
    if _TEST_DIRS & set(path.parts):
        return True
    name = path.name
    return (name == "conftest.py"
            or name.startswith("test_")
            or name.endswith("_test.py"))


@dataclass
class Face:
    """One of the twelve faces, with its measured verdict."""

    number: int
    name: str
    verdict: str
    detail: str
    evidence: Dict[str, object] = field(default_factory=dict)


def _iter_python(root: Path, include_tests: bool = False) -> List[Path]:
    """Return candidate .py files under `root`, skipping vendor/scratch dirs."""
    out: List[Path] = []
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & _SKIP_DIRS:
            continue
        if any(any(part.startswith(pre) for pre in _SKIP_DIR_PREFIXES)
               for part in p.parts):
            continue
        if not include_tests and _looks_like_test(p):
            continue
        out.append(p)
    return sorted(out)


def _count(root: Path, pattern: str, include_tests: bool = False) -> int:
    """Count regex matches across python sources. Never raises on read error."""
    rx = re.compile(pattern)
    total = 0
    for path in _iter_python(root, include_tests=include_tests):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(rx.findall(text))
    return total


def _has_file(root: Path, *names: str) -> Optional[str]:
    for n in names:
        if (root / n).exists():
            return n
    return None


def _glob_any(root: Path, *patterns: str) -> List[str]:
    hits: List[str] = []
    for pat in patterns:
        hits.extend(sorted(p.as_posix() for p in root.glob(pat)))
    return sorted(set(hits))


# --------------------------------------------------------------------------
# Faces
# --------------------------------------------------------------------------

def _mirrored_tree_report(root: Path) -> Tuple[List[str], List[str]]:
    """Compare `scripts/` against `src/SCRIPTS/` for DRIFT, not just overlap.

    Sharing a filename is not itself a defect -- `monitor_learning.py` is a
    deliberate byte-identical mirror kept in sync on purpose, and every
    package has an `__init__.py`. What actually matters is whether the
    mirrored copies have DIVERGED, because then two definitions of the same
    module exist and one of them is wrong.

    Returns (identical, diverged) filename lists.
    """

    left, right = root / "scripts", root / "src/SCRIPTS"
    if not (left.is_dir() and right.is_dir()):
        return [], []
    identical: List[str] = []
    diverged: List[str] = []
    for p in sorted(left.glob("*.py")):
        other = right / p.name
        if p.name == "__init__.py" or not other.exists():
            continue
        try:
            h1 = hashlib.md5(p.read_bytes()).hexdigest()
            h2 = hashlib.md5(other.read_bytes()).hexdigest()
        except OSError:
            diverged.append(p.name)
            continue
        (identical if h1 == h2 else diverged).append(p.name)
    return identical, diverged


def face_1_codebase(root: Path) -> Face:
    """I. Codebase -- one repo, many deploys; no parallel divergent copies."""
    identical, diverged = _mirrored_tree_report(root)
    vcs = (root / ".git").exists()
    verdict = "gap" if diverged else ("pass" if vcs else "partial")
    if diverged:
        detail = ("%d mirrored file(s) have DIVERGED between scripts/ and "
                  "src/SCRIPTS/: %s" % (len(diverged), ", ".join(diverged[:5])))
    elif identical:
        detail = ("single git repo; %d mirrored file(s) verified byte-identical "
                  "(drift risk, not a defect)" % len(identical))
    else:
        detail = "single git repo; no mirrored module tree"
    return Face(1, "Codebase", verdict, detail,
                {"git": vcs, "mirrored_identical": identical,
                 "mirrored_diverged": diverged})


def face_2_dependencies(root: Path) -> Face:
    """II. Dependencies -- explicitly declared and isolated."""
    decl = _glob_any(root, "requirements*.txt", "pyproject.toml", "setup.py")
    pins = 0
    for rel in decl:
        if rel.endswith(".txt"):
            try:
                txt = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            pins += len(re.findall(r"^[A-Za-z0-9_.\-]+\s*==", txt, re.M))
    verdict = "pass" if (decl and pins) else ("partial" if decl else "gap")
    detail = ("%d manifest(s), %d pinned requirement(s)" % (len(decl), pins)
              if decl else "no dependency manifest found")
    return Face(2, "Dependencies", verdict, detail,
                {"manifests": decl, "pinned": pins})


def face_3_config(root: Path) -> Face:
    """III. Config -- stored in the environment, with a declarative sample."""
    env_calls = _count(root, r"os\.environ|getenv\(")
    sample = _has_file(root, ".env.example", ".env.sample", ".env.template")
    live = _has_file(root, ".env")
    if env_calls and sample:
        verdict, detail = "pass", "env-driven (%d reads) + declarative sample (%s)" % (
            env_calls, sample)
    elif env_calls:
        verdict = "partial"
        detail = ("env-driven (%d reads) but NO .env.example -- the required "
                  "config keys are undocumented and undiscoverable" % env_calls)
    else:
        verdict, detail = "gap", "no environment-based configuration detected"
    return Face(3, "Config", verdict, detail,
                {"env_reads": env_calls, "sample": sample, "live_env_file": live})


def _credential_literals(root: Path) -> List[str]:
    """Return `path:line: snippet` for real-looking hardcoded credentials.

    Three exclusions, each earned by a measured false positive:
      * the value is a known placeholder (`anonymous`, `block_begin`, ...)
      * the variable name marks a deliberate fixture (`invalid_code_secret`)
      * the value is shorter than 12 chars and has no secret-looking prefix,
        i.e. it is a label rather than key material
    """
    rx = re.compile(
        r"(?i)(api[_-]?key|password|secret|token|passwd|credential)"
        r"\s*=\s*([\"'])(?P<val>[^\"']{6,})\2")
    secret_prefix = re.compile(r"^(sk-|pk-|ghp_|gho_|xoxb-|AKIA|AIza)")
    name_rx = re.compile(r"(?i)(api[_-]?key|password|secret|token|passwd|credential)\s*=") 
    out: List[str] = []
    for path in _iter_python(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = rx.search(line)
            if not m:
                continue
            val = m.group("val").strip()
            if val.lower() in _PLACEHOLDER_VALUES:
                continue
            # Name-based fixture exclusion: look at the assignment target.
            target = line[:m.start(1)].rsplit("=", 1)[0]
            target_name = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", target)
            name = target_name[-1] if target_name else ""
            if name.lower().startswith(_FIXTURE_PREFIXES):
                continue
            if len(val) < 12 and not secret_prefix.match(val):
                continue
            if not name_rx.search(line):
                continue
            out.append("%s:%d: %s" % (path.as_posix(), i, line.strip()[:120]))
    return out


def face_4_backing_services(root: Path) -> Face:
    """IV. Backing services -- attached resources, not hardcoded."""
    hosts = _count(root, r'"localhost"|\'localhost\'|127\.0\.0\.1')
    creds = _credential_literals(root)
    verdict = "gap" if creds else "pass"
    detail = ("no hardcoded credential literals; %d local endpoint(s) "
              "(localhost is a legitimate local backing service)" % hosts
              if not creds else
              "%d hardcoded credential literal(s): %s"
              % (len(creds), "; ".join(creds[:3])))
    return Face(4, "Backing services", verdict, detail,
                {"localhost_refs": hosts, "credential_literals": creds})


def face_5_build_release_run(root: Path) -> Face:
    """V. Build/release/run -- strictly separated stages."""
    build = _glob_any(root, "build.bat", "build.sh", "Makefile", "*.spec",
                      "scripts/build*.py")
    entry = _glob_any(root, "src/main.py", "main.py", "src/cli.py",
                      "src/CORE/azazael_ctl.py")
    verdict = "pass" if (build and entry) else ("partial" if (build or entry) else "gap")
    detail = "%d build artifact(s), %d entry point(s)" % (len(build), len(entry))
    return Face(5, "Build/release/run", verdict, detail,
                {"build": build[:8], "entry": entry[:8]})


def face_6_processes(root: Path) -> Face:
    """VI. Processes -- stateless, share-nothing.

    Deliberately `manual`: whether a long-lived process actually keeps no
    in-memory session state between requests cannot be decided by reading
    source. We report the *observable* proxy (module-level mutable globals)
    as evidence, but do not convert it into a verdict.
    """
    globals_mut = _count(root, r"^_[A-Z_]+\s*(?::[^=]+)?=\s*(\{|\[|set\()", 0)
    verdict = "manual"
    detail = ("runtime property; %d module-level mutable global(s) found as an "
              "indicative proxy only" % globals_mut)
    return Face(6, "Processes", verdict, detail,
                {"module_level_mutable_globals": globals_mut})


def face_7_port_binding(root: Path) -> Face:
    """VII. Port binding -- the app exports its service by binding a port."""
    binds = _count(root, r"\.bind\(\s*\(|HTTPServer\(|uvicorn\.run\(|serve_forever\(")
    from_env = _count(root, r"(?i)port.{0,40}os\.environ|os\.environ.{0,40}port")
    verdict = "pass" if (binds and from_env) else ("partial" if binds else "gap")
    detail = ("%d bind site(s), %d sourced from the environment" % (binds, from_env))
    return Face(7, "Port binding", verdict, detail,
                {"bind_sites": binds, "port_from_env": from_env})


def face_8_concurrency(root: Path) -> Face:
    """VIII. Concurrency -- scale out via the process model."""
    thread_use = _count(root, r"ThreadPoolExecutor|FreeThreadPool|threading\.Thread")
    proc_use = _count(root, r"multiprocessing|ProcessPoolExecutor|subprocess\.Popen")
    verdict = "pass" if (thread_use and proc_use) else ("partial" if thread_use else "gap")
    detail = "%d thread site(s), %d process site(s)" % (thread_use, proc_use)
    return Face(8, "Concurrency", verdict, detail,
                {"thread_sites": thread_use, "process_sites": proc_use})


def face_9_disposability(root: Path) -> Face:
    """IX. Disposability -- fast startup, graceful shutdown on SIGTERM."""
    signals = _count(root, r"signal\.signal\(|SIGTERM|SIGINT")
    atexit_ = _count(root, r"atexit\.register\(")
    verdict = "pass" if (signals and atexit_) else ("partial" if (signals or atexit_) else "gap")
    detail = "%d signal site(s), %d atexit handler(s)" % (signals, atexit_)
    return Face(9, "Disposability", verdict, detail,
                {"signal_sites": signals, "atexit_handlers": atexit_})


def face_10_dev_prod_parity(root: Path) -> Face:
    """X. Dev/prod parity -- same backing services, small gap, same tooling."""
    test_files = len(_glob_any(root, "tests/test_*.py"))
    same_runtime = _has_file(root, "pytest.ini", "pyproject.toml", "tox.ini")
    verdict = "pass" if (test_files and same_runtime) else ("partial" if test_files else "gap")
    detail = ("%d test module(s) sharing the project config" % test_files
              if same_runtime else
              "%d test module(s) but no shared test config" % test_files)
    return Face(10, "Dev/prod parity", verdict, detail,
                {"test_modules": test_files, "shared_config": same_runtime})


def face_11_logs(root: Path) -> Face:
    """XI. Logs -- treat logs as event streams (stdout), not files."""
    files = _count(root, r"FileHandler\(")
    stdout = _count(root, r"StreamHandler\(|print\(")
    verdict = "pass" if stdout > files else ("partial" if stdout else "gap")
    detail = ("%d stream/stdout site(s) vs %d file handler(s); "
              "event-stream dominant" % (stdout, files) if stdout > files else
              "%d file handler(s) vs %d stream site(s); logs are being written "
              "to files" % (files, stdout))
    return Face(11, "Logs", verdict, detail,
                {"file_handlers": files, "stream_sites": stdout,
                 "ratio": round(stdout / files, 2) if files else None})


def face_12_admin_processes(root: Path) -> Face:
    """XII. Admin processes -- one-off admin tasks run in the same env."""
    admin = _glob_any(root, "scripts/*maintenance*.py", "scripts/*_ctl.py",
                      "scripts/nightly*.py", "scripts/*migrate*.py",
                      "src/CORE/azazael_ctl.py")
    verdict = "pass" if admin else "gap"
    detail = "%d admin/one-off entry point(s) in the same tree" % len(admin)
    return Face(12, "Admin processes", verdict, detail, {"admin": admin[:10]})


FACES: Tuple[Callable[[Path], Face], ...] = (
    face_1_codebase, face_2_dependencies, face_3_config, face_4_backing_services,
    face_5_build_release_run, face_6_processes, face_7_port_binding,
    face_8_concurrency, face_9_disposability, face_10_dev_prod_parity,
    face_11_logs, face_12_admin_processes,
)


def audit(root: Path) -> Dict[str, object]:
    """Run all twelve faces and return the artifact payload."""
    faces = [fn(root) for fn in FACES]
    tally: Dict[str, int] = {}
    for f in faces:
        tally[f.verdict] = tally.get(f.verdict, 0) + 1
    # "Solid" = measured and holding. `manual` and `gap` are both open faces.
    solid = tally.get("pass", 0)
    return {
        "framework": "twelve-factor",
        "faces_total": 12,
        "faces_solid": solid,
        "faces_open": 12 - solid,
        "tally": tally,
        "faces": [asdict(f) for f in faces],
        "note": ("No framework named 'dodecahedron milestone' exists; this is "
                 "the Twelve-Factor App (12 factors) audited against the tree. "
                 "'manual' faces are runtime properties a static scan cannot "
                 "decide and are deliberately NOT counted as passing."),
    }


def render(payload: Dict[str, object]) -> str:
    lines = [
        "Twelve-Factor audit -- %d/12 faces solid, %d open"
        % (payload["faces_solid"], payload["faces_open"]),
        "",
        "%-4s %-22s %-8s %s" % ("#", "FACE", "VERDICT", "DETAIL"),
    ]
    for f in payload["faces"]:
        lines.append("%-4s %-22s %-8s %s" % (
            f["number"], f["name"], f["verdict"], f["detail"]))
    lines.append("")
    lines.append("tally: " + ", ".join(
        "%s=%d" % (k, v) for k, v in sorted(payload["tally"].items())))
    lines.append("gaps and manual faces are both OPEN; manual is never a pass.")
    return "\n".join(lines)


#: Env-var names the code reads. Covers os.environ["X"], os.environ.get("X"),
#: and getenv("X") in both quoting styles.
_ENV_NAME_RX = re.compile(
    r"(?:os\.environ\.get|os\.environ|getenv)"
    r"\s*(?:\[|\(\s*)?\s*[\"'](?P<name>[A-Z][A-Z0-9_]{2,})[\"']")

#: Substrings that mark a variable as secret-bearing (value must stay blank).
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CRED",
                 "AUTH", "PRIVATE")


#: Variables that are part of the host OS / interpreter, not this project's
#: configuration. Emitting these would make the sample noise.
_ENV_IGNORE = re.compile(
    r"^(PYTHON|PIP|VIRTUAL_ENV|PATH$|HOME$|USERPROFILE|APPDATA|LOCALAPPDATA|"
    r"TEMP$|TMP$|SYSTEMROOT|WINDIR|COMSPEC|PATHEXT|NUMBER_OF_PROCESSORS|"
    r"PROCESSOR_|OS$|LANG$|LC_|TERM|SHELL|COMSPEC|HOSTNAME|COMPUTERNAME)")


def discover_env_names(root: Path) -> Dict[str, List[str]]:
    """Map each env var name this project reads to the files that read it."""
    found: Dict[str, List[str]] = {}
    for path in _iter_python(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _ENV_NAME_RX.finditer(text):
            name = m.group("name")
            if _ENV_IGNORE.match(name):
                continue
            # Store RELATIVE paths. An earlier draft wrote absolute paths and
            # leaked the operator's home directory into a committed file.
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name
            found.setdefault(name, []).append(rel)
    return found


def render_env_example(names: Dict[str, List[str]]) -> str:
    """Build a .env.example body: every name, secrets blank, rest commented."""
    secrets = sorted(n for n in names
                     if any(h in n for h in _SECRET_HINTS))
    plain = sorted(n for n in names if n not in secrets)
    lines = [
        "# .env.example -- generated by tools/twelve_factor_audit.py",
        "#",
        "# Twelve-Factor, Factor III: configuration lives in the environment, and",
        "# the REQUIRED keys must be listed declaratively so the app is not",
        "# undiscoverable to a new operator. This file was missing, which is why",
        "# the audit reported Factor III as `partial`.",
        "#",
        "# Copy to .env and fill in. Secret-bearing names are emitted EMPTY on",
        "# purpose -- never commit a real value to this file.",
        "# Regenerate:  python tools/twelve_factor_audit.py --write-env-example",
        "",
        "# ---- secret-bearing: leave blank in the sample ----",
    ]
    for n in secrets:
        lines.append("# read by: %s" % _summarize_sources(names[n]))
        lines.append("%s=" % n)
    lines.append("")
    lines.append("# ---- non-secret / toggles ----")
    for n in plain:
        lines.append("# read by: %s" % _summarize_sources(names[n]))
        lines.append("# %s=" % n)
    lines.append("")
    return "\n".join(lines)


def _summarize_sources(paths: List[str]) -> str:
    uniq = sorted(set(paths))
    if len(uniq) <= 2:
        return ", ".join(uniq)
    return "%s (+%d more)" % (", ".join(uniq[:2]), len(uniq) - 2)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="project root (default: repo root)")
    ap.add_argument("--out", default=None,
                    help="JSON artifact path (default: <root>/TWELVE_FACTOR_AUDIT.json)")
    ap.add_argument("--no-write", action="store_true", help="print only, do not write")
    ap.add_argument("--json", action="store_true", help="print the raw payload")
    ap.add_argument("--write-env-example", action="store_true",
                    help="generate/refresh .env.example from the env names the "
                         "code actually reads (closes Factor III)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _project_root()

    if args.write_env_example:
        names = discover_env_names(root)
        body = render_env_example(names)
        (root / ".env.example").write_text(body, encoding="utf-8")
        print("wrote .env.example -- %d env var name(s) discovered" % len(names))

    payload = audit(root)

    if not args.no_write:
        out = Path(args.out) if args.out else (root / "TWELVE_FACTOR_AUDIT.json")
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render(payload))

    # Exit 1 when a face is a measured GAP. `manual` faces do not fail the
    # run: they are unmeasured, which is different from measured-and-broken.
    return 1 if payload["tally"].get("gap") else 0


if __name__ == "__main__":
    sys.exit(main())
