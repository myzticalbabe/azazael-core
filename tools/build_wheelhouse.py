"""Build cp316t wheelhouse from a requirements list. Skips what is built.

Usage: python tools/build_wheelhouse.py <reqfile> <wheeldir>
Logs one line per package: BUILT <file> | FAILED <pkg> <tail>.
Idempotent: re-run to resume.
"""
import subprocess
import sys

PY = "C:/Azazael/.py316t/python.exe"

# The driver owns its build env: meson/ninja/f2py launchers live in the
# standalone's Scripts. Prepended in-process so no shell (MSYS or cmd)
# can mangle the PATH separator on the way through.


def main() -> int:
    reqfile, wheeldir = sys.argv[1], sys.argv[2]
    import os
    # Backslashes: this env crosses into CreateProcess/meson; forward
    # slashes survive Python but not every native lookup. MESON/NINJA are
    # the documented overrides -- PATH alone never satisfied mesonpy here.
    scripts = os.path.join(os.path.dirname(PY), "Scripts").replace("/", "\\")
    os.environ["PATH"] = scripts + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("MESON", scripts + "\\meson.exe")
    os.environ.setdefault("NINJA", scripts + "\\ninja.exe")

    have = {f.split("-")[0].lower().replace("_", "-") for f in os.listdir(wheeldir)}
    built = failed = skipped = 0
    with open(reqfile, encoding="utf-8") as fh:
        reqs = [ln.strip() for ln in fh if ln.strip()]
    for req in reqs:
        name = req.split("==")[0].lower().replace("_", "-")
        if name in have or any(
            f.lower().startswith(name + "-") for f in os.listdir(wheeldir)
        ):
            skipped += 1
            continue
        # --no-build-isolation: backends + compiler already live in the
        # standalone (setuptools, mesonpy, ninja, numpy, Cython). Isolation
        # re-bootstraps pip under 3.16t and every backend import fails there.
        r = subprocess.run(
            [PY, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
             "--wheel-dir", wheeldir, req],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=280,
        )
        if r.returncode == 0:
            new = [
                f
                for f in os.listdir(wheeldir)
                if f.lower().startswith(name + "-")
            ]
            print(f"BUILT {req} -> {new[-1] if new else '?'}", flush=True)
            built += 1
        else:
            tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
            print(f"FAILED {req} :: {(tail[-1] if tail else 'no output')[:160]}",
                  flush=True)
            failed += 1
    print(f"DONE built={built} failed={failed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
