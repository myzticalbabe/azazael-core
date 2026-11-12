"""technique_exercise_only_audit.py — which registered techniques are EXERCISE-ONLY?

READ THIS BEFORE QUOTING THE OUTPUT. The name says "blind" but the measurement
is narrower, and conflating the two produced a wrong finding once already:

    EXERCISE-ONLY = the registry's `where` symbol has no reference outside
                    `technique_wiring/` (its own exercise adapter calls it).
    BLIND         = no reference ANYWHERE. A STRICT SUBSET of exercise-only.

This tool reports the former. `technique_wiring/` is excluded from the corpus on
purpose (we want *production* callers), so a symbol that only its own adapter
calls scores here — that is exercise-only, NOT "zero callers". Measured against
the excluded region, every symbol this tool reports does have a call site inside
`technique_wiring/`.

GENERAL RULE this tool taught the hard way: a zero is only as good as the corpus
you searched. Always state the corpus, and re-test any zero against the region you
excluded. Four separate false zeros in one session came from excluded,
ill-matched, or error-masked corpora -- the fourth being a `|| echo "NONE"` that
rendered four `fatal: not a git repository` errors as confident zeros.

CORPUS GUARDS (below): this tool now REFUSES to report when the corpus is too
thin to be this repository, because a wrong cwd turns every symbol into a false
exercise-only. A loud refusal is not a zero.

Intra-module calls COUNT as references (v1 wrongly excluded the defining FILE,
which false-flagged every symbol called from a sibling function — e.g. EP461
divergence_gate is called at verification_gate.py:431 in its own module).

A reference must be MODULE-QUALIFIED (engram #682). Matching a binding by its
LAST dotted component alone is a bare token, and a generic one manufactures a
false POSITIVE: `CORE.latent_refine:refine` reduces to `refine`, and every
occurrence of the English word "refine" in prose then scored a production call.
A hit now counts only if its file imports the bound module (directly, or via
`from MOD import sym [as alias]`) or is the module that defines the symbol — the
predicate #682 verified against all 18 disputed rows.

Also separates PROSE `where` values ("ACID agents: ...", survey/doctrine rows)
from real MODULE:symbol bindings -- prose rows have no mechanism to be blind.
"""
import re
from pathlib import Path

EXCL = ("technique_wiring", "__pycache__", "archive", "archived",
        ".build", ".venvs", "node_modules", "site-packages")

#: Minimum caller-corpus size. This repository's production surface is well over
#: a thousand files; a run that sees a handful is a run from the wrong directory,
#: and every symbol in it would score as exercise-only. Chosen as a floor, not a
#: measurement, so it needs no maintenance as the tree grows.
MIN_CORPUS_FILES = 500

# ---------- 0. corpus guard (fail loud, never report a thin-corpus zero) ----------
if not Path("src/CORE/technique_wiring").is_dir() or \
        not Path("src/CORE/technique_runtime.py").is_file():
    raise SystemExit(
        "REFUSING TO RUN: no src/CORE/technique_wiring/ in the current directory.\n"
        "Run this from the repository root (C:/Azazael/azazael). cwd=%s"
        % Path.cwd())

# ---------- 1. resolve `where` ----------
srcs = list(Path("src/CORE/technique_wiring").glob("*.py"))
srcs += [Path("src/CORE/technique_runtime.py")]
wmap = {}
for p in srcs:
    t = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"['\"](EP\d+)['\"](.{0,320}?)(?=['\"]EP\d+['\"]|\Z)", t, re.S):
        ep, blob = m.group(1), m.group(2)
        strs = re.findall(r"['\"]([^'\"]{4,})['\"]", blob)
        cand = [s for s in strs if ":" in s or s.startswith("no-host")]
        if cand and ep not in wmap:
            wmap[ep] = cand[0]

nohost, prose, real = [], [], {}
for e, w in wmap.items():
    if w.startswith("no-host"):
        nohost.append((e, w))
    elif ":" not in w:
        prose.append((e, w))
    else:
        mod, sym = w.split(":", 1)
        mod, sym = mod.strip(), sym.strip()
        # a real binding has a dotted module path and no spaces
        if " " in mod or "/" in sym or not sym:
            prose.append((e, w))
        else:
            real[e] = (mod, sym)

print("EPs with resolved `where` : %d" % len(wmap))
print("  declared no-host        : %d" % len(nohost))
print("  prose / survey (no sym) : %d" % len(prose))
print("  bound to MODULE:symbol  : %d" % len(real))

# ---------- 2. index ONLY the target symbols ----------
targets = set()
for mod, sym in real.values():
    targets.add(sym.split(".")[-1])

