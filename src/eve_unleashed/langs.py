"""Native Rust/Go/Python in Eve: check, format, fix, plus arch contracts.

Eve shells the real toolchains (rustc/cargo/rustfmt/clippy, go/gofmt/vet)
-- no reimplementation, no mocks. The honest boundary is explicit:

- check: compiler diagnostics verbatim (the bug, named by the compiler).
- fmt/fix: only mechanical, compiler-owned fixes (rustfmt, gofmt) applied
  with a .evelang backup; then re-checked. Semantic bugs are surfaced
  with the compiler's own help text, never guessed at.
"""
import shutil
import subprocess
from pathlib import Path

from .model_registry import note_failure

RUST_TOOLS = ("rustc", "cargo", "rustfmt")
GO_TOOLS = ("go", "gofmt")

#: New architectures Gamora routes to. Names, not paths: the contract is
#: what the arch promises (a callable shape), never a file listing.
ARCH_CONTRACTS = ("gamora", "lucy", "jaeger-316t")


def detect(path):
    """Language of a source file: 'rust', 'go', 'python'. Raises ValueError."""
    suf = Path(path).suffix.lower()
    if suf == ".rs":
        return "rust"
    if suf == ".go":
        return "go"
    if suf == ".py":
        return "python"
    raise ValueError("eve langs handles .rs, .go and .py only: %s" % path)


def have_tools(lang):
    """Tool availability: {tool: path or None}."""
    if lang == "python":
        import sys
        return {"python": sys.executable or None}
    names = RUST_TOOLS if lang == "rust" else GO_TOOLS
    return {t: shutil.which(t) for t in names}


def _run(cmd, timeout=300):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError as e:
        return 127, "", "tool missing: %s" % e
    except subprocess.TimeoutExpired:
        note_failure("langs:timeout", Exception(str(cmd[:2])))
        return 124, "", "compiler timed out"
    return proc.returncode, proc.stdout, proc.stderr


def check(path):
    """Compile-check one file. Returns {lang, ok, diagnostics}."""
    lang = detect(path)
    if lang == "python":
        # Syntax only, never executed, no .pyc written: the builtin
        # compile() names the bug (SyntaxError) the way rustc/go vet do.
        try:
            text = Path(path).read_text(encoding="utf-8")
            compile(text, str(path), "exec")
        except SyntaxError as e:
            return {"lang": lang, "ok": False,
                    "diagnostics": ("%s:%s: %s: %s"
                                      % (e.lineno, e.offset, e.msg,
                                         (e.text or "").strip()))[:2000]}
        except OSError as e:
            return {"lang": lang, "ok": False,
                    "diagnostics": "unreadable: %s" % e}
        return {"lang": lang, "ok": True, "diagnostics": ""}
    if lang == "rust":
        import tempfile
        # rustc cannot emit to NUL (archive rename fails), so emit the
        # throwaway rlib into a temp dir on the same drive instead.
        with tempfile.TemporaryDirectory(prefix="evelang") as tmp:
            out_path = str(Path(tmp) / (Path(path).stem + ".rlib"))
            code, out, err = _run(["rustc", "--edition", "2021",
                                   "--crate-type", "lib", str(path),
                                   "-o", out_path])
        diagnostics = (out + err).strip()
        return {"lang": lang, "ok": code == 0, "diagnostics": diagnostics}
    code, out, err = _run(["go", "vet", str(path)])
    diagnostics = (out + err).strip()
    return {"lang": lang, "ok": code == 0, "diagnostics": diagnostics}


def fmt(path):
    """Format in place with the language's own formatter.

    Backs up to <file>.evelang.bak first. Returns True if bytes changed.
    Python has no stdlib formatter and none is vendored, so .py is an
    honest refusal -- check() is the Python path, not a guessed style.
    """
    lang = detect(path)
    if lang == "python":
        raise ValueError("no Python formatter vendored: check() covers .py")
    p = Path(path)
    bak = p.with_suffix(p.suffix + ".evelang.bak")
    if not bak.exists():
        bak.write_bytes(p.read_bytes())
    before = p.read_bytes()
    if lang == "rust":
        tool = ["rustfmt", "--edition", "2021", str(path)]
    else:
        tool = ["gofmt", "-w", str(path)]
    code, out, err = _run(tool)
    if code != 0:
        note_failure("langs:fmt", Exception((out + err)[:200]))
        raise RuntimeError("formatter failed: %s" % (out + err).strip()[:300])
    return p.read_bytes() != before


def fix(path):
    """Mechanical fix: format, then re-check. Returns the report dict.

    {fixed_by_format, check_before, check_after}. Anything still failing
    after formatting is a semantic bug: reported with diagnostics, not
    guessed at. Python has no formatter step, so its report is the
    check itself with fixed_by_format False.
    """
    before = check(path)
    if before["lang"] == "python":
        return {"lang": "python", "fixed_by_format": False,
                "check_before": before, "check_after": before,
                "clean": before["ok"]}
    changed = fmt(path)
    after = check(path)
    return {"lang": before["lang"], "fixed_by_format": bool(changed),
            "check_before": before, "check_after": after,
            "clean": after["ok"]}


def _repo_root():
    # src/eve_unleashed/langs.py -> parents[2] is the repo checkout.
    return Path(__file__).resolve().parents[2]


def check_arch(name):
    """Native contract check for a new architecture. Returns {arch, ok, detail}.

    gamora: the harness imports and its doctor passes.
    lucy: companion_response carries the offline grounding script.
    jaeger-316t: inject() wraps a prompt with a trace-bearing header.
    Unknown names raise ValueError. Never raises for a known arch:
    a missing checkout or part reports ok False with the reason named.
    """
    if name not in ARCH_CONTRACTS:
        raise ValueError("unknown arch %r (known: %s)"
                         % (name, ", ".join(ARCH_CONTRACTS)))
    import sys
    repo = _repo_root()
    # Standalone-exe proof: a frozen exe has the modules compiled in but
    # no repo checkout on disk. Prefer direct import (works frozen and
    # live); fall back to repo-rooted sys.path only when needed.
    for p in (str(repo / "src"), str(repo / "Project_GAMORA"),
              str(repo / "Project_LUCY")):
        if p not in sys.path:
            try:
                if Path(p).is_dir():
                    sys.path.insert(0, p)
            except Exception:
                pass  # scanner:ignore silent_except_pass -- best-effort path probe; a failed insert just skips that root
    try:
        if name == "gamora":
            import io
            from contextlib import redirect_stdout
            import gamora as _g
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = _g.cmd_doctor(None)
            return {"arch": name, "ok": rc == 0,
                    "detail": "doctor rc=%s" % rc}
        if name == "lucy":
            import lucy as _l
            out = _l.companion_response("stressed", "arch check")
            ok = "Breathe in 4 counts" in out["response"]
            return {"arch": name, "ok": ok,
                    "detail": "grounding %s" % ("present"
                                                 if ok else "MISSING")}
        import jaeger_inject as _j
        out = _j.inject("arch check", capability="eve.langs",
                        arm_id="langs")
        ok = ("<jaeger-316t" in out["injected"]
              and out["trace_id"] in out["injected"])
        return {"arch": name, "ok": ok,
                "detail": "trace %s recorded=%s"
                            % (out["span_id"][:8], out["recorded"])}
    except Exception as e:
        try:
            note_failure("langs:arch", e)
        except Exception:
            pass  # scanner:ignore silent_except_pass -- telemetry must never break the run; the error detail is already returned below
        return {"arch": name, "ok": False, "detail": str(e)[:300]}
