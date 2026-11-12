#!/usr/bin/env python3
# coding: utf-8
"""head_state — the numbers a session may quote without measuring, with their date.

Every instruction file in this estate used to carry its own cached counts ("5,380
tests exist", "204 skills remain loadable"), and they rot silently: measured
2026-11-02, the test claim was 16% low against a live collection of 6,277. A
reader cannot tell a stale number from a fresh one, so the number is worse than
absent — it reads as knowledge.

So the counts live in exactly one place, written by one command, stamped with the
commit they describe:

  python tools/head_state.py --write     # state/head.json at the current HEAD
  python tools/head_state.py --check     # exit 1 when HEAD has moved or a count drifted
  python tools/head_state.py --show      # print what the prompt may quote

`state/` is gitignored: this is a reading of *this working tree*, not a claim that
travels with the repository. A lane that needs it runs --write and gets a fresh one.
Exit 2 means the instrument could not run, and is never a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = REPO / "state"
HEAD_FILE = STATE_DIR / "head.json"
SKILLS = REPO / ".agents" / "skills"
TESTS = REPO / "tests"
SCHEMA = "head-state/1"
#: A count is "the same reading" within this fraction: collection numbers move with
#: a single parametrised test, and a gate that fails on ±1 gets switched off.
TOLERANCE = 0.10


def _run(argv: Sequence[str], timeout: int = 240) -> subprocess.CompletedProcess:
    # `text=True` alone decodes with the LOCALE codec: on this host that is cp1252,
    # so a pytest line containing a non-ASCII test id raises UnicodeDecodeError
    # inside the collection step and the reading comes back BLIND for a reason that
    # has nothing to do with the suite. Pinned, with errors=replace so one odd byte
    # degrades a line instead of costing the count.
    return subprocess.run(list(argv), cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def git(*args: str) -> str:
    done = _run(("git",) + args, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


class ReadingError(Exception):
    """A count could not be taken. Never zero, never empty string.

    Every function below used to answer with a default on failure (`return 0`
    skills, `return ""` digest, `return None` on an unreadable state file). A
    default is indistinguishable from a measurement once it is written into the
    state file, and this file exists precisely so the instruction files can quote
    a count instead of measuring: a zero that came from an `except` would be
    quoted as fact. The scanner in this repo flagged all four sites.
    """


def _collect(interpreter: str) -> int:
    try:
        done = _run((interpreter, "-m", "pytest", "--collect-only", "-q"), timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadingError("%s could not run: %s" % (interpreter, exc)) from exc
    match = re.search(r"(\d+)\s+tests? collected", done.stdout)
    if not match:
        raise ReadingError("%s produced no 'N tests collected' line (rc=%s)"
                           % (interpreter, done.returncode))
    return int(match.group(1))


def collected_tests() -> int:
    """Collect-only, bounded, on the first interpreter that can answer.

    Raises ReadingError when every interpreter failed, listing each reason. The
    caller decides what a missing count means; this function never invents one.
    """
    reasons: List[str] = []
    for interpreter in (sys.executable, "python", "python3"):
        try:
            return _collect(interpreter)
        except ReadingError as exc:
            reasons.append(str(exc))
    raise ReadingError("; ".join(reasons) or "no interpreter could be tried")


def skills() -> int:
    try:
        entries = list(SKILLS.iterdir())
    except OSError as exc:
        raise ReadingError("cannot list %s: %s" % (SKILLS, exc)) from exc
    return sum(1 for child in entries
               if child.is_dir() and (child / "SKILL.md").is_file())


def dirty() -> int:
    out = git("status", "--short")
    return len([line for line in out.splitlines() if line.strip()])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def lane_prompts() -> Dict[str, str]:
    """The instruction files this host actually has, by path."""
    home = Path.home()
    candidates = [home / "AGENTS.md", Path("C:/Azazael/AGENTS.md"),
                  home / ".config/kilo/AGENTS.md", home / ".config/kilo/BEAM_AGENTS.md",
                  home / ".config/opencode/AGENTS.md", home / ".claude/CLAUDE.md",
                  home / ".codebuff/rules.md"]
    prompts: Dict[str, str] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            prompts[str(path)] = digest(path)
        except OSError as exc:
            # Recorded as a state, not replaced by a default: "this prompt could
            # not be read" is a fact the next session needs, and an empty digest
            # would compare equal to an unhashed file.
            prompts[str(path)] = "unreadable: %s" % exc
    return prompts


def build() -> Dict:
    return {
        "schema": SCHEMA,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "head": git("rev-parse", "HEAD"),
        "short": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "collected_tests": collected_tests(),
        "skills": skills(),
        "dirty_files": dirty(),
        "lane_prompts": lane_prompts(),
        "note": ("Quote these or measure again. A count with no date is a rumour; "
                 "state/ is gitignored, so this describes THIS working tree only."),
    }


def read() -> Dict:
    """The recorded state, or raise ReadingError naming why it cannot be read."""
    try:
        return json.loads(HEAD_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReadingError("%s is absent or unreadable: %s" % (HEAD_FILE, exc)) from exc
    except json.JSONDecodeError as exc:
        raise ReadingError("%s is not valid JSON: %s" % (HEAD_FILE, exc)) from exc


def check() -> int:
    try:
        state = read()
    except ReadingError as exc:
        print("BLIND: %s -- run --write before quoting any count" % exc)
        return 2
    head_now = git("rev-parse", "HEAD")
    problems = []
    if state.get("head") != head_now:
        problems.append("HEAD moved: state says %s, tree is %s"
                        % ((state.get("head") or "?")[:8], head_now[:8]))
    recorded = state.get("collected_tests")
    try:
        fresh = collected_tests()
    except ReadingError as exc:
        # An unmeasurable count is a finding, not a pass: `--check` must never
        # report clean because it could not look.
        problems.append("could not measure the test count now: %s" % exc)
    else:
        if recorded:
            drift = abs(fresh - recorded) / max(recorded, 1)
            if drift > TOLERANCE:
                problems.append("test count drifted: state says %s, live collection is %s"
                                % (recorded, fresh))
    for path, sha in (state.get("lane_prompts") or {}).items():
        if Path(path).is_file() and digest(Path(path)) != sha:
            problems.append("lane prompt changed since the reading: %s" % path)
    for problem in problems:
        print("STALE: " + problem)
    print("%s -- head %s, %s tests, %s skills, %s dirty, %d lane prompt(s)"
          % ("clean" if not problems else "%d problem(s)" % len(problems),
             (head_now or "?")[:8], recorded or "?", state.get("skills", "?"),
             state.get("dirty_files", "?"), len(state.get("lane_prompts") or {})))
    return 0 if not problems else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        return check()
    if args.show:
        try:
            state = read()
        except ReadingError as exc:
            print("BLIND: %s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(state, indent=1, sort_keys=True))
        return 0
    try:
        state = build()
    except ReadingError as exc:
        print("BLIND: %s; refusing to write a state file without a real reading" % exc,
              file=sys.stderr)
        return 2
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HEAD_FILE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    print("wrote %s -- head %s, %s tests, %s skills"
          % (HEAD_FILE, state["short"], state["collected_tests"], state["skills"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
