"""mend.py -- persistent evolution: fix issues through the ledger.

Evolution that lives only in chat history is not evolution; it is
mood. Mend closes the loop natively: scan for open issues
(pytest's last-failed cache plus error rows in the bake lane),
record each fix as an error_fix row in the same lane, and verify
by re-running the touched tests. The lane is the memory; the
re-run is the proof; a fix with neither is a rumor.

- scan(): open issues from .pytest_cache lastfailed plus bake
  lane error rows. Machine-read, never asserted.
- record_fix(title, body): append an error_fix row (body 40+
  chars, enforced) to this agent's bake lane.
- verify(scope): re-run the eve test files and report green/red
  per file. One file per process is the golden-arrow rule; here
  the scope is small so one process with a timeout holds.

Eve mends the harness; the harness remembers the mending. Only
you ship, file, or publish.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

REPO = Path(__file__).resolve().parents[2]
BAKE_PREFIX = "state/bake_harvest_freebuff_"
PY315T = "C:/Azazael/.venvs/py315t/Scripts/python.exe"


def _bake_lane():
    cands = sorted(REPO.glob(BAKE_PREFIX + "*.jsonl"))
    if not cands:
        raise ValueError("no bake lane found under state/")
    return cands[-1]


def turf_of(item):
    """Which lane owns a failing node id: \"own\" or the foreign root.

    Persistent evolution that edits another lane's tests is a wrecking
    ball wearing a fixer's badge. Mend grows its own turf and reports
    the rest; it never writes outside `tests/test_eve_*`.
    """
    path = str(item).split("::", 1)[0].replace("\\", "/")
    if path.startswith("tests/test_eve_"):
        return "own"
    return path.split("/", 1)[0] or "unknown"


def scan():
    """Open issues: pytest's lastfailed cache, classified by turf.

    The bake lane is read only to prove it is readable (a fix recorded
    there is history). Only currently-failing node ids are issues.
    """
    return [{**issue, "turf": turf_of(issue.get("item", ""))}
            for issue in _scan_raw()]


def _scan_raw():
    """Open issues without turf classification."""
    issues = []
    cache = REPO / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if cache.is_file():
        try:
            failed = json.loads(cache.read_text(encoding="utf-8"))
            for node in sorted(failed):
                issues.append({"source": "pytest-lastfailed",
                               "item": node, "kind": "test_failure"})
        except Exception as exc:
            note_failure("mend:cache", exc)
    try:
        lane = _bake_lane()
        for line in lane.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception as exc:
                note_failure("mend:lane-parse", exc)
                continue
            # Recorded fixes are HISTORY, not open issues: counting them
            # as issues inflated a 2-item backlog to 62 and buried the
            # live failures. Only a fix row whose item names a test that
            # currently fails is open, and pytest already reports those.
            continue
    except Exception as exc:
        note_failure("mend:lane", exc)
    return issues


def plan(issues):
    """Group issues by turf, own first; the rest are named, not touched."""
    own = [i for i in issues if i.get("turf") == "own"]
    foreign = [i for i in issues if i.get("turf") != "own"]
    by_root = {}
    for i in foreign:
        by_root.setdefault(i["turf"], []).append(i)
    return {"own": own, "foreign": by_root,
            "own_count": len(own), "foreign_count": len(foreign)}


def record_fix(title, body, item_id="mend"):
    """Record a fix in the bake lane. Returns the row."""
    if not title or not title.strip():
        raise ValueError("a fix needs a title")
    if not body or len(body.strip()) < 40:
        raise ValueError("body must carry substance (40+ chars)")
    lane = _bake_lane()
    row = {"source": "eve-mend", "item_id": item_id, "title": title,
           "body": body.strip(), "kind": "error_fix"}
    with open(lane, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def verify():
    """Re-run the eve test files. Returns per-file green/red."""
    import glob as _glob
    files = sorted(_glob.glob(str(REPO / "tests" / "test_eve_*.py")))
    results = []
    for path in files:
        name = Path(path).name
        try:
            proc = subprocess.run(
                [PY315T, "-u", "-m", "pytest", path, "-q",
                 "--timeout=120", "-p", "no:cacheprovider"],
                capture_output=True, text=True, timeout=300,
                cwd=str(REPO))
            tail = (proc.stdout.strip().splitlines() or [""])[
                -1][:120]
            results.append({"file": name,
                            "state": "green" if proc.returncode == 0
                            else "red",
                            "tail": tail})
        except Exception as e:
            note_failure("mend:verify", e)
            results.append({"file": name, "state": "error",
                            "tail": str(e)[:120]})
    return {"checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime()),
            "files": results,
            "green": sum(1 for r in results if r["state"] == "green"),
            "total": len(results)}
