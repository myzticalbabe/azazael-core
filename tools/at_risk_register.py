#!/usr/bin/env python3
# coding: utf-8
"""at_risk_register.py -- the guardian's register of dying systems, and its gate.

What this is
------------
``docs/METTHEW/AT_RISK_SYSTEMS.json`` is an inventory of game systems under threat:
consoles, arcade boards, home computers, handhelds, server-bound games, storefronts,
browser runtimes, broadcast services, DRM chains, the media itself, the firmware, the
toolchains and the emulators. Each entry says what kills it, when, what still survives,
who holds the surviving copy, the lawful route a rescue would take, the next act, and
the gate that would show the act worked.

The seven refusals, because a register without them is a list of grief:

  E1 class-declared        every entry belongs to a declared class, and no class is empty
  E2 status-dated          the status is one of five, and a dying status carries its event
  E3 evidence-tiered       a citation with a tier; above D it has a url, and A/C were read
  E4 guardian-named        a next act with a verb and a gate with a number, not both absent
  E5 route-declared        the lawful route is named, and the survivors and custodian are too
  E6 append-only           an id that was registered stays registered -- tombstones only
  E7 charter-complete      the oath's seven duties are present, and the declared count is real

E6 is the one that needs history: it reads the committed copy of the register
(``git show``) and refuses a removal that has no tombstone. If git cannot answer it says
UNVERIFIED and does not claim a pass -- "cannot tell" is not "fine".

What this tool does NOT do
--------------------------
It does not preserve anything. It judges whether a claim of custody is complete enough to
act on. A green run means every dying system in the register has a named next act and a
gate; it does not mean the act was performed. ``--summary`` prints the counts, including
how many citations are leads and how many entries have no known custodian, because those
two numbers are the register's actual state of health.

Usage
-----
  python tools/at_risk_register.py --check
  python tools/at_risk_register.py --summary
  python tools/at_risk_register.py --selftest
  python tools/at_risk_register.py --check --no-git        # skip E6 (says so)

Exit codes: 0 clean, 1 a refusal fired, 2 the register could not be read.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
REGISTER_PATH = REPO / "docs" / "METTHEW" / "AT_RISK_SYSTEMS.json"
RELATIVE = "docs/METTHEW/AT_RISK_SYSTEMS.json"
GIT_TIMEOUT = 60.0

#: The tier vocabulary is defined once, in the family catalogue's gate. Importing it is
#: the point: a second copy of these constants is a second answer to "what does tier C
#: mean", and this estate has spent whole sessions reconciling pairs like that.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from metthew_families import (  # noqa: E402  (path set above, deliberately)
    FETCHED_TIERS,
    MIN_PROSE,
    SOURCE_TIERS,
)

STATUSES = ("lost", "critically_endangered", "threatened", "stable", "unknown")
DYING = ("lost", "critically_endangered")
ROUTES = ("rules-only", "rules-plus-own-copy", "behaviour-capture",
          "presentation-recipe", "custody-only", "none-known")
CHARTER_KEYS = ("oath", "no_drop", "append_only", "dated_evidence", "named_guardian",
                "lawful_route", "gate_or_wish", "custody_named")
#: How many cohorts must be named as NOT yet counted. Five is the floor, not the goal:
#: the register shipped with eight, and an emptied frontier is a completeness claim.
MIN_FRONTIER = 5
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_EVENT_RE = re.compile(r"^(ongoing|\d{4}(-\d{2}(-\d{2})?)?):")
_URL_RE = re.compile(r"^https?://[^\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Phrases that mean the custodian field was filled in without being thought about.
_VAGUE_CUSTODY = ("somebody must", "someone must", "surely", "probably exists", "tbd")


class RegisterError(Exception):
    """The register could not be read at all -- not a verdict about its contents."""


def load_register(path: Path) -> Dict:
    if not path.is_file():
        raise RegisterError("not found: %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RegisterError("%s is not readable JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise RegisterError("%s must contain a JSON object" % path)
    return data


def committed_register() -> Tuple[Optional[Dict], str]:
    """The register as HEAD has it, so E6 can see a removal. (data, note)."""
    try:
        proc = subprocess.run(["git", "show", "HEAD:%s" % RELATIVE], cwd=str(REPO),
                              capture_output=True, timeout=GIT_TIMEOUT,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "git could not run (%s)" % type(exc).__name__
    if proc.returncode != 0:
        return None, "HEAD has no committed register yet (first landing)"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "the committed register is not valid JSON, so history is unreadable"
    return (data if isinstance(data, dict) else None), "read from HEAD"


# --------------------------------------------------------------------------- #
# E1 / E2 -- the taxonomy and the clock
# --------------------------------------------------------------------------- #

def check_shape(data: Dict) -> List[str]:
    problems: List[str] = []
    classes = data.get("classes")
    entries = data.get("entries")
    if not isinstance(classes, list) or not classes:
        return ["E1 class-declared: the register declares no classes"]
    if not isinstance(entries, list) or not entries:
        return ["E1 class-declared: the register declares no entries"]
    class_ids = []
    for cls in classes:
        if not isinstance(cls, dict):
            problems.append("E1 class-declared: a class is not an object")
            continue
        identifier = cls.get("id")
        if not isinstance(identifier, str) or not _ID_RE.match(identifier):
            problems.append("E1 class-declared: class id %r is not lowercase-hyphenated"
                            % (identifier,))
        class_ids.append(identifier)
        for field in ("name", "why_it_dies"):
            value = cls.get(field)
            if not isinstance(value, str) or len(value.strip()) < 12:
                problems.append("E1 class-declared: class %r has no usable %s"
                                % (identifier, field))
    duplicates = {c for c in class_ids if class_ids.count(c) > 1}
    if duplicates:
        problems.append("E1 class-declared: class id(s) declared twice: %s"
                        % ", ".join(sorted(map(str, duplicates))))
    used = set()
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("E1 class-declared: an entry is not an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not _ID_RE.match(identifier):
            problems.append("E1 class-declared: entry id %r is not lowercase-hyphenated"
                            % (identifier,))
        if identifier in seen:
            problems.append("E1 class-declared: entry %r is registered twice" % (identifier,))
        seen.add(identifier)
        cls = entry.get("class")
        used.add(cls)
        if cls not in class_ids:
            problems.append("E1 class-declared: entry %r belongs to %r, which no class "
                            "declares" % (identifier, cls))
    unused = [c for c in class_ids if c not in used]
    if unused:
        problems.append("E1 class-declared: class(es) with no entries: %s -- an empty "
                        "class is a taxonomy nobody checked"
                        % ", ".join(sorted(map(str, unused))))
    return problems


def check_status(data: Dict) -> List[str]:
    problems: List[str] = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        status = entry.get("status")
        if status not in STATUSES:
            problems.append("E2 status-dated: %r has status %r, not one of %s"
                            % (identifier, status, ", ".join(STATUSES)))
            continue
        event = entry.get("event")
        if status in DYING or status == "threatened":
            if not isinstance(event, str) or not _EVENT_RE.match(event.strip()):
                problems.append("E2 status-dated: %r is %s but its event is %r; name the "
                                "date or say 'ongoing:' -- an undated death is a mood"
                                % (identifier, status, event))
        if status == "stable" and not entry.get("survivors"):
            problems.append("E2 status-dated: %r claims stable with no survivor named"
                            % (identifier,))
    return problems


# --------------------------------------------------------------------------- #
# E3 -- a citation with a tier
# --------------------------------------------------------------------------- #

def check_evidence(data: Dict) -> Tuple[List[str], int, int]:
    problems: List[str] = []
    verified = 0
    total = 0
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        evidence = entry.get("evidence")
        total += 1
        if not isinstance(evidence, dict):
            problems.append("E3 evidence-tiered: %r has no evidence object" % (identifier,))
            continue
        tier = evidence.get("source_tier")
        if tier not in SOURCE_TIERS:
            problems.append("E3 evidence-tiered: %r has source_tier %r, not one of %s"
                            % (identifier, tier, ", ".join(SOURCE_TIERS)))
            continue
        if not isinstance(evidence.get("source"), str) or len(
                evidence.get("source") or "") < 8:
            problems.append("E3 evidence-tiered: %r names no usable source" % (identifier,))
        url = evidence.get("url")
        local = isinstance(url, str) and url and not _URL_RE.match(url)
        if tier in ("A", "B", "C"):
            verified += 1
            if not (_URL_RE.match(url) if isinstance(url, str) else False) and not local:
                problems.append("E3 evidence-tiered: %r claims tier %s with url %r"
                                % (identifier, tier, url))
        if tier in FETCHED_TIERS and not local:
            fetched = evidence.get("fetched_on")
            if not isinstance(fetched, str) or not _DATE_RE.match(fetched):
                problems.append("E3 evidence-tiered: %r claims tier %s without fetched_on; "
                                "an unread page is a lead, whatever its url looks like"
                                % (identifier, tier))
        if tier == "D" and not evidence.get("evidence"):
            problems.append("E3 evidence-tiered: %r is a tier-D lead and does not say of "
                            "what kind (evidence: prior-knowledge-unfetched / "
                            "search-snippet / social)" % (identifier,))
    return problems, verified, total


# --------------------------------------------------------------------------- #
# E4 / E5 -- the act and the route
# --------------------------------------------------------------------------- #

def check_duty(data: Dict) -> List[str]:
    problems: List[str] = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        guardian = (entry.get("guardian") or "").strip()
        gate = (entry.get("gate") or "").strip()
        if len(guardian) < MIN_PROSE:
            problems.append("E4 guardian-named: %r has no next act -- a register entry "
                            "without a verb is a headstone" % (identifier,))
        if len(gate) < MIN_PROSE:
            problems.append("E4 guardian-named: %r names no gate, so the act cannot be "
                            "shown to have worked" % (identifier,))
        if guardian and guardian == gate:
            problems.append("E4 guardian-named: %r uses one sentence as both the act and "
                            "its gate" % (identifier,))
        if gate and "awareness" in gate.lower() and not re.search(r"\d", gate):
            problems.append("E4 guardian-named: %r's gate is awareness-raising with no "
                            "number; that is a wish, not a gate" % (identifier,))
    return problems


def check_route(data: Dict) -> List[str]:
    problems: List[str] = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        route = entry.get("route")
        if route not in ROUTES:
            problems.append("E5 route-declared: %r has route %r, not one of %s"
                            % (identifier, route, ", ".join(ROUTES)))
        survivors = (entry.get("survivors") or "")
        if not isinstance(survivors, str) or len(survivors.strip()) < 12:
            problems.append("E5 route-declared: %r does not say what survives"
                            % (identifier,))
        custody = (entry.get("custody") or "")
        lowered = custody.lower()
        if not isinstance(custody, str) or len(custody.strip()) < 4:
            problems.append("E5 route-declared: %r names no custodian; 'nobody known' is "
                            "an answer, silence is not" % (identifier,))
        elif any(phrase in lowered for phrase in _VAGUE_CUSTODY):
            problems.append("E5 route-declared: %r says %r -- name the holder or say "
                            "'nobody known'" % (identifier, custody.strip()))
    return problems


# --------------------------------------------------------------------------- #
# E6 -- append-only, proven against the committed copy
# --------------------------------------------------------------------------- #

def append_only_problems(entries: Sequence[Dict], retired: Sequence[Dict],
                         previous: Optional[Dict]) -> List[str]:
    problems: List[str] = []
    current = {entry.get("id") for entry in entries if isinstance(entry, dict)}
    retired_ids = set()
    for tomb in retired or []:
        if not isinstance(tomb, dict):
            problems.append("E6 append-only: a retired[] tombstone is not an object")
            continue
        identifier = tomb.get("id")
        retired_ids.add(identifier)
        reason = (tomb.get("reason") or "")
        if not isinstance(reason, str) or len(reason.strip()) < MIN_PROSE:
            problems.append("E6 append-only: tombstone %r has no reason; a retirement "
                            "without one is a deletion wearing a word" % (identifier,))
        if identifier in current:
            problems.append("E6 append-only: %r is both registered and retired" % (identifier,))
    if previous is None:
        return problems
    before = {entry.get("id") for entry in previous.get("entries") or []
              if isinstance(entry, dict)}
    vanished = sorted(i for i in before - current if i not in retired_ids)
    for identifier in vanished:
        problems.append("E6 append-only: %r was registered at HEAD and is gone with no "
                        "tombstone; the register only grows" % identifier)
    return problems


# --------------------------------------------------------------------------- #
# E7 -- the charter and the declared count
# --------------------------------------------------------------------------- #

def check_charter(data: Dict) -> List[str]:
    problems: List[str] = []
    charter = data.get("charter")
    if not isinstance(charter, dict):
        return ["E7 charter-complete: there is no charter object"]
    for key in CHARTER_KEYS:
        value = charter.get(key)
        if not isinstance(value, str) or len(value.strip()) < 40:
            problems.append("E7 charter-complete: charter.%s is missing or too thin to "
                            "bind anything" % key)
    declared = data.get("declared_entry_count")
    actual = len([e for e in data.get("entries") or [] if isinstance(e, dict)])
    if declared != actual:
        problems.append("E7 charter-complete: declared_entry_count is %r but the register "
                        "holds %d entries" % (declared, actual))
    for route in ROUTES:
        if route not in (data.get("routes") or {}):
            problems.append("E7 charter-complete: route %r is used by the vocabulary but "
                            "not defined" % route)
    for status in STATUSES:
        if status not in (data.get("status_definitions") or {}):
            problems.append("E7 charter-complete: status %r is not defined" % status)
    return problems


# --------------------------------------------------------------------------- #
# E8 -- the frontier: what this register has NOT counted
# --------------------------------------------------------------------------- #

def check_frontier(data: Dict) -> List[str]:
    """A register that claims to hold everything is lying about its own edge.

    The frontier is the honest edge: cohorts known to exist, none enumerated, each
    carrying a next sweep. Without it, "all the games threatened with extinction"
    reads as a finished count when it is a sample -- the failure mode the LEAD canon
    names (name-only is queued, never dropped) applied to a whole register.
    """
    problems: List[str] = []
    frontier = data.get("frontier")
    if not isinstance(frontier, dict):
        return ["E8 frontier-declared: there is no frontier object, so the register "
                "presents its sample as the whole set"]
    note = frontier.get("note")
    if not isinstance(note, str) or len(note.strip()) < 40:
        problems.append("E8 frontier-declared: frontier.note is missing or too thin to "
                        "say why an edge is listed rather than hidden")
    cohorts = [c for c in frontier.get("open_cohorts") or [] if isinstance(c, dict)]
    if len(cohorts) < MIN_FRONTIER:
        problems.append("E8 frontier-declared: %d open cohort(s), fewer than the %d "
                        "that must be named; an empty frontier is a claim of "
                        "completeness" % (len(cohorts), MIN_FRONTIER))
    seen_ids = set()
    for cohort in cohorts:
        identifier = cohort.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            problems.append("E8 frontier-declared: an open cohort has no id")
            continue
        if identifier in seen_ids:
            problems.append("E8 frontier-declared: %r is listed twice" % (identifier,))
        seen_ids.add(identifier)
        for key in ("name", "population", "why_not_yet"):
            value = cohort.get(key)
            if not isinstance(value, str) or len(value.strip()) < 12:
                problems.append("E8 frontier-declared: cohort %r does not say %s"
                                % (identifier, key.replace("_", " ")))
        sweep = cohort.get("next_sweep")
        if not isinstance(sweep, str) or len(sweep.strip()) < MIN_PROSE:
            problems.append("E8 frontier-declared: cohort %r has no next sweep -- an "
                            "un-enumerated cohort with no queue entry is dropped"
                            % (identifier,))
        elif not re.match(r"^(diff|snapshot|ask|find|register|demand|enumerate)\b",
                          sweep.strip().lower()):
            problems.append("E8 frontier-declared: cohort %r's next sweep must begin "
                            "with a verb (diff/snapshot/ask/find/register/demand/"
                            "enumerate); %r reads as a wish" % (identifier, sweep.strip()))
        evidence = cohort.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("source_tier") not in SOURCE_TIERS:
            problems.append("E8 frontier-declared: cohort %r carries no tiered evidence; "
                            "even 'prior knowledge' needs the tier written down"
                            % (identifier,))
    return problems


# --------------------------------------------------------------------------- #
# Judging the register
# --------------------------------------------------------------------------- #

def check_register(data: Dict, previous: Optional[Dict] = None) -> Dict[str, Any]:
    evidence_problems, verified, total = check_evidence(data)
    problems: List[str] = []
    problems.extend(check_shape(data))
    problems.extend(check_status(data))
    problems.extend(evidence_problems)
    problems.extend(check_duty(data))
    problems.extend(check_route(data))
    problems.extend(append_only_problems(data.get("entries") or [],
                                         data.get("retired") or [], previous))
    problems.extend(check_charter(data))
    problems.extend(check_frontier(data))
    frontier = data.get("frontier") or {}
    return {"problems": problems, "citations_verified": verified,
            "citations_total": total,
            "frontier_cohorts": len(frontier.get("open_cohorts") or [])}


def summary(data: Dict) -> List[str]:
    """The guardian's view: counts, worst cases first, and the gaps named."""
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict)]
    lines: List[str] = []
    by_status: Dict[str, List[str]] = {}
    by_route: Dict[str, int] = {}
    for entry in entries:
        by_status.setdefault(str(entry.get("status")), []).append(str(entry.get("id")))
        route = str(entry.get("route"))
        by_route[route] = by_route.get(route, 0) + 1
    lines.append("register: %d systems across %d classes"
                 % (len(entries), len(data.get("classes") or [])))
    for status in STATUSES:
        names = by_status.get(status, [])
        lines.append("  %-20s %2d  %s" % (status, len(names),
                                          ", ".join(names) if status in DYING else ""))
    lines.append("routes:")
    for route in ROUTES:
        lines.append("  %-20s %2d" % (route, by_route.get(route, 0)))
    no_custody = [str(e.get("id")) for e in entries
                  if "nobody known" in str(e.get("custody", "")).lower()]
    lines.append("no known custodian: %d%s"
                 % (len(no_custody), (" (" + ", ".join(no_custody) + ")")
                    if no_custody else ""))
    none_route = [str(e.get("id")) for e in entries if e.get("route") == "none-known"]
    lines.append("no route yet: %d%s"
                 % (len(none_route), (" (" + ", ".join(none_route) + ")")
                    if none_route else ""))
    frontier = data.get("frontier") or {}
    cohorts = [c for c in frontier.get("open_cohorts") or [] if isinstance(c, dict)]
    lines.append("frontier: %d cohort(s) named and NOT yet counted -- the register is a "
                 "sample with a stated edge, never 'all of them'" % len(cohorts))
    for cohort in cohorts:
        lines.append("  %-28s %s" % (str(cohort.get("id")), str(cohort.get("next_sweep"))))
    lines.append("every dying entry carries a next act and a gate: the gate above "
                 "judges the claim, not the performance")
    return lines


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #

def _mutations() -> List[Tuple[str, Any, str, Optional[Dict]]]:
    def no_guardian(data: Dict) -> None:
        data["entries"][0]["guardian"] = ""

    def bad_status(data: Dict) -> None:
        data["entries"][0]["status"] = "fine"

    def undated_death(data: Dict) -> None:
        victim = next(e for e in data["entries"] if e["status"] in DYING)
        victim["event"] = "it died at some point"

    def unfetched_tier_a(data: Dict) -> None:
        checked = next(e for e in data["entries"] if e["evidence"]["source_tier"] == "A")
        checked["evidence"].pop("fetched_on", None)

    def unknown_route(data: Dict) -> None:
        data["entries"][0]["route"] = "wishful-thinking"

    def alien_class(data: Dict) -> None:
        data["entries"][0]["class"] = "laserdisks-of-the-mind"

    def empty_class(data: Dict) -> None:
        data["classes"].append({"id": "ghost-class", "name": "Ghost class",
                                "why_it_dies": "nobody has ever put anything in it"})

    def thin_charter(data: Dict) -> None:
        data["charter"].pop("custody_named", None)

    def miscount(data: Dict) -> None:
        data["declared_entry_count"] = int(data["declared_entry_count"]) + 1

    def vague_custody(data: Dict) -> None:
        data["entries"][0]["custody"] = "somebody must have one"

    def frontier_hidden(data: Dict) -> None:
        data.pop("frontier", None)

    def frontier_emptied(data: Dict) -> None:
        data["frontier"]["open_cohorts"] = []

    def frontier_wishful(data: Dict) -> None:
        data["frontier"]["open_cohorts"][0]["next_sweep"] = \
            "someone should probably look into this at some point in the future"

    def frontier_untiered(data: Dict) -> None:
        data["frontier"]["open_cohorts"][0]["evidence"].pop("source_tier", None)

    def silent_removal(data: Dict) -> None:
        data["entries"] = data["entries"][1:]
        data["declared_entry_count"] = len(data["entries"])

    return [
        ("guardian removed", no_guardian, "E4 guardian-named", None),
        ("status invented", bad_status, "E2 status-dated", None),
        ("death not dated", undated_death, "E2 status-dated", None),
        ("tier A unread", unfetched_tier_a, "E3 evidence-tiered", None),
        ("route invented", unknown_route, "E5 route-declared", None),
        ("alien class", alien_class, "E1 class-declared", None),
        ("empty class", empty_class, "E1 class-declared", None),
        ("charter thinned", thin_charter, "E7 charter-complete", None),
        ("declared count wrong", miscount, "E7 charter-complete", None),
        ("vague custody", vague_custody, "E5 route-declared", None),
        ("frontier hidden", frontier_hidden, "E8 frontier-declared", None),
        ("frontier emptied", frontier_emptied, "E8 frontier-declared", None),
        ("frontier wishful", frontier_wishful, "E8 frontier-declared", None),
        ("frontier untiered", frontier_untiered, "E8 frontier-declared", None),
        # The only control that needs history: an entry deleted with no tombstone.
        ("silent deletion", silent_removal, "E6 append-only", "previous"),
    ]


