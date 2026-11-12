#!/usr/bin/env python3
"""
check_tooling.py — Session startup tooling verification + auto-recorder.

MANDATORY: Run at the start of every Azazael session.
Records session_start and tooling_verify events in LocalMemoryStore.
See azazael-sop skill Rule 5 and azazael-startup skill Step 2.

Also guards graph inputs (section [8]): a file whose bytes do not match its
extension can abort a ~40-minute `graphify update .` run, so the mislabel audit
(`tools/audit_parseable_files.py --check`) runs here, before the graph is built.

Usage:
    python scripts/check_tooling.py

Auto-records (via CodebuffTracker):
    - session_start: One per session with environment context
    - tooling_verify: Each run with CLI tool availability data
"""

import os
import sys
import json
import subprocess
import shutil
import threading

# Add project src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _resolve_cmd(name: str) -> list:
    """Resolve a CLI command, handling Windows .CMD files via cmd.exe /c."""
    resolved = shutil.which(name)
    if resolved and resolved.lower().endswith('.cmd'):
        return ['cmd.exe', '/c', resolved]
    return [name]


def check_cli(name: str, version_flag: str = "--version") -> dict:
    """Check if a CLI tool is available and return its version."""
    try:
        cmd_path = _resolve_cmd(name)
        result = subprocess.run(
            cmd_path + [version_flag], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip()
            return {"name": name, "available": True, "version": version.split("\n")[0]}
        return {"name": name, "available": False, "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"name": name, "available": False, "error": "not found in PATH"}
    except Exception as e:
        return {"name": name, "available": False, "error": str(e)}


def _find_npm() -> str:
    """Find npm executable. Delegates to _resolve_cmd for .CMD handling."""
    npm_path = shutil.which("npm")
    if npm_path:
        return npm_path
    return "npm"


def check_npm_package(package: str) -> dict:
    """Check if an npm global package is installed."""
    try:
        npm_exe = _find_npm()
        # On Windows, .cmd files need cmd.exe /c wrapper, not shell=True
        if npm_exe.endswith('.cmd'):
            cmd = ['cmd.exe', '/c', npm_exe, 'list', '-g', package, '--depth=0']
        else:
            cmd = [npm_exe, 'list', '-g', package, '--depth=0']
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace"
        )
        available = result.returncode == 0
        version = ""
        if available:
            for line in result.stdout.split("\n"):
                if f" {package}@" in line or f"{package}@" in line:
                    version = line.split("@")[-1].strip()
                    break
        return {
            "name": package,
            "available": available,
            "version": version or (result.stdout.strip()[:80] if result.stdout else ""),
        }
    except Exception as e:
        return {"name": package, "available": False, "error": str(e)}


def check_pip_package(name: str, import_name: str = None) -> dict:
    """Check if a pip package is available by trying to import it."""
    try:
        import importlib
        mod = importlib.import_module(import_name or name)
        ver = getattr(mod, "__version__", "installed")
        return {"name": name, "available": True, "version": str(ver)}
    except ImportError:
        return {"name": name, "available": False, "error": "not installed"}
    except Exception as e:
        return {"name": name, "available": False, "error": str(e)}


def check_graph_input_hygiene(timeout: int = 300) -> dict:
    """Catch a mislabelled file BEFORE a ~40-minute graph extraction runs.

    `graphify update .` chooses a parser from a file's SUFFIX alone. A suffix
    that lies -- an HTML block page saved as .pdf, as on 2026-09-21 -- sends the
    wrong parser at the bytes and aborts the whole extraction, which then reads
    as an unexplained stalled refresh. The audit is fast (it reads only the
    first few KB of each binary file), so running it at startup is cheap
    insurance against losing the run. `--check` fails only on NEW mislabels;
    known vendored ones are baselined and stay quiet.

    `--deep` is requested as well. Magic bytes catch a file wearing the WRONG
    suffix, but a truncated PDF still claims `%PDF` in its header and only a
    real pypdf parse rejects it -- and pypdf is precisely the parser graphify
    hands a `.pdf` to. A cheap magic-byte scan would wave through the exact
    class of file that killed the 2026-09-21 run, so the deeper check is on by
    default here. It measured under a second on a tree with no PDFs, and the
    extra cost is bounded by the number of PDFs present.
    """
    tool = os.path.join(os.path.dirname(__file__), "..", "tools",
                        "audit_parseable_files.py")
    if not os.path.exists(tool):
        return {"available": False, "ok": True, "note": "audit tool not found"}
    try:
        proc = subprocess.run(
            [sys.executable, tool, "--check", "--deep"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "ok": True, "note": f"timed out after {timeout}s"}
    except Exception as e:  # noqa: BLE001 -- startup must never hard-fail on this
        return {"available": False, "ok": True, "note": str(e)}
    out = (proc.stdout + proc.stderr).strip().splitlines()
    new_line = next((ln.strip() for ln in out if ln.strip().startswith("NEW mislabels")), "")
    pdf_line = next((ln.strip() for ln in out if ln.strip().startswith("deep-parsed pdfs")), "")
    unavail = next((ln.strip() for ln in out if ln.strip().startswith("deep-unavailable")), "")
    note = new_line or (out[-1].strip() if out else "")
    if pdf_line:
        # Surface how much the deep pass actually read. A tree with PDFs makes
        # the deep scan legitimately slow, and reporting the count keeps that
        # from looking like a stalled startup.
        note = f"{note} [{pdf_line}]" if note else pdf_line
    if unavail:
        note = f"{note} [{unavail}]" if note else unavail
    return {
        "available": True,
        "ok": proc.returncode == 0,
        "note": note,
        # The deep check ran but its engine (pypdf) was missing, so it checked
        # nothing. Reported as degraded rather than OK, and never as a hard
        # failure -- startup must not depend on an optional third-party dep.
        "degraded": bool(unavail),
    }


def _record_with_timeout(results: dict, timeout_seconds: int = 45) -> bool:
    """Record session events with a timeout guard.

    CodebuffTracker.get_tracker() triggers discover_worms() (~38s cold start
    on 3.14t). This wrapper prevents the tool from hanging indefinitely if
    worm discovery takes longer than expected.
    """
    result_holder = [False]
    exception_holder = [None]

    def _do_record():
        try:
            from CORE.codebuff_tracker import get_tracker
            tracker = get_tracker()
            tracker.record_tooling_verify(
                tools_found=results.get("tools_found", 0),
                tools_missing=results.get("tools_missing", 0),
                details={
                    "cli_tools": results.get("cli_tools", []),
                    "pip_packages": results.get("pip_packages", []),
                    "npm_packages": results.get("npm_packages", []),
                    "python_314t": results.get("python_314t", "not found"),
                },
            )
            result_holder[0] = True
        except Exception as e:
            exception_holder[0] = e

    t = threading.Thread(target=_do_record, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)

    if t.is_alive():
        # Timed out — worm registry discovery likely slow on cold start
        print(f"    [--] Session recording timed out after {timeout_seconds}s "
              f"(worm registry cold-start may be slow)")
        return False

    if exception_holder[0]:
        print(f"    [--] Session recording failed: {exception_holder[0]}")
        return False

    return result_holder[0]


def main():
    print("=" * 60)
    print("  Azazael — Tooling Verification (with auto-recording)")
    print("=" * 60)

    results = {
        "cli_tools": [],
        "pip_packages": [],
        "npm_packages": [],
        "tools_found": 0,
        "tools_missing": 0,
        "python_314t": "not checked",
    }

    # -- 1. CLI tools --
    print("\n  [1] CLI Tools:")
    tools = [
        ("python"), ("git"), ("node"), ("npm"), ("freebuff"),
    ]
    for name in tools:
        info = check_cli(name)
        status = "[OK]" if info["available"] else "[--]"
        version = info.get("version", info.get("error", "?"))
        print(f"    {status} {name}: {version}")
        results["cli_tools"].append(info)
        if info["available"]:
            results["tools_found"] += 1
        else:
            results["tools_missing"] += 1

    # -- 2. pip packages --
    print("\n  [2] pip Packages (Python dependencies):")
    pip_packages = [
        ("Pillow", "PIL"),
        ("psutil", None),
        ("psycopg2-binary", "psycopg2"),
        ("numpy", "numpy"),
    ]
    for name, import_name in pip_packages:
        info = check_pip_package(name, import_name)
        status = "[OK]" if info["available"] else "[--]"
        note = info.get("version", info.get("error", "?"))
        if name == "psycopg2-binary" and not info["available"]:
            note = "broken on 3.14t (cp314t wheel missing libpq DLLs)"
        print(f"    {status} {name}: {note}")
        pkg_info = {"name": name, "available": info["available"]}
        if info.get("version"):
            pkg_info["version"] = note
        results["pip_packages"].append(pkg_info)

    # -- 3. npm global packages --
    print("\n  [3] npm Global Packages:")
    npm_packages = ["freebuff", "pnpm", "node-gyp"]
    for pkg in npm_packages:
        info = check_npm_package(pkg)
        status = "[OK]" if info["available"] else "[--]"
        print(f"    {status} {pkg}: {info.get('version', info.get('error', '?'))}")
        results["npm_packages"].append(info)

    # -- 4. Python 3.15t check --
    print("\n  [4] Python 3.15t:")
    python315_paths = [
        r"C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe",
        os.path.expanduser("~/.local/bin/python3.15t.exe"),
    ]
    found_315t = False
    for p in python315_paths:
        if os.path.exists(p):
            try:
                result = subprocess.run(
                    [p, "--version"], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
                )
                print(f"    [OK] {p}")
                print(f"          {result.stdout.strip()}")
                found_315t = True
                results["python_315t"] = result.stdout.strip()
            except Exception:
                print(f"    [--] {p} (exists but failed)")
    if not found_315t:
        print("    [??] python3.15t not found at expected paths")

    # -- 5. LocalMemoryStore schema check --
    print("\n  [5] LocalMemoryStore Schema:")
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        ic = store.integrity_check()
        ok = ic.get("ok", ic.get("passed", False))
        print(f"    [{'OK' if ok else '--'}] integrity: passed={ok}")
        for k, v in ic.items():
            if k in ("ok", "passed", "errors"):
                continue
            if isinstance(v, (int, float, str, bool)):
                print(f"         {k}: {v}")
        if ic.get("errors"):
            for e in ic["errors"][:3]:
                print(f"         error: {e}")
    except Exception as e:
        print(f"    [--] integrity check failed: {e}")

    # -- 6. azazael-core & Nuitka build prerequisites --
    print("\n  [6] azazael-core Package & Nuitka Build:")
    core_path = os.path.join(os.path.dirname(__file__), "..", "packages", "azazael-core")
    if os.path.exists(core_path):
        cli_path = os.path.join(core_path, "src", "azazael_core", "cli.py")
        readme = os.path.join(core_path, "README.md")
        print(f"    [OK] azazael-core dir: {core_path}")
        print(f"         CLI source exists: {os.path.exists(cli_path)}")
        print(f"         README exists: {os.path.exists(readme)}")
    else:
        print(f"    [--] azazael-core not found at {core_path}")

    try:
        import importlib
        nuitka_mod = importlib.import_module("nuitka")
        nuitka_ver = getattr(nuitka_mod, "__version__", "installed")
        print(f"    [OK] Nuitka: {nuitka_ver}")
    except ImportError:
        print(f"    [--] Nuitka: not installed (for build: pip install nuitka)")

    try:
        result = subprocess.run(
            ["python", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        pyver = result.stdout.strip()
        print(f"    [OK] Python (default): {pyver}")
        major = int(pyver.split(".")[0])
        minor = int(pyver.split(".")[1])
        if major == 3 and minor == 15:
            print(f"         Nuitka-ready: YES (Python {pyver} is the build Python, patches applied via patch_nuitka_315t.py)")
        elif major == 3 and minor == 13:
            print(f"         Nuitka-ready: check (Python {pyver} - use 3.15t for builds)")
        else:
            print(f"         Nuitka-ready: check (Python {pyver} - builds require 3.15t)")
    except Exception as e:
        print(f"    [--] Python version check: {e}")

    # -- 7. freebuff_ready events in memory --
    print("\n  [7] freebuff_ready Events (LocalMemoryStore):")
    events = []
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        events = store.query_memories(event_type="freebuff_ready", limit=5)
    except Exception:
        pass
    if events:
        for ev in events:
            data = ev.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            pm = data.get("package_manager", data.get("install_method", "?"))
            ver = data.get("version", "?")
            print(f"    [OK] id={ev.get('id')} v{ver} pm={pm}")
    else:
        print("    [??] No freebuff_ready events found")

    # -- 8. Graph input hygiene (would a bad file abort the next graph run?) --
    print("\n  [8] Graph Input Hygiene (mislabel guard):")
    hygiene = check_graph_input_hygiene()
    if hygiene["available"]:
        status = "[OK]" if hygiene["ok"] else "[!!]"
        if hygiene.get("degraded"):
            status = "[~~]"
        print(f"    {status} parseable files: {hygiene['note']}")
        if hygiene.get("degraded"):
            print("         deep PDF check UNAVAILABLE (pypdf not importable) --")
            print("         truncated PDFs would NOT be caught; install pypdf")
            print("         in this interpreter to restore the check.")
        if not hygiene["ok"]:
            print("         a new mislabelled file can abort `graphify update .`;")
            print("         run: python tools/audit_parseable_files.py")
    else:
        print(f"    [--] parseable-file audit skipped ({hygiene['note']})")

    # -- 9. Auto-record session events (Rule 5 mandate) --
    # Uses timeout guard: get_tracker() triggers discover_worms() (~38s cold)
    print("\n  [9] Session Recording (mandatory):")
    recorded = _record_with_timeout(results, timeout_seconds=60)
    if recorded:
        print("    [OK] session_start + tooling_verify recorded in LocalMemoryStore")
    else:
        print("    [--] Session recording skipped (timeout or unavailable)")
        print("         Run 'python scripts/check_tooling.py' again after registry warms up")

    print("\n" + "=" * 60)
    print("  Tooling check complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
