"""Eve Unleashed -- the forge: grow the capability a gap names.

`cognition.audit()` reports what Eve does not run (eight named gaps). The
forge turns one of those names into a working contract: a stub module, a
test that states the behaviour, and a run that says red or green.

It writes only under a staging root (`state/forge` by default) -- never
into `src/` or `tests/`. Evolution happens in three steps and the last one
is not the forge's:

  1. contract  -- the gap's acceptance test, generated from its spec;
  2. run       -- pytest on the staged pair; red is the expected first
                  answer and is reported as red, never as a skip;
  3. graduation -- a human or a lane moves the pair into the tree once it
                  is green, and only then does `cognition` stop calling it
                  a gap.

A forge that could write straight into the tree would be an evolution
nobody reviewed. This one cannot, by construction.
"""

import os
import subprocess
import sys

from .cognition import FORGE_GAPS

#: gap name -> (module name, function, test assertion, acceptance command)
#: The assertion is written as the property, not as a number, so it stays
#: true when the implementation changes shape.
SPECS = {
    "consolidation": (
        "consolidate", "consolidate(events)",
        "forty identical answers collapse to one prior with its count",
    ),
    "incubation": (
        "incubate", "incubate(blocked, attempts)",
        "a blocked task returns with a different rung than the one that failed",
    ),
    "priming": (
        "prime", "prime(previous, next_request)",
        "the previous task's host raises the next guess's confidence, and a "
        "disjoint host does not",
    ),
    "prospective-memory": (
        "intentions", "add_intention(text, trigger)",
        "an intention added for a trigger fires once when the trigger is "
        "seen, and never fires twice",
    ),
    "inhibition": (
        "inhibit", "suppress(host, rung, attempts)",
        "three blocked attempts at a rung for a host suppress it for that "
        "host only",
    ),
    "counterfactual-replay": (
        "counterfactual", "replay_choice(record, alternative)",
        "replaying a past choice against the recorded outcome returns "
        "better/same/worse with the delta",
    ),
    "salience": (
        "salience", "weigh(events)",
        "a blocked event outranks a later ok event of the same kind",
    ),
    "affect": (
        "affect", "valence(outcomes)",
        "a losing streak lowers valence and a win raises it, bounded to "
        "-1..1",
    ),
}


def _root(root):
    """Absolute staging root -- a relative path plus cwd=root resolves twice."""
    return os.path.abspath(root or os.path.join("state", "forge"))


def gaps():
    """Every named gap with its spec, so the forge cannot invent targets."""
    out = []
    for name, stratum, what in FORGE_GAPS:
        mod, fn, assertion = SPECS[name]
        out.append({"gap": name, "stratum": stratum, "what": what,
                    "module": mod, "function": fn, "assertion": assertion})
    return out


def contract(gap):
    """The generated module + test text for one gap. Raises on unknown."""
    spec = {g["gap"]: g for g in gaps()}.get(gap)
    if spec is None:
        raise ValueError("unknown gap %r (known: %s)"
                         % (gap, ", ".join(g["gap"] for g in gaps())))
    mod = spec["module"]
    fn = spec["function"]
    module_text = '"""%s -- forged for the gap: %s.\n\nStub: returns the ' \
                  'empty answer until implemented. Nothing here is claimed '\
                  'to work.\n"""\n\n\ndef %s(*args, **kwargs):\n    """%s"""\n' \
                  '    raise NotImplementedError("%s: forged stub, ' \
                  'implement to graduate")\n' % (
                      mod, spec["what"], fn.split("(")[0], spec["assertion"],
                      gap)
    test_text = '"""Contract for the %s gap: %s."""\n\nimport pytest\n\n' \
                'from %s import %s\n\n\ndef test_%s_contract():\n    """%s"""\n' \
                '    with pytest.raises(NotImplementedError):\n        %s()\n' \
                '\n# MOVE: when %s no longer raises, replace this contract ' \
                'with the property assertion:\n#   %s\n' % (
                    gap, spec["what"], mod, fn.split("(")[0],
                    gap.replace("-", "_"), spec["assertion"],
                    fn.split("(")[0], fn.split("(")[0], spec["assertion"])
    return {"gap": gap, "stratum": spec["stratum"], "module": mod,
            "function": fn, "assertion": spec["assertion"],
            "module_text": module_text, "test_text": test_text}


def materialize(gap, root=None):
    """Write the pair under the staging root and run its contract."""
    c = contract(gap)
    root = _root(root)
    os.makedirs(root, exist_ok=True)
    mod_path = os.path.join(root, c["module"] + ".py")
    test_path = os.path.join(root, "test_" + c["module"] + ".py")
    with open(mod_path, "w", encoding="utf-8") as fh:
        fh.write(c["module_text"])
    with open(test_path, "w", encoding="utf-8") as fh:
        fh.write(c["test_text"])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", os.path.basename(test_path),
         "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=120, cwd=root,
        env={**os.environ, "PYTHONPATH": root + os.pathsep
             + os.environ.get("PYTHONPATH", "")})
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    ok = proc.returncode == 0
    # The staged test asserts that the STUB raises. Passing it proves the
    # scaffold is wired, NOT that the capability exists -- so the state is
    # named for the scaffold, and capability stays 'absent' until the
    # property assertion replaces the raises() block.
    return {"gap": gap, "module": mod_path, "test": test_path,
            "state": "scaffold-ok" if ok else "scaffold-broken",
            "capability": "absent",
            "tail": tail[-1] if tail else "no output",
            "note": "the staged test proves the stub is wired and raises; "
                    "the capability is absent until the property assertion "
                    "replaces it, and graduation is a human step"}


def report(root=None):
    """Which gaps have a staged pair; capability stays absent until proven."""
    root = _root(root)
    rows = []
    for g in gaps():
        mod_path = os.path.join(root, g["module"] + ".py")
        test_path = os.path.join(root, "test_" + g["module"] + ".py")
        staged = os.path.exists(mod_path) and os.path.exists(test_path)
        rows.append({"gap": g["gap"], "module": mod_path,
                     "test": test_path, "staged": staged})
    return {"root": root, "rows": rows,
            "staged": sum(1 for r in rows if r["staged"]),
            "total": len(rows)}
