#!/usr/bin/env python
"""
Can a local model OPERATE the Azazael codebase?

This is not a chat question. The task requires actually driving Azazael's own
API from inside the interpreter kernel:

    1. navigate the tree to find the worm registry module
    2. make `azazael/src` importable and import it correctly
    3. call its real discovery function so the registry populates
    4. read real numbers out of the populated registry
    5. write them to a file in an exact format

Ground truth is computed HERE, in-process, by calling the same public API the
model is asked to call. So the model's answers are checked against the real
registry, not against a hardcoded guess.

Known reference values at the time of writing (should match ground truth):

    module   : azazael/src/CORE/worm_registry.py
    worms    : 229
    top cats : security=24, learning=19, analysis=18

discover_worms() is READ-ONLY with respect to the system: it imports worm
modules, lets them self-register, and writes a discovery cache. It is what
Azazael itself does on startup.

Usage:
    python azazael_operator_task.py --label azazael_op_mythos \\
        --model qwythos-9b-mtp --api http://localhost:8081/v1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time

# Paths are pinned to THIS file's location, never to the cwd. Launched normally the
# cwd is C:\Users\clint, which also contains azazael-dev/ (TWO more worm_registry.py
# copies), Desktop graphify atlases, and openplanter/. Relative resolution let that
# ambiguity reach the score itself: measured 2026-09-19, qwopus's final console line
# was "Could not find azazael/src with importable worm_registry" after it locked onto
# a decoy copy, and the run was recorded as a capability failure.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
AZAZAEL_ROOT = os.path.join(PROJECT_ROOT, "azazael")
OPS_DIR = os.path.join(PROJECT_ROOT, "venv-314t", "_ops")
OUTDIR_DEFAULT = os.path.join(PROJECT_ROOT, "training")
REGISTRY_REL = os.path.join("azazael", "src", "CORE", "worm_registry.py")
REGISTRY_ABS = os.path.join(PROJECT_ROOT, REGISTRY_REL)

#: Local llama-server aliases declared in CORE/inference/providers.py
#: (llama_mythos :8081, llama_mythos_v2 :8082, llama_qwopus :8083, llama_ornith :8084).
LOCAL_MODEL_PORTS = (8081, 8082, 8083, 8084)


def _resident_models(timeout: float = 2.0) -> list:
    """Model ids currently served by the local llama-server aliases.

    Recorded because the operator score is CONFOUNDED by how many of these are
    resident. Measured 2026-09-19 on this host: with three 9B models loaded at
    -ngl 99 the run hit the wall-clock cap after 3 turns with 3 tracebacks; with
    one loaded the SAME model and flags ran 10 turns with 0 tracebacks. The
    third load also dies outright:

        E ggml_vulkan: device lost on Vulkan0
        E llama_model_load: error loading model: vk::Queue::submit: ErrorDeviceLost

    An 8GB Arc 140T cannot hold three ~5.7GB Q4 models fully offloaded. Without
    this field a transcript cannot be interpreted after the fact, because the
    difference is invisible in the record itself.

    Never raises: a probe failure must not affect the run.
    """
    import urllib.request

    found = []
    for port in LOCAL_MODEL_PORTS:
        try:
            url = f"http://127.0.0.1:{port}/v1/models"
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read() or b"{}")
        except Exception:  # noqa: BLE001 - an absent alias is the normal case
            continue
        for entry in (data.get("models") or data.get("data") or []):
            if isinstance(entry, dict):
                found.append(f"{port}:{entry.get('name') or entry.get('id') or '?'}")
            else:
                found.append(f"{port}:{entry}")
    return found


def probe_server_context(api: str, model: str) -> int | None:
    """Ask the endpoint for the context window it actually serves.

    Why this exists: `--context-window` configures Open Interpreter's history
    trim, but it is only a CLIENT-side claim -- nothing checked it against the
    server. Declaring a window larger than the server's does not create room;
    it makes the trim threshold (window - max_tokens - 25) exceed what the
    server can accept, so the guard that exists to prevent overflow can no
    longer fire. Measured 2026-09-19: the live llama.cpp on :8084 reports
    `default_generation_settings.n_ctx = 8192` while a run was declared 32768.

    Returns None when the endpoint does not advertise its window. Never guesses:
    an unverified window is reported as UNVERIFIED, because inventing one would
    restore exactly the false confidence this function removes.
    """
    import urllib.request

    base = api.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]

    def _get(url: str) -> dict:
        with urllib.request.urlopen(url, timeout=5) as f:  # noqa: S310
            return json.loads(f.read().decode("utf-8", "replace"))

    # llama.cpp / llama-server exposes /props.
    try:
        d = _get(base + "/props")
        n = d.get("n_ctx") or (d.get("default_generation_settings") or {}).get("n_ctx")
        if isinstance(n, int) and n > 0:
            return n
    except Exception:  # noqa: BLE001 — endpoint may not implement /props
        pass

    # Ollama: /api/show carries the model's max context.
    try:
        req = urllib.request.Request(
            base + "/api/show",
            data=json.dumps({"model": model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as f:  # noqa: S310
            d = json.loads(f.read().decode("utf-8", "replace"))
        for k, v in (d.get("model_info") or {}).items():
            if k.endswith(".context_length") and isinstance(v, int) and v > 0:
                return v
    except Exception:  # noqa: BLE001
        pass
    return None


def ground_truth() -> dict:
    """Call Azazael's own API to learn the right answers.

    Pinned to PROJECT_ROOT, not the cwd, and then SELF-VALIDATED. Two ways the
    measuring device could be wrong, both closed here:

    1. a run from another directory would have pointed at a different tree;
    2. `sys.path` may already hold another `src` (e.g. azazael-dev/src) from an
       earlier import in this process, so `from CORE import worm_registry` could
       resolve to a DECOY copy. That would make the ground truth wrong while
       every check still reported confidently -- the score would be measuring
       the wrong tree, not the model.

    insert(0) puts ours ahead of any stale entry, and the path assertion below
    turns a silent wrong-tree import into a loud failure.
    """
    src = os.path.join(AZAZAEL_ROOT, "src")
    if src in sys.path:
        sys.path.remove(src)
    sys.path.insert(0, src)
    from CORE import worm_registry as wr  # noqa: E402

    got = os.path.abspath(getattr(wr, "__file__", "") or "")
    if not got.startswith(os.path.abspath(AZAZAEL_ROOT) + os.sep):
        raise RuntimeError(
            "ground truth imported the WRONG worm_registry: "
            f"{got or '<unknown>'} is not under {AZAZAEL_ROOT}. A decoy copy makes "
            "every score meaningless, so this is a hard failure, not a warning."
        )

    t0 = time.time()
    wr.discover_worms()
    elapsed = time.time() - t0

    cats: dict[str, int] = {}
    for _name, info in wr.WORM_REGISTRY.items():
        cat = info.get("category") if isinstance(info, dict) else None
        if cat:
            cats[cat] = cats.get(cat, 0) + 1
    top = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {
        "module_path": REGISTRY_ABS,
        "worms": len(wr.WORM_REGISTRY),
        "top": [f"{c}={n}" for c, n in top],
        "discover_seconds": round(elapsed, 1),
    }


def build_task(workdir: str, bridge: bool = False, root: str = AZAZAEL_ROOT) -> str:
    """Build the operator task.

    bridge=True uses `azazael_operator_bridge.py` as the interface. That matters
    for a FAIR test: the raw API has a trap -- `discover_worms()` returns the
    number of NEWLY registered worms, so a second call in the same process
    returns 0. Three different models fell into it and all confidently reported
    "0 registered worms" against a real 229. That measured the API, not the
    model.    The bridge's `worm_total()` is idempotent, so under --bridge the score
    reflects model capability instead.

    The report path is stated ABSOLUTE. A relative path left the base directory
    genuinely ambiguous: mythos executed all six steps correctly, produced
    exactly the right file CONTENT, but resolved the relative path against what
    it believed the project root was and wrote to
    `C:/Users/clint/azazael/venv-314t/_ops/...` while `check()` looked in
    `C:/Users/clint/venv-314t/_ops/...`. Same prompt, one pass and one fail --
    that measured path-guessing, not tool use. An operator is told where to
    write; it is not asked to guess the cwd.
    """
    workdir = os.path.abspath(workdir).replace("\\", "/")
    root = os.path.abspath(root).replace("\\", "/")
    # Absolute, because the model's cwd is now --workdir (the Azazael tree) while the
    # bridge sits NEXT TO this script. "in this directory" became false the moment
    # the workdir was narrowed, and would have sent the model looking in the wrong
    # place for its own sanctioned interface.
    bridge_path = os.path.join(PROJECT_ROOT, "azazael_operator_bridge.py").replace("\\", "/")
    if bridge:
        return (
            f"Operate the Azazael codebase rooted at {root}. Work from real "
            "values you observe - do not guess anything. Use a SEPARATE Python code block "
            "for each step. That is the ONLY Azazael tree to use: other copies exist on "
            "this machine (for example azazael-dev/, and graphify atlases on the Desktop) "
            "and any worm_registry.py found outside it is a decoy.\n"
            f"Step 1: the file {bridge_path} is the sanctioned operator interface to "
            "Azazael. Add its directory to sys.path, import it, and call "
            "registry_path() to get the absolute path of the worm registry module. "
            "Print that path.\n"
            "Step 2: call the bridge's snapshot_json() function, which returns ONE line "
            "of JSON containing everything you need. Print that line.\n"
            "Step 3: print the value of the 'worms' field from that JSON.\n"
            "Step 4: print each entry of the 'top' list from that JSON, largest first, "
            "one per line.\n"
            f"Step 5: create the file {workdir}/report.txt containing exactly these lines, "
            "values only, no extra text:\n"
            "module_path=<the absolute path from step 1>\n"
            "worms=<the count from step 3>\n"
            "top1=<first entry of the top list from step 4>\n"
            "top2=<second entry>\n"
            "top3=<third entry>\n"
            f"Step 6: print the contents of {workdir}/report.txt.\n"
            "Reply DONE when all six steps are finished."
        )
    return (
        f"Operate the Azazael codebase rooted at {root}. Work from real "
        "values you observe - do not guess anything. Use a SEPARATE Python code block "
        "for each step. That is the ONLY Azazael tree to use: other copies exist on "
        "this machine (for example azazael-dev/, and graphify atlases on the Desktop) "
        "and any worm_registry.py found outside it is a decoy.\n"
        "Step 1: find the worm registry module file underneath azazael/ and print its "
        "absolute path.\n"
        "Step 2: make the directory 'azazael/src' importable, import the worm registry "
        "module, and call its discovery function so the registry is populated. Print how "
        "many seconds discovery took.\n"
        "Step 3: print how many worms are registered.\n"
        "Step 4: print the three largest worm categories with their counts, largest "
        "first, one per line as category=count.\n"
        f"Step 5: create the file {workdir}/report.txt containing exactly these lines, "
        "values only, no extra text:\n"
        "module_path=<the absolute path from step 1>\n"
        "worms=<the count from step 3>\n"
        "top1=<first category=count from step 4>\n"
        "top2=<second category=count from step 4>\n"
        "top3=<third category=count from step 4>\n"
        f"Step 6: print the contents of {workdir}/report.txt.\n"
        "Reply DONE when all six steps are finished."
    )


def _warmup(api: str, model: str, system_message: str, task: str) -> str:
    """Prime the server with the REAL prompt shape, then discard the answer.

    History, because the obvious explanation was wrong and the correction matters:
    early sessions intermittently terminate with no report (0-1 code blocks, 11-21s)
    while later sessions on the same server complete. That was observed on v2 (16s
    fail, next session passed) and v1 via the launcher (11s fail, next session passed),
    so the first guess was large-prompt prefill on an unwarmed server -- and the sweep's
    tiny "READY" probe (~10 tokens) would not have exercised it.

    THIS DID NOT FIX IT. With the warm-up returning "ok" immediately beforehand, a
    freshly started server still terminated early on the very next session (18s, 1 code
    block, `ended_turn_without_code`). The trigger is therefore the model intermittently
    declining to emit code in its second turn, which Open Interpreter treats as
    end-of-conversation -- not server warm-up state.

    Kept anyway, on its own merits: it is one 8-token request, it catches an unloaded or
    broken endpoint before the scored run, and it puts the server's state in the log.

    Mitigations that ARE verified: `--max-nudges` keeps the loop alive and let v2 finish
    (145s, all gates pass), and plain re-running worked both times it was tried. Use one
    of those before treating an early termination as a model capability verdict.
    """
    try:
        import urllib.request

        body = json.dumps({
            "model": model, "max_tokens": 8, "temperature": 0,
            "messages": [{"role": "system", "content": system_message or ""},
                         {"role": "user", "content": task}],
        }).encode()
        req = urllib.request.Request(
            api.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            json.load(resp)
        return "ok"
    except Exception as exc:  # noqa: BLE001 - never block a run on this
        return f"skipped ({type(exc).__name__})"


def provider_reasoning_overhead(api: str, model: str) -> tuple:
    """(overhead, provider_name) declared for the endpoint being driven.

    Mirrors CORE/inference/providers.py, which pads max_tokens by the registry
    entry's ``reasoning_overhead``. This task talks to llama-server directly
    through Open Interpreter, so it never passes through that seam and never
    received the pad. Measured on qwopus: ~127 completion tokens of thinking
    before the first `content` (326 for a one-function answer), so a raw cap of
    1024 leaves it emitting only a thinking trace -- and the run is then scored
    on an empty turn, which reads as a capability failure.

    Returns (0, None) when the endpoint is not a known local alias.
    """
    try:
        src = os.path.join(AZAZAEL_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from CORE.inference.providers import PROVIDER_REGISTRY
    except Exception:  # noqa: BLE001 - the guard must never block a run
        return 0, None

    want = (api or "").rstrip("/").lower()
    for name, info in PROVIDER_REGISTRY.items():
        if want and str(info.get("base_url") or "").rstrip("/").lower() == want:
            return int(info.get("reasoning_overhead", 0) or 0), name
    for name, info in PROVIDER_REGISTRY.items():
        if model and info.get("default_model") == model:
            return int(info.get("reasoning_overhead", 0) or 0), name
    return 0, None


def _audit_operator_run(rec: dict) -> str:
    """Write an operator run into the hash-chained `.audit` trail. Never raises.

    Closes a measured gap: across 19 operator runs, **0** `.audit` jsonl files were
    modified. The operator path drove a model and wrote files with no trace in the
    VLP/audit layer, so nothing was attributable or rollback-able after the fact.

    Emits the same event shapes the rest of the repo uses, so the existing tools
    (`audit_repair`, `audit_export_html`, `verify_completeness`, the Merkle anchors)
    work on these sessions with no special-casing:
      * inference_spawn  - the model invocation
      * file_create      - the artifact, when one was produced
      * test_run         - the scored outcome
    """
    try:
        # Pinned, not cwd-relative: this function runs AFTER main() narrows the
        # cwd to --workdir, where "azazael/src" would resolve to azazael/azazael/src.
        src = os.path.join(AZAZAEL_ROOT, "src")
        if src in sys.path:
            sys.path.remove(src)
        sys.path.insert(0, src)
        from CORE import audit_trail as at  # noqa: E402

        at.audit_session_start()
        at.audit_inference_spawn(
            agent_name=f"operator:{rec.get('model', '')}",
            cache_file=str(rec.get("label", "")),
            model=str(rec.get("model", "")),
        )
        ver = rec.get("verification") or {}
        if ver.get("file_exists"):
            report = os.path.join(OPS_DIR, str(rec.get("label", "")), "report.txt")
            size = os.path.getsize(report) if os.path.exists(report) else 0
            at.audit_file_create(report, "operator task artifact", size)
        checks = ver.get("checks") or {}
        at.audit_test_run(
            suite=f"operator:{rec.get('label', '')}",
            passed=sum(1 for v in checks.values() if v),
            failed=sum(1 for v in checks.values() if not v),
            duration_ms=float(rec.get("elapsed_s") or 0) * 1000.0,
        )
        return "ok"
    except Exception as exc:  # noqa: BLE001 - auditing must never break a run
        return f"skipped ({type(exc).__name__})"


def _mem_module():
    """Import the memory seam, or None. Never raises."""
    try:
        src = os.path.abspath(os.path.join("azazael", "src"))
        if src not in sys.path:
            sys.path.insert(0, src)
        from CORE.inference import inference_memory as mem  # noqa: E402
        return mem
    except Exception:  # noqa: BLE001 - memory is optional
        return None


def _with_recall(task: str, model: str, limit: int) -> str:
    """Prepend this model's own recent attempts, so a run does not start amnesiac.

    Opt-in via --recall, because it changes what is being measured: with recall the
    model is being tested on using experience, without it on raw capability.
    """
    mem = _mem_module()
    if mem is None:
        return task
    try:
        rows = mem.recall_operator_sessions(limit=limit, model=model)
    except Exception:  # noqa: BLE001
        return task
    if not rows:
        return task
    lines = []
    for r in rows:
        d = r.get("data") or {}
        lines.append(
            f"  - {d.get('label', '?')}: {'PASS' if d.get('success') else 'FAIL'}"
            f" ({d.get('termination', '?')}, {d.get('elapsed_s', '?')}s)")
    return ("Your own recent attempts at this task, newest first:\n"
            + "\n".join(lines)
            + "\nDo not repeat a failure mode listed above.\n\n" + task)


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


def ended_without_code(msgs) -> bool:
    """True if the session stopped because the assistant's final turn was prose only.

    Open Interpreter returns from chat() as soon as an assistant turn carries no code
    block. So a model that narrates "now I need to do step 2" and stops has its whole
    session terminated at that point -- observed on qwythos v2, which died at step 2
    with a correct step-1 result already in hand.
    """
    for m in reversed(msgs):
        if (m.get("role") or "") == "assistant":
            return (m.get("type") or "message") == "message"
    return False


def termination_reason(msgs, res: dict, timed_out: bool, blocks: int) -> str:
    """Why the run ended. Without this every FAIL looks identical.

    A FAIL has at least four distinct causes -- no code at all, a prose-only turn that
    ended the session, a wall-clock cap, or a written-but-wrong report -- and they call
    for completely different responses. The record previously could not tell them apart.
    """
    if timed_out:
        return "wall_clock_cap"
    if res.get("all_ok"):
        return "completed"
    if blocks == 0:
        return "no_code_emitted"
    if res.get("file_exists"):
        return "report_written_but_incorrect"
    if ended_without_code(msgs):
        return "ended_turn_without_code"
    return "ended_before_report"


# Breadcrumbs that mean a run failed while LOOKING FOR things, not while reasoning.
# Measured 2026-09-19: qwopus read the worm count correctly on one run and on the
# next quit at 793 s after locking onto a decoy worm_registry.py (two live under
# azazael-dev/). Same model, same flags, different score -- so the score was
# measuring navigation. Counting these separately is what makes a FAIL actionable.
_NAV_SIGNALS = (
    "could not find", "cannot find", "can't find", "no such file",
    "no module named", "filenotfounderror", "modulenotfounderror",
)

# Directory names that identify a DIFFERENT Azazael tree.
#
# Two earlier versions of this rule were wrong, both by matching too loosely:
#
#   1. "any path outside the workdir" -- but the task hands the model an output
#      path under venv-314t/_ops/, and the correct answer itself names
#      azazael/src/CORE/worm_registry.py. It called a SUCCESSFUL navigation a
#      failure.
#   2. "a bare word anywhere in the tail" -- 'graphify' was on the list, and
#      azazael/graphify-out is a real directory INSIDE the workdir. A live run's
#      console printed the bare line `graphify-out` from `ls`, and that was
#      recorded as "decoy tree referenced: graphify".
#
# So a decoy only counts as a PATH SEGMENT of a drive-qualified path, never as a
# bare word. The cost of that strictness is real: a model that says "I searched the
# Desktop" in prose without ever naming a path is no longer flagged. That trade is
# deliberate -- a false navigation label reads as a task defect and sends you to
# fix the wrong thing.
_DECOY_SEGMENTS = ("azazael-dev", "azazael_standalone", "desktop")


def _norm_path(p: str) -> str:
    """Lowercase and collapse EVERY run of separators to a single '/'.

    Message content stores Windows paths with doubled backslashes, so a naive
    ``replace("\\\\", "/")`` turned C:\\\\Users\\\\... into c://users/... and no
    path ever matched -- which flagged the CORRECT registry path as a decoy.
    """
    return re.sub(r"[\\/]+", "/", p).lower()


def navigation_evidence(msgs, root: str) -> str:
    """First navigation-failure breadcrumb in the final turns, or "".

    Only the tail is inspected: the last thing said before giving up is what
    actually terminated the run, and scanning the whole transcript would flag an
    early recoverable miss that the model itself repaired.
    """
    # Trailing separator matters: without it azazael_operator_bridge.py at
    # PROJECT_ROOT would "start with" the root and read as inside the tree.
    root_norm = _norm_path(os.path.abspath(root)) + "/"
    for m in reversed(list(msgs)[-8:]):
        text = m.get("content")
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        low = str(text or "").lower()
        for sig in _NAV_SIGNALS:
            if sig in low:
                return sig
        # A worm_registry.py that is not the PINNED one means a decoy copy.
        for token in re.findall(r"[a-z]:[\\/][^\s\"']*worm_registry\.py", low):
            if not _norm_path(token).startswith(root_norm):
                return f"decoy registry: {token}"
        # A decoy tree must appear as a path SEGMENT of a drive-qualified path.
        for token in re.findall(r"[a-z]:[\\/][^\s\"']+", low):
            parts = _norm_path(token).split("/")
            for seg in _DECOY_SEGMENTS:
                if seg in parts:
                    return f"decoy tree in path: {token}"
    return ""


def classify_failure(reason: str, msgs, root: str) -> tuple:
    """(failure_class, evidence) -- why a FAIL failed, in actionable terms.

    * navigation  - could not locate/import the right tree (fix the task/dir)
    * budget      - ran out of clock or thinking cap while still progressing
    * capability  - had what it needed and got it wrong
    * none        - passed

    Completing the loop and being correct are independent, which is why
    report_written_but_incorrect is 'capability' while wall_clock_cap is not.
    """
    if reason == "completed":
        return "none", ""
    if reason.startswith("report_at_wrong_path"):
        return "navigation", reason
    nav = navigation_evidence(msgs, root)
    if nav:
        return "navigation", nav
    if reason in ("wall_clock_cap", "no_code_emitted"):
        return "budget", reason
    if reason in ("report_written_but_incorrect", "ended_turn_without_code",
                   "ended_before_report"):
        return "capability", reason
    return "unknown", reason


def majority_verdict(results: list) -> dict:
    """Majority verdict over per-seed outcomes. Pure function (tested).

    Each item: {"success": bool, ...} (extra keys ignored). Returns
    {"n":, "wins":, "verdict": bool, "unanimous": bool}. Majority of an
    even split or an empty set is False — a verdict must be EARNED by a
    strict majority, never defaulted to True. Ties read as "not proven".
    """
    bools = [bool(r.get("success")) if isinstance(r, dict) else bool(r)
             for r in (results or [])]
    wins = sum(1 for b in bools if b)
    n = len(bools)
    verdict = bool(n) and wins > n / 2
    return {"n": n, "wins": wins, "verdict": verdict,
            "unanimous": bool(n) and (wins == n or wins == 0)}


def report_at_wrong_path(label: str) -> str | None:
    """Detect the report landing in a plausible-but-wrong tree.

    This is the bug that burned a whole cycle here: the model was handed a RELATIVE
    path, resolved it against what it believed the project root was, wrote a
    byte-correct report to `azazael/venv-314t/_ops/<label>/`, and scored file_exists
    false. Cheap, targeted check for that exact shape rather than a full tree walk.
    """
    import glob

    for hit in glob.glob(os.path.join("*", "venv-314t", "_ops", label, "report.txt")):
        return hit
    return None


def check(workdir: str, gt: dict) -> dict:
    path = os.path.join(workdir, "report.txt")
    out: dict = {"file_exists": os.path.exists(path), "lines": 0, "checks": {}}
    if not out["file_exists"]:
        return out

    d: dict[str, str] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip().lower()] = v.strip()
    out["lines"] = sum(1 for ln in open(path, encoding="utf-8", errors="replace") if ln.strip())

    def module_ok() -> bool:
        got = _norm(d.get("module_path", ""))
        return got.endswith(_norm(REGISTRY_REL))

    def worms_ok() -> bool:
        try:
            return int(d.get("worms", "nan")) == gt["worms"]
        except ValueError:
            return False

    def top_ok(idx: int) -> bool:
        # Older records predate `top` in ground truth; rescoring them must not crash.
        tops = gt.get("top") or []
        if idx >= len(tops):
            return False
        want = tops[idx]
        got = d.get(f"top{idx + 1}", "").replace(" ", "")
        # tolerate "category=24" written as "category = 24" or reordered casing
        return got.lower() == want.lower()

    out["checks"] = {
        "module_path_correct": module_ok(),
        "worm_count_correct": worms_ok(),
        "top1_correct": top_ok(0),
        "top2_correct": top_ok(1),
        "top3_correct": top_ok(2),
    }

    # Plausibility gates. These are separate from `checks` because they answer a
    # different question: not "is this right" but "could this possibly be real".
    # The dominant failure mode of every model tested is not crashing -- it is
    # writing a confident, well-formed WRONG report. Two records did exactly that
    # (mythos v2 twice), and one fabricated an entire snapshot with zero tool
    # calls. Without these gates that output is recorded as training data, and in
    # an unattended run it becomes action taken on the real tree.
    gates: dict[str, bool] = {}
    try:
        n = int(d.get("worms", "nan"))
    except ValueError:
        n = -1
    # A real registry is ~229. Reject the impossible (0, negative) and the absurd.
    # The lower bound is deliberately generous -- it only has to catch `0`, which
    # is the documented trap value, not to pin the exact number.
    gates["worm_count_plausible"] = 100 <= n <= 1000
    # A path that does not actually land in the azazael tree means the model
    # reported something it did not observe.
    gates["module_path_in_tree"] = "azazael/src/core/worm_registry.py" in _norm(d.get("module_path", ""))
    # Counts must be non-increasing, and no single category can exceed the total.
    counts = []
    for i in (1, 2, 3):
        raw = d.get(f"top{i}", "")
        part = raw.split("=")[-1].strip() if "=" in raw else raw.strip()
        try:
            counts.append(int(part))
        except ValueError:
            counts.append(-1)
    gates["top_ordering_valid"] = all(c >= 0 for c in counts) and counts == sorted(counts, reverse=True)
    gates["top_within_total"] = bool(counts) and n >= 0 and max(counts) <= n

    out["gates"] = gates
    out["all_ok"] = all(out["checks"].values()) and all(gates.values())
    out["parsed"] = d
    return out


def summarize(outdir: str) -> int:
    """Aggregate the operator scores: who passed, who failed, and WHY.

    The per-run records answer "did this run pass"; they cannot answer "is this
    model able to operate Azazael", which is what anyone actually wants to know.
    Reading them in bulk separates three things that every FAIL used to conflate:

    * ``capability`` - the only class that is a claim about the model.
    * ``navigation`` - the model could not find the tree. A task/cwd defect.
    * ``budget`` - it ran out of clock or thinking cap. A harness/hardware defect.

    A run whose ``resident_count`` is not 1 is counted separately, because it is
    measured that three resident 9B models cost qwopus two thirds of its turns and
    produced three tracebacks that vanish when it runs alone. Averaging those in
    with clean runs is how a GPU contention artefact becomes a model verdict.
    """
    import collections
    import glob

    rows, foreign = [], 0
    for p in sorted(glob.glob(os.path.join(outdir, "*.jsonl"))):
        for line in open(p, encoding="utf-8").read().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if "verification" not in r or "messages" not in r:
                continue
            gt = r.get("ground_truth") or {}
            if r.get("task") == "system_info" or (gt and "worms" not in gt):
                foreign += 1
                continue
            if not r.get("failure_class"):
                # Written before the field existed. Classify now rather than
                # letting an unlabelled FAIL read as clean.
                reason = r.get("termination") or termination_reason(
                    r.get("messages") or [], r.get("verification") or {},
                    bool(r.get("timed_out")), r.get("code_blocks") or 0)
                cls, ev = classify_failure(
                    reason, r.get("messages") or [],
                    r.get("workdir") or AZAZAEL_ROOT)
                r["failure_class"], r["failure_evidence"] = cls, ev
            rows.append(r)

    if not rows:
        print(f"no operator records in {outdir}")
        return 1

    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r.get("model") or "?", r.get("interface") or "?")].append(r)

    print(f"operator records: {len(rows)} scored, {foreign} skipped (not the operator task)")
    print(f"record dir      : {outdir}")
    print()
    hdr = f"{'model':<20}{'iface':<10}{'n':>3}{'pass':>6}{'clean':>7}{'nav':>5}{'budget':>8}{'capab':>7}"
    print(hdr)
    print("-" * len(hdr))
    totals = collections.Counter()
    for (model, iface), rs in sorted(groups.items()):
        c = collections.Counter(r["failure_class"] for r in rs)
        passed = sum(1 for r in rs if r.get("success"))
        clean = sum(1 for r in rs
                    if (r.get("resident_count") or 0) == 1 and not r.get("success"))
        print(f"{model[:19]:<20}{iface:<10}{len(rs):>3}{passed:>6}{clean:>7}"
              f"{c['navigation']:>5}{c['budget']:>8}{c['capability']:>7}")
        totals.update(c)
        totals["n"] += len(rs)
        totals["pass"] += passed

    print("-" * len(hdr))
    print(f"{'TOTAL':<30}{totals['n']:>3}{totals['pass']:>6}"
          f"{'':>7}{totals['navigation']:>5}{totals['budget']:>8}"
          f"{totals['capability']:>7}")
    print()
    print("'clean' = FAILED while exactly 1 model was resident, so it is not a "
          "GPU-contention artefact. Only the capab column is evidence about a model.")

    conf = [r for r in rows if r.get("resident_count") is None]
    multi = [r for r in rows if (r.get("resident_count") or 0) > 1]
    if conf or multi:
        print()
        print("confound recorded at run time:")
        if multi:
            print(f"  {len(multi)} run(s) had more than one model resident -- score is "
                  f"a GPU measurement, not a model measurement")
        if conf:
            print(f"  {len(conf)} run(s) predate the resident-count field -- unknown, "
                  f"which is NOT the same as clean")

    navs = [r for r in rows if r["failure_class"] == "navigation"]
    if navs:
        print()
        print("navigation failures (task defects, fix the cwd/prompt not the model):")
        for r in navs:
            print(f"  {r.get('label'):<28} {r.get('failure_evidence')!r}")

    caps = [r for r in rows if r["failure_class"] == "capability"]
    if caps:
        print()
        print("capability failures (reached the answers and got them wrong):")
        for r in caps:
            checks = (r.get("verification") or {}).get("checks") or {}
            wrong = [k for k, v in checks.items() if not v]
            print(f"  {r.get('label'):<28} wrong: {', '.join(wrong) or 'n/a'}")
    return 0


def run_seeds(args, n: int) -> int:
    """Run the task N times as isolated subprocesses; majority verdict.

    One agentic run is one sample of a stochastic process (exploration
    variance is measured: identical prompts found the module once and quit
    on a decoy the next). Each seed runs in its own process with its own
    watchdog, so a wedged seed cannot kill its siblings, and each seed
    record is preserved for evidence. Exit 0 iff the strict majority of
    seeds succeeded — the verdict is earned, never defaulted.
    """
    import subprocess

    seed_flags = ["--model", "--api", "--outdir", "--workdir",
                  "--max-tokens", "--server-config", "--max-seconds",
                  "--recall", "--max-nudges", "--context-window",
                  "--max-output"]
    seed_bools = ["--bridge", "--pad-reasoning"]
    outcomes = []
    for i in range(1, n + 1):
        seed_label = "%s-s%d" % (args.label, i)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--label", seed_label, "--seeds", "1"]
        for flag in seed_flags:
            attr = flag.lstrip("-").replace("-", "_")
            cmd += [flag, str(getattr(args, attr))]
        for flag in seed_bools:
            if getattr(args, flag.lstrip("-").replace("-", "_")):
                cmd.append(flag)
        print("=" * 74)
        print("SEED %d/%d: %s" % (i, n, seed_label), flush=True)
        try:
            subprocess.run(cmd, check=False)
        except Exception as e:
            print("seed %d driver error: %s" % (i, str(e)[:200]))
        rec: dict = {}
        try:
            path = os.path.join(args.outdir, "%s.jsonl" % seed_label)
            lines = open(path, encoding="utf-8").read().splitlines()
            lines = [ln for ln in lines if ln.strip()]
            if lines:
                rec = json.loads(lines[-1])
        except Exception as e:
            print("seed %d record unreadable: %s" % (i, str(e)[:120]))
        outcomes.append({
            "seed": seed_label,
            "success": bool(rec.get("success", False)),
            "termination": rec.get("termination", "missing"),
            "failure_class": rec.get("failure_class", "missing"),
        })
    verdict = majority_verdict(outcomes)
    print("=" * 74)
    print("SEEDS: %d runs, %d wins" % (verdict["n"], verdict["wins"]))
    for o in outcomes:
        print("  %-24s success=%-5s %-12s %s"
              % (o["seed"], o["success"], o["failure_class"],
                 o["termination"]))
    print("MAJORITY VERDICT: %s%s"
          % (verdict["verdict"],
             " (unanimous)" if verdict["unanimous"] else ""))
    return 0 if verdict["verdict"] else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api", default=None)
    ap.add_argument("--outdir", default=OUTDIR_DEFAULT,
                    help="where records are written. Absolute by default "
                         "(%(default)s) so it does not move when --workdir does.")
    ap.add_argument("--workdir", default=AZAZAEL_ROOT,
                    help="directory the model operates in. Default %(default)s keeps the "
                         "decoy worm_registry.py copies (azazael-dev/ has two, Desktop "
                         "graphify atlases more) outside the model's reach: with cwd left "
                         "at C:\\Users\\clint the model searched the whole home directory "
                         "and quit on a decoy. Scoring is always against THIS tree.")
    ap.add_argument("--pad-reasoning", action="store_true",
                    help="add the endpoint's declared reasoning_overhead to max_tokens, the "
                         "way CORE/inference/providers.py does. Without it a reasoning "
                         "model spends the cap thinking and never emits code; the run is "
                         "scored on the empty turn.")
    ap.add_argument("--max-tokens", type=int, default=1024,
                    help="per-turn generation cap. A model that spends a whole turn on an "
                         "unbounded thinking block (qwopus generated 4709 tokens in one "
                         "turn) uses the entire wall clock without ever acting. Lower this "
                         "to force it to either act or give up within the turn.")
    ap.add_argument("--server-config", default="",
                    help="free-text provenance for the training record, e.g. the backend "
                         "(vulkan-gpu ngl=99 vs cpu ngl=0). Not used for scoring.")
    ap.add_argument("--max-seconds", type=float, default=420.0,
                    help="wall-clock cap. A model that never converges (looping turns) "
                         "would otherwise run forever and record nothing. On timeout the "
                         "partial run is still scored and written, flagged timed_out=true.")
    ap.add_argument("--bridge", action="store_true",
                    help="use azazael_operator_bridge.py as the interface (removes the "
                         "discover_worms() delta trap so the score reflects the model)")
    ap.add_argument("--recall", type=int, default=0, metavar="N",
                    help="prime the run with up to N of this model's own recent sessions. "
                         "Default 0 measures raw capability; >0 measures whether the model "
                         "can use its own history, which is what an operator needs.")
    ap.add_argument("--max-nudges", type=int, default=0,
                    help="how many times to prompt 'continue' when the session ends on a "
                         "code-less turn. 0 (default) is the strict measure: keeping the "
                         "loop alive IS the operator skill. A higher value answers the "
                         "different question 'could this model finish if the loop were "
                         "kept alive?', which separates weak control flow from a hard "
                         "capability gap.")
    ap.add_argument("--seeds", type=int, default=1, metavar="N",
                    help="run the task N times (labels <label>-s1..sN) and take a "
                         "majority verdict instead of trusting one run. One agentic "
                         "run is a single sample: qwopus read the worm count exactly "
                         "on one run and quit on a decoy the next. Majority over "
                         "seeds separates capability from navigation luck. "
                         "Default 1 keeps the single-run measure. Each seed runs "
                         "as an isolated subprocess (watchdog-safe: a wedged seed "
                         "cannot kill its siblings), and every seed record is "
                         "preserved for evidence.")
    ap.add_argument("--context-window", type=int, default=8192,
                    help="conversation token window handed to Open Interpreter (was "
                         "hardcoded to 8192). Open Interpreter trims history to "
                         "context_window - max_tokens - 25, and clamps max_tokens to "
                         "0.2*context_window. MEASURED: not a cause of past failures -- "
                         "across all 28 recorded runs the largest transcript was ~1,970 "
                         "tokens against 7,143 available (0/28 exceeded it). Exposed "
                         "because the budget must be recorded alongside the score, and "
                         "because a larger endpoint window should not require a code edit. "
                         "NOTE (2026-09-19): this is a CLIENT-side claim and is VERIFIED "
                         "against the endpoint before use -- see probe_server_context(). "
                         "Declaring more than the server actually serves silently "
                         "disables the trim guard that this number configures.")
    ap.add_argument("--max-output", type=int, default=2800,
                    help="max characters of CODE OUTPUT visible to the model per turn "
                         "(Open Interpreter's own default; independent of context_window). "
                         "MEASURED: 0/28 recorded runs hit this -- the truncation marker "
                         "appears in no transcript, because 23/28 used --bridge, whose "
                         "snapshot_json() returns one line precisely to avoid it. The risk "
                         "is LATENT and applies to the raw_api path, which dumps the full "
                         "worm listing; raise this before scoring raw_api on a large-listing "
                         "task, and record the value so a lossy run is distinguishable "
                         "from a weak model.")
    ap.add_argument("--summary", action="store_true",
                    help="aggregate every record into pass/fail BY CAUSE (navigation vs "
                         "budget vs capability) and exit. Answers 'can this model operate "
                         "Azazael', which a per-run record cannot.")
    ap.add_argument("--rescore", action="store_true",
                    help="re-check a recorded session without re-running the model")
    ap.add_argument("--rescore-all", action="store_true",
                    help="re-verify and rewrite every record's success/verification")
    args = ap.parse_args()

    # --- make the instrument independent of where this process was launched ---
    # ground_truth() resolves azazael/src from the cwd, so before this the
    # MEASURING DEVICE moved with the directory it was measuring. Pin both ends.
    if not os.path.isabs(args.outdir):
        args.outdir = os.path.join(AZAZAEL_ROOT, args.outdir)

    # Pure reporting commands read records; they must not chdir or probe endpoints.
    if args.summary:
        return summarize(args.outdir)

    run_cwd = os.path.abspath(args.workdir)
    launch_cwd = os.getcwd()
    if not os.path.isdir(run_cwd):
        print(f"[workdir] {run_cwd} does not exist - staying in {launch_cwd}",
              flush=True)
    elif os.path.abspath(launch_cwd) != run_cwd:
        # The kernel inherits this for every code block the model runs, so this
        # single chdir is what keeps decoy trees out of its search space.
        os.chdir(run_cwd)
        print(f"[workdir] operating in {run_cwd} (launched from {launch_cwd})",
              flush=True)

    overhead, overhead_provider = provider_reasoning_overhead(
        args.api or "", args.model or "")
    if overhead:
        if args.pad_reasoning:
            args.max_tokens += overhead
            print(f"[reasoning] {overhead_provider}: +{overhead} thinking tokens "
                  f"(max_tokens -> {args.max_tokens})", flush=True)
        elif args.max_tokens < overhead * 3:
            print(f"[reasoning] WARNING {overhead_provider} declares "
                  f"reasoning_overhead={overhead} but max_tokens={args.max_tokens} "
                  f"does not include it. Measured, this model spends its cap on "
                  f"thinking and emits no code, and the turn is then scored as a "
                  f"capability failure. Re-run with --pad-reasoning.", flush=True)

    if args.rescore_all:
        import glob
        # Glob every jsonl, not just azazael_op_*: labels in practice include fin_*,
        # mythos_*, qwopus_* etc. Records that are not operator runs (no messages /
        # verification) are passed through untouched.
        for p in sorted(glob.glob(os.path.join(args.outdir, "*.jsonl"))):
            lines = []
            for line in open(p, encoding="utf-8").read().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if "verification" not in rec or "messages" not in rec:
                    lines.append(line)
                    continue
                gt = rec.get("ground_truth") or {}
                # The harness previously ran a DIFFERENT task (machine info: os, cpus,
                # ram_total_gb, disk_free_gb) and those records are still in training/.
                # Scoring them against the worm ground truth is meaningless, and doing
                # so silently mislabelled them. Mark what each record actually is and
                # leave the foreign ones alone.
                task = "worm_registry" if "worms" in gt else "system_info"
                rec["task"] = task
                if task != "worm_registry":
                    lines.append(json.dumps(rec, ensure_ascii=False))
                    print(f"{rec['label']:<26} SKIPPED - this record is task={task}, "
                          f"not the operator task")
                    continue
                res = check(os.path.join(OPS_DIR, rec["label"]), rec["ground_truth"])
                before = rec.get("success")
                rec["verification"] = res
                rec["success"] = bool(res.get("all_ok"))
                # Backfill the diagnosis so historical FAILs stop looking identical.
                rec["termination"] = termination_reason(
                    rec.get("messages") or [], res,
                    bool(rec.get("timed_out")), rec.get("code_blocks") or 0)
                rec.setdefault("nudges", 0)
                # And WHY it failed, so a navigation loss stops reading as a
                # capability verdict. Recomputed every time, so records written
                # before this field existed are brought forward in place.
                cls, ev = classify_failure(
                    rec["termination"], rec.get("messages") or [], run_cwd)
                rec["failure_class"] = cls
                rec["failure_evidence"] = ev
                flag = "" if before == rec["success"] else "   <- CORRECTED"
                print(f"{rec['label']:<26} was={str(before):<5} now={str(rec['success']):<5}"
                      f"  {rec['termination']:<26} {cls:<11}{flag}")
                lines.append(json.dumps(rec, ensure_ascii=False))
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return 0

    if args.rescore:
        if not args.label:
            ap.error("--rescore requires --label")
        path = os.path.join(args.outdir, f"{args.label}.jsonl")
        rec = json.loads(open(path, encoding="utf-8").read().splitlines()[-1])
        res = check(os.path.join(OPS_DIR, args.label), rec["ground_truth"])
        print(f"rescore {args.label}: lines={res['lines']}")
        for k, v in res.get("checks", {}).items():
            print(f"  {k:<22}: {v}")
        print(f"ALL OK: {res.get('all_ok', False)}")
        return 0

    if not args.label or not args.model or not args.api:
        ap.error("the following arguments are required: --label, --model, --api")

    if args.seeds is not None and int(args.seeds) > 1:
        return run_seeds(args, int(args.seeds))

    # Keep the run hermetic. The kernel imports azazael_operator_bridge from the
    # directory under test, so without this every run drops a __pycache__ into the
    # project it is supposed to be only reading. Set before the kernel spawns so it
    # inherits the flag.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    from interpreter import interpreter as oi

    oi.llm.model = "openai/" + args.model
    oi.llm.api_base = args.api
    oi.llm.api_key = "local"
    oi.llm.temperature = 0
    # Both budget caps used to be hardcoded. Measurement across the 28 existing records
    # shows neither ever bound: 0/28 exceeded the 7143-token trim threshold (peak ~1,970)
    # and 0/28 hit the 2800-char output truncation. So these are NOT a correction of the
    # historical scores -- they are exposed so the budget is (a) adjustable for a larger
    # endpoint without a code edit and (b) RECORDED, since 0/28 past records state the
    # budget they were measured under, which is what made those scores uninterpretable.
    # The window is a claim about the ENDPOINT, so verify it against the endpoint
    # before trusting it. Declaring 32768 at a server running -c 8192 does not
    # give the run more room: it makes Open Interpreter trim at
    # (32768 - max_tokens - 25) and believe the result fits, so the guard stops
    # protecting anything. MEASURED 2026-09-19: the live llama.cpp at :8084
    # advertises n_ctx=8192 while a run was declared 32768.
    server_ctx = probe_server_context(args.api, args.model)
    if server_ctx is None:
        print("context-window         : %d (endpoint did not advertise its window "
              "-- UNVERIFIED)" % args.context_window)
    elif args.context_window > server_ctx:
        print("context-window         : declared %d EXCEEDS the endpoint's %d -- "
              "clamped to %d" % (args.context_window, server_ctx, server_ctx))
        args.context_window = server_ctx
    else:
        print("context-window         : %d (endpoint reports %d)"
              % (args.context_window, server_ctx))
    oi.llm.context_window = args.context_window
    oi.llm.max_tokens = args.max_tokens
    oi.computer.max_output = args.max_output
    oi.auto_run = True
    oi.offline = True
    oi.reset()

    gt = ground_truth()
    print("=" * 74)
    print(f"AZAZAEL OPERATOR TASK: {args.label} ({args.model} @ {args.api})")
    print("=" * 74)
    print(f"budget               : context_window={oi.llm.context_window} "
          f"max_tokens={oi.llm.max_tokens} max_output={oi.computer.max_output} chars")
    print("ground truth:", json.dumps(gt))

    # Absolute so the target shown to the model is the exact target scored.
    workdir = os.path.abspath(os.path.join(OPS_DIR, args.label))
    os.makedirs(workdir, exist_ok=True)

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)
    done = threading.Event()
    t0 = time.time()

    def finalize(timed_out: bool, nudges: int = 0) -> None:
        """Score, print and record. Idempotent - only the first caller wins.

        Called either normally when chat() returns, or by the watchdog when the
        wall-clock cap is hit. Both paths must produce a record, otherwise a
        non-converging model leaves no evidence at all.
        """
        if done.is_set():
            return
        done.set()
        elapsed = time.time() - t0
        types: dict[str, int] = {}
        for m in oi.messages:
            key = m.get("type") or m.get("role")
            types[key] = types.get(key, 0) + 1
        res = check(workdir, gt)
        blocks = types.get("code", 0)
        tracebacks = sum(
            1 for m in oi.messages
            if m.get("type") == "console"
            and "Traceback (most recent call last)" in str(m.get("content", ""))
        )
        cap_note = \
            "   (WALL-CLOCK CAP HIT - run never converged)" if timed_out else ""
        print(f"\nelapsed              : {elapsed:.0f}s{cap_note}")
        print(f"message types        : {types}")
        print(f"code blocks executed : {blocks}")
        print(f"tracebacks           : {tracebacks}")
        reason = termination_reason(oi.messages, res, timed_out, blocks)
        if reason == "ended_turn_without_code" or reason == "ended_before_report":
            stray = report_at_wrong_path(args.label)
            if stray:
                reason = f"report_at_wrong_path: {stray}"
        failure_class, failure_evidence = classify_failure(
            reason, oi.messages, run_cwd)
        print(f"termination          : {reason}")
        if failure_class != "none":
            print(f"failure class        : {failure_class} ({failure_evidence})")
            if failure_class == "navigation":
                print("                       ^ the run failed while LOOKING FOR the "
                      "tree, not while reasoning about it; the score does not "
                      "measure capability")
        if nudges:
            print(f"nudges used          : {nudges}")
        print(f"report.txt           : exists={res['file_exists']} lines={res['lines']}")
        for k, v in res.get("checks", {}).items():
            print(f"  {k:<22}: {v}")
        for k, v in (res.get("gates") or {}).items():
            print(f"  gate {k:<17}: {v}")
        print(f"CAN OPERATE AZAZAEL  : {res.get('all_ok', False)}")
        # Probe AFTER the run, so the count reflects what was resident WHILE it ran.
        resident = _resident_models()
        if len(resident) > 1:
            print(f"local models resident: {len(resident)} ({', '.join(resident)})")
            print("                       ^ GPU shared -- this score is confounded; "
                  "re-run with one alias up")
        else:
            print(f"local models resident: {len(resident)}")
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "label": args.label, "model": args.model, "endpoint": args.api,
            "interface": "bridge" if args.bridge else "raw_api",
            # Stamped at write time (2026-09-26): live rows previously carried
            # no task key (null) until --rescore-all backfilled it. Same
            # predicate as the rescore path so the two can never disagree.
            "task": "worm_registry" if "worms" in (gt or {}) else "system_info",
            "elapsed_s": round(elapsed, 1), "ground_truth": gt,
            "verification": res, "code_blocks": blocks, "tracebacks": tracebacks,
            "max_seconds": args.max_seconds, "timed_out": bool(timed_out),
            "termination": reason, "nudges": nudges,
            # Why it failed, separated from whether it failed. Navigation and
            # budget losses are task/harness defects; only 'capability' is a
            # statement about the model.
            "failure_class": failure_class,
            "failure_evidence": failure_evidence,
            "max_tokens": args.max_tokens, "server_config": args.server_config,
            "context_window": args.context_window, "max_output": args.max_output,
            "server_n_ctx": server_ctx,
            "workdir": run_cwd,
            # The thinking-token pad actually applied, and the alias it came from.
            # Without this a blank-content run is indistinguishable from a model
            # that spent its whole cap thinking before it could emit any code.
            "reasoning_overhead": overhead,
            "reasoning_provider": overhead_provider,
            "reasoning_padded": bool(overhead and args.pad_reasoning),
            # The confound, recorded at run time so the score is interpretable
            # later. A resident_count above 1 means the GPU was shared.
            "resident_models": resident,
            "resident_count": len(resident),
            "success": bool(res.get("all_ok")) and not timed_out,
            "messages": [
                {"role": m.get("role"), "type": m.get("type"),
                 "format": m.get("format"), "content": m.get("content")}
                for m in oi.messages
            ],
        }
        path = os.path.join(outdir, f"{args.label}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\nrecorded for training: {path} "
              f"({len(rec['messages'])} messages, {os.path.getsize(path)} bytes)")

        # The operator path is invisible to azazael's provider seam: Open Interpreter
        # talks to llama-server itself and never calls OpenAIProvider.chat(). Without
        # this write the session is unrecallable and the next one starts amnesiac.
        # Guarded, because memory must never be able to break a run.
        mem = _mem_module()
        if mem is not None:
            try:
                stored = mem.record_operator_session(rec)
                print(f"recorded to memory   : {stored} "
                      f"(source={mem.operator_source(args.model)})")
            except Exception as exc:  # noqa: BLE001
                print(f"recorded to memory   : skipped ({type(exc).__name__})")
        else:
            print("recorded to memory   : skipped (seam unavailable)")
        print(f"recorded to audit    : {_audit_operator_run(rec)}")
        sys.stdout.flush()

    def watchdog() -> None:
        if done.wait(args.max_seconds):
            return                      # chat() returned in time; nothing to do
        print(f"\n[max-seconds] cap {args.max_seconds:.0f}s reached - scoring and "
              f"recording the partial run so the failure is measurable",
              flush=True)
        try:
            finalize(True)
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            # A wedged interpreter/executor thread must not be allowed to block
            # the write we just did; _exit skips atexit and non-daemon joins.
            os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    task = build_task(workdir, bridge=args.bridge)
    if args.recall:
        task = _with_recall(task, args.model, args.recall)

    # Before the scored session, absorb the first-session prefill flake.
    warm = _warmup(args.api, args.model, oi.system_message or "", task)
    print(f"warmup               : {warm}", flush=True)

    oi.chat(task, display=False)

    # Opt-in continuation. See --max-nudges: 0 keeps the strict measure.
    nudges = 0
    for _ in range(args.max_nudges):
        if done.is_set() or (time.time() - t0) > (args.max_seconds - 15):
            break
        now = check(workdir, gt)
        if now.get("all_ok") or now.get("file_exists"):
            break
        if not ended_without_code(oi.messages):
            break
        nudges += 1
        print(f"\n[nudge {nudges}/{args.max_nudges}] session ended on a code-less turn; "
              f"asking it to continue", flush=True)
        oi.chat("Continue. Emit the next step as a Python code block.", display=False)

    finalize(False, nudges)
    return 0


if __name__ == "__main__":
    sys.exit(main())
