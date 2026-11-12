# coding: utf-8
"""bible_ship -- install the canon, its hooks and its injection block into a repo.

WHY THIS EXISTS
---------------
`bible_check.py` makes the corpus *citable*. That is worth nothing to an agent
working in the repository, because nothing in an agent's session reads
`docs/bible/bible.json`. Measured in this tree: an assistant can be handed a
canon of statutes and still claim a control is "in place" while it has never
been run, and can still write a commit message asserting completion with no
command behind it, because the corpus is a file the agent never opens.

So the canon has to arrive through a channel the client actually reads. In this
client that channel is the instruction file: the MCP `instructions` field is
discarded, and the plugin API has no assistant-output hook, so **a managed block
in `AGENTS.md` is the only push channel that reaches an agent before it acts.**
This tool generates that block from the corpus, keeps it current, and installs
the git hooks that make staleness fail loudly.

WHAT SHIPS
----------
    the injected block     rendered from the corpus: statutes, pinned config, the
                           decision-block contract, the followup count
    .githooks/pre-commit   runs the corpus checker and the block-freshness check
    .githooks/pre-push     PUSH INJECTION: refresh the block; if it had gone
                           stale, abort the push so the refresh is committed
    .githooks/commit-msg   a message that claims completion must name the
                           command that verified it (the corpus's own rule)
    plugin                 an opencode plugin that appends the block to the
                           session's system prompt, syntax-checked with `node`

USAGE
-----
    python tools/bible_ship.py --inject                # create/refresh the block
    python tools/bible_ship.py --check                 # exit 1 when any block is stale
    python tools/bible_ship.py --install-hooks DIR     # idempotent, marker-delimited
    python tools/bible_ship.py --verify DIR            # hooks wired + block current + plugin parses
    python tools/bible_ship.py --render                # print the block, change nothing
    python tools/bible_ship.py --self-test             # negative controls

Every write is marker-delimited (`BIBLE:BEGIN` / `BIBLE:END`, `BIBLE-HOOK v1`), so
re-running is idempotent and a hand-edited block is reported rather than
silently overwritten. Nothing here touches a hook it did not write.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "docs" / "bible" / "bible.json"

BEGIN = "<!-- BIBLE:BEGIN"
END = "<!-- BIBLE:END -->"
HOOK_MARK = "BIBLE-HOOK v1"
PLUGIN_NAME = "bible.mjs"

#: Files a client may read before the first turn. None of them is created by
#: default, and the reason is a measured hazard rather than caution: this tree was
#: run once from a repository whose parent directory already held a 38 KB lane
#: instruction file, and creating a repo-local `AGENTS.md` containing only the
#: canon block would have made the nearest file win -- silently retiring session
#: order, the memory rules and every other inherited obligation. So creation is
#: opt-in (`--create-agents`), refused when a parent already has one, and
#: `--target PATH` exists for the case where the instruction file lives elsewhere.
INJECTION_TARGETS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".cursorrules",
)
CREATE_IF_MISSING: Tuple[str, ...] = ()


def parent_instruction_file(root: Path) -> Optional[Path]:
    """The nearest AGENTS.md above `root`, if any. Creating one under it would
    shadow it for every client that resolves the nearest file."""
    for parent in Path(root).resolve().parents:
        candidate = parent / "AGENTS.md"
        if candidate.is_file():
            return candidate
    return None

#: The number the user fixed for this estate. The corpus does not derive it: it
#: is a contract about the shape of the reply, not a measurement.
FOLLOWUP_COUNT = 14


def load_corpus(path: Path = CORPUS) -> Dict:
    if not path.exists():
        raise SystemExit("bible_ship: no corpus at %s -- nothing to ship" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def _statutes(data: Dict) -> List[Tuple[str, str, str]]:
    """(rule, text, check) for every verse that carries a statute, in corpus order."""
    found = []
    for book in data.get("books", []):
        for chapter in book.get("chapters", []):
            for verse in chapter.get("verses", []):
                statute = verse.get("statute")
                if isinstance(statute, dict) and statute.get("rule"):
                    found.append((statute["rule"],
                                  " ".join(str(verse.get("text", "")).split()),
                                  statute.get("check", "")))
    return found


def render_block(data: Dict, followups: int = FOLLOWUP_COUNT) -> str:
    """The canon as an instruction block. Generated, never hand-edited.

    It carries four things and no more, because a block that restates the whole
    corpus is a second source of truth: the statutes with their checks, the
    pinned configuration, the decision-block contract, and the followup count.
    """
    lines = [
        "%s (generated by tools/bible_ship.py from docs/bible/bible.json -- edit"
        " the corpus, not this block) -->" % BEGIN,
        "",
        "## The Book of the Tree — canon in force",
        "",
        "This repository ships its own canon at `docs/bible/bible.json`. It is checked on",
        "every commit (`tools/bible_check.py --check`), so what follows is the current",
        "reading, not a summary somebody wrote once.",
        "",
    ]
    statutes = _statutes(data)
    if statutes:
        lines.append("### Statutes, each with the command that executes it")
        lines.append("")
        for rule, text, check in statutes:
            lines.append("- **%s** — %s" % (rule, text))
            if check:
                lines.append("  check: `%s`" % check)
        lines.append("")
    configs = data.get("configs") or []
    if configs:
        lines.append("### Configuration — do not change a value without the corpus")
        lines.append("")
        lines.append("| setting | value | where | if it drifts |")
        lines.append("|---|---|---|---|")
        for config in configs:
            where = config.get("file") or config.get("key") or ""
            if config.get("key"):
                where = "%s[%s]" % (where, config["key"])
            lines.append("| %s | `%s` | %s | %s |" % (
                config.get("name", config.get("id", "")),
                config.get("value", ""),
                where,
                " ".join(str(config.get("governs", "")).split())[:160],
            ))
        lines.append("")
    lines.extend([
        "### Before any state-changing action, print a decision block",
        "",
        "````",
        "DECISION  <one line: what you are about to change>",
        "  target:    <files / services touched>",
        "  evidence:  <the command you ran and what it printed>",
        "  blast:     <what breaks if this is wrong, and who notices>",
        "  verify:    <the command that will prove it worked>",
        "  rollback:  <how to undo it>",
        "````",
        "",
        "A block with `evidence: none yet` is allowed once, for a read-only probe.",
        "It is not allowed before a write.",
        "",
        "### When the turn ends",
        "",
        ("Give exactly **%d** followups: short, goal-oriented, each naming its target,\n"
         "none of them a chore the agent cannot do itself. The thirteenth is always the\n"
         "abyss card -- `Build, land, in the abyss`, one click that plans, builds, verifies\n"
         "and commits clean; the fourteenth is zero-shot reasoning over the evidence the\n"
         "thirteen produced. Count them before sending — a reply with %d or %d is a\n"
         "miscount, not a rounding.")
        % (followups, followups - 1, followups + 1),
        "",
        "### A claim needs its command",
        "",
        "`done`, `fixed`, `passes`, `verified`, `in place`, `landed` are claims. Each",
        "one is worth exactly what the command beside it is worth. If you cannot name",
        "the command and its output, say what you actually did instead.",
        "",
        END,
    ])
    return "\n".join(lines) + "\n"


def current_block(path: Path) -> Optional[str]:
    """The block already in a file, or None. Marker-delimited, so a hand-written
    paragraph near it is never part of the managed region."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find(BEGIN)
    if start < 0:
        return None
    end = text.find(END, start)
    if end < 0:
        return text[start:]
    return text[start:end + len(END)]


