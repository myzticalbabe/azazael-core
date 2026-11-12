#!/usr/bin/env python3
# coding: utf-8
"""code_evo_pilot.py — human-gated code self-modification pilot (DGM rung 1).

Full closed-loop self-modification (agent rewrites its own code, evaluates,
keeps winners) is the stated big bet. This is rung 1 of that ladder,
deliberately NOT closed-loop: a human (or a supervised agent) supplies the
patch; the machine supplies sandboxing, the test gate, and rollback.

Flow:
    1. Copy the target module tree into a temp sandbox (prod untouched).
    2. Apply a unified-diff patch file inside the sandbox only.
    3. Run the named pytest gate inside the sandbox (importable via path).
    4. Report PASS/FAIL with the failing output. Exit 0 only on gate pass.
    5. With --apply (plus --i-understand, two deliberate flags), copy the
       patched file back over prod. Without both flags nothing is written
       outside the temp dir, which is always removed.

What this proves before rung 2 (autonomous proposer): that patch ->
sandbox -> gate -> apply/rollback is airtight. Rung 2 adds a proposer
that writes candidate diffs; the gate here does not change.

Usage:
    python scripts/code_evo_pilot.py --target src/CORE/WORMS/expansion/morph.py \\
        --patch /tmp/change.diff --tests tests/test_tensorgene.py -q
    python scripts/code_evo_pilot.py --target ... --patch ... --tests ... \\
        --apply --i-understand
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import os as _os

ROOT = Path(__file__).resolve().parent.parent


def _text(blob) -> str:
    """TimeoutExpired stdout/stderr stay bytes even under text=True."""
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return str(blob)


def _run(cmd, cwd, timeout=600, env=None):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "")[-3000:], (r.stderr or "")[-3000:]
    except subprocess.TimeoutExpired as e:
        # rc 124 is authoritative and the partial streams are the evidence: the
        # old return replaced stderr with the literal "TIMEOUT", which dropped
        # pytest's own header/traceback -- the part that says WHERE it hung.
        return 124, _text(e.stdout)[-3000:], (_text(e.stderr)[-3000:] or "TIMEOUT")
    except Exception as e:
        return 127, "", "%s: %s" % (type(e).__name__, str(e)[:200])


def apply_unified_diff(base_dir: Path, diff_text: str) -> dict:
    """Apply a unified diff inside base_dir. Returns {applied:[], failed:[]}.

    Minimal applier for single-file ``--- a/x +++ b/x`` diffs with context.
    Rejects everything else loudly rather than half-applying: a pilot gate
    that mis-applies a patch is worse than one that refuses.
    """
    applied, failed = [], []
    chunks = []
    cur_old, cur_new, cur_hunks = None, None, []
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            cur_old = line[4:].strip()
            if cur_old.startswith("a/"):
                cur_old = cur_old[2:]
        elif line.startswith("+++ "):
            cur_new = line[4:].strip()
            if cur_new.startswith("b/"):
                cur_new = cur_new[2:]
        elif line.startswith("@@"):
            cur_hunks.append([line])
        elif cur_hunks and line[:1] in (" ", "-", "+", "\\"):
            cur_hunks[-1].append(line)
        elif line.strip() == "":
            continue
        else:
            if cur_old is not None:
                chunks.append((cur_old, cur_new, cur_hunks))
                cur_old, cur_new, cur_hunks = None, None, []
    if cur_old is not None:
        chunks.append((cur_old, cur_new, cur_hunks))
    for old, new, hunks in chunks:
        if not old or old == "/dev/null" or not hunks:
            failed.append(str(old))
            continue
        target = (base_dir / old).resolve()
        try:
            target.relative_to(base_dir.resolve())
        except ValueError:
            failed.append("%s (escapes sandbox)" % old)
            continue
        if not target.is_file():
            failed.append("%s (no such file in sandbox)" % old)
            continue
        try:
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception:
            failed.append("%s (unreadable)" % old)
            continue
        out, pos, ok = [], 0, True
        for hunk in hunks:
            try:
                hdr = hunk[0]
                inner = hdr[2:hdr.index("@@", 2)].strip()
                old_range = inner.split(" ")[0].split(",")
                start = int(old_range[0].lstrip("-+")) - 1
            except Exception:
                ok = False
                break
            while pos < start:
                out.append(lines[pos])
                pos += 1
            for dl in hunk[1:]:
                if dl.startswith(" "):
                    if pos >= len(lines) or lines[pos].rstrip("\n") != dl[1:].rstrip("\n"):
                        ok = False
                        break
                    out.append(lines[pos])
                    pos += 1
                elif dl.startswith("-"):
                    if pos >= len(lines) or lines[pos].rstrip("\n") != dl[1:].rstrip("\n"):
                        ok = False
                        break
                    pos += 1
                elif dl.startswith("+"):
                    out.append(dl[1:] + ("\n" if not dl.endswith("\n") else ""))
                elif dl.startswith("\\"):
                    continue
                else:
                    ok = False
                    break
            if not ok:
                break
        if not ok:
            failed.append(str(old))
            continue
        out.extend(lines[pos:])
        try:
            target.write_text("".join(out), encoding="utf-8")
            applied.append(str(old))
        except Exception:
            failed.append("%s (unwritable)" % old)
    return {"applied": applied, "failed": failed}


def _root_pytest_addopts() -> list:
    """The rootdir pytest.ini's addopts, applied to the sandbox pytest too.

    pytest applies an ini's addopts only to runs ROOTED at that ini, and the
    gate runs its pytest inside a TEMP sandbox with no ini — so the blocklist
    that keeps this environment's broken `pytest11` entry points from aborting
    collection (fugue_test -> `import triad`, langsmith_plugin -> ABI-mismatched
    pydantic_core; see the comment in pytest.ini) never reached the gate.
    Measured 2026-09-27: every sandbox gate died in plugin loading before one
    test of the patched code had run. Derived from the rootdir ini, never
    copied, so the two runs cannot drift apart.
    """
    try:
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(str(ROOT / "pytest.ini"), encoding="utf-8")
        raw = parser.get("pytest", "addopts", fallback="").split()
        # Strip -n/--numprocesses: sandbox has no pytest.ini and xdist
        # workers collect different tests (sandbox has only the patched
        # file), causing "Different tests were collected" errors.
        out = []
        skip_next = False
        for tok in raw:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-n", "--numprocesses"):
                skip_next = True
                continue
            if tok.startswith("-n") and len(tok) > 2:
                continue
            out.append(tok)
        return out
    except Exception:
        return []


def run_gate(sandbox: Path, tests: str, python: str = None) -> dict:
    """Run pytest gate inside the sandbox. Returns {passed, output}.

    PYTHONPATH pins the sandbox FIRST: site .pth entries inject the REAL
    src into every interpreter, and conftest imports CORE before test
    modules run their sys.path redirects — without the pin the gate
    silently tests prod instead of the patch (measured 2026-09-26).
    """
    py = python or sys.executable
    env = dict(_os.environ)
    env["PYTHONPATH"] = str(sandbox / "src") + _os.pathsep + env.get(
        "PYTHONPATH", "")
    # Ship the sandbox its own pytest config and point -c at it: pytest's ini
    # discovery otherwise walks UP from the sandbox cwd and adopts a stray
    # pyproject.toml at the drive root (C:\pyproject.toml exists) as the
    # configfile — then conftest loading / collection touches the OS temp
    # root mid-rmtree, producing ambient FileNotFoundError collection errors
    # (measured 2026-10-20: 16 errors in one gate run, 1 in a single-file
    # repro; `--rootdir .` alone did NOT stop this — it overrides the
    # reported root only, not config discovery).
    ini = sandbox / "pytest.ini"
    if not ini.exists():
        ini.write_text("[pytest]\n", encoding="utf-8")
    code, out, err = _run(
        [py, "-m", "pytest", tests, "-q", "-p", "no:warnings",
         "-p", "no:cacheprovider", "-c", str(ini),
         *_root_pytest_addopts()],
        cwd=sandbox, timeout=900, env=env)
    return {"passed": code == 0, "code": code, "output": (out + err)[-4000:]}


def _record_pilot(stage: str, target: str, detail: dict) -> None:
    """Remember a pilot run. Fail-soft: memory must never break the gate."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        if store is None:
            return
        store.record_event(
            source="code_evo_pilot", event_type="pilot_run",
            data={"stage": stage, "target": target,
                  "detail": json.dumps(detail, default=str)[:2000]},
            severity="info", tags=["pilot", "self-modification"])
    except Exception:
        pass