#: module dotted path -> the source file that defines it (module or package).
#: A symbol's own module is where an intra-module reference lives. Candidates
#: include the repo ROOT (e.g. `scripts/meta_reflect:...` -> scripts/meta_reflect.py)
#: as well as the `src/` tree -- resolving only under src/ drops the intra-module
#: call of every scripts-rooted binding (missed EP427/EP155/EP270/EP275 once).
def _module_files(mod):
    rel = mod.replace(".", "/")
    cands = [Path("src") / (rel + ".py"), Path("src") / rel / "__init__.py",
             Path(rel + ".py"), Path(rel) / "__init__.py"]
    return [str(p).replace("\\", "/") for p in cands if p.is_file()]

mod_files = {mod: _module_files(mod) for mod, _ in real.values()}

hits = {s: [] for s in targets}
files = []
#: Paths that are NOT production callers. `tests/` and `state/` are excluded
#: because a test or a throwaway probe that merely NAMES a symbol would otherwise
#: score as a production caller -- contamination observed live: adding a probe
#: script that listed the symbol names moved this report from 122/22 to 129/15
#: without a single line of production code changing.
NOT_PRODUCTION = ("tests/", "state/", "tools/technique_exercise_only_audit.py")

for f in Path(".").rglob("*.py"):
    s = str(f).replace("\\", "/")
    if any(x in s for x in EXCL):
        continue
    if any(s.startswith(x) for x in NOT_PRODUCTION):
        continue
    files.append((f, s))
print("caller-corpus files       : %d" % len(files))

# ---------- 2b. corpus sanity: a thin corpus is a wrong cwd, not a finding ----------
if len(files) < MIN_CORPUS_FILES:
    raise SystemExit(
        "REFUSING TO REPORT: caller corpus is only %d files (floor %d).\n"
        "A corpus this thin is a wrong working directory, and it would turn every\n"
        "symbol into a false 'exercise-only'. cwd=%s"
        % (len(files), MIN_CORPUS_FILES, Path.cwd()))

#: per-file import + alias index, built so a module-qualified use is provable
#: and an aliased use (`from MOD import sym as alias`) still counts.
imports = {}
alias_orig = {}
for f, s in files:
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    mods, aliases = set(), {}
    for line in text.splitlines():
        st = line.split("#", 1)[0].strip()
        if st.startswith("from ") and " import " in st:
            head, rest = st[5:].split(" import ", 1)
            mods.add(head.strip())
            for part in rest.split(","):
                bits = part.strip().split(" as ")
                name = bits[0].strip()
                if name:
                    mods.add(head.strip() + "." + name)
                    if len(bits) > 1 and name in targets:
                        aliases[bits[1].strip()] = name
        elif st.startswith("import "):
            for part in st[7:].split(","):
                name = part.strip().split(" as ")[0].strip()
                if name:
                    mods.add(name)
    imports[s] = mods
    alias_orig[s] = aliases
    for i, ln in enumerate(text.splitlines(), 1):
        for tok in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ln)):
            tgt = tok if tok in hits else aliases.get(tok)
            if tgt in hits:
                hits[tgt].append((s, i, ln))

print("indexed target symbols    : %d" % len(hits))

# --- 3. exercise-only = no MODULE-QUALIFIED hit outside technique_wiring/ ---
# A hit is a reference only when its file imports the bound module (directly or
# by symbol) or IS that module -- otherwise a generic word in prose scores.
BLIND, LIVE, qual = [], 0, 0
for ep, (mod, sym) in sorted(real.items(), key=lambda kv: int(kv[0][2:])):
    short = sym.split(".")[-1]
    mfiles = set(mod_files.get(mod, []))
    refs = []
    for s, i, ln in hits.get(short, []):
        st = ln.strip()
        if re.match(r"^(async\s+)?(def|class)\s+%s\b" % re.escape(short), st):
            continue                      # the definition itself, not a use
        if s in mfiles or mod in imports.get(s, ()) \
                or (mod + "." + short) in imports.get(s, ()):
            refs.append((s, i))
    if refs:
        LIVE += 1
        qual += len(refs)
    else:
        BLIND.append((ep, mod + ":" + sym, short))

print("module-qualified refs     : %d" % qual)

print()
print("PRODUCTION-CALLED      : %d" % LIVE)
print("EXERCISE-ONLY (of those): %d" % len(BLIND))
print("  ^ these are called ONLY from technique_wiring/ -- they are NOT uncalled.")
print()
for ep, w, short in BLIND:
    print("  %-7s %-56s sym=%s" % (ep, w[:56], short))