def inject(path: Path, block: str, create: bool = False) -> str:
    """Write or refresh the block. Returns one of:
    created | updated | unchanged | absent | unclosed-markers."""
    if not path.exists():
        if not create:
            return "absent"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block + "\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find(BEGIN)
    if start < 0:
        separator = "" if text.endswith("\n") else "\n"
        path.write_text(text + separator + "\n" + block, encoding="utf-8")
        return "updated"
    end = text.find(END, start)
    if end < 0:
        return "unclosed-markers"
    end += len(END)
    existing = text[start:end]
    if existing.strip() == block.strip():
        return "unchanged"
    path.write_text(text[:start] + block.rstrip("\n") + text[end:], encoding="utf-8")
    return "updated"


def injection_report(data: Dict, root: Path, inject_block: bool = False,
                     create_agents: bool = False,
                     targets: Sequence[str] = ()) -> List[Tuple[str, str]]:
    block = render_block(data)
    results = []
    relatives = list(targets) if targets else list(INJECTION_TARGETS)
    for relative in relatives:
        path = Path(relative) if Path(relative).is_absolute() else root / relative
        create = create_agents and Path(relative).name == "AGENTS.md"
        if create and not path.exists():
            parent = parent_instruction_file(Path(root))
            if parent is not None:
                results.append((relative,
                                "refused: %s already owns this repository's agent "
                                "instructions; a local file would shadow it" % parent))
                continue
        if inject_block:
            results.append((relative, inject(path, block, create=create)))
            continue
        existing = current_block(path)
        if existing is None:
            results.append((relative, "absent" if not path.exists() else "no-block"))
        elif existing.strip() == block.strip():
            results.append((relative, "current"))
        else:
            results.append((relative, "stale"))
    return results


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #

PRE_COMMIT = """#!/bin/sh
# {mark} -- planted by tools/bible_ship.py. Re-running install is idempotent.
# Bypass (declared, never silent): BIBLE_SKIP=1 git commit ...
[ "${BIBLE_SKIP:-}" = "1" ] && { echo "[Bible] skipped: BIBLE_SKIP=1 declared."; exit 0; }
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$root/tools/bible_check.py" ] || { echo "[Bible] no corpus gate here -- passing loud"; exit 0; }
py=$(command -v python3 || command -v python)
[ -n "$py" ] || { echo "[Bible] no python -- gate unrunnable, passing loud"; exit 0; }
echo "[Bible] checking the corpus resolves its own citations..."
out=$("$py" "$root/tools/bible_check.py" --check 2>&1); rc=$?
echo "$out" | tail -15
[ $rc -eq 0 ] || { echo "[Bible] BLOCKED: the corpus contradicts itself."; exit 1; }
if [ -f "$root/tools/bible_ship.py" ]; then
    echo "[Bible] checking the injected canon block is current..."
    out=$("$py" "$root/tools/bible_ship.py" --check 2>&1); rc=$?
    echo "$out" | tail -6
    if [ $rc -ne 0 ]; then
        echo "[Bible] BLOCKED: an instruction file carries a stale canon block."
        echo "[Bible] Fix: $py tools/bible_ship.py --inject && git add -A"
        exit 1
    fi
fi
exit 0
"""

