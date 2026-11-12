#!/usr/bin/env python
# coding: utf-8
"""Engram live supervisor gate (run at each turn boundary).

Engram's own live supervision is a per-prompt hook (UserPromptSubmit nudge,
PreToolUse gating) plus a nudge appended to MCP tool responses. This client
exposes no hook surface, so supervision is delivered here as an explicit gate
that consults engram's own supervisory outputs and prints a verdict:

  doctor     -> store invariants; blocked/errors is a NO-GO
  conflicts  -> unjudged contradiction pairs: pending -> CLARIFY (advisory);
                an ORPHANED relation -> NO-GO
  hook       -> the nudge / required first action engram would have pushed
  plan       -> open plan + mandate items still owed

Usage: python tools/engram_supervise.py [project]
Exit 0 = GO, or CLARIFY (unjudged conflict pairs are outstanding - advisory,
         not a block), or supervisor unavailable (fail-open to avoid deadlock).
Exit 1 = NO-GO (doctor blocked/errors, or an orphaned relation whose endpoint
         observation is missing - a structurally broken relation).

Why CLARIFY and not NO-GO for unjudged pairs: the store creates candidates by
itself (every `mem_save` spawns pairs for comparison), so hard-blocking on them
refuses the next tool call until they are judged - and judging is one of the
calls being refused, so the block can only be cleared by doing the thing it
forbids. NO-GO stays reserved for breakage, and the outstanding count is still
printed in full.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import re
import re as _re

HERE = Path(__file__).resolve().parent
PROJECT = os.environ.get("ENGRAM_PROJECT", "azazael")
ENGRAM_BIN = os.environ.get("ENGRAM_BIN") or shutil.which("engram") or str(
    Path.home() / "go" / "bin" / "engram.exe"
)


def run(args, stdin_data=None, timeout=60):
    """Run engram, returning stdout. Never raises.

    `timeout` bounds the engram.exe child. Default 60 keeps the existing
    behavior for every other consumer; the supervisor's verdict legs pass
    a value at-or-below their own wall-clock budget, because subprocess.run
    kills the child ON ITS OWN timeout and nothing else does -- an
    unbounded child outlives an aborted leg as a ghost process.
    """
    try:
        r = subprocess.run(
            [ENGRAM_BIN] + args, input=stdin_data,
            timeout=timeout, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
        return r.stdout or ""
    except subprocess.TimeoutExpired:
        # Tagged, not plain: a timeout means the store was busy enough to
        # overrun the budget, which is a different fact from a binary that
        # cannot be launched. Consumers still test the __ERR__ prefix.
        return f"__ERR__ timeout: engram did not answer within {timeout:g}s"
    except Exception as e:
        return f"__ERR__ {e}"


def strip_banner(text):
    """Drop engram's update-available banner and blank filler."""
    out = []
    skip = 0
    for line in text.splitlines():
        if line.startswith("Update available:"):
            skip = 3
            continue
        if skip:
            skip -= 1
            continue
        out.append(line)
    return "\n".join(out).strip()


def check_doctor(timeout=60):
    text = strip_banner(run(["doctor"], timeout=timeout))
    if text.startswith("__ERR__"):
        if "timeout:" in text.splitlines()[0]:
            return "unknown", [
                "doctor SLOW, not unreachable: " + text,
                "  a store busy with writes overruns this budget; re-run before "
                "treating it as a fault",
            ]
        return "unknown", ["doctor unreachable: " + text]
    head = text.splitlines()[0] if text else ""
    blocked = errors = 0
    for line in text.splitlines():
        if line.startswith("Checks:"):
            for part in line.replace("Checks:", "").split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k in ("blocked", "errors"):
                        try:
                            n = int(v)
                        except ValueError:
                            n = 0
                        if k == "blocked":
                            blocked = n
                        else:
                            errors = n
    detail = []
    for line in text.splitlines():
        if line.startswith("[") and not line.startswith("[ok]"):
            detail.append(line[:160])
    if blocked or errors:
        return "NO-GO", [f"doctor: {head}"] + detail
    return "GO", [f"doctor: {head}"]


