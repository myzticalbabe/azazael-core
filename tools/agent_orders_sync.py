#!/usr/bin/env python
# coding: utf-8
"""agent_orders_sync -- one source per standing-order block, generated into every lane.

WHY THIS EXISTS
On 2026-10-30 the MCP gate standing order lived in FOUR instruction files
(`~/AGENTS.md`, `C:/Azazael/AGENTS.md`, `~/.config/kilo/AGENTS.md`,
`~/.claude/CLAUDE.md`). The gate underneath it was rewritten the same day -- the
"nonce returns zero rows" assertion was replaced by a differential test and the
thresholds moved 0.62/0.78 -> 0.89/0.70 -- and all four copies went stale within
hours. Every session then began by reading instructions that described a check
that no longer existed. Duplication did not cause a slow drift; it caused
misinformation in the first thing an agent reads.

The FLOCK canon (2 copies) and META-LEVELS canon (2 copies, already condensed
differently) had the same shape waiting for the same failure.

SO: each block has exactly one source file in `azazael/docs/agent_orders/`.
Targets carry generated copies between markers. `--check` compares bytes and
exits 1 on drift, and `mcp_startup_gate.py` runs it as the `orders-in-sync`
check, so drift is a gate failure rather than a discovery six sessions later.

USAGE
  python tools/agent_orders_sync.py            # check (default); exit 1 on drift
  python tools/agent_orders_sync.py --check    # same
  python tools/agent_orders_sync.py --apply    # rewrite every generated block
  python tools/agent_orders_sync.py --list     # blocks, sources, targets
  python tools/agent_orders_sync.py --hook     # commit-gate mode (see below)

TARGETS ARE HOST-ABSOLUTE, SO `--hook` IS NOT STRICT ABOUT THEIR EXISTENCE
The lane files are host-absolute (`~/AGENTS.md`, the kilo/opencode/
claude paths, `C:/Azazael/AGENTS.md`, `~/.codebuff/rules.md`). A Kali lane or a
fresh clone has none of
them, and a check that blocks every commit there for files it cannot see would be
switched off within a day. `--hook` therefore compares only the targets that
exist, PRINTS the ones it skipped, and still exits non-zero if none exist at all
(nothing verified is not a pass). `--check` stays strict: a missing target is a
defect when you are running deliberately on a host that is supposed to have all
of them.

A MISSING block is reported, never appended: WHERE a block sits in a file is an
editorial decision (a precedence statement at the bottom of a file is useless),
so the tool inserts nothing and tells you which file needs the markers.

MARKER SHAPE
  <!-- AO:<key> START -->
  <!-- generated ... -->
  <source body>
  <!-- AO:<key> END -->
"""
import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # azazael/
ORDERS = ROOT / "docs" / "agent_orders"   # TRACKED: `state/` is gitignored, and a
                                        # single source that cannot be committed
                                        # cannot be reviewed or reverted either
HOME = Path.home()

#: Blocks that must sit at the TOP of a file, not where an editor happened to leave
#: them. A precedence statement appended under 20 KB of canon is a precedence
#: statement nobody reaches: measured 2026-11-02, the seven canon documents the
#: session order names total 1.12 MB (~280k tokens), so a zero-shot session that
#: reads top-down meets the operating contract last or never. For these keys the
#: sync will INSERT the block when its markers are absent (a missing block is
#: still reported for every other key, because where those sit is editorial).
PREPEND = {"start-here"}

