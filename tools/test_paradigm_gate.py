"""P1-P4 testing-paradigm gate (Engram #890 item 4, policy memo #1175).

Blocking check (6th) for .githooks/pre-commit once wired; until then runs
standalone. Scope is deliberately narrow so it cannot false-positive on the
existing tree:

  Rule G1 (P1/P3, blocking): every NEW src/ module (added to the index, not
    yet committed) must be named by at least one tests/ file. A module no
    test references is untestable by construction.
  Rule G2 (P4, advisory): reports @given (Hypothesis) coverage over the
    changed src/ files. Never fails; instruments drift for the next lane.

Usage:
  python tools/test_paradigm_gate.py          # staged (index vs HEAD)
  python tools/test_paradigm_gate.py --all    # all src/ modules (census)

Exit 0 = gate green, 1 = G1 violation (names the module).
Subprocess calls pin encoding="utf-8", errors="replace" (#590/#591 r5).
"""
import subprocess
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60).stdout


_unreadable = []


def _stem(rel):
    """Module stem anchored on the src/ segment, not on repo depth.

    The runner may resolve ROOT one level high (yields azazael/src/...) or
    exact (yields src/...); anchoring on 'src' keeps both correct. A stem
    built from the wrong depth silently over-counts violations, so this is
    load-bearing, not cosmetic."""
    rel = rel.replace("\\", "/")
    if "src/" in rel:
        rel = rel.split("src/", 1)[1]
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel


def _new_src_modules():
    out = _git("diff", "--cached", "--name-only", "--diff-filter=A")
    mods = []
    for line in out.splitlines():
        p = line.strip()
        if p.replace(chr(92), "/").startswith("src/") and p.endswith(".py"):
            mods.append((p, _stem(p)))
    return mods


def _test_files_naming(stem):
    short = stem.split(".")[-1]
    hits = []
    tests = (ROOT / "tests").rglob("test_*.py")
    for tf in tests:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _unreadable.append(tf.name)
            continue
        if stem in text or short in text:
            hits.append(tf.name)
    return hits


def main(argv):
    check_all = "--all" in argv
    if check_all:
        mods = []
        for p in (ROOT / "src").rglob("*.py"):
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            mods.append((rel, _stem(rel)))
    else:
        mods = _new_src_modules()
    violations = []
    for path, stem in mods:
        if not _test_files_naming(stem):
            violations.append((path, stem))
    given = 0
    for tf in (ROOT / "tests").rglob("test_*.py"):
        try:
            given += tf.read_text(
                encoding="utf-8", errors="replace").count("@given")
        except OSError:
            _unreadable.append(tf.name)
    print("PARADIGM_GATE modules=%d violations=%d tests_with_given_markers=%d unreadable_tests=%d"
          % (len(mods), len(violations), given, len(_unreadable)))
    for path, stem in violations:
        print("  G1 new src module with no referencing test: %s (%s)"
              % (path, stem))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
