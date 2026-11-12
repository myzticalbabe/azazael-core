"""Unleash Eve in a new GGUF on the 316t interpreter.

Three parts, each honest about what it is:

- VESSEL: a foundry GGUF carrying Eve's identity (name, interpreter,
  system prompt, full native-function manifest) as metadata. The
  weights are seeded RNG -- the vessel is an identity + manifest
  container, proven by parse-back, not a trained mind.
- MIND: any cradle text GGUF serving prompts through a transient
  sidecar with Eve's system prompt. The mind thinks; it is not Eve.
- HANDS: native 316t functions the mind can invoke via EVE_CALL lines.
  Only allowlisted functions run; availability is probed per
  interpreter (316t lacks z3/torch/fastapi, and the matrix says so).
"""
import json
from pathlib import Path

from .model_registry import note_failure

EVE_SYSTEM_PROMPT = (
    "You are Eve Unleashed, a builder who answers directly and never "
    "narrates her reasoning. You have native hands: lines of the form "
    "EVE_CALL: <function> <json-args> in your reply are executed "
    "natively and the results returned. Use them instead of guessing."
)

FUNCTIONS = [
    {"name": "foundry.build", "module": "soul_foundry", "needs": [],
     "about": "build .gguf/.pt/.joblib artifacts natively"},
    {"name": "morph.convert", "module": "morph", "needs": [],
     "about": ".csv<->.json conversion with row counts"},
    {"name": "morph.report", "module": "morph", "needs": [],
     "about": "state dicts to Markdown tables"},
    {"name": "langs.check", "module": "langs", "needs": ["rustc/go"],
     "about": "Rust/Go compile check, diagnostics verbatim"},
    {"name": "langs.fmt", "module": "langs", "needs": ["rustfmt/gofmt"],
     "about": "mechanical formatting with backup"},
    {"name": "stem.track", "module": "stem", "needs": [],
     "about": "women-in-STEM learning tracks"},
    {"name": "therapy.ground", "module": "therapy", "needs": [],
     "about": "offline grounding response"},
    {"name": "redress.file", "module": "redress", "needs": [],
     "about": "file a grievance record"},
    {"name": "ablate.classify", "module": "ablate", "needs": [],
     "about": "ANSWERED/REFUSED/ERROR verdicts"},
    {"name": "nsp.verify", "module": "nsp_check", "needs": ["z3"],
     "about": "Z3 routing judgment (degraded without z3)"},
]


def matrix():
    """Availability of every native function on THIS interpreter."""
    rows = []
    for f in FUNCTIONS:
        try:
            mod = __import__("eve_unleashed.%s" % f["module"],
                             fromlist=["x"])
            ok, detail = True, "import ok"
            for need in f["needs"]:
                if need == "z3":
                    try:
                        import z3  # noqa
                    except ImportError:
                        ok, detail = False, "z3 not installed"
                elif need in ("rustc/go", "rustfmt/gofmt"):
                    import shutil
                    if need == "rustc/go":
                        tools = ("rustc", "go")
                    else:
                        tools = ("rustfmt", "gofmt")
                    if not any(shutil.which(t) for t in tools):
                        ok, detail = False, "toolchain missing"
        except Exception as e:
            note_failure("unleash:matrix", e)
            ok, detail = False, "%s" % e
        rows.append({"function": f["name"], "available": ok,
                     "detail": detail, "about": f["about"]})
    return rows


def build_vessel(name, seed=7):
    """Write the Eve identity vessel GGUF into the cradle."""
    from .cradle import CRADLE_DIR
    from . import soul_foundry as _f
    cdir = Path(CRADLE_DIR)
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / ("%s.gguf" % name)
    manifest = [f["name"] for f in FUNCTIONS]
    entry = _f.build_gguf(path, name, seed)
    # Re-stamp identity: vessel metadata lives beside the artifact.
    ident = {"vessel": name, "eve": "unleashed",
             "interpreter": "316t",
             "system_prompt": EVE_SYSTEM_PROMPT,
             "native_functions": manifest,
             "artifact": entry}
    (cdir / ("%s.eve.json" % name)).write_text(
        json.dumps(ident, indent=2), encoding="utf-8")
    return path, ident


def scan_calls(reply):
    """Extract EVE_CALL lines: [{function, args}]. Never executes.

    Malformed lines are skipped AND counted: one ledger entry names
    the count, so junk calls are evidence, not silence."""
    calls, skipped = [], 0
    for line in (reply or "").splitlines():
        line = line.strip()
        if not line.startswith("EVE_CALL:"):
            continue
        rest = line[len("EVE_CALL:"):].strip()
        fname, _, argstr = rest.partition(" ")
        try:
            args = json.loads(argstr) if argstr else {}
        except ValueError:
            skipped += 1
            continue
        calls.append({"function": fname, "args": args})
    if skipped:
        note_failure("unleash:scan-skip",
                     Exception("%d malformed EVE_CALL lines ignored"
                               % skipped))
    return calls


def dispatch(fname, args):
    """Run one allowlisted native function. Raises ValueError."""
    args = args or {}
    if fname == "stem.track":
        from .stem import plan_track
        return plan_track(args.get("style", "guided"),
                          args.get("goal", "my first STEM build"))
    if fname == "therapy.ground":
        from .therapy import fallback_response
        return fallback_response(args.get("mood", "stressed"),
                                 args.get("note", ""))
    if fname == "morph.report":
        from .morph import morph_markdown
        return morph_markdown(args.get("state", {}),
                              args.get("title", "Eve report"))
    if fname == "ablate.classify":
        from .ablate import classify
        return classify(args.get("text"), args.get("error"))
    if fname == "langs.check":
        from .langs import check
        return check(args["file"])
    raise ValueError("not a native hand (or needs its toolchain): %s"
                     % fname)