#: block key -> (source file name, [target instruction files])
TARGETS = {
    # Added 2026-11-02 on user directive: "clear the field for next session ... with
    # needed zero-shot prompts". The other blocks explain why a rule exists; this one
    # is the contract a fresh model acts on before reading any of them.
    "start-here": (
        "01_start_here.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/kilo/BEAM_AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
    "session-order": (
        "00_session_order.md",
        [
            HOME / "AGENTS.md",                     # freebuff, opencode, kilo (home scope)
            Path("C:/Azazael/AGENTS.md"),           # every client whose cwd is C:/Azazael
            HOME / ".config/kilo/AGENTS.md",        # kilo lane
            HOME / ".config/opencode/AGENTS.md",    # opencode lane (own file, since 2026-10-30)
            HOME / ".claude/CLAUDE.md",             # claude code lane
            # freebuff lane. Added 2026-10-30 on user directive: "add the
            # standing-order block to .codebuff/rules.md so the freebuff lane also
            # gets the startup gate". It was previously excluded on the reasoning
            # that freebuff already receives ~/AGENTS.md in full, so the block
            # would push the same text twice into one prompt. A lane that must
            # DEPEND on another lane's file for its session order is exactly the
            # duplication this tool exists to end, so the block now lives here too
            # and the home pre-commit verifies it (`.codebuff/` is gitignored, so
            # nothing else ever would).
            HOME / ".codebuff/rules.md",
        ],
    ),
    "flock-canon": (
        "flock_canon.md",
        [HOME / "AGENTS.md", Path("C:/Azazael/AGENTS.md")],
    ),
    "meta-levels": (
        "meta_levels_canon.md",
        [HOME / "AGENTS.md", HOME / ".config/kilo/AGENTS.md"],
    ),
    # Added 2026-10-31 on user directive: every lane drives work in batch loops
    # off the board, with opencode as the push hook. The protocol is one thing,
    # so it is one source: seven hand-maintained copies would repeat exactly the
    # failure this tool was built to end. Targets are every file a client reads
    # as its instructions, including the beam lane file.
    # Added 2026-10-31: domain trees (sentinel/cain/eve_rani/hives/gosp/ml/
    # shards/edge0) are declared in tools/domain_trees.py and drawn by the same
    # renderer as the environment forests. Every lane may add a domain, so every
    # lane needs the one rule that keeps them interop: declaration, not a fork.
    "domain-trees": (
        "04_domain_trees.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/kilo/BEAM_AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
    # Added 2026-11-02 after prompt_check measured the cost of not having it: this
    # section was hand-copied into six instruction files and existed in NO source,
    # so its shard counts had no owner and went three days stale with nothing able
    # to notice. Every block above had a source and a byte-check; this one had
    # neither and was the only prose in the estate that could rot invisibly.
    "render-handoff": (
        "07_render_handoff.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
    "lane-queue": (
        "03_lane_queue.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/kilo/BEAM_AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
    # Added 2026-11-02 on user directive: "hardcode the best practices we have
    # developed into authoritative canon". These eight were each paid for by a
    # measured failure (an import sweep that missed a live NameError, a suite
    # that wedged instead of finishing, a reading that under-reported its own
    # count, a gate that would be switched off if it failed when the network was
    # down). They are the defaults every lane runs, so every lane reads them from
    # one source: the alternative is eight hard-coded constants per tool and no
    # one able to say which default is live.
    "hyper-edge": (
        "05_hyper_edge_defaults.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/kilo/BEAM_AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
    # Added 2026-11-07 on user directive: "EVOLVE-TO-RESOLVE CANON". Eve was
    # designed to evolve and had never been trained; the first live training
    # runs (simulated fitness taught nothing, live ladder taught priors /
    # captcha-wall / policy evolution) are now nine rules in one source, pushed
    # to every lane the same way the hyper-edge defaults were on 2026-11-02.
    "evolve-to-resolve": (
        "08_evolve_to_resolve.md",
        [
            HOME / "AGENTS.md",
            Path("C:/Azazael/AGENTS.md"),
            HOME / ".config/kilo/AGENTS.md",
            HOME / ".config/kilo/BEAM_AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codebuff/rules.md",
        ],
    ),
}


#: The WSL lanes read their orders from the Linux tree, so the Windows block
#: text cannot be copied in verbatim: `C:/Users/clint/...` is not a path there
#: and the interpreter is `python3`, not `python`. Measured 2026-10-31: neither
#: distro carried a single AO block -- Ubuntu-26.04 /root/AGENTS.md was 2003
#: bytes with 0 blocks, kali-linux /root/BCC-REINVESTIGATION/AGENTS.md was
#: 14783 bytes with 0 blocks. Both lanes therefore started WITHOUT the
#: lane-queue/domain-trees protocol while every Windows lane had it, which is
#: the split-brain this tool exists to end.
WSL_TARGETS = {
    "Ubuntu-26.04": ["/root/AGENTS.md"],
    "kali-linux": ["/root/BCC-REINVESTIGATION/AGENTS.md"],
}

#: A path command the WSL block depends on. Rewriting a path without checking
#: the rewrite lands on something real is how a block ships pointing at a file
#: that does not exist -- the reader then complies with a broken instruction.
WSL_PROBE = "test -f /mnt/c/Users/clint/_board.py && command -v python3"


def rewrite_for_wsl(text):
    """Translate one block's text to what a Linux lane can actually run.

    Separate from `render_for_wsl` so the translation can be tested on its own
    -- and so its IDEMPOTENCE is testable, which is the property that matters
    when an apply is retried after a partial write.
    """
    out = []
    for line in text.split("\n"):
        line = line.replace("C:/Users/clint", "/mnt/c/Users/clint")
        line = line.replace("C:/Azazael", "/mnt/c/Azazael")
        # `python X`/`python C:/...` -> `python3 /mnt/c/...`: the Linux lanes
        # have no `python` alias on a stock install, so an untranslated
        # invocation fails with "command not found" and looks like a broken
        # board rather than a broken block.
        line = re.sub(r"\bpython3? (?=/mnt/c/)", "python3 ", line)
        out.append(line)
    return "\n".join(out)


def render_for_wsl(key, source_name):
    """The Windows block, translated to what the Linux lane can actually run."""
    return rewrite_for_wsl(render(key, source_name))


def _wsl(distro, script, timeout=90, stdin_data=None):
    """Run a shell script in a distro. Returns (rc, clean output).

    wsl.exe emits NUL-padded UTF-16 on some builds and a benign
    "Failed to create stream fd" line on stderr; both are stripped so a real
    failure is not buried under transport noise.

    `stdin_data` exists because a large document CANNOT ride on the command
    line: writing Kali's 19037-byte AGENTS.md through argv raised
    FileNotFoundError [WinError 206] "filename or extension is too long" after
    base64 inflated it past the Windows limit. Content goes on stdin; argv
    carries only the path.

    STDOUT AND STDERR ARE DECODED SEPARATELY, and that is a repair, not tidying.
    They used to be concatenated as bytes, so a file whose last line had no
    trailing newline had stderr's first line glued onto it; that combined line
    contained the noise string below, was therefore dropped, and the block it
    carried read as absent. Measured 2026-10-31: Ubuntu's /root/AGENTS.md held
    both lane-queue markers and the gate still reported "MARKERS ABSENT" --
    exactly the false-negative that makes a lane distrust a working control.
    """
    p = subprocess.run(["wsl", "-d", distro, "-e", "bash", "-lc", script],
                       capture_output=True, timeout=timeout, input=stdin_data)

    def _decode(raw):
        txt = (raw.decode("utf-16-le", "ignore") if b"\x00" in raw[:4]
               else raw.decode("utf-8", "replace"))
        return txt.replace("\x00", "")

    out = _decode(p.stdout or b"")
    keep = [ln for ln in _decode(p.stderr or b"").split("\n")
            if "Failed to create stream fd" not in ln]
    txt = out + ("\n" + "\n".join(keep) if keep else "")
    return p.returncode, txt.strip()


def ensure_wsl_distro(distro, probe_timeout=20, boot_timeout=60):
    """Start a WSL distro as needed; report live vs unresponsive.

    START-AS-NEEDED, permanent (2026-11-07): the commit hook used to burn
    90s per probe per path against a wedged WSL service and then BLOCK the
    commit -- a leg that cannot run failing the run. Now: quick probe; on
    failure ONE boot attempt (`wsl -d <distro> -e true` boots a stopped VM
    and is a no-op on a running one); re-probe once. Still dead means the
    *service* is wedged (measured: even `wsl.exe --status` wedges with zero
    output), which no userspace boot can fix -- that is UNRESPONSIVE, a
    skip-with-reason, never per-path DRIFT. Returns (state, reason) where
    state is 'live' or 'unresponsive'.
    """
    try:
        rc, _out = _wsl(distro, WSL_PROBE, timeout=probe_timeout)
        if rc == 0:
            return "live", ""
        boot_reason = "probe rc=%s" % rc
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        boot_reason = "probe %s: %s" % (type(exc).__name__, exc)
    boot_note = ""
    try:
        _wsl(distro, "true", timeout=boot_timeout)
    except Exception as exc:  # noqa: BLE001 -- best-effort, recorded not silent
        boot_note = "; boot attempt raised %s" % type(exc).__name__
    try:
        rc, _out = _wsl(distro, WSL_PROBE, timeout=probe_timeout)
        if rc == 0:
            return "live", ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ("unresponsive", "%s%s; boot attempt did not revive it"
                % (boot_reason, boot_note))
    return ("unresponsive", "%s%s; boot attempt did not revive it"
            % (boot_reason, boot_note))


def check_wsl(apply=False):
    """Verify (and optionally write) the same blocks inside each WSL distro.

    ONE WRITE PER TARGET. A first version wrote inside the per-block loop and
    rebuilt every block from the SAME pre-read text, so each write clobbered
    the one before it: measured 2026-10-31 on Kali, the file held `lane-queue`
    and nothing else while the other four reported MARKERS ABSENT immediately
    after a "successful" apply. The document is now assembled in memory across
    all blocks and written once, and the result is READ BACK -- a write that
    was not re-read is a claim, not a delivery.
    """
    problems, applied, unresponsive, ok = [], [], [], 0
    # A host with no WSL is not a drift finding, and a gate that raises on it
    # would be red forever on such a host. Absent is reported as absent.
    if shutil.which("wsl") is None:
        print("        (no wsl on PATH -- WSL targets not verified)")
        return problems, applied, ok, unresponsive
    for distro, paths in WSL_TARGETS.items():
        state, reason = ensure_wsl_distro(distro)
        if state != "live":
            unresponsive.append((distro, reason + "; remedy: restart the "
                                 "WSL service (LxssManager) or reboot, then "
                                 "re-run -- the leg re-checks every commit"))
            continue
        for path in paths:
            rc, text = _wsl(distro, "cat %s 2>/dev/null" % path)
            exists = rc == 0 and text.strip() != ""
            work = normalise(text) if exists else ""
            changed = False
            for key, (source_name, _t) in TARGETS.items():
                if not (ORDERS / source_name).exists():
                    problems.append((key, "<source>", "SOURCE MISSING: %s" % source_name))
                    continue
                want = render_for_wsl(key, source_name)
                have = extract(work, key) if work else None
                if have is not None and have.rstrip("\n") == want.rstrip("\n"):
                    ok += 1
                    continue
                if not apply:
                    problems.append((key, "%s:%s" % (distro, path),
                                     "MARKERS ABSENT -- block never delivered to this lane"
                                     if have is None else "DRIFT"))
                    continue
                if have is None:
                    work = (want + "\n" + work.lstrip("\n")) if key in PREPEND \
                        else (work.rstrip("\n") + "\n\n" if work.strip() else "") + want
                else:
                    s, e = markers(key)
                    i = work.find(s)
                    j = work.find(e, i) + len(e)
                    tail = work[j:].lstrip("\n")
                    work = work[:i] + want + ("\n" + tail if tail else "")
                changed = True
            if not (apply and changed):
                continue
            b64 = base64.b64encode(work.encode("utf-8", "surrogateescape"))
            wrc, wout = _wsl(distro, "mkdir -p $(dirname %s) && base64 -d > %s" % (path, path),
                             stdin_data=b64)
            _rrc, rtext = _wsl(distro, "cat %s 2>/dev/null" % path)
            delivered = sum(1 for k in TARGETS if extract(normalise(rtext), k) is not None)
            if wrc != 0 or delivered != len(TARGETS):
                problems.append((distro, path,
                                 "WRITE FAILED: rc=%s, %d/%d block(s) readable after write"
                                 % (wrc, delivered, len(TARGETS))))
            else:
                applied.append(("%d blocks" % delivered, "%s:%s" % (distro, path)))
    return problems, applied, ok, unresponsive


def markers(key):
    return ("<!-- AO:%s START -->" % key, "<!-- AO:%s END -->" % key)


def render(key, source_name):
    """The exact text a target must hold between its markers."""
    body = (ORDERS / source_name).read_text(encoding="utf-8")
    start, end = markers(key)
    return "\n".join([
        start,
        "<!-- generated by azazael/tools/agent_orders_sync.py from "
        "azazael/docs/agent_orders/%s -- edit the source, not this copy -->" % source_name,
        "",
        body.rstrip("\n"),
        end,
        "",
    ]).replace("\n", "\n")


def extract(text, key):
    """Current block between the markers, or None when the markers are absent."""
    start, end = markers(key)
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i)
    if j < 0:
        return None            # opening marker with no close is its own defect
    return text[i:j + len(end)] + "\n"


def normalise(s):
    return s.replace("\r\n", "\n").replace("\r", "\n")


def check(apply=False, as_json=False, hook_mode=False, wsl=False):
    problems = []
    applied = []
    skipped = []
    seen = 0
    ok_count = 0
    for key, (source_name, targets) in TARGETS.items():
        if not (ORDERS / source_name).exists():
            problems.append((key, "<source>", "SOURCE MISSING: %s" % source_name))
            continue
        want = render(key, source_name)
        for path in targets:
            if not path.exists():
                if hook_mode:
                    skipped.append((key, str(path)))
                    continue
                problems.append((key, str(path), "TARGET MISSING (file does not exist)"))
                continue
            seen += 1
            text = path.read_bytes().decode("utf-8")
            have = extract(text, key)
            if have is None:
                if apply and key in PREPEND:
                    nl = "\r\n" if "\r\n" in text else "\n"
                    block = want.replace("\n", nl).rstrip("\r\n")
                    new = block + nl + nl + text.lstrip("\r\n")
                    path.write_bytes(new.encode("utf-8"))
                    applied.append((key, str(path) + " (prepended)"))
                    continue
                problems.append((key, str(path),
                                 "MARKERS ABSENT -- insert %s / %s where the block belongs"
                                 % markers(key)))
                continue
            if normalise(have).rstrip("\n") == normalise(want).rstrip("\n"):
                ok_count += 1
                continue
            if apply:
                start, end = markers(key)
                i = text.find(start)
                j = text.find(end, i) + len(end)
                nl = "\r\n" if "\r\n" in text else "\n"
                block = want.replace("\n", nl)          # ends with exactly one nl
                tail = text[j:].lstrip("\r\n")          # drop old separators
                new = text[:i] + block + (nl + tail if tail else nl)
                path.write_bytes(new.encode("utf-8"))
                applied.append((key, str(path)))
            else:
                problems.append((key, str(path), "DRIFT"))
    wsl_ok = 0
    if wsl:
        w_problems, w_applied, wsl_ok, w_unresp = check_wsl(apply=apply)
        problems.extend(w_problems)
        applied.extend(w_applied)
        for distro, why in w_unresp:
            print("WSL-UNRESPONSIVE  %-14s %s" % (distro, why))
    if hook_mode and seen == 0 and wsl_ok == 0:
        problems.append(("*", "*", "NO TARGETS ON THIS HOST -- nothing was verified"))
    if as_json:
        import json
        print(json.dumps({"ok": not problems, "problems": problems,
                          "applied": applied, "skipped": skipped}, indent=2))
    else:
        for key, path, why in problems:
            print("DRIFT  %-14s %-45s %s" % (key, path, why))
        for key, path in applied:
            print("WROTE  %-14s %s" % (key, path))
        if hook_mode:
            print("        %d target(s) present, %d absent on this host%s"
                  % (seen, len(skipped),
                     (" (" + ", ".join(p for _, p in skipped) + ")") if skipped else ""))
        print("agent-orders: %d target(s) in sync, %d problem(s), %d written%s"
              % (ok_count + wsl_ok, len(problems), len(applied),
                 (" (incl. %d WSL block(s))" % wsl_ok) if wsl else ""))
    return 1 if problems else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--list" in args:
        for key, (source_name, targets) in TARGETS.items():
            print("%-14s <- docs/agent_orders/%s" % (key, source_name))
            for t in targets:
                print("    %s" % t)
        return 0
    return check(apply="--apply" in args, as_json="--json" in args,
                 hook_mode="--hook" in args, wsl="--wsl" in args)


if __name__ == "__main__":
    sys.exit(main())
