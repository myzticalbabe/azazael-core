#!/usr/bin/env python3
"""Azazael Unified CLI — One tool to run all azazael commands.

Usage:
    python3 unified_cli.py hydra --status
    python3 unified_cli.py health
    python3 unified_cli.py import-test
    python3 unified_cli.py benchmark
    python3 unified_cli.py scan
    python3 unified_cli.py backup
"""

import os
import signal
import sys
import time
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path.home() / "Desktop"
PROJECT_DIR = SCRIPTS_DIR / "azazael-project-e"

COMMANDS = {
    "hydra": f"cd {PROJECT_DIR} && python3 HYDRA.py",
    "health": f"KERAS_BACKEND=torch python3 {SCRIPTS_DIR}/health_check_azazael.py",
    "import-test": f"KERAS_BACKEND=torch python3 {SCRIPTS_DIR}/test_azazael_imports.py",
    "benchmark": f"KERAS_BACKEND=torch python3 {SCRIPTS_DIR}/benchmark_frameworks.py",
    "ner": f"python3 {SCRIPTS_DIR}/visualize_ner.py",
    "scan": f"python3 {SCRIPTS_DIR}/compare_scans.py",
    "memory": f"KERAS_BACKEND=torch python3 {SCRIPTS_DIR}/memory_profile.py",
    "backup": f"python3 {SCRIPTS_DIR}/backup_to_g.py",
    "api": f"cd {SCRIPTS_DIR} && python3 -m uvicorn hydra_api:app --reload --port 8000",
    "dashboard": f"cd {SCRIPTS_DIR} && python3 flask_dashboard.py",
    "test": f"cd {SCRIPTS_DIR} && KERAS_BACKEND=torch python3 -m pytest hydra_test.py hydra_integration_test.py -v",
    "graph": f"cd {PROJECT_DIR} && python3 {SCRIPTS_DIR}/hydra_graph.py",
    "qlearn": f"KERAS_BACKEND=torch python3 {SCRIPTS_DIR}/qlearning_env.py --episodes 100",
}


def _kill_tree(pid: int) -> str:
    """Terminate `pid` and its children. Returns the mechanism used, or why none ran.

    `taskkill /T` is the Windows way to reach a grandchild started through the shell;
    on POSIX the child is its own process-group leader, so a group kill reaches it.
    Never raises: a dispatch that cannot kill must still report what happened, and a
    swallowed OSError would read as "killed" when nothing was.
    """
    if sys.platform == "win32":
        try:
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return "taskkill failed (%s)" % exc
        return "taskkill /T (rc=%d)" % done.returncode
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError as exc:
        return "killpg failed (%s)" % exc
    return "killpg(SIGTERM)"


def _run_foreground(full_cmd: str) -> int:
    """Run `full_cmd` on inherited stdio until it exits or the operator interrupts.

    The child gets its own process group so the interrupt reaches the tree, not just
    the shell that owns it -- and that is the only bound this dispatcher declares.

    EXEMPTION (dated 2026-11-06): no timeout can be expressed here because the
    target's lifetime is the operator's, not a deadline. A watchdog thread would
    kill a legitimately long-running process; SIGTERM forwarding is already handled
    by the process-group mechanism (Ctrl-C reaches the tree). Lifting this
    exemption would require a --timeout flag opt-in, which changes the CLI contract.
    Reviewed: this is a foreground interactive dispatcher, not a batch worker.
    """
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(  # gate:allow-no-timeout: dispatcher: the target's lifetime is the operator's, not a deadline
        full_cmd, shell=True, encoding="utf-8", **kwargs)
    try:
        # Polled rather than a bare proc.wait(): every look at the child is bounded
        # (so Ctrl-C is never held behind a call that cannot return), while the loop
        # itself has no deadline -- the target's lifetime is the contract. A 0.25 s
        # poll costs four wakeups per second and at most 0.25 s of exit latency.
        while proc.poll() is None:
            time.sleep(0.25)
        return proc.returncode
    except KeyboardInterrupt:
        how = _kill_tree(proc.pid)
        print(f"\n■ interrupted: {how}")
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print("■ the tree did not exit within 10 s of the kill")
            return 130


def main():
    # This console is cp1252 on Windows, and every banner below carries a glyph
    # (▶, ■). Measured 2026-11-02: an unconfigured stdout raised
    # UnicodeEncodeError on the ▶ line and the dispatch never ran, so the CLI
    # reported nothing at all. Replace rather than raise -- a banner is not
    # worth a traceback, and the child's own output is unaffected.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError) as exc:
            # ASCII only: this message may be written to the very stream that
            # refused the reconfigure, and a glyph here would raise inside the
            # handler. Say the banner may be mangled rather than stay silent.
            print("warning: %s not reconfigured as utf-8 (%s); banner glyphs may "
                  "be mangled" % (getattr(stream, "name", "stream"), exc),
                  file=sys.stderr)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Azazael Unified CLI")
        print()
        print("Usage:  python3 unified_cli.py <command> [args...]")
        print()
        print("Commands:")
        for cmd, run in sorted(COMMANDS.items()):
            print(f"  {cmd:<15s}  {run[:60]}...")
        print()
        print("Examples:")
        print("  python3 unified_cli.py hydra --status")
        print("  python3 unified_cli.py health")
        print("  python3 unified_cli.py import-test")
        return

    cmd = sys.argv[1]
    extra_args = " ".join(sys.argv[2:])

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(sorted(COMMANDS.keys()))}")
        sys.exit(1)

    full_cmd = f"{COMMANDS[cmd]} {extra_args}"
    print(f"▶ {full_cmd}\n")
    # Foreground dispatcher: the child inherits stdio and streams to the
    # terminal, and this is a lifetime bound at the operator's interrupt, not a
    # wall-clock bound. Several targets are servers on purpose (`api` ->
    # uvicorn --reload, `dashboard` -> flask_dashboard), so a timeout would kill
    # a working server to satisfy a number.
    #
    # Measured 2026-11-02, and it corrects the earlier exemption: `subprocess.run(
    # shell=True, timeout=...)` kills the SHELL and leaves the grandchild writing
    # (a 20-line heartbeat kept printing 5..19 after the timeout at 2 s) -- a bound
    # of that shape is worse than none. A real bound IS expressible
    # (CREATE_NEW_PROCESS_GROUP + `taskkill /F /T`, measured: the tree stopped at
    # 6 lines), so the exemption is no longer "cannot": the mechanism is here, and
    # what remains deliberately unset is the deadline. Ctrl-C now reaches the
    # whole tree instead of only the shell.
    # Exemption reviewed 2026-11-02; re-review 2027-02-02.
    # The child's status is this tool's status: returning 0 for a failed dispatch
    # would tell a caller the command worked. Measured 2026-11-02 before the fix:
    # `unified_cli.py graph` printed nothing and exited 0 while the shell failed.
    rc = _run_foreground(full_cmd)  # gate:allow-no-timeout: foreground dispatcher; the bound is the operator's interrupt, forwarded to the process tree (measured 2026-11-02: CREATE_NEW_PROCESS_GROUP + taskkill /T stops the tree, a subprocess timeout does not)
    sys.exit(rc)


if __name__ == "__main__":
    main()
