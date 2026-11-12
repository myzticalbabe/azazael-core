#!/usr/bin/env python3
"""oxenford_spawn.py -- resolve once, smoke always (harness improvement #3).

Every engine used to trust PATH: `shutil.which` found nmake on a captured
vsenv Path, yet CreateProcess raised FileNotFoundError under the same env
(2026-11-06, nasm self-host). PATH-resolution inside a foreign env block is
unreliable, so no engine resolves by search anymore:

  resolve_tool(patterns) -> absolute path (filesystem glob, newest wins)
  smoke_spawn(path, args, env) -> returncode (proves it executes HERE)

Usage from an engine:
  from oxenford_spawn import resolve_tool, smoke_spawn
  nmake = resolve_tool([r"C:\\...\\HostX64\\x64\\nmake.exe"])
  assert smoke_spawn(nmake, ["/?"], env) in (0, 1)
"""
import glob as _glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def resolve_tool(patterns):
    """Newest absolute match wins. None when nothing exists on disk."""
    cands = []
    for pat in patterns:
        cands.extend(_glob.glob(os.path.expandvars(pat)))
    cands = sorted(p for p in cands if os.path.isfile(p))
    return cands[-1] if cands else None


def smoke_spawn(path, args=None, env=None, timeout=120):
    """Run path with args; return its exit code. -1 when it cannot start.

    A tool that cannot spawn is not \"installed but broken\" -- it is absent.
    Callers treat -1 as resolve-the-toolchain, never as retry-the-command.
    """
    try:
        p = subprocess.run([path] + list(args or []),
                           capture_output=True, env=env, timeout=timeout)
        return p.returncode
    except Exception:
        return -1


def normalize_foreign_env(base_env, captured):
    """Merge a captured env (e.g. vcvars `set` output) over a base mapping.

    One spelling per key (case-insensitive: PATH wins over Path/path on
    Windows), because a doubled PATH is how \"which found it but CreateProcess
    did not\" happens.
    """
    merged = {k: v for k, v in base_env.items()}
    seen = {k.lower(): k for k in merged}
    for k, v in captured.items():
        if k.lower() in seen:
            del merged[seen[k.lower()]]
        seen[k.lower()] = k
        merged[k] = v
    return merged


def self_test():
    ok = True
    exe = sys.executable
    got = resolve_tool([os.path.dirname(exe) + os.sep + "*.exe"])
    print("resolve self:", "OK" if got else "FAIL", got)
    ok &= bool(got)
    rc = smoke_spawn(exe, ["-c", "pass"])
    print("smoke self:", "OK rc=%d" % rc if rc == 0 else "FAIL rc=%s" % rc)
    ok &= rc == 0
    import tempfile
    absent = os.path.join(tempfile.gettempdir(), "oxenford-absent-tool-xyz", "x.exe")
    rc = smoke_spawn(absent)
    print("smoke absent:", "OK rc=-1" if rc == -1 else "FAIL rc=%s" % rc)
    ok &= rc == -1
    env = normalize_foreign_env({"Path": "base", "X": "1"},
                                {"PATH": "captured", "Y": "2"})
    keys = sorted(env)
    print("normalize:", "OK" if keys == ["PATH", "X", "Y"] else "FAIL %s" % keys)
    ok &= keys == ["PATH", "X", "Y"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(self_test())
