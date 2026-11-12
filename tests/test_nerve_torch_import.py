# coding: utf-8
"""The nerve's lazy torch loader must not leave a live importer behind.

`_LazyTorch` exists so the expansion package stays importable when torch is slow
or absent. Its first version bounded the import by running it on a daemon thread
and walking away when the join expired. That is not a bound, it is a fork: the
abandoned thread keeps executing torch's import body, the half-built module
lands in `sys.modules`, and the next `import torch` *anywhere* in the process is
handed the corpse. Measured cost of that fork: 25 unrelated torch tests failing
with `partially initialized module 'torch' ... has no attribute 'distributed'`,
in two separate long runs -- every uncontended run of the same suite green.

These tests pin the property, not the prose:

* the import happens on the thread that asks for it (no helper thread exists),
* a failed probe declines *without* importing anything in-process,
* a successful load is a *complete* module (the attributes the corpse lacks),
* concurrent first users share one import instead of racing it.

Each assertion is written to fail against the daemon-thread version.
"""

import os
import sys
import threading

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_src_dir = os.path.join(_project_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from CORE.WORMS.expansion.nerve import _LazyTorch  # noqa: E402

#: Attributes a completed `import torch` always has, and a partial one lacks.
_COMPLETE = ("nn", "Tensor", "optim", "autograd")


def test_the_import_runs_on_the_calling_thread(monkeypatch):
    """No helper thread is constructed -- so none can be abandoned.

    Falsified by the daemon-thread version, which constructs exactly one thread
    named ``torch-import`` before any load happens.
    """
    made = []
    real_thread = threading.Thread

    class Recorder(real_thread):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            made.append(kwargs.get("name"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(threading, "Thread", Recorder)
    lazy = _LazyTorch()
    lazy.get()
    assert made == [], f"get() started helper threads: {made}"
    assert not [t for t in threading.enumerate() if t.name == "torch-import"]


def test_a_failed_probe_declines_without_importing(monkeypatch):
    """Unknown-host means numpy, and nothing left running to race.

    ``seconds == 0.0`` is the falsifiable half: it holds only if the in-process
    import never ran. The daemon-thread version ignores the probe entirely and
    returns a module here.
    """
    monkeypatch.setattr(_LazyTorch, "_probe", lambda self: False)
    import importlib.util
    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch absent here: probe premise needs torch installed")
    lazy = _LazyTorch()
    assert lazy.get() is None
    assert lazy.state == "timeout"
    assert lazy.seconds == 0.0


def test_a_ready_module_is_complete():
    """The state that failed in the suite: present but missing its attributes."""
    lazy = _LazyTorch()
    mod = lazy.get()
    if mod is None:
        pytest.skip(f"torch not importable here (state={lazy.state})")
    assert lazy.state == "ready"
    missing = [a for a in _COMPLETE if getattr(mod, a, None) is None]
    assert not missing, f"partially initialized torch: missing {missing}"
    assert lazy.seconds > 0.0, "import time was not recorded -- not observable"


def test_concurrent_first_users_share_one_import():
    """Three threads, one import, no error, one module object.

    The interpreter-level hazard this guards: two threads first-importing torch
    at once is not safe on the free-threaded build (reproduced standalone as
    `ValueError: module functions cannot set METH_CLASS or METH_STATIC`).
    """
    lazy = _LazyTorch()
    modules = []
    errors = []

    def load():
        try:
            modules.append(lazy.get())
        except BaseException as exc:  # noqa: BLE001 - the point is to observe it
            errors.append(repr(exc))

    workers = [threading.Thread(target=load) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert errors == []
    assert len(modules) == 3
    assert len({id(m) for m in modules}) == 1
    if modules[0] is None:
        assert lazy.state in {"absent", "timeout"}
    else:
        assert lazy.state == "ready"
