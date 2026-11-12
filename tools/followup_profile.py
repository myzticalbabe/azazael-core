#!/usr/bin/env python3
"""The followup profile: the 14 cards, editable to individual taste.

WHY THIS EXISTS
---------------
RULE 0Z-B names 14 followups per response (13 hyper-edge cards, then
zero-shot reasoning over their evidence). The *number* and three *slots* are
the mandate; the *wording* was not, and a fixed list read the same to every
operator. A card the operator does not want is a card that gets ignored, and
an ignored card is indistinguishable from a missing mandate.

So the list is data. This tool owns it:

    python tools/followup_profile.py --show          # the current 14
    python tools/followup_profile.py --emit          # paste-ready prompts
    python tools/followup_profile.py --set 3 --prompt "Write the migration test for X"
    python tools/followup_profile.py --set 13 --card "Land it, then say what broke"
    python tools/followup_profile.py --check         # validate, exit 1 on a defect
    python tools/followup_profile.py --reset         # back to the shipped default

WHAT THE USER MAY CHANGE, AND WHAT THE MANDATE KEEPS
----------------------------------------------------
Every card's `card` name and `prompt` is the operator's to rewrite -- that is
the point of a profile. Three slots are `locked`, which here means "cannot be
deleted or renumbered", not "cannot be reworded":

  * slot 12 -- hyper-edge adaptive upgrade
  * slot 13 -- Build, land, in the abyss (one click: plan, build, verify, commit clean)
  * slot 14 -- Zero-shot reasoning (last, over the evidence the thirteen produced)

Deleting one of those is refused with the reason, because the count is not the
whole rule: a 14-card set with no abyss card satisfies the number and drops the
thing the number was widened for. Rewording them is allowed and recorded.

The tool never edits canon. `MANDATES.md`, the skill and
`src/SCRIPTS/request_auth.py` carry the count; this file carries the words.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VERSION = 1
DEFAULT_PATH = Path("config") / "followup_profile.json"

#: Slots a profile may reword but not remove or renumber.
LOCKED = {
    12: "hyper-edge adaptive upgrade",
    13: "Build, land, in the abyss",
    14: "Zero-shot reasoning",
}

DEFAULT_CARDS = [
    ("Architecture", "Add session-scoped filtering to DualLedgerValidator"),
    ("Testing", "Write unit tests for the governance.py module"),
    ("Documentation", "Update COMPLIANCE_CHECKLIST.md with session-end status"),
    ("Refactoring", "Extract the 15-layer VLP into its own module"),
    ("Execution", "Run the full worm registry integration test"),
    ("Recording", "Record all pending session actions to DirectMemory"),
    ("Code Review", "Run a full anti-pattern scan on azazael/src/"),
    ("Sync", "Run sync_skills.py to propagate all canonical changes"),
    ("Verification", "Run the compliance orchestrator self-test"),
    ("Cleanup", "Clean up any temporary scripts and stale files"),
    ("Audit", "Run VLP 15-layer audit on all audit trail sessions"),
    ("Hyper-edge adaptive upgrade",
     "Take the sharpest capability jump available right now and land it."),
    ("Build, land, in the abyss",
     "Plan, build, verify and commit clean: take this tree to a green gate and a "
     "clean commit in one pass, then name what is still broken."),
    ("Zero-shot reasoning",
     "Reason zero-shot from the evidence the previous thirteen cards produced: state "
     "the question, cite the evidence by path or command, answer in one pass with no "
     "worked example."),
]


def default_profile(operator: str = "") -> dict:
    return {
        "version": VERSION,
        "rule": "0Z-B",
        "count": len(DEFAULT_CARDS),
        "operator": operator,
        "note": ("wording is yours; the count and the three locked slots are the rule. "
                 "Locked slots may be reworded, never deleted."),
        "cards": [
            {"slot": i + 1, "card": name, "prompt": prompt,
             "locked": (i + 1) in LOCKED}
            for i, (name, prompt) in enumerate(DEFAULT_CARDS)
        ],
    }


# ── io ──────────────────────────────────────────────────────────────────────
def load(path: Path) -> dict:
    if not path.exists():
        return default_profile()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit("%s is not valid JSON: %s" % (path, exc)) from exc


def save(path: Path, profile: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")


# ── validation ──────────────────────────────────────────────────────────────
def problems(profile: dict) -> list:
    """Everything wrong with a profile, as sentences. Empty means valid.

    A checker that returns a bool cannot say what to fix, and a profile that is
    quietly wrong produces 14 plausible cards with the mandate's one fixed
    slot missing -- the exact failure the count was raised for.
    """
    out = []
    cards = profile.get("cards")
    if not isinstance(cards, list):
        return ["`cards` is not a list"]
    slots = []
    for entry in cards:
        if not isinstance(entry, dict):
            out.append("a card entry is not an object: %r" % (entry,))
            continue
        slot = entry.get("slot")
        if not isinstance(slot, int):
            out.append("slot %r is not an integer" % (slot,))
            continue
        slots.append(slot)
        if not str(entry.get("prompt") or "").strip():
            out.append("slot %d has an empty prompt" % slot)
        if not str(entry.get("card") or "").strip():
            out.append("slot %d has an empty card name" % slot)
    if len(slots) != len(set(slots)):
        dupes = sorted({s for s in slots if slots.count(s) > 1})
        out.append("duplicate slot number(s): %s" % dupes)
    declared = profile.get("count")
    if declared != len(cards):
        out.append("count says %r but there are %d card(s)" % (declared, len(cards)))
    if len(cards) != len(DEFAULT_CARDS):
        out.append("the mandate is %d cards; this profile has %d"
                   % (len(DEFAULT_CARDS), len(cards)))
    for slot, name in LOCKED.items():
        if slot not in slots:
            out.append("locked slot %d (%s) is missing" % (slot, name))
    stray = [s for s in slots if not 1 <= s <= len(DEFAULT_CARDS)]
    if stray:
        out.append("slot(s) outside 1..%d: %s" % (len(DEFAULT_CARDS), stray))
    return out


# ── commands ────────────────────────────────────────────────────────────────
def cmd_show(profile: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(profile, indent=2))
        return 0
    print("followup profile -- RULE %s, %d card(s)%s"
          % (profile.get("rule", "0Z-B"), len(profile.get("cards") or []),
             (" for %s" % profile["operator"]) if profile.get("operator") else ""))
    for entry in sorted(profile.get("cards") or [], key=lambda e: e.get("slot", 0)):
        flag = " [locked]" if entry.get("locked") or entry.get("slot") in LOCKED else ""
        print("  %2d. %s%s" % (entry.get("slot", 0), entry.get("card", ""), flag))
        print("      %s" % entry.get("prompt", ""))
    return 0


def cmd_emit(profile: dict) -> int:
    """Print the prompts in slot order -- what goes into suggest_followups."""
    bad = problems(profile)
    if bad:
        for line in bad:
            print("REFUSED: %s" % line, file=sys.stderr)
        return 1
    for entry in sorted(profile["cards"], key=lambda e: e["slot"]):
        print("%d|%s|%s" % (entry["slot"], entry["card"], entry["prompt"]))
    return 0


def cmd_set(profile: dict, slot: int, card: str, prompt: str) -> int:
    hit = None
    for entry in profile.get("cards") or []:
        if entry.get("slot") == slot:
            hit = entry
            break
    if hit is None:
        print("REFUSED: no slot %d in this profile (1..%d)"
              % (slot, len(DEFAULT_CARDS)), file=sys.stderr)
        return 1
    if card:
        hit["card"] = card
    if prompt:
        hit["prompt"] = prompt
    hit["locked"] = slot in LOCKED
    return 0


def cmd_remove(profile: dict, slot: int) -> int:
    if slot in LOCKED:
        print("REFUSED: slot %d is %r -- locked slots may be reworded with --set, "
              "never deleted" % (slot, LOCKED[slot]), file=sys.stderr)
        return 1
    before = len(profile.get("cards") or [])
    profile["cards"] = [e for e in profile.get("cards") or [] if e.get("slot") != slot]
    if len(profile["cards"]) == before:
        print("REFUSED: no slot %d to remove" % slot, file=sys.stderr)
        return 1
    # Renumber so the set stays 1..n, then let --check report the shortfall: a
    # removed card leaves 13, and 13 is a defect the operator must fix by adding
    # one back, not something this tool invents for them.
    for i, entry in enumerate(sorted(profile["cards"], key=lambda e: e["slot"]), start=1):
        entry["slot"] = i
        entry["locked"] = i in LOCKED
    profile["count"] = len(profile["cards"])
    return 0


def cmd_add(profile: dict, card: str, prompt: str) -> int:
    cards = profile.setdefault("cards", [])
    nxt = max([e.get("slot", 0) for e in cards] or [0]) + 1
    if nxt > len(DEFAULT_CARDS):
        print("REFUSED: the mandate is %d cards; remove one before adding another"
              % len(DEFAULT_CARDS), file=sys.stderr)
        return 1
    cards.append({"slot": nxt, "card": card, "prompt": prompt, "locked": nxt in LOCKED})
    profile["count"] = len(cards)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=str(DEFAULT_PATH), help="profile file")
    ap.add_argument("--show", action="store_true", help="print the current cards")
    ap.add_argument("--emit", action="store_true", help="print slot|card|prompt rows")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--init", action="store_true", help="write the default profile")
    ap.add_argument("--reset", action="store_true", help="overwrite with the default")
    ap.add_argument("--check", action="store_true", help="validate, exit 1 on a defect")
    ap.add_argument("--set", type=int, metavar="SLOT", help="reword one card")
    ap.add_argument("--remove", type=int, metavar="SLOT", help="delete a non-locked card")
    ap.add_argument("--add", action="store_true", help="append a card (if under the count)")
    ap.add_argument("--card", default="", help="card name for --set / --add")
    ap.add_argument("--prompt", default="", help="prompt text for --set / --add")
    ap.add_argument("--operator", default="", help="whose taste this profile encodes")
    args = ap.parse_args(argv)

    path = Path(args.path)
    profile = load(path)
    touched = False

    if args.reset:
        profile = default_profile(args.operator)
        touched = True
    elif args.init:
        if path.exists():
            print("%s already exists; use --reset to overwrite" % path, file=sys.stderr)
            return 1
        profile = default_profile(args.operator)
        touched = True
    if args.operator:
        profile["operator"] = args.operator
        touched = True
    if args.set is not None:
        rc = cmd_set(profile, args.set, args.card, args.prompt)
        if rc:
            return rc
        touched = True
    if args.remove is not None:
        if cmd_remove(profile, args.remove):
            return 1
        touched = True
    if args.add:
        if not (args.card and args.prompt):
            print("REFUSED: --add needs --card and --prompt", file=sys.stderr)
            return 1
        if cmd_add(profile, args.card, args.prompt):
            return 1
        touched = True

    if touched:
        save(path, profile)
        print("wrote %s" % path)

    if args.check:
        bad = problems(profile)
        for line in bad:
            print("DEFECT: %s" % line)
        if bad:
            return 1
        print("OK: %d cards, locked slots %s present"
              % (len(profile["cards"]), sorted(LOCKED)))
        return 0
    if args.emit:
        return cmd_emit(profile)
    if args.show or args.json or not touched:
        return cmd_show(profile, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