PRE_PUSH = """#!/bin/sh
# {mark} -- push injection. Planted by tools/bible_ship.py.
# WHY A PUSH HOOK: pre-commit is optional on somebody else's machine (it needs
# core.hooksPath set, and a clone can skip it). Push is the moment a change leaves
# this machine, so it is the last point where a stale canon block can be caught
# before it becomes other people's instruction file. This hook REFRESHES the block
# and then ABORTS the push if it had to change anything: injecting silently during
# a push would ship an uncommitted file that no reviewer saw.
# Bypass (declared): BIBLE_PUSH_BYPASS=1 git push ...
# Also on this hook: branch-merge readiness before the push leaves the machine
# (tools/git_merge_gate.py --pre-push; bypass MERGE_GATE_PUSH_BYPASS=1).
[ "${BIBLE_PUSH_BYPASS:-}" = "1" ] && { echo "[Bible] push injection bypassed: declared."; exit 0; }
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$root/tools/bible_ship.py" ] || exit 0
py=$(command -v python3 || command -v python)
[ -n "$py" ] || { echo "[Bible] no python -- injection skipped, passing loud"; exit 0; }
# ── What is being pushed, read ONCE (2026-11-03) ──
# stdin carries one line per pushed ref: `<local ref> <local sha> <remote ref>
# <remote sha>`. It used to be read inside the merge stanza below, so a bypassed
# merge gate (MERGE_GATE_PUSH_BYPASS=1, or the tool absent) left stdin untouched
# and every later stanza blind to the push it was gating. Capture it here, before
# anything else can consume it.
refs=$(cat)
# ── Merge readiness (2026-11-01) ──
# A branch-merge conflict is a property of the PAIR (this branch, the remote tip),
# so it changes when the other side moves, not when you type. Judged per commit it
# re-answers the same question on every keystroke and still misses the drift that
# happens between two commits; the push is the moment the branch actually meets the
# remote. tools/git_merge_gate.py does the three-way merge in the object database
# (`git merge-tree --write-tree`): no worktree, no index, no merge state to abort.
# Exit 1 = real CONFLICTS, abort. Exit 2 = could not judge (a git too old for
# merge-tree, a shallow clone): say so and PASS LOUD -- blocking every push on a
# host that cannot run the check is how a gate gets deleted rather than fixed.
if [ "${MERGE_GATE_PUSH_BYPASS:-}" != "1" ] && [ -f "$root/tools/git_merge_gate.py" ]; then
    if [ -n "$refs" ]; then
        merge_out=$(printf '%s\n' "$refs" | "$py" "$root/tools/git_merge_gate.py" --pre-push 2>&1)
        merge_rc=$?
        echo "$merge_out" | tail -12
        if [ $merge_rc -eq 1 ]; then
            echo ""
            echo "[Merge] PUSH ABORTED: a pushed branch does not merge with its remote tip."
            echo "[Merge] Conflicted paths are listed above. Resolve them here first:"
            echo "[Merge]   git fetch && git merge origin/<branch>"
            echo "[Merge] Bypass (declared): MERGE_GATE_PUSH_BYPASS=1 git push ..."
            exit 1
        fi
        if [ $merge_rc -eq 2 ]; then
            echo "[Merge] merge readiness NOT judged (see the line above) -- passing loud, not green."
        fi
    fi
elif [ "${MERGE_GATE_PUSH_BYPASS:-}" != "1" ]; then
    echo "[Merge] tools/git_merge_gate.py is absent -- merge readiness NOT judged (passing loud)."
fi
"$py" "$root/tools/bible_ship.py" --check >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[Bible] the canon block was stale at push time. Refreshing it now:"
    "$py" "$root/tools/bible_ship.py" --inject 2>&1 | tail -5
    echo ""
    echo "[Bible] PUSH ABORTED: the block changed. Review it, commit it, push again."
    echo "[Bible]   git add -A && git commit -m 'Refresh the injected canon block' && git push"
    exit 1
fi
if [ -f "$root/tools/bible_check.py" ]; then
    "$py" "$root/tools/bible_check.py" --check >/dev/null 2>&1 || {
        "$py" "$root/tools/bible_check.py" --check 2>&1 | tail -10
        echo "[Bible] PUSH ABORTED: corpus contradicts itself."
        exit 1
    }
fi
# -- Evidence tier (2026-11-02 split) --
# The whole-tree half of the commit gate lives in tools/evidence_tier.sh: the
# canon corpora, the platform claims, the anti-pattern and reference ratchets,
# the mesh/wire/evidence ledgers, the span-receipt selftest, the METTHEW
# catalogue, the at-risk register, the gate registry. Those checks moved OUT of
# pre-commit because one run cost 182.2 s (measured 2026-11-02) and every one of
# them answers a question about the TREE, which cannot change between two
# commits. They run HERE, unchanged, before anything leaves the machine.
#
# IT LIVES IN tools/ AND IS CALLED BY ABSOLUTE ROOT PATH. The first version of
# this stanza called `.githooks/lib/evidence.sh`, which `.gitignore`'s
# `.githooks/*` rule excludes: the gate ran on the host that wrote it and every
# clone this template generates a hook for would have called a file it does not
# have. The path below is tracked, and this template is the thing that makes the
# push hook exist, so the two cannot drift apart again without this paragraph
# going stale.
#
# A push is the last moment this tree is still ours: a stale ledger or a corpus
# that contradicts itself is cheap to fix now and other people's instruction
# file once it lands. Failing closed is therefore correct here in a way it was
# not at every commit. An absent tier file PASSES LOUD with its reason, because
# a host that cannot run it must not be blocked forever by a gate it cannot see.
#
# ── WHICH CHANGESET THE TIER JUDGES (2026-11-03) ──
# Until today the tier judged the git INDEX: whichever files happened to be staged
# when the push fired. On a worktree several lanes share, that is another lane's
# work -- commit 12e47073 was refused over 7 forensic files a concurrent lane had
# staged, none of them in the commit, and the refusal named a gate that could not
# tell "my change is too big" from "somebody else's directory is dirty".
# The base is the REMOTE sha of the ref being pushed, and only when that ref's
# local sha is this worktree's HEAD: that is the changeset actually leaving.
# With no such ref (a brand-new branch, or a push of a local ref that is not HEAD)
# the tier stays index-scoped and says so, because a guessed base would judge a
# changeset nobody asked about -- worse than the hijack it replaces.
head_sha=$(git rev-parse HEAD 2>/dev/null)
ev_base=""
if [ -n "$refs" ] && [ -n "$head_sha" ]; then
    ev_base=$(printf '%s\n' "$refs" | awk -v h="$head_sha" '$2 == h { print $4; exit }')
    case "$ev_base" in
        "") ;;
        *[!0]*) ;;                 # a real sha: judge ev_base..HEAD
        *) ev_base="" ;;           # all zeroes: the remote has no such ref yet
    esac
fi
# Bypass (declared): EVIDENCE_TIER_BYPASS=1 git push ...
if [ "${EVIDENCE_TIER_BYPASS:-}" = "1" ]; then
    echo "[Evidence] tier bypassed at push: EVIDENCE_TIER_BYPASS=1 declared."
    echo "[Evidence] the tree is leaving this machine with its evidence unread."
elif [ -f "$root/tools/evidence_tier.sh" ]; then
    ev_t0=$(date +%s)
    if [ -n "$ev_base" ]; then
        sh "$root/tools/evidence_tier.sh" --base "$ev_base"
    else
        echo "[Evidence] no pushed commit matched HEAD -- judging the git index."
        sh "$root/tools/evidence_tier.sh"
    fi
    ev_rc=$?
    echo "[Evidence] tier exit=$ev_rc wall=$(( $(date +%s) - ev_t0 ))s"
    if [ $ev_rc -ne 0 ]; then
        echo ""
        echo "[Evidence] PUSH ABORTED: the tree does not pass its whole-tree evidence tier."
        echo "[Evidence] The refusal above names the gate. Run it alone to iterate:"
        echo "[Evidence]   sh tools/evidence_tier.sh"
        echo "[Evidence]   sh tools/evidence_tier.sh --base <rev>   # judge a commit"
        echo "[Evidence] Bypass (declared): EVIDENCE_TIER_BYPASS=1 git push ..."
        exit 1
    fi
else
    echo "[Evidence] tools/evidence_tier.sh absent -- evidence tier NOT run (passing loud)."
fi
exit 0
"""

