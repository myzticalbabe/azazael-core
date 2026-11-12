#!/usr/bin/env python3
"""ray_serve_verify.py — run src/ORCHESTRATION/ray_serve_deployment.py on real Ray.

Why this exists: `ray` is installed in none of the Windows venvs, so on this host
that module can only be type-checked there. Type-clean is not the same claim as
"the decorator seam produces real deployments", and the seam is exactly what a
type checker cannot see: `serve.deployment` returns a `Deployment` object and
grafts `.bind` at runtime, which is why the call sites read as attributes missing
from a plain class.

Run it where Ray exists (WSL Ubuntu-26.04, ray 2.58.0 in /root/venv-314-gil):

    cd /mnt/c/Azazael/azazael
    PYTHONPATH=src /root/venv-314-gil/bin/python tools/ray_serve_verify.py

Ray Serve's worker processes are separate interpreters, so its own serve extras
must be importable *by them*. Where the venv lacks them (fastapi, starlette,
pydantic, anyio, ...), point `RAY_VERIFY_DEPS` at a directory holding them — a
shim of symlinks to another site-packages is enough, and it keeps an older ray
in that directory from shadowing the venv's own:

    for p in fastapi starlette anyio sniffio pydantic pydantic_core \\
             annotated_types typing_inspection idna typing_extensions.py; do
        ln -sf /home/clint/.local/lib/python3.14/site-packages/$p /tmp/raydeps/$p
    done

Exit codes: 0 both phases pass, 1 a phase failed, 2 no Ray runtime could start
(unavailable is not a pass).
"""
from __future__ import annotations

import os
import sys
import time

sys.path.append(
    os.environ.get("RAY_VERIFY_LOCAL",
                   "/home/clint/.local/lib/python3.14/site-packages"))

import ray                       # noqa: E402  (after the path adjustment)
import ray.serve as serve        # noqa: E402

REPO_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src"
DEPS = os.environ.get("RAY_VERIFY_DEPS", "/tmp/raydeps")
APP = "ray_verify"


def phase_a() -> int:
    """The seam: real Deployment objects, and .bind() builds an app graph."""
    import ORCHESTRATION.ray_serve_deployment as m

    names = sorted(n for n in dir(m) if n.endswith("Deployment"))
    print("deployment attributes:", len(names), flush=True)
    bad = []
    for n in names:
        obj = getattr(m, n)
        is_dep = isinstance(obj, serve.Deployment)
        inner = getattr(obj, "func_or_class", None)
        node = obj.bind() if hasattr(obj, "bind") else None
        print(f"  {n:<32} Deployment={is_dep} inner={getattr(inner, '__name__', None)} "
              f"app_node={type(node).__name__}", flush=True)
        if not (is_dep and inner is not None and node is not None):
            bad.append(n)
    print("phase A:", "PASS" if not bad else f"FAIL {bad}", flush=True)
    return 0 if not bad else 1


def phase_b() -> int:
    """A real rollout: the Serve controller must report the app RUNNING."""
    import ORCHESTRATION.ray_serve_deployment as m

    runtime_env = {"env_vars": {"PYTHONPATH": f"{DEPS}:{REPO_SRC}"}}
    attempts = (
        ("cluster", dict(num_cpus=2, include_dashboard=False, ignore_reinit_error=True,
                         object_store_memory=400 * 1024 * 1024, logging_level="ERROR",
                         runtime_env=runtime_env)),
        ("local_mode", dict(local_mode=True, include_dashboard=False,
                            ignore_reinit_error=True, logging_level="ERROR")),
    )
    started = None
    for label, kwargs in attempts:
        try:
            ray.init(**kwargs)
            started = label
            print(f"  ray.init ok ({label})", flush=True)
            break
        except Exception as exc:                      # name the reason, never hide it
            print(f"  ray.init failed ({label}): {type(exc).__name__}: {str(exc)[:160]}",
                  flush=True)
    if started is None:
        print("phase B: UNAVAILABLE -- no Ray runtime could start on this host", flush=True)
        return 2

    serve.run(m.FeastFeatureStoreDeployment.bind(), name=APP, route_prefix="/verify",
              blocking=False)
    state, deadline = "", time.time() + 120
    while time.time() < deadline:
        state = str(getattr((serve.status().applications or {}).get(APP), "status", ""))
        print("  app status:", state, flush=True)
        if state.lower().endswith("running"):
            break
        time.sleep(5)
    ok = state.lower().endswith("running")
    print("phase B:", f"PASS -- rollout RUNNING ({started})" if ok
          else f"FAIL -- {state!r}", flush=True)
    try:
        serve.shutdown()
    finally:
        ray.shutdown()
    return 0 if ok else 1


def main() -> int:
    print("python", sys.version.split()[0], "| ray", ray.__version__, "|", ray.__file__,
          flush=True)
    rc = phase_a()
    if rc:
        return rc
    return phase_b()


if __name__ == "__main__":
    sys.exit(main())
