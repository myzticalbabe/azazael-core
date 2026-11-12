#!/usr/bin/env python3
# coding: utf-8
"""prompt_check — FLAMING SWORD's missing gate: the zero-shot prompt is falsifiable.

Everything else in this tree is checked: the generated order blocks against their
source, the staged diff against a scanner, the suite per file, the bible against
its own citations. The instruction file a fresh model reads FIRST was prose
nobody verified — and it had rotted where nobody was looking: measured
2026-11-02, it claimed 5,380 tests against a live collection of 6,277 (16% low),
and nothing in the estate could notice.

Four checks, all mechanical. Each resolves something against the disk or against a
recorded reading; none can be satisfied by better prose.

  P1 start-block-present   the START-HERE contract exists, and sits in the first
                           4,000 characters. A precedence statement under 20 KB of
                           canon is met last or never.
  P2 paths-resolve         every backticked path in the prompt exists on this host.
                           A command pointing at a file that is gone is an
                           instruction to fail, read by someone who cannot tell.
  P3 numbers-dated         every count ("N tests", "N skills", "N rules") matches
                           state/head.json within tolerance, or carries "measured
                           <date>" / "cached" / "historical" on the same line.
                           When the reading is ABSENT the gate records one by
                           running tools/head_state.py --write, because that file
                           is gitignored by design and a fresh host would
                           otherwise be Blind on every commit.
                           A number with no date is a rumour wearing knowledge.
  P4 handoff-resolves      the newest docs/*HANDOFF*.md names a commit that
                           resolves. A handoff citing a SHA nobody can check is a
                           story about a session.

Exit codes: 0 clean, 1 a prompt defect, 2 the instrument could not run (no prompt
found, no git). 2 is never a pass -- the same contract as every other gate here.

    python tools/prompt_check.py                 # controls, then the live prompts
    python tools/prompt_check.py --selftest      # controls only
    python tools/prompt_check.py --json          # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
#: The recorded reading, relative to whichever tree the gate is judging. It is
#: deliberately derived from `repo` and not pinned to this file's location: the
#: controls build a scratch tree and must be able to point the gate at it.
STATE_REL = Path("state") / "head.json"
#: The contract must be reachable at the top. 4,000 characters is roughly the first
#: screen and a half; a block below that competes with the canon it is meant to
#: replace, and the measurement behind the number is that the canon it would sit
#: under is 1.12 MB long.
TOP_WINDOW = 4000
#: A count is "the same reading" within this fraction -- one parametrised test moves
#: a collection number, and a gate that fails on ±1 gets switched off within a day.
TOLERANCE = 0.10
START_MARKER = "<!-- AO:start-here START -->"
#: Briefs are a second class of prompt, discovered the same way and judged on the
#: claims they can carry. Measured 2026-11-02: `docs/ZERO_SHOT_PROMPTS_2026-11-02.md`
#: sat outside the gate's seven lane files, so a stale count written into the sheet a
#: fresh model is handed passed every check in the estate. It is not a lane prompt --
#: no START-HERE block (P1 is the lane contract, not a brief's), no handoff (P4) --
#: but its addresses and its counts are exactly as falsifiable as the lane files'.
BRIEF_GLOB = "docs/*SHOT_PROMPTS*.md"
#: Which checks apply to which class. A brief judged against P1 would fail for being
#: a different kind of document, and a gate that fails for that reason teaches the
#: next session to delete it.
LANE_CLAIMS: Tuple[str, ...] = ("P1", "P2", "P3", "P4")
BRIEF_CLAIMS: Tuple[str, ...] = ("P2", "P3")

_PATH = re.compile(r"`([\w][\w./\\:-]*\.(?:py|md|json|jsonl|ya?ml|cmd|vbs|toml|txt))`")
# The thousands branch must come FIRST and the lookbehind must exclude `,`: with
# `(\d{2,4}(?:,\d{3})*)` the engine rejected "6,27" and settled on the tail "277",
# so 6,277 read as 277 -- a control caught it, and it would have failed every
# honest current count while passing a stale one that happened to end in the
# recorded digits.
_COUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d{2,4})\s+"
                    r"(tests?|skills?|rules?|modules?|shards?)\b")
_DATED = re.compile(r"measured\s+\d{4}-\d{2}-\d{2}|cached|historical|unverified", re.I)
#: The one word that lets a stale figure stand: it declares the number as a past
#: reading, not a claim about the tree as it is now.
_HISTORICAL = re.compile(r"\bhistorical(ly)?\b", re.I)
_SHA = re.compile(r"\b([0-9a-f]{7,40})\b")
#: Hosts this process cannot stat. Named, printed, never silently skipped.
REMOTE_PREFIXES = ("/root/", "/mnt/c/")
#: Roots that only exist on the Kali lane. A Windows run that called these dead
#: would fail the beam prompt for a tree it has never been able to see.
REMOTE_ROOTS = ("BCC-REINVESTIGATION/", "03_tooling/", "00_raw/")
#: Directories that hold artefacts a command WRITES. A prompt naming
#: `logs/golden_arrow_staged.json` before the first run is not citing a missing file,
#: so the absence is correct until the command runs. Deliberately a short list of
#: generated-output roots -- a general "parent exists" rule would pass almost every
#: wrong path, since most of them would sit in a directory that really exists.
OUTPUT_PREFIXES = ("logs/", "state/", "renders/", "runs/", "artifacts/", "out/")


class Blind(Exception):
    """The check could not be performed. Never a pass, never a defect."""


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _roots(repo: Path) -> Tuple[Path, ...]:
    """Every base a lane prompt legitimately writes a path against.

    Measured on this box: the Kilo prompt says `azazael/MANDATES.md` (relative to
    C:/Azazael), another lane says `tools/golden_arrow.py` (relative to the repo
    root), a third gives an absolute Windows path. All three name the same file. A
    resolver that knows only one base reports working commands as dead -- and a
    gate that cries on correct instructions is a gate the next session learns to
    ignore, which is how the stale counts survived in the first place.
    """
    home = Path.home()
    appdata = os.environ.get("APPDATA")
    startup = (Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
               if appdata else None)
    roots: List[Path] = []
    for root in (repo, repo.parent, home, home / ".config", startup):
        if root is not None and root not in roots:
            roots.append(root)
    return tuple(roots)


def resolve(path: str, repo: Path = REPO) -> Optional[bool]:
    """True when the path exists here, False when it does not, None when not an address.

    None is returned in two honest cases: a path on a host this process cannot see
    (a WSL lane's `/root/...`), and a bare filename with no directory part. A bare
    `SKILL.md` in prose is a mention -- "a skill is a directory holding SKILL.md" --
    not a claim about a file at a known address, and resolving mentions by basename
    search would pass a wrong path as easily as a right one. The caller counts them
    aloud so the difference between verified and unverifiable stays visible.
    """
    cleaned = _norm(os.path.expanduser(os.path.expandvars(path)))
    if cleaned.startswith(REMOTE_PREFIXES):
        return None
    if cleaned.startswith(REMOTE_ROOTS) and not Path(cleaned).is_absolute():
        return None
    # `Startup\pmb-floor-proxy.vbs` is how the prompt names the logon shortcut; the
    # folder is not under any repo root, so it needs naming here or the gate fails
    # a file that is running on this box right now.
    if cleaned.lower().startswith("startup/"):
        cleaned = cleaned[len("startup/"):]
        for root in _roots(repo):
            if (root / cleaned).exists():
                return True
    if "/" not in cleaned.strip("/"):
        return None
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return candidate.exists()
    return any((root / cleaned).exists() for root in _roots(repo))


def _load_state(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Blind("cannot read %s: %s" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise Blind("%s is not valid JSON: %s" % (path, exc)) from exc


def record_reading(repo: Path = REPO, timeout: float = 900.0) -> Dict[str, object]:
    """Record a reading for this tree, by running the tool that owns the recording.

    This is the fresh-host path. `state/head.json` is gitignored on purpose -- it
    describes ONE working tree, not a claim that travels with the repository -- so
    a clone or a new machine has no reading and the gate used to answer BLIND
    (exit 2), which the commit stanza then passed loud. A gate that is unrunnable
    on every host but the one that wrote it is the same defect as one that cannot
    fail: measured 2026-11-02, the only host that could judge the instruction files
    was the one already carrying a state file.

    The remedy is NOT a tracked seed (a seed is stale by construction and would be
    compared against as if current) and NOT a loosened comparison. The gate records
    what it needs, from the tool that owns the format, and then judges against
    that. A failure to record is Blind -- never a pass.
    """
    script = repo / "tools" / "head_state.py"
    if not script.is_file():
        raise Blind("no %s and no head_state.py to record one with" % (repo / STATE_REL))
    try:
        done = subprocess.run([sys.executable, "-X", "utf8", str(script), "--write"],
                              cwd=str(repo), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blind("could not run %s --write: %s" % (script, exc)) from exc
    if done.returncode != 0:
        detail = (done.stdout + done.stderr).strip().replace("\n", " ")[:400]
        raise Blind("%s --write exited %s: %s" % (script, done.returncode, detail))
    print("reading: %s --write recorded %s" % (script.name, done.stdout.strip()[:160]))
    return _load_state(repo / STATE_REL)


def readings(repo: Path = REPO) -> Dict[str, object]:
    path = repo / STATE_REL
    if path.is_file():
        return _load_state(path)
    return record_reading(repo)


def _section_start(text: str, pos: int) -> int:
    """Offset of the nearest markdown heading above `pos` (0 when there is none).

    A heading sets the context for everything under it until the next heading, so
    "measured 2026-10-31" in a caption dates the table beneath it.
    """
    starts = [m.start() for m in re.finditer(r"(?m)^#{1,4} ", text) if m.start() < pos]
    return starts[-1] if starts else 0


def check_text(text: str, *, repo: Path = REPO,
               measured: Optional[Dict[str, object]] = None,
               sha_resolves: Optional[Callable[[str], bool]] = None,
               claims: Tuple[str, ...] = LANE_CLAIMS) -> List[str]:
    """Every defect in one prompt. Pure: no disk writes, no globals.

    `claims` is the class of document being judged: a brief carries P2 and P3, a lane
    prompt all four. Selecting the claims is the caller's job and is stated here so a
    brief cannot pass by being measured against the wrong contract.
    """
    problems: List[str] = []
    measured = measured or {}

    # P1 -- the contract is present, and near the top.
    if "P1" in claims:
        index = text.find(START_MARKER)
        if index < 0:
            problems.append("P1 START-HERE block absent (%s)" % START_MARKER)
        elif index > TOP_WINDOW:
            problems.append("P1 START-HERE block starts at char %d, past the %d-char "
                            "window: the contract is below the canon it exists to replace"
                            % (index, TOP_WINDOW))

    # P2 -- every backticked path that is an address resolves.
    for match in _PATH.finditer(text):
        raw = match.group(1)
        if resolve(raw, repo) is not False:
            continue
        if _norm(raw).startswith(OUTPUT_PREFIXES):
            continue
        line = text.count("\n", 0, match.start()) + 1
        problems.append("P2 line %d: `%s` does not exist under %s"
                        % (line, raw, " | ".join(str(r) for r in _roots(repo))))

    # P3 -- every count is either current or dated.
    for match in _COUNT.finditer(text):
        number = int(match.group(1).replace(",", ""))
        kind = match.group(2).lower().rstrip("s")
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start:line_end if line_end > 0 else len(text)]
        # The number names its line. A quoted fragment alone is not an address in a
        # 2,000-line prompt: measured 2026-11-02, the hook blocked a commit with a
        # P3 defect that named the file and only the text, so the reader had to
        # search for it. P2 had carried a line number since it was written.
        line_no = text.count("\n", 0, match.start()) + 1
        # The date belongs to the section, not to every row of its table: a caption
        # reading "measured 2026-10-31" over forty rows is one dating act, and
        # demanding it on each row produces noise that gets the gate switched off.
        # The window must reach the END OF THE LINE as well -- a date written after
        # the number ("5,380 tests existed (measured 2026-10-30)") is the commonest
        # form there is, and a slice ending at the number fails every honest one.
        window = text[_section_start(text, match.start()):line_end]
        dated = _DATED.search(window) is not None
        key = {"test": "collected_tests", "skill": "skills"}.get(kind)
        expected = measured.get(key) if key else None
        if isinstance(expected, int) and expected:
            if abs(number - expected) / expected <= TOLERANCE:
                continue
            # A DATE IS NOT A LICENCE TO BE WRONG. Measured 2026-11-02: the prompt
            # carried "5,380 tests" under a heading that said "measured 2026-10-30",
            # so the number was dated, defensible, and 16% low -- a fresh session
            # read it as the size of the suite it was about to trust. When a
            # reading exists, a mismatch is a defect whether or not it is dated;
            # the one exemption is the word "historical", which says the figure is
            # a past reading quoted for its lesson rather than a claim about now.
            if _HISTORICAL.search(window):
                continue
            problems.append("P3 line %d: %r claims %d %ss; the recorded reading is %d%s. "
                            "Fix the number, or mark the line historical"
                            % (line_no, line.strip()[:70], number, kind, expected,
                               " (dated, but the tree moved past that date)" if dated else ""))
        elif not dated:
            problems.append("P3 line %d: %r quotes %d %ss with no recorded reading to "
                            "compare against -- measure, or mark it cached"
                            % (line_no, line.strip()[:70], number, kind))

    # P4 -- a handoff's commit resolves.
    if "P4" in claims and sha_resolves is not None:
        handoffs = sorted(repo.glob("docs/*HANDOFF*.md"), key=lambda p: p.stat().st_mtime,
                          reverse=True)
        if handoffs:
            text_h = handoffs[0].read_text(encoding="utf-8", errors="replace")
            claims = [m.group(1) for m in _SHA.finditer(text_h)]
            if not claims:
                problems.append("P4 %s names no commit at all: a handoff with no HEAD "
                                "cannot be checked" % handoffs[0].name)
            elif not any(sha_resolves(sha) for sha in claims):
                problems.append("P4 %s cites %s and none of them resolve"
                                % (handoffs[0].name, ", ".join(sorted(set(claims))[:4])))
    return list(dict.fromkeys(problems))


# --------------------------------------------------------------------------- #
# Live run
# --------------------------------------------------------------------------- #

def lane_prompts() -> List[Path]:
    home = Path.home()
    candidates = [home / "AGENTS.md", Path("C:/Azazael/AGENTS.md"),
                  home / ".config/kilo/AGENTS.md", home / ".config/kilo/BEAM_AGENTS.md",
                  home / ".config/opencode/AGENTS.md", home / ".claude/CLAUDE.md",
                  home / ".codebuff/rules.md"]
    return [path for path in candidates if path.is_file()]


def brief_prompts(repo: Path = REPO) -> List[Path]:
    """The sheets a fresh model is actually pasted from.

    A single brief is pasted alone, so the sheet is not read like a lane prompt --
    but every command it carries and every count it quotes is a claim, and those two
    are the checks that transfer.
    """
    return sorted(repo.glob(BRIEF_GLOB))


def git_resolver(repo: Path = REPO) -> Callable[[str], bool]:
    """True when the commit exists, False when git ran and said it does not.

    A git that cannot RUN (absent binary, a timeout) raises Blind rather than
    answering False: "this commit does not resolve" and "I could not ask" are
    different findings, and the scanner in this repo flagged the first version of
    this function for exactly that -- `except: return False` converts an
    unrunnable instrument into a defect on the thing it was meant to measure.
    """
    def resolves(sha: str) -> bool:
        try:
            done = subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                                  cwd=str(repo), capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Blind("git could not run (%s), so handoff commits cannot be "
                        "resolved on this host" % exc)
        return done.returncode == 0
    return resolves


def live(json_out: bool = False, explicit: Optional[Sequence[str]] = None) -> int:
    """Judge the lane prompts. `explicit` exists for the executable control,
    which must drive this gate on a fixture whose verdict is known in advance --
    a control that can only run against whatever is on the host cannot prove the
    gate fails when it should."""
    prompts = [Path(p) for p in explicit] if explicit else lane_prompts()
    if not prompts:
        raise Blind("no lane prompt found on this host -- nothing was verified")
    # An explicit --prompt is judged as a lane prompt: it exists for the executable
    # control, which must see P1 fail on a fixture with no start block.
    briefs = [] if explicit else brief_prompts(REPO)
    measured = readings(REPO)
    resolver = git_resolver()
    findings: List[Tuple[str, str]] = []
    for path, claims in [(p, LANE_CLAIMS) for p in prompts] + [(p, BRIEF_CLAIMS) for p in briefs]:
        text = path.read_text(encoding="utf-8", errors="replace")
        for problem in check_text(text, measured=measured, sha_resolves=resolver,
                                  claims=claims):
            findings.append((str(path), problem))
    if json_out:
        print(json.dumps({"ok": not findings, "prompts": [str(p) for p in prompts],
                          "findings": findings}, indent=2))
    else:
        for path, problem in findings:
            print("DEFECT %-38s %s" % (Path(path).name, problem))
        per_file: Dict[str, int] = {}
        for path, _ in findings:
            per_file[str(path)] = per_file.get(str(path), 0) + 1
        for path, count in sorted(per_file.items(), key=lambda kv: -kv[1]):
            print("%-64s %3d defect(s)" % (path, count))
        print("%s -- %d lane prompt(s) + %d brief(s), %d defect(s)"
              % ("clean" if not findings else "%d defects" % len(findings),
                 len(prompts), len(briefs), len(findings)))
    return 0 if not findings else 1


# --------------------------------------------------------------------------- #
# Controls -- both directions, because a gate that only ever says "clean" is decor
# --------------------------------------------------------------------------- #

def selftest() -> int:
    good = (START_MARKER + "\n\n" + "python tools/golden_arrow.py\n"
            "state/head.json holds the counts.\n")
    failures: List[str] = []

    def expect(name: str, text: str, *, want: str, measured: Optional[Dict] = None,
               resolver: Optional[Callable[[str], bool]] = None) -> None:
        problems = check_text(text, measured=measured if measured is not None else {},
                              sha_resolves=resolver)
        hit = [p for p in problems if p.startswith(want)]
        if want and not hit:
            failures.append("%s: expected a %s finding, got %r" % (name, want, problems))
        if not want and problems:
            failures.append("%s: expected no finding, got %r" % (name, problems))

    expect("clean prompt", good, want="")
    expect("start block absent", "python tools/golden_arrow.py\n", want="P1")
    expect("start block buried", ("x" * (TOP_WINDOW + 10)) + START_MARKER, want="P1")
    expect("path gone", good + "`tools/does_not_exist.py`\n", want="P2")
    expect("path exists", good + "`tools/golden_arrow.py`\n", want="")
    expect("stale count", good + "5,380 tests exist.\n", want="P3",
           measured={"collected_tests": 6277})
    expect("current count", good + "6,277 tests exist.\n", want="",
           measured={"collected_tests": 6277})
    # The pair that matters: a DATED stale count is still a defect (that is exactly
    # the state this gate was built to catch), while the same count marked
    # `historical` is a past reading quoted for its lesson and must pass.
    expect("count dated but stale", good + "5,380 tests existed (measured 2026-10-30).\n",
           want="P3", measured={"collected_tests": 6277})
    expect("count marked historical",
           good + "5,380 tests existed (historical: the suite has since grown).\n",
           want="", measured={"collected_tests": 6277})
    expect("skills stale", good + "1,042 skills remain.\n", want="P3",
           measured={"skills": 204})
    expect("no reading at all", good + "9,999 tests exist.\n", want="P3")
    # A finding must name its LINE. Measured 2026-11-02: the hook refused a real
    # commit on a P3 defect that named the file and quoted the offending text but
    # gave no line number, so the reader had to search a 2,000-line prompt for it.
    # P2 had carried a line number from the start; P3 had not.
    numbered = check_text("one\ntwo\n5,380 tests exist.\n",
                          measured={"collected_tests": 6277})
    if not [p for p in numbered if p.startswith("P3 line 3:")]:
        failures.append("a P3 finding does not name its line: %r" % numbered)

    # The brief class, both directions. A brief has no start block and no handoff, so
    # judging it as a lane prompt would fail it for being a different document; judged
    # on its own claims, its addresses and its counts are as falsifiable as any lane
    # file's. Measured 2026-11-02: the sheet carrying these briefs sat outside the
    # discovered set entirely, so a stale count in it passed every gate here.
    brief = "## 1.1\n**EVIDENCE** `python tools/golden_arrow.py`\n"
    if check_text(brief, measured={}, claims=BRIEF_CLAIMS):
        failures.append("clean brief was refused: %r"
                        % check_text(brief, measured={}, claims=BRIEF_CLAIMS))
    brief_stale = brief + "5,380 tests exist.\n"
    if not [p for p in check_text(brief_stale, measured={"collected_tests": 6277},
                                   claims=BRIEF_CLAIMS) if p.startswith("P3")]:
        failures.append("stale count in a brief was accepted")
    brief_dead = brief + "`tools/does_not_exist.py`\n"
    if not [p for p in check_text(brief_dead, measured={}, claims=BRIEF_CLAIMS)
            if p.startswith("P2")]:
        failures.append("dead address in a brief was accepted")

    # P4 needs a handoff file; build one in a scratch repo.
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "docs").mkdir()
        (root / "docs" / "HANDOFF_test.md").write_text("HEAD 0123456 is the commit.\n",
                                                       encoding="utf-8")
        bad = check_text(good, repo=root, sha_resolves=lambda sha: False)
        if not [p for p in bad if p.startswith("P4")]:
            failures.append("handoff with an unresolvable commit was accepted: %r" % bad)
        ok = check_text(good, repo=root, sha_resolves=lambda sha: True)
        if [p for p in ok if p.startswith("P4")]:
            failures.append("handoff with a resolving commit was refused: %r" % ok)
        (root / "docs" / "HANDOFF_test.md").write_text("no commit here\n", encoding="utf-8")
        empty = check_text(good, repo=root, sha_resolves=lambda sha: True)
        if not [p for p in empty if p.startswith("P4")]:
            failures.append("handoff naming no commit was accepted: %r" % empty)

    for failure in failures:
        print("CONTROL FAILED: " + failure)
    print("%s -- %d control(s)" % ("controls pass" if not failures else "controls failed",
                                   10 + 3 + 3 + 1))
    return 0 if not failures else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true", help="controls only")
    parser.add_argument("--no-selftest", action="store_true",
                        help="skip the built-in controls (used by the executable control)")
    parser.add_argument("--no-live", action="store_true", help="skip the real prompts")
    parser.add_argument("--prompt", action="append", default=None, metavar="PATH",
                        help="judge this file instead of the discovered lane prompts")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not args.no_selftest:
            rc = selftest()
            if rc != 0:
                return rc
        if args.selftest or args.no_live:
            return 0
        return live(json_out=args.json, explicit=args.prompt)
    except Blind as blind:
        print("BLIND: %s" % blind, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