COMMIT_MSG = """#!/bin/sh
# {mark} -- the corpus's own rule, mechanised: a claim needs its command.
# A message that says done/fixed/complete/passes/verified must also carry a
# `Verified:` line naming the command that settles it. Measured reason: two lanes
# reported tasks complete that had not run, and a commit message is the one place
# an overclaim becomes permanent.
# Bypass (declared): BIBLE_CLAIM_BYPASS=1 git commit ...
[ "${BIBLE_CLAIM_BYPASS:-}" = "1" ] && exit 0
# Git passes $1 as a path. Under Windows Git Bash a backslashed path is not
# testable with `[ -f ]`, and the guard then exits 0 on every commit -- a gate
# that never fires while looking installed. cygpath translates it when present;
# git's own COMMIT_EDITMSG is the fallback, because that is the path the hook is
# actually handed in the normal case.
msg_file="$1"
if command -v cygpath >/dev/null 2>&1; then
    converted=$(cygpath -u "$1" 2>/dev/null)
    [ -n "$converted" ] && msg_file="$converted"
fi
[ -f "$msg_file" ] || msg_file=".git/COMMIT_EDITMSG"
[ -f "$msg_file" ] || exit 0
body=$(grep -v '^#' "$msg_file")
# No \b in the pattern: the backslash did not survive being written into this
# template once already and became a literal backspace (0x08), so the gate
# matched nothing and passed every overclaim. A bracket class has no escapes.
if printf '%s' "$body" | grep -Eiq '([^a-z]|^)(done|fixed|complete|completed|passes|verified|landed|in place)([^a-z]|$)'; then
    if ! printf '%s' "$body" | grep -Eiq '^(Verified|verified):'; then
        echo "[Bible] BLOCKED: the message claims completion with no Verified: line." >&2
        echo "[Bible] Add the command and its result, e.g." >&2
        echo "[Bible]   Verified: python -m pytest tests/ -q -> 66 passed" >&2
        echo "[Bible] Or say what you actually did instead of that the work is done." >&2
        echo "[Bible] Bypass (declared): BIBLE_CLAIM_BYPASS=1 git commit ..." >&2
        exit 1
    fi
fi
exit 0
"""

HOOKS = {
    "pre-commit": PRE_COMMIT,
    "pre-push": PRE_PUSH,
    "commit-msg": COMMIT_MSG,
}