def _judgment_status_counts(text):
    """Counts from the `By judgment_status:` section ONLY.

    The same payload carries a `By relation type:` section whose keys
    (related / compatible / supersedes / scoped) hold unrelated numbers. A
    parser that accepts any `key: digit` line from the whole document can let a
    relation count masquerade as a judgment count, so this walks section by
    section: a new `By ...` header switches the block and `Deferred`/`Dead` end
    it. Blank lines do NOT end a block - the real payload indents its entries
    directly under the header with no separator.
    """
    counts = {}
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("by "):
            in_block = low.startswith("by judgment_status")
            continue
        if low.startswith("deferred") or low.startswith("dead"):
            in_block = False
            continue
        if not in_block or ":" not in s:
            continue
        k, v = s.split(":", 1)
        v = v.strip()
        if v.isdigit():
            counts[k.strip().lower()] = int(v)
    return counts


def verdict_from_conflicts_stats(text):
    """Pure verdict for one `conflicts stats` payload -> (verdict, lines).

    verdict is "GO" | "CLARIFY" | "NO-GO" | "unknown". Split out from the
    subprocess call so the parsing is pinned against text transcribed from a
    real run rather than against whatever the parser happens to expect.
    """
    # An unrecognized response (bad flag, usage dump, schema change) must NOT
    # be silently read as healthy - that is the fail-open trap.
    if "Conflicts Stats" not in text and "No relations found" not in text:
        first = (text.splitlines() or ["<empty>"])[0][:120]
        return "unknown", ["conflicts: unrecognized output, treated as unknown: " + first]
    if "No relations found" in text:
        return "GO", ["conflicts: 0 unresolved"]
    stats = _judgment_status_counts(text)
    pending = stats.get("pending", 0)
    orphaned = stats.get("orphaned", 0)
    judged = (stats.get("accepted", 0) + stats.get("rejected", 0)
              + stats.get("judged", 0))
    if orphaned:
        return "NO-GO", [
            f"conflicts: {orphaned} orphaned relation(s) - endpoint observation is missing",
            f"  (pending={pending}, judged={judged})",
        ]
    if pending:
        # CLARIFY, not NO-GO: these pairs are created by the store itself, so a
        # hard block here refuses every later tool call until they are judged -
        # and judging is one of the calls being refused. The count is printed.
        return "CLARIFY", [
            f"conflicts: {pending} unjudged pair(s) outstanding "
            f"(judgment_status=pending) - judge them; not blocking",
            f"  (judged={judged}, orphaned={orphaned})",
        ]
    return "GO", [f"conflicts: 0 unresolved (judged={judged})"]


def check_conflicts(timeout=60):
    """Fetch engram's REAL `conflicts stats` and hand it to the pure parser.

    There is NO `Total:` line in `stats` - `conflicts list` prints Total,
    `stats` does not (cmd/engram/conflicts.go:146 vs :259-291). Gating on a
    fabricated `total` key fails open: it reports GO on a store full of
    unresolved contradictions. Verified against the Go source.
    """
    text = strip_banner(run(["conflicts", "stats", "--project", PROJECT],
                            timeout=timeout))
    if text.startswith("__ERR__"):
        return "unknown", ["conflicts unreachable: " + text]
    return verdict_from_conflicts_stats(text)


def check_hook(timeout=60):
    payload = json.dumps({"session_id": "supervisor", "cwd": str(HERE.parent)})
    text = run(["hook", "codex-user-prompt-submit"], stdin_data=payload,
               timeout=timeout)
    msg = ""
    parse_error = ""
    try:
        msg = (json.loads(text.strip().splitlines()[-1]) or {}).get("systemMessage", "")
    except Exception as exc:
        # A hook whose output cannot be READ is not a hook with nothing to
        # say. Collapsing the two would report a broken supervisor as "none
        # due" - the failure shown as a clean verdict.
        parse_error = "%s: %s" % (type(exc).__name__, exc)
    if parse_error:
        return ["nudge: UNREADABLE (" + parse_error[:160] + ")"]
    if not msg:
        return ["nudge: none due"]
    first = msg.splitlines()[0][:200]
    return ["nudge: " + first]