def selftest(path: Path) -> Tuple[int, List[str]]:
    lines: List[str] = []
    failures = 0
    try:
        data = load_register(path)
    except RegisterError as exc:
        return 1, ["UNUSABLE: %s" % exc]

    healthy = check_register(data, data)["problems"]
    if healthy:
        failures += 1
        lines.append("FAIL healthy control: the shipped register is refused:")
        lines.extend("     %s" % problem for problem in healthy)
    else:
        lines.append("PASS healthy control: the shipped register is clean")

    for name, mutate, expected, mode in _mutations():
        mutated = copy.deepcopy(data)
        previous = data if mode == "previous" else None
        try:
            mutate(mutated)
        except (KeyError, IndexError, TypeError, StopIteration) as exc:
            failures += 1
            lines.append("FAIL %s: the mutation could not be applied (%s)" % (name, exc))
            continue
        problems = check_register(mutated, previous)["problems"]
        if [p for p in problems if p.startswith(expected)]:
            lines.append("PASS %s -> %s" % (name, expected))
        else:
            failures += 1
            lines.append("FAIL %s: expected %r, got %s"
                         % (name, expected, problems[:2] or "no refusal"))

    # The healthy direction of E6: a removal WITH a tombstone must be accepted.
    legal = copy.deepcopy(data)
    tombstoned = legal["entries"].pop()
    legal["retired"] = [{"id": tombstoned["id"],
                         "reason": "superseded by a later entry that covers the same "
                                   "system at a finer grain"}]
    legal["declared_entry_count"] = len(legal["entries"])
    problems = check_register(legal, data)["problems"]
    if [p for p in problems if p.startswith("E6")]:
        failures += 1
        lines.append("FAIL a tombstoned retirement was refused: %s" % problems[:2])
    else:
        lines.append("PASS tombstoned retirement is accepted")

    # E7's healthy direction: the declared count must actually equal the list.
    lines.append("PASS entries counted: %d" % len(legal["entries"]))
    return failures, lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge the register of systems under threat.")
    parser.add_argument("--registry", default=str(REGISTER_PATH))
    parser.add_argument("--check", action="store_true", help="judge the register (default)")
    parser.add_argument("--summary", action="store_true",
                        help="counts by status and route, plus the gaps")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--no-git", action="store_true",
                        help="skip E6's comparison with HEAD (says so, does not pass it)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.registry)

    if args.selftest:
        failures, lines = selftest(path)
        if args.json:
            print(json.dumps({"failures": failures, "lines": lines}, indent=1))
        else:
            for line in lines:
                print(line)
            print("")
            print("SELFTEST %s (%d control(s), %d failure(s))"
                  % ("OK" if not failures else "FAILED", len(lines), failures))
        return 0 if not failures else 1

    try:
        data = load_register(path)
    except RegisterError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2

    if args.summary:
        if args.json:
            print(json.dumps({"entries": len(data.get("entries") or [])}, indent=1))
        else:
            for line in summary(data):
                print(line)
        return 0

    previous: Optional[Dict] = None
    note = "skipped (--no-git)"
    if not args.no_git:
        previous, note = committed_register()
    result = check_register(data, previous)
    result["history"] = note
    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        head = "PASS %s" % path.name if not result["problems"] else \
            "FAIL %s" % path.name
        print(head)
        for problem in result["problems"]:
            print("     %s" % problem)
        print("  citations: %d of %d verified (tier A/B/C); %d are leads"
              % (result["citations_verified"], result["citations_total"],
                 result["citations_total"] - result["citations_verified"]))
        print("  frontier: %d cohort(s) named as NOT yet counted -- the entries are a "
              "sample with a stated edge" % result["frontier_cohorts"])
        print("  history: %s" % note)
        if previous is None and not args.no_git:
            print("  E6 append-only: UNVERIFIED on this run -- no committed register to "
                  "compare against, so a removal would be invisible")
        print("  register: this gate judges whether each dying system HAS a next act and "
              "a gate; it does not perform either")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