def install_hooks(root: Path, only: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
    """Write the hooks into `root/.githooks`, marker-delimited and executable.

    A file that exists WITHOUT our marker is never overwritten: it is somebody
    else's hook, and silently replacing it is the destructive move this whole
    corpus is about avoiding. It is reported as `occupied` and left alone.

    `only` exists because adding a gate where a gate already runs is not defence:
    this tree's own `prepare-commit-msg` already guards the commit message, so
    planting a second guard on the same claim would produce two opinions and the
    false sense of depth that comes with them.
    """
    hooks_dir = root / ".githooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    results = []
    selected = HOOKS if not only else {name: HOOKS[name] for name in only if name in HOOKS}
    for name, template in selected.items():
        path = hooks_dir / name
        body = template.replace("{mark}", HOOK_MARK)
        if path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARK not in existing:
                results.append((name, "occupied"))
                continue
            if existing.strip() == body.strip():
                results.append((name, "unchanged"))
            else:
                path.write_text(body, encoding="utf-8")
                results.append((name, "updated"))
        else:
            path.write_text(body, encoding="utf-8")
            results.append((name, "created"))
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return results


def hooks_path(root: Path) -> Tuple[Optional[str], str]:
    """(configured hooksPath, note). Never assumes the setting is present."""
    try:
        completed = subprocess.run(["git", "config", "core.hooksPath"], cwd=str(root),
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "git could not be run (%s)" % type(exc).__name__
    value = (completed.stdout or "").strip()
    return (value or None), "" if value else "core.hooksPath is not set"


def wire_hooks_path(root: Path) -> str:
    """Set core.hooksPath to .githooks, or refuse if it points somewhere else.

    Refusing matters: a repository that already routes hooks at a directory of
    its own will lose its gates the moment this tool rewrites the setting, and
    the failure would be silent from then on.
    """
    value, _ = hooks_path(root)
    if value in (".githooks", str(root / ".githooks")):
        return "already wired"
    if value:
        return "refused: core.hooksPath is %r -- merge the stanzas by hand" % value
    try:
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(root),
                       check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return "failed: %s" % type(exc).__name__
    return "wired"


# --------------------------------------------------------------------------- #
# Plugin
# --------------------------------------------------------------------------- #

PLUGIN = """// {mark} -- generated by tools/bible_ship.py. Appends the canon block to every
// session's system prompt, because this client DISCARDS the MCP `instructions`
// field and has no assistant-output hook: a file the client reads is the only
// channel that reaches the agent before its first tool call.
//
// The block is read from disk on every session, so refreshing it needs no code
// change: python tools/bible_ship.py --inject
import fs from "node:fs";
import path from "node:path";

const BEGIN = "<!-- BIBLE:BEGIN";
const END = "<!-- BIBLE:END -->";

export function canonBlock(root) {
  for (const name of ["AGENTS.md", "CLAUDE.md", ".cursorrules"]) {
    const file = path.join(root, name);
    let text = "";
    try {
      text = fs.readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const start = text.indexOf(BEGIN);
    if (start < 0) continue;
    const end = text.indexOf(END, start);
    if (end < 0) continue;
    return text.slice(start, end + END.length);
  }
  return "";
}

export const BiblePlugin = async ({ directory }) => {
  const root = directory || process.cwd();
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      const block = canonBlock(root);
      if (!block) return;
      output.system.push(block);
    },
  };
};

export default BiblePlugin;
"""


def install_plugin(root: Path) -> str:
    """Write the plugin and syntax-check it. Unrunnable is reported, not assumed."""
    target = root / ".opencode" / "plugin" / PLUGIN_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    body = PLUGIN.replace("{mark}", HOOK_MARK)
    existed = target.exists()
    if existed and target.read_text(encoding="utf-8", errors="replace") == body:
        written = "unchanged"
    else:
        target.write_text(body, encoding="utf-8")
        written = "updated" if existed else "created"
    node = shutil.which("node")
    if not node:
        return "%s; syntax unchecked (node not on PATH)" % written
    try:
        completed = subprocess.run([node, "--check", str(target)],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "%s; syntax unchecked (%s)" % (written, type(exc).__name__)
    if completed.returncode != 0:
        return "%s; SYNTAX ERROR: %s" % (written, (completed.stderr or "").strip()[:300])
    return "%s; node --check passed" % written


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_render(data: Dict) -> int:
    sys.stdout.write(render_block(data))
    return 0


def cmd_inject(data: Dict, root: Path, create_agents: bool = False,
               targets: Sequence[str] = ()) -> int:
    results = injection_report(data, root, inject_block=True,
                               create_agents=create_agents, targets=targets)
    for relative, state in results:
        print("  %-34s %s" % (relative, state))
    changed = [r for r, s in results if s in ("created", "updated")]
    current = [r for r, s in results if s == "current"]
    refused = [(r, s) for r, s in results if s.startswith("refused")]
    if changed:
        print("\nblock written to: %s" % ", ".join(changed))
        print("Commit it: the block is the only channel that reaches an agent before it acts.")
        return 0
    if current:
        print("\nall %d block(s) current" % len(current))
        return 0
    print("\nNOTHING INJECTED: no instruction file here carries a canon block.")
    for relative, state in refused:
        print("  refused %s: %s" % (relative, state))
    print("  If the instruction file lives elsewhere, name it:")
    print("    python tools/bible_ship.py --inject --target /path/to/AGENTS.md")
    print("  If this is a fresh repository with no inherited file:")
    print("    python tools/bible_ship.py --inject --create-agents")
    return 1


def cmd_check(data: Dict, root: Path) -> int:
    results = injection_report(data, root)
    for relative, state in results:
        tag = {"current": "ok  ", "absent": "none"}.get(state, "STALE")
        print("  [%s] %-40s %s" % (tag, relative, state))
    stale = [r for r, s in results if s not in ("current", "absent")]
    missing = [r for r, s in results if s == "absent"]
    if missing:
        print("\nnot present (fine unless you expect it): %s" % ", ".join(missing))
    if stale:
        print("\n%d block(s) stale or unmanaged. Fix: python tools/bible_ship.py --inject"
              % len(stale))
        return 1
    print("\ncanon block current in every instruction file that carries one")
    return 0


def cmd_install(data: Dict, root: Path, only: Optional[Sequence[str]] = None) -> int:
    print("hooks -> %s" % (root / ".githooks"))
    for name, state in install_hooks(root, only):
        print("  %-14s %s" % (name, state))
    print("hooksPath: %s" % wire_hooks_path(root))
    print("plugin -> %s" % (root / ".opencode" / "plugin" / PLUGIN_NAME))
    print("  %s" % install_plugin(root))
    results = injection_report(data, root, inject_block=True)
    print("injection:")
    for relative, state in results:
        print("  %-34s %s" % (relative, state))
    return 0


def cmd_verify(data: Dict, root: Path, only: Optional[Sequence[str]] = None) -> int:
    problems: List[str] = []
    hooks_dir = root / ".githooks"
    selected = list(only) if only else list(HOOKS)
    for name in selected:
        path = hooks_dir / name
        if not path.exists():
            problems.append("%s missing" % name)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARK not in text:
            problems.append("%s exists but is not ours (no %s marker)" % (name, HOOK_MARK))
        if not os.access(str(path), os.X_OK):
            problems.append("%s is not executable" % name)
        completed = subprocess.run(["sh", "-n", str(path)], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=120)
        if completed.returncode != 0:
            problems.append("%s has a syntax error: %s"
                            % (name, (completed.stderr or "").strip()[:200]))
    value, note = hooks_path(root)
    if value != ".githooks":
        problems.append("core.hooksPath is %r (%s) -- the hooks will not run" % (value, note))
    for relative, state in injection_report(data, root):
        if state not in ("current", "absent"):
            problems.append("%s carries a %s canon block" % (relative, state))
    plugin = root / ".opencode" / "plugin" / PLUGIN_NAME
    if plugin.exists():
        node = shutil.which("node")
        if node:
            completed = subprocess.run([node, "--check", str(plugin)],
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=60)
            if completed.returncode != 0:
                problems.append("plugin fails node --check: %s"
                                % (completed.stderr or "").strip()[:200])
        else:
            print("  note: node absent, plugin syntax unverified")
    else:
        problems.append("plugin not installed")
    if problems:
        print("VERIFY FAILED")
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("VERIFY PASSED: %d hook(s) present, executable and syntax-clean; hooksPath wired;"
          % len(selected))
    print("  canon block current; plugin installed and node --check clean")
    return 0


def self_test() -> int:
    """Negative controls: the injector must report staleness, and must not
    overwrite a hook it did not write."""
    failures: List[str] = []
    data = {"schema": "azazael/bible@1", "books": [
        {"id": "genesis", "name": "Genesis", "genre": "etiology", "chapters": [
            {"number": 1, "verses": [
                {"number": 1, "text": "A control shall be exercised.",
                 "statute": {"rule": "RULE 0B-D", "check": "python tools/preflight_all.py"}},
            ]},
        ]},
    ], "configs": [{"id": "floor", "name": "the floor", "value": "0.89",
                    "file": "tools/x.py", "governs": "noise is answered"}]}
    block = render_block(data)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agents = root / "AGENTS.md"

        if inject(agents, block) != "absent":
            failures.append("a missing AGENTS.md was created without --create")
        if inject(agents, block, create=True) != "created":
            failures.append("--create did not create AGENTS.md")
        if inject(agents, block, create=True) != "unchanged":
            failures.append("a second inject was not idempotent")
        if current_block(agents) != block.rstrip("\n") and \
                current_block(agents) != block.strip():
            failures.append("the block could not be read back from AGENTS.md")

        # Stale detection: change the corpus, the block must read as stale.
        changed = json.loads(json.dumps(data))
        changed["configs"][0]["value"] = "0.95"
        if injection_report(changed, root)[0][1] != "stale":
            failures.append("a changed corpus did not make the block stale")

        # The managed region: text outside it survives a refresh.
        agents.write_text("# Notes\n\nhand-written\n\n" + block, encoding="utf-8")
        inject(agents, block, create=True)
        if "hand-written" not in agents.read_text(encoding="utf-8"):
            failures.append("inject destroyed prose outside the managed block")

        # An occupied hook is reported, never overwritten.
        hooks = root / ".githooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text("#!/bin/sh\necho somebody else\n", encoding="utf-8")
        states = dict(install_hooks(root))
        if states.get("pre-push") != "occupied":
            failures.append("an unmarked pre-push was not reported as occupied")
        if "somebody else" not in (hooks / "pre-push").read_text(encoding="utf-8"):
            failures.append("an unmarked hook was overwritten")
        for name in ("pre-commit", "commit-msg"):
            if states.get(name) != "created":
                failures.append("%s was not created" % name)

        # The plugin must parse, or say it could not be checked.
        outcome = install_plugin(root)
        if "SYNTAX ERROR" in outcome:
            failures.append("the shipped plugin does not parse: %s" % outcome)

    if failures:
        print("SELF-TEST FAILED")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("SELF-TEST PASSED: create/idempotent/stale/occupied/prose-preserved/plugin-parsed")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ship the canon: inject, install, verify.")
    parser.add_argument("--inject", action="store_true", help="create or refresh the block")
    parser.add_argument("--check", action="store_true", help="exit 1 when a block is stale")
    parser.add_argument("--install-hooks", metavar="DIR", help="install hooks into DIR/.githooks")
    parser.add_argument("--hooks", default=None,
                        help="comma-separated subset (default: all three)")
    parser.add_argument("--create-agents", action="store_true",
                        help="allow creating AGENTS.md (refused when a parent has one)")
    parser.add_argument("--target", action="append", default=[],
                        help="inject into this file instead of the default set")
    parser.add_argument("--verify", metavar="DIR", help="hooks wired, block current, plugin parses")
    parser.add_argument("--render", action="store_true", help="print the block")
    parser.add_argument("--root", default=None, help="repository root (default: this tree)")
    parser.add_argument("--self-test", action="store_true", help="negative controls")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve() if args.root else REPO
    data = load_corpus(CORPUS if root == REPO else root / "docs" / "bible" / "bible.json")

    if args.render:
        return cmd_render(data)
    if args.inject:
        return cmd_inject(data, root, create_agents=args.create_agents,
                          targets=args.target)
    if args.check:
        return cmd_check(data, root)
    if args.install_hooks:
        only = [name.strip() for name in args.hooks.split(",")] if args.hooks else None
        return cmd_install(data, Path(args.install_hooks).resolve(), only)
    if args.verify:
        only = [name.strip() for name in args.hooks.split(",")] if args.hooks else None
        return cmd_verify(data, Path(args.verify).resolve(), only)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