def _plan_tasks(text):
    """Parse `search --type task` headers -> [(numeric_id, title)].

    Header format (transcribed from a real run, 2026-10-17):
      [1] #1007 (task) — Task — Kilo: has #985 tail-gate ...
    followed within a few lines by a date line:
      2026-10-17 09:47:42 | project: azazael | scope: project
    Returns [(numeric_id, title, yyyy-mm-dd)] or None when the output is
    unrecognized (never an empty list - an empty list means "no tasks",
    which is a claim, not a fallback).
    """
    tasks = []
    cur = None
    seen_header = False
    for line in text.splitlines():
        m = re.match(r"^\[\d+\] #(\d+) \(task\) — (.*)$", line)
        if m:
            seen_header = True
            if cur is not None:
                tasks.append(cur)
            cur = [m.group(1), m.group(2).strip(), ""]
            continue
        dm = re.match(r"^\s*(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \|", line)
        if dm and cur is not None and not cur[2]:
            cur[2] = dm.group(1)
    if cur is not None:
        tasks.append(cur)
    if not seen_header and "Found" not in text and "No memories" not in text:
        return None
    return [(tid, title, date) for tid, title, date in tasks]


def _superseded_task_ids(text):
    """Task numbers closed by a judged `supersedes` relation.

    Block format (transcribed from a real run, 2026-10-17):
      relation:       related
      judgment_status: judged
      source:         obs-378f32744e609fdd — Task — Kilo: ... (c...
      target:         obs-9149fa4509d9380a — #1046 verdict: ... (c...
    Closure records are titled "Task #NNNN DONE — ..."; any judged
    supersedes edge touching such an endpoint closes that task number.
    Matching is by NUMBER, not title text: titles truncate (~60 chars
    + ...) and a closure title never equals the task title word for
    word, so prefix matching broke on the first real use. Only judged
    supersedes counts: related/compatible/not_conflict leave the task
    owed, and pending pairs are not verdicts yet. The DONE marker is
    required so an outcome record's own #NNN (e.g. a verdict citing
    other tasks) cannot close tasks it merely mentions. Returns None
    when the output is unrecognized.
    """
    ids = set()
    relation = status = ""
    endpoints = []
    recognized = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("relation:"):
            relation = s.split(":", 1)[1].strip()
            endpoints = []
        elif s.startswith("judgment_status:"):
            status = s.split(":", 1)[1].strip()
        elif s.startswith("source:") or s.startswith("target:"):
            recognized = True
            endpoints.append(s.split("—", 1)[1].strip() if "—" in s else "")
            if s.startswith("target:"):
                if relation == "supersedes" and status == "judged":
                    for ep in endpoints:
                        if "DONE" in ep.upper():
                            ids.update(_re.findall(r"#(\d{3,})\b", ep))
                relation = status = ""
                endpoints = []
    if not recognized and "Total:" not in text and "No relations" not in text:
        return None
    return ids