def pilot(target: str, patch_file: str, tests: str, apply: bool = False,
          understood: bool = False, keep_sandbox: bool = False) -> dict:
    """Full pilot run. Prod is written ONLY with apply+understood on gate pass."""
    target_path = (ROOT / target).resolve()
    try:
        target_path.relative_to(ROOT)
    except ValueError:
        return {"ok": False, "error": "target escapes repo: %s" % target}
    if not target_path.is_file():
        return {"ok": False, "error": "no such target: %s" % target}
    try:
        diff_text = Path(patch_file).read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": "cannot read patch: %s" % str(e)[:200]}
    sandbox = Path(tempfile.mkdtemp(prefix="code_evo_"))
    try:
        # Sandbox carries the target's package context: the module dir tree
        # up to repo root for its package (src/...) plus the tests dir.
        for sub in ("src", "tests"):
            src = ROOT / sub
            if src.is_dir():
                shutil.copytree(src, sandbox / sub,
                                ignore=shutil.ignore_patterns(
                                    "__pycache__", "*.pyc", ".pytest_cache"))
        res = apply_unified_diff(sandbox, diff_text)
        if res["failed"]:
            return {"ok": False, "stage": "apply",
                    "detail": res, "sandbox": str(sandbox)}
        gate = run_gate(sandbox, tests)
        if not gate["passed"]:
            return {"ok": False, "stage": "gate", "gate": gate,
                    "sandbox": str(sandbox)}
        if apply:
            if not understood:
                return {"ok": False, "stage": "apply",
                        "error": "refusing: --apply needs --i-understand",
                        "sandbox": str(sandbox)}
            patched = sandbox / target
            if not patched.is_file():
                return {"ok": False, "stage": "apply",
                        "error": "patched target missing from sandbox",
                        "sandbox": str(sandbox)}
            backup = target_path.with_suffix(target_path.suffix + ".pre-pilot-bak")
            try:
                shutil.copy2(target_path, backup)
                shutil.copy2(patched, target_path)
            except Exception as e:
                return {"ok": False, "stage": "apply",
                        "error": str(e)[:200], "sandbox": str(sandbox)}
            _record_pilot("applied", target,
                          {"gate": gate.get("code"),
                           "backup": str(backup)})
            return {"ok": True, "stage": "applied", "gate": gate,
                    "backup": str(backup),                     "sandbox": str(sandbox)}
        _record_pilot("gate-passed", target, {"gate": gate.get("code")})
        return {"ok": True, "stage": "gate-passed", "gate": gate,
                "sandbox": str(sandbox)}
    finally:
        if not keep_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="code_evo_pilot",
                                 description="Human-gated code-evolution pilot")
    ap.add_argument("--target", required=True, help="Repo-relative module path")
    ap.add_argument("--patch", required=True, help="Unified diff file")
    ap.add_argument("--tests", required=True, help="Pytest gate (node id)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-understand", action="store_true")
    ap.add_argument("--keep-sandbox", action="store_true")
    args = ap.parse_args(argv)
    rep = pilot(args.target, args.patch, args.tests, apply=args.apply,
                understood=args.i_understand, keep_sandbox=args.keep_sandbox)
    print("stage: %s ok=%s" % (rep.get("stage"), rep.get("ok")))
    if rep.get("gate"):
        print(rep["gate"]["output"][-2000:])
    if rep.get("error"):
        print("error: %s" % rep["error"])
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())