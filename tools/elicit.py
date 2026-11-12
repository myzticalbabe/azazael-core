#!/usr/bin/env python3
# coding: utf-8
"""Administer the PSYCHE construct battery and store what comes back.

WHY THIS IS A TOOL AND NOT A FUNCTION IN THE PACKAGE
----------------------------------------------------
`src/PSYCHE/elicitation.py` defines the items and the scoring. This is the part
that touches a person: it asks, it records, and it appends to a store. Keeping
the asking here means the library stays importable without ever prompting, which
is what makes the scoring testable, and it means the store path is a CLI
argument rather than a constant hidden in a module.

THE STORE IS JSONL AND THAT IS DELIBERATE
-----------------------------------------
One JSON object per line, appended, never rewritten. An elicitation is a datum
about a person at a moment; a store that rewrites itself (a SQLite table with an
upsert, a JSON array reserialised on save) can lose a session to a crash and
cannot be diffed. `tools/fit_affect_bridge.py --from-records` reads this file
directly, which is why there is no export step to forget to run.

RECORDS ARE NEVER EDITABLE
--------------------------
`Record.digest` is a content hash computed at scoring time. `verify` re-derives
it, so a store that has been edited by hand is detectable rather than silently
believed. Nothing here enforces that; it reports it.

Usage:
  python tools/elicit.py items                       # the battery, as served
  python tools/elicit.py items --json
  python tools/elicit.py record --pad -0.4,0.6,-0.3 \
      --answers threat_1=3,threat_2=2,...            # score one session, append
  python tools/elicit.py record --pad -0.4,0.6,-0.3 --ask
  python tools/elicit.py stats                       # count, homogeneity, digests
  python tools/elicit.py pairs --out pairs.json      # the fitter's input shape

Exit codes: 0 = ok, 2 = refused (incomplete session, no records, failed digest),
1 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

#: Imported rather than restated. Two tools that disagree about how much data is
#: enough is the failure mode where the reassuring number wins: a restated 50 here
#: and a real 50 there agree until one of them is changed.
from fit_affect_bridge import MIN_PEOPLE_RECORDS  # noqa: E402
from PSYCHE import elicitation  # noqa: E402
from PSYCHE.elicitation import (  # noqa: E402
    INSTRUMENT_VERSION, ITEMS, SCALE_LABELS, Record, Session, IncompleteSession,
    homogeneity, new_session, score_session,
)

#: Resolved from the environment so a monorepo run and an installed run agree, and
#: so a test can point it at a temporary file without patching a module constant.
STORE_ENV = "PSYCHE_ELICITATION_STORE"
DEFAULT_STORE = Path.home() / ".psyche" / "elicitations.jsonl"


def store_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    override = os.environ.get(STORE_ENV)
    return Path(override).expanduser() if override else DEFAULT_STORE


def read_store(path: Path) -> List[Record]:
    """Every readable record, in file order. Unparseable lines are reported.

    Returning a shorter list silently would let a corrupt store look like a
    smaller sample, and the fitter's refusal threshold is a row count -- so a
    lost line is a fit that should have been refused and was not.
    """
    if not path.exists():
        return []
    records: List[Record] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(Record.from_row(json.loads(line)))
        except (ValueError, KeyError, TypeError) as exc:
            print("UNREADABLE line %d: %s" % (number, exc), file=sys.stderr)
    return records


def append_store(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record.to_row(), sort_keys=True) + "\n")


def parse_pad(text: str) -> List[float]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise ValueError("--pad needs three comma-separated values, got %r" % text)
    values = [float(p) for p in parts]
    for value in values:
        if not -1.0 <= value <= 1.0:
            raise ValueError("PAD values are on -1..1; %r is outside" % value)
    return values


def parse_answers(text: str) -> Dict[str, int]:
    answers: Dict[str, int] = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError("expected key=value, got %r" % chunk)
        key, _, value = chunk.partition("=")
        answers[key.strip()] = int(value)
    return answers


def ask(answers: Dict[str, int], order: List[str]) -> Dict[str, int]:
    """Prompt in the session's randomised order.

    Answers already supplied on the command line are not re-asked, which is what
    makes a half-finished session resumable: the second run only asks what is
    still missing.
    """
    by_key = {item.key: item for item in ITEMS}
    done = dict(answers)
    for index, key in enumerate(order, 1):
        if key in done:
            continue
        item = by_key[key]
        prompt = "  %2d/%d  %s  [0-4: %s]  " % (
            index, len(order), item.text, ", ".join("%d=%s" % (i, label)
                                                    for i, label in enumerate(SCALE_LABELS)))
        while True:
            raw = input(prompt).strip()
            if raw.isdigit() and int(raw) in elicitation.SCALE:
                done[key] = int(raw)
                break
            print("    %r is not one of %s" % (raw, list(elicitation.SCALE)))
    return done


def render_items(as_json: bool) -> str:
    if as_json:
        return json.dumps({"instrument": INSTRUMENT_VERSION,
                           "scale": list(elicitation.SCALE),
                           "scale_labels": list(SCALE_LABELS),
                           "items": [item.to_dict() for item in ITEMS]}, indent=1)
    lines = ["%s  (%d items, %d constructs, scale 0-4)"
             % (INSTRUMENT_VERSION, len(ITEMS), len(elicitation.CONSTRUCTS)), ""]
    grouped: Dict[str, List] = {}
    for item in ITEMS:
        grouped.setdefault(item.construct, []).append(item)
    for construct in elicitation.CONSTRUCTS:
        lines.append("  %s" % construct)
        for item in grouped.get(construct, []):
            lines.append("    [%s] %s%s" % (item.key, item.text,
                                            "  (reverse-scored)" if item.reverse else ""))
    lines += ["", "These items were written for this project. They are not a validated",
              "instrument, they have no norms, and no factor structure has been",
              "established for them. See src/PSYCHE/elicitation.py."]
    return "\n".join(lines)


def render_stats(records: Sequence[Record], path: Path) -> str:
    if not records:
        return ("no records in %s\n"
                "That is the current state the fitter refuses on, not an error." % path)
    lines = ["%d record(s) in %s" % (len(records), path),
             "instrument(s): %s" % ", ".join(sorted({r.instrument for r in records})),
             "range: %s .. %s" % (records[0].taken_at, records[-1].taken_at), ""]
    gaps = homogeneity(records)
    lines.append("reverse-vs-forward agreement (lower is better; >1.0 means the")
    lines.append("person answered the direction, not the content):")
    for construct, gap in sorted(gaps.items(), key=lambda kv: -kv[1]):
        flag = "  <-- re-take rather than fit" if gap >= 1.5 else ""
        lines.append("  %-20s %.2f%s" % (construct, gap, flag))
    lines.append("")
    lines.append("Two different fits read this store, with different thresholds:")
    lines.append("  tools/psyche_fit_pams.py      graph edges -- needs 3 sessions, one")
    lines.append("                                per observation; 4 gives nodes and no")
    lines.append("                                edges, which is the honest answer at 4.")
    lines.append("  tools/fit_affect_bridge.py    affect -> construct -- needs %d sessions"
                 % MIN_PEOPLE_RECORDS)
    lines.append("    --from-records              from ONE instrument version, and refuses")
    lines.append("                                below that rather than fitting a line")
    lines.append("                                through four points.")
    return "\n".join(lines)


def verify_digests(records: Sequence[Record]) -> List[str]:
    """Records whose content no longer matches the hash taken at scoring time.

    Compares the RECOMPUTED digest against the one stored with the row. The
    obvious version of this function -- `record.digest != record.to_row()["digest"]`
    -- compares a value to itself and can never fail, which is the exact bug this
    project has already paid for once in `fit_affect_bridge.py`. A record with no
    stored digest is reported too: it came from somewhere that did not hash it.
    """
    return [r.taken_at for r in records
            if not r.stored_digest or r.digest != r.stored_digest]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", help="record store (default %s or $%s)"
                        % (DEFAULT_STORE, STORE_ENV))
    sub = parser.add_subparsers(dest="command")

    items_cmd = sub.add_parser("items", help="print the battery")
    items_cmd.add_argument("--json", action="store_true")

    record_cmd = sub.add_parser("record", help="score one session and append it")
    record_cmd.add_argument("--pad", required=True,
                            help="pleasure,arousal,dominance on -1..1")
    record_cmd.add_argument("--answers", default="", help="key=value,key=value")
    record_cmd.add_argument("--ask", action="store_true",
                            help="prompt for any items not given in --answers")
    record_cmd.add_argument("--seed", type=int,
                            help="item order seed, so a re-run asks the same order")
    record_cmd.add_argument("--note", default="")
    record_cmd.add_argument("--at", help="ISO timestamp (default: now, UTC)")

    sub.add_parser("stats", help="counts, homogeneity, digest check")

    pairs_cmd = sub.add_parser("pairs", help="emit pad/construct pairs for the fitter")
    pairs_cmd.add_argument("--out", help="write here instead of stdout")

    args = parser.parse_args(list(argv) if argv is not None else None)
    path = store_path(args.store)
    command = args.command or "stats"

    if command == "items":
        print(render_items(args.json))
        return 0

    if command == "stats":
        print(render_stats(read_store(path), path))
        broken = verify_digests(read_store(path))
        if broken:
            print("\nFAILED digest check on: %s" % ", ".join(broken), file=sys.stderr)
            return 2
        return 0

    if command == "pairs":
        rows = [dict(r.to_row(), pad=list(r.pad), constructs=dict(r.constructs))
                for r in read_store(path)]
        if not rows:
            print("no records in %s -- nothing to emit" % path, file=sys.stderr)
            return 2
        text = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print("wrote %d record(s) to %s" % (len(rows), args.out))
        else:
            print(text, end="")
        return 0

    # record
    try:
        pad = parse_pad(args.pad)
        answers = parse_answers(args.answers)
    except ValueError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    session: Session = new_session(args.seed)
    if args.ask:
        try:
            answers = ask(answers, session.order)
        except (EOFError, KeyboardInterrupt):
            print("\nREFUSED: session interrupted; nothing written.", file=sys.stderr)
            return 2
    taken_at = args.at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        record = score_session(answers, pad, taken_at=taken_at,
                               order=session.order, note=args.note)
    except IncompleteSession as exc:
        # A partial session is not a shorter record: scoring 24 of 30 items would
        # renormalise the missing construct and every stored record would carry a
        # small unmarked error in whatever direction was skipped.
        print("REFUSED: %s" % exc, file=sys.stderr)
        print("Nothing written. A partial session is not a shorter record.",
              file=sys.stderr)
        return 2
    append_store(path, record)
    print("appended %s  digest %s" % (record.taken_at, record.digest[:16]))
    print("  %d item(s), instrument %s, store %s"
          % (len(answers), record.instrument, path))
    print("  constructs: %s" % ", ".join("%s %+.2f" % (k, v)
                                         for k, v in sorted(record.constructs.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