def check_plan():
    """Open plan items = tasks with no judged `supersedes` pointing at them.

    A task is done when an execution record judges it superseded
    (source=execution, target=task). Minted-but-unexecuted and
    executed-but-never-judged are both still owed: the next session
    cannot tell them apart, so the gate must not pretend otherwise.

    (Replaces a parser that matched `[topic:plan/` against `context`
    output - a marker the command never emits, so the count read 0
    forever. Found 2026-10-17: `context` renders `- [task] **Title**`.)
    """
    # NOTE: the search API caps at 20 rows per query with no offset, so
    # no single query enumerates all tasks (verified 2026-10-17: every
    # broad query returns exactly 20). Union several diverse queries and
    # dedupe by numeric id. Closure is not provable from outside, so the
    # count is reported as a lower bound when the union stops growing -
    # which is still infinitely more honest than the old silent 0.
    # 2026-10-17: a 20-token probe outside this list surfaced dozens of
    # missed tasks (#592-#999: DeepSeek/opencode/MiMo-era backlog). The
    # union only covers vocabularies it queries, so the list grows when
    # probes find gaps. Still a lower bound, never a census.
    queries = ("task", "Kilo", "Freebuff", "fix", "verify", "wire",
               "bandit", "grpo", "hybrid", "consumer",
               "DeepSeek", "opencode", "MiMo", "audit", "build",
               "design", "retire", "quantize", "ablation",
               "quarantine", "manifest", "adjudicate", "falsify",
               "persist", "commit", "map", "mock")
    tasks = {}
    for q in queries:
        raw = strip_banner(run(
            ["search", q, "--type", "task", "--project", PROJECT,
             "--limit", "100"]))
        if raw.startswith("__ERR__"):
            return ["open plan items: unknown (task search unreachable)"]
        parsed = _plan_tasks(raw)
        if parsed is None:
            return ["open plan items: unknown (unrecognized search output)"]
        for tid, title, date in parsed:
            tasks.setdefault(tid, (title, date))
    # NOTE: `--limit` caps at 500 and the list is newest-first, so very
    # old supersedes (below the window) read as still-open. That errs
    # toward showing work, which is the safe direction for a plan gate -
    # the opposite error (silent 0) is what this fix removes.
    raw_rels = strip_banner(run(
        ["conflicts", "list", "--project", PROJECT, "--limit", "500"]))
    if raw_rels.startswith("__ERR__"):
        return ["open plan items: unknown (relations unreachable)"]
    done_ids = _superseded_task_ids(raw_rels)
    if done_ids is None:
        return ["open plan items: unknown (unrecognized relations output)"]
    # No recency split: this store's whole backlog is 6 days old, so any
    # day-cutoff is arbitrary line-drawing inside one active lane, not a
    # signal. Every unjudged task is listed; closing dead ones is triage
    # work (judge them superseded), not display work. Dates are still
    # captured per task for future use.
    open_items = [(tid, t) for tid, (t, d) in tasks.items()
                  if tid not in done_ids]
    lines = ["open plan items: %d (union of %d queries; lower bound)"
             % (len(open_items), len(queries))]
    # Newest first: the current lane lives at the top of the numbering,
    # so descending puts tonight's plans where eyes land.
    for tid, t in sorted(open_items, key=lambda x: -int(x[0]))[:20]:
        lines.append("  - #%s %s" % (tid, t[:100]))
    if len(open_items) > 20:
        lines.append("  - ... +%d more" % (len(open_items) - 20))
    return lines


def safe_print(text):
    """Print on Windows consoles whose codec is cp1252, not UTF-8.

    Titles carry arrows (U+2192) and em-dashes; an unguarded print()
    raises UnicodeEncodeError and kills the gate AFTER it measured -
    the failure mode is a crash that looks like a clean bill."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(enc, "replace").decode(enc) + "\n")


def main() -> int:
    if not (Path(ENGRAM_BIN).exists() or shutil.which(ENGRAM_BIN)):
        print("engram_supervise: engram binary not found; fail-open (exit 0)")
        return 0

    verdicts = []
    print("=== ENGRAM SUPERVISOR (" + PROJECT + ") ===")
    for fn in (check_doctor, check_conflicts):
        verdict, lines = fn()
        verdicts.append(verdict)
        for l in lines:
            print("  " + l)
    for l in check_hook():
        safe_print("  " + l)
    for l in check_plan():
        safe_print(l)

    nogos = [v for v in verdicts if v == "NO-GO"]
    if nogos:
        print("VERDICT: NO-GO - resolve the items above before proceeding")
        return 1
    clarifies = [v for v in verdicts if v == "CLARIFY"]
    if clarifies:
        print("VERDICT: CLARIFY - unjudged conflict pair(s) outstanding; judge "
              "them when convenient. Advisory, not a block (exit 0).")
        return 0
    print("VERDICT: GO - proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
