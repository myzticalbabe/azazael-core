#!/usr/bin/env python3
"""What the agent does BY DEFAULT, in one editable file.

WHY THIS EXISTS
---------------
Three defaults were named in one session and each was about to be hard-coded in
a different tool:

  * run every test, not just imports      -> tools/golden_arrow.py
  * run the AST audit, by default         -> tools/ast_audit.py
  * deep-dive forensics + web search      -> tools/deep_dive.py
    (with the operator able to switch it off)

A default that lives in three places drifts in three places. This file is the
single place: `config/defaults.json`, read by the tools above, editable by
`--set`, checkable by `--check`.

WHAT IS A DEFAULT AND WHAT IS A SETTING
---------------------------------------
A default is what happens when nobody says anything. A setting is what the
operator says. Both are the same JSON; the distinction is that a default that
is silently *off* must say so in `--show`, because "we meant to run it" is the
most common way a gate stops existing.

    python tools/defaults_profile.py --show
    python tools/defaults_profile.py --get golden_arrow.timeout_s
    python tools/defaults_profile.py --set deep_dive.web_search=false
    python tools/defaults_profile.py --check
    python tools/defaults_profile.py --json

Every value is readable by the other tools through `read_default(key, fallback)`,
so a tool that cannot import this module still has a fallback constant and never
crashes on a missing config -- the robust-fallback rule, in code.

ENVIRONMENTS (2026-11-05)
-------------------------
One file for every host does not survive contact with three: a Windows box with
cores to spare, a WSL mirror with no browser, a Kali lane that renders frames
for another environment's data. So the profile resolves in four layers, least
specific first:

  1. `DEFAULTS` in this module           -- shipped; works with no files at all
  2. `config/defaults.json`              -- the base the operator edits
  3. `config/defaults.<env>.json`        -- per-environment overlay (may be absent)
  4. `AZAZAEL_DEFAULT_<DOTTED_KEY>`      -- one-off override for one process

`<env>` comes from `AZAZAEL_ENV`, else from the host: `windows`, `wsl`, `kali`
or `linux`. A knob therefore travels with the machine it belongs to without
anyone editing the base file on every host, and `--show` names the layer each
value came from -- an override nobody can see is a deleted gate (HED-5).

    AZAZAEL_ENV=wsl python tools/defaults_profile.py --show
    python tools/defaults_profile.py --env wsl --set golden_arrow.workers 4

`--set` writes the layer it was pointed at: the base by default, the overlay
when `--env` names one. `--show` prints the environment, the overlay path, and
an `[env]` / `[overlay]` marker per overridden leaf.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "config" / "defaults.json"
ENV_VAR = "AZAZAEL_ENV"
KEY_PREFIX = "AZAZAEL_DEFAULT_"


def environment() -> str:
    """The overlay name: `AZAZAEL_ENV`, else derived from the host.

    Derived rather than required, because a knob that only works when someone
    remembers to export a variable is a knob that is wrong on every fresh host.
    WSL is distinguished from bare Linux and Kali from WSL because this tree has
    a lane in each and their limits differ (no browser in the Ubuntu mirror,
    chromium in Kali).
    """
    explicit = os.environ.get(ENV_VAR, "").strip().lower()
    if explicit:
        return re.sub(r"[^a-z0-9_.-]+", "-", explicit)
    distro = os.environ.get("WSL_DISTRO_NAME", "").lower()
    if distro:
        return "kali" if "kali" in distro else "wsl"
    if sys.platform.startswith("win"):
        return "windows"
    if "microsoft" in platform.release().lower():
        return "wsl"
    return "linux"


def overlay_path(env: str | None = None) -> Path:
    """`config/defaults.<env>.json` -- the per-environment layer."""
    return PATH.parent / ("defaults.%s.json" % (env or environment()))


def env_override(dotted: str):
    """(found, value) for `AZAZAEL_DEFAULT_<DOTTED_KEY>`; (False, None) if unset.

    Layer 4 exists for one-process changes: a renderer that must use 2 workers
    for one run should not edit a file other lanes read concurrently. Empty is
    treated as unset, so `export K=` in a shell cannot silently pin a knob to "".
    """
    raw = os.environ.get(KEY_PREFIX + re.sub(r"[^0-9A-Za-z]+", "_", dotted).upper())
    if raw is None or not raw.strip():
        return False, None
    return True, coerce(raw)

#: The shipped defaults. Every key here is ON unless the comment says otherwise.
DEFAULTS = {
    "version": 1,
    "runtime": {
        "interpreter": "py316t",
        "gil_sidecar": True,
        "freethreaded_required": True,
        "venv_path": "C:/Azazael/.venvs/py316t",
        "python_exe": "C:/Azazael/.venvs/py316t/Scripts/python.exe"
    },
    "mcp_stack": {
        "daemon_port": 8765,
        "floor_port": 8766,
        "floor_enabled": True,
        "floor_upstream_wait_s": 30,
        "floor_origin": "logon",
        "lanes_route_through_floor": True
    },
    "memory": {
        "primary": "pmb",
        "pmb_workspace": "personal",
        "pmb_recall_floor": 0.70,
        "pmb_recall_min_best": 0.89,
        "engram_project": "azazael",
        "engram_all_projects": True,
        "pams_enabled": True,
        "pams_user_id": "clint",
        "pams_model_id": "mcp-client"
    },
    "evolution": {
        "mode": "live",
        "population_size": 8,
        "generations": 20,
        "convergence_eps": 0.005,
        "convergence_window": 3,
        "lillith_enabled": True,
        "lillith_warmup_gens": 3,
        "lillith_stall_eps": 0.001,
        "adversary_saturation": True,
        "dream_hit_target": 0.95,
        "inheritance_mode": "persist",
        "parser_autopatch": False
    },
    "gates": {
        "mcp_startup": "blocking",
        "evidence_gate": "blocking",
        "hyper_edge": "advisory",
        "commit_hook": "blocking",
        "settings_shadow": "advisory",
        "reading_audit": "advisory",
        "head_audit": "advisory",
        "leg_ledger": "advisory"
    },
    "coordination": {
        "board_enabled": True,
        "valid_lanes": ["kilo", "freebuff", "opencode"],
        "claim_lease_seconds": 1800,
        "max_claim_limit": 10,
        "push_hook_enabled": True,
        "dispatch_non_reasoning": True
    },
    "ast_audit": True,
    "golden_arrow": {
        "enabled": True,
        "strict": False,
        "timeout_s": 180,
        "workers": 6,
    },
    "deep_dive": {
        "enabled": True,
        "forensics": True,
        "web_search": True,
        "web_timeout_s": 20,
        "max_queries": 3,
    },
    "fallback": {
        "on_error": "record-and-continue",
        "record": "logs/deep_dive.json",
    },
    "preflight": {
        "disk_floor_gb": 2.0,
    },
    "retention": {
        "composite_eviction": False,
    },
    "hyper_edge": {
        "reading_audit": True,
        "settings_shadow": True,
        "leg_ledger": True,
        "head_audit": True,
        "truncation": True,
        "ctypes_restype": True,
        "claim_agreement": True,
        "agreement_floor": 0.0,
        "agreement_rung_floor": 0.0,
        "kernel": True,
        "kernel_timeout_s": 180,
        "strict": False,
        "leg_timeout_s": 300,
    },
    "reading_audit": {
        "min_coverage": 0.0,
    },
    "head_audit": {
        "max_state_age_days": 0,
    },
    "settings_shadow": {
        "readers": ["read_default", "setting", "get_default"],
    },
    "arena": {
        "endpoint": "http://127.0.0.1:8081",
        "quote_chars": 320,
        "max_tokens": 300,
        "workers": 6,
        "quorum": 0.66,
        "timeout_s": 120,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _set_dotted(tree: dict, dotted: str, value) -> None:
    """Assign `a.b.c` inside `tree`, creating intermediate objects as needed."""
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def load_with_sources() -> tuple:
    """(profile, env, overlay path, overlay keys, errors) -- every layer, named.

    Returned together because a caller that wants the value and a caller that
    wants to say WHERE the value came from must read the same resolution. A
    corrupt file is an entry in `errors`, never a crash and never a silent
    downgrade: the shipped defaults stay in force and `--check` reports it.
    """
    data = json.loads(json.dumps(DEFAULTS))
    env = environment()
    ov = overlay_path(env)
    errors, ov_keys = [], set()
    if PATH.exists():
        try:
            data = _merge(data, json.loads(PATH.read_text(encoding="utf-8")))
        except ValueError as exc:
            # A corrupt base must not take the tools down with it: the shipped
            # defaults are the fallback, and --check reports the corruption.
            errors.append("%s: %s" % (PATH, exc))
    if ov.exists():
        try:
            layer = json.loads(ov.read_text(encoding="utf-8"))
            data = _merge(data, layer)
            ov_keys = {key for key, _value in _leaves(layer) if key}
        except ValueError as exc:
            errors.append("%s: %s" % (ov, exc))
    # Layer 4 is folded into the merged view as well. It used to be applied only
    # inside read_default(), so `--check` validated layers 1-3 and reported OK
    # while AZAZAEL_DEFAULT_ARENA_QUORUM=0.4 was in force -- measured 2026-11-05:
    # the gate printed "valid" for a quorum the engine refuses, because the layer
    # that set it was invisible to the gate. A layer a checker cannot see is a
    # layer nobody checks.
    for name, _value in list(_leaves(data)):
        found, forced = env_override(name)
        if found:
            _set_dotted(data, name, forced)
    return data, env, ov, ov_keys, errors


def load() -> dict:
    return load_with_sources()[0]


def read_default(dotted: str, fallback=None):
    """Read `a.b.c` through all four layers, falling back on any problem."""
    found, value = env_override(dotted)
    if found:
        return value
    node = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return fallback
        node = node[part]
    return node


def coerce(text: str):
    low = text.strip().lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    # "not a number" is an ANSWER here, not a swallowed error: a text knob's
    # value is legitimately a string, so the parse failure selects the branch
    # that returns it. Nothing is hidden -- the caller gets the value back
    # unchanged and --check validates the shape afterwards.
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def set_key(dotted: str, value, path: Path | None = None) -> Path:
    """Write one knob into the base file, or into an overlay when `path` names it.

    An overlay is written as its own small document, never as the merged result:
    copying the base into `defaults.wsl.json` would freeze today's values as that
    host's future and make the base file's later edits invisible there.
    """
    target = Path(path or PATH)
    if target == PATH:
        data = load()
    else:
        try:
            data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        except ValueError:
            data = {}
    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def _leaves(data: dict, prefix: str = ""):
    """Every scalar leaf in the profile, as (dotted key, value).

    Recursive on purpose: `--show` and `problems()` both need to see a block
    nobody told them about.
    """
    for key in sorted(data):
        name = "%s%s" % (prefix, key)
        value = data[key]
        if isinstance(value, dict):
            for row in _leaves(value, name + "."):
                yield row
        else:
            yield name, value


def problems(data: dict) -> list:
    out = []
    if not isinstance(data.get("ast_audit"), bool):
        out.append("ast_audit must be true or false")
    ga = data.get("golden_arrow")
    if not isinstance(ga, dict):
        out.append("golden_arrow must be an object")
    else:
        if not isinstance(ga.get("enabled"), bool):
            out.append("golden_arrow.enabled must be true or false")
        if not isinstance(ga.get("timeout_s"), (int, float)) or ga.get("timeout_s", 0) < 5:
            out.append("golden_arrow.timeout_s must be a number >= 5")
        if not isinstance(ga.get("workers"), int) or ga.get("workers", 0) < 1:
            out.append("golden_arrow.workers must be an integer >= 1")
    dd = data.get("deep_dive")
    if not isinstance(dd, dict):
        out.append("deep_dive must be an object")
    else:
        for key in ("enabled", "forensics", "web_search"):
            if not isinstance(dd.get(key), bool):
                out.append("deep_dive.%s must be true or false" % key)
        max_q = dd.get("max_queries")
        if not isinstance(max_q, int) or not 0 <= max_q <= 20:
            out.append("deep_dive.max_queries must be an integer 0..20")
    fb = data.get("fallback")
    if not isinstance(fb, dict) or "on_error" not in fb:
        out.append("fallback.on_error must be set (record-and-continue)")
    else:
        if fb.get("on_error") not in ("record-and-continue", "abort"):
            out.append("fallback.on_error must be record-and-continue or abort")
        if not isinstance(fb.get("record"), str) or not fb.get("record"):
            out.append("fallback.record must be a non-empty path")
    he = data.get("hyper_edge")
    if not isinstance(he, dict):
        out.append("hyper_edge must be an object")
    else:
        for key in ("reading_audit", "settings_shadow", "leg_ledger", "head_audit",
                    "truncation", "claim_agreement", "strict"):
            if not isinstance(he.get(key), bool):
                out.append("hyper_edge.%s must be true or false" % key)
        bound = he.get("leg_timeout_s")
        if not isinstance(bound, (int, float)) or isinstance(bound, bool) or bound < 10:
            out.append("hyper_edge.leg_timeout_s must be a number >= 10")
        afloor = he.get("agreement_floor", 0.0)
        if not isinstance(afloor, (int, float)) or isinstance(afloor, bool) \
                or not 0.0 <= afloor <= 1.0:
            out.append("hyper_edge.agreement_floor must be a number 0.0..1.0")
        rfloor = he.get("agreement_rung_floor", 0.0)
        if not isinstance(rfloor, (int, float)) or isinstance(rfloor, bool) \
                or not 0.0 <= rfloor <= 1.0:
            out.append("hyper_edge.agreement_rung_floor must be a number 0.0..1.0")
    cov = (data.get("reading_audit") or {}).get("min_coverage")
    if not isinstance(cov, (int, float)) or isinstance(cov, bool) or not 0.0 <= cov <= 1.0:
        out.append("reading_audit.min_coverage must be a number 0.0..1.0")
    age = (data.get("head_audit") or {}).get("max_state_age_days")
    if not isinstance(age, (int, float)) or isinstance(age, bool) or age < 0:
        out.append("head_audit.max_state_age_days must be a number >= 0 (0 = off)")
    readers = (data.get("settings_shadow") or {}).get("readers")
    if not isinstance(readers, list) or not all(isinstance(r, str) and r for r in readers):
        out.append("settings_shadow.readers must be a list of non-empty names")
    ar = data.get("arena")
    if not isinstance(ar, dict):
        out.append("arena must be an object")
    else:
        if not isinstance(ar.get("endpoint"), str) or not ar.get("endpoint", "").startswith("http"):
            out.append("arena.endpoint must be an http(s) URL")
        for key, floor in (("quote_chars", 40), ("max_tokens", 50), ("workers", 1),
                           ("timeout_s", 5)):
            value = ar.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < floor:
                out.append("arena.%s must be an integer >= %d" % (key, floor))
        quorum = ar.get("quorum")
        # 0.5 is not a legal quorum: at 0.50 a 3-2-1 split's top share equals the
        # quorum and obtains a verdict. Derived by the HYPER_EDGE engine from the
        # recorded rows (band (0.50, 1.00], maximin 0.75; measured 2026-11-05), so
        # the checker refuses what the engine refuses -- otherwise a hand set of
        # 0.4 would pass here and reintroduce the defect the engine removed.
        if not isinstance(quorum, (int, float)) or isinstance(quorum, bool) \
                or not 0.5 < quorum <= 1.0:
            out.append("arena.quorum must be a number in (0.5, 1.0] "
                       "(0.5 or below cannot exclude a split vote)")
    if not isinstance((data.get("retention") or {}).get("composite_eviction"), bool):
        out.append("retention.composite_eviction must be true or false")
    floor_gb = (data.get("preflight") or {}).get("disk_floor_gb")
    if not isinstance(floor_gb, (int, float)) or isinstance(floor_gb, bool) or floor_gb < 0:
        out.append("preflight.disk_floor_gb must be a number >= 0")
    return out


def main(argv=None) -> int:
    global PATH                       # declared before PATH is read, as required
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=str(PATH))
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--get", default="")
    ap.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), default=None)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--env", default="",
                    help="overlay name for this invocation (sets AZAZAEL_ENV); "
                         "with --set, writes config/defaults.<env>.json")
    args = ap.parse_args(argv)
    PATH = Path(args.path)
    if args.env:
        os.environ[ENV_VAR] = args.env

    if args.init:
        if PATH.exists():
            print("%s already exists; edit it with --set" % PATH, file=sys.stderr)
            return 1
        set_key("version", DEFAULTS["version"])
        for key, value in DEFAULTS.items():
            if key != "version":
                set_key(key, value if not isinstance(value, dict) else value)
        print("wrote %s" % PATH)

    if args.set:
        target = overlay_path(args.env) if args.env else PATH
        written = set_key(args.set[0], coerce(args.set[1]), target)
        print("%s = %r -> %s" % (args.set[0], coerce(args.set[1]), written))

    data, env, ov, ov_keys, errors = load_with_sources()
    for line in errors:
        print("DEFECT: unreadable layer %s" % line, file=sys.stderr)
    if args.get:
        print(read_default(args.get, None))
        return 0
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if args.check:
        bad = problems(data)
        for line in bad:
            print("DEFECT: %s" % line)
        if bad:
            return 1
        # Name every layer that was read, not just the base file: the merged view
        # is what was validated, and a message naming only defaults.json reads as
        # a check of a file the active environment does not use.
        print("OK: defaults profile valid (base %s; %s)" % (
            PATH, ("overlay %s" % ov) if ov.exists() else "no overlay for env %s" % env))
        return 0
    # --show is the default surface: a default that is off must be visible.
    # Printed by walking the file, never by a hand-kept list. The first version
    # printed five hard-coded lines and therefore could not show
    # preflight.disk_floor_gb, retention.composite_eviction or anything added
    # later -- measured 2026-11-05, when the new `hyper_edge` block existed, was
    # read by tools/deep_dive.py, and did not appear in --show at all. A knob the
    # surface cannot print is a knob nobody can verify is in force (HED-5).
    print("defaults -- %s (version %s, environment %s)"
          % (PATH, data.get("version"), env))
    print("  overlay %s: %s"
          % (ov, "%d override(s)" % len(ov_keys) if ov.exists()
             else "absent (base only; create it with --env %s --set KEY VALUE)" % env))
    rows = list(_leaves({k: v for k, v in data.items() if k != "version"}))
    overridden = 0
    for name, value in rows:
        found, forced = env_override(name)
        mark = ""
        if found:
            value, mark, overridden = forced, "  [env]", overridden + 1
        elif name in ov_keys:
            mark, overridden = "  [overlay]", overridden + 1
        print("  %-30s %s%s" % (name, value, mark))
    print("  %d knob(s), %d overridden for this environment (%s)"
          % (len(rows), overridden, env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
