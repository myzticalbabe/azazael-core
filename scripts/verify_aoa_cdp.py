"""CDP render verification harness (2026-09-02).

Launches fresh headless Chrome with a temp profile, opens each HTML file in its
own navigation, captures Runtime.consoleAPICalled + Runtime.exceptionThrown, and
reports errors/exceptions per file. Resilient: a per-file ws timeout fails just
that file instead of aborting the whole sweep.

Scope selection:
  - argv files: python verify_aoa_cdp.py f1.html f2.html ...
  - azazael/.cdp_filelist.txt if present (newline-separated paths)
  - else all root *.html in the current working directory

Origin-aware error classification (2026-09-05): an exception/error is LOCAL
(defects the file) when its stack or source location references the local
file:// document or a same-root relative resource. Exceptions whose stack
points only at remote hosts (https://...) are EXTERNAL — third-party CDN
scripts (e.g. ProPublica / archive.org capture pages) that fail because the
document is opened via file:// instead of their real origin. External errors
are reported but do not FAIL the file, so a sweep over third-party whole-page
captures (bcc_pp2.html, wb_datasets.html) can still be ALL CLEAN for the
corpus's own HTML. Proven incident: both capture pages threw only remote-origin
errors (ProPublica hydrate-svelte fetch, archive.org sendBeacon-over-file://)
while every corpus doc stayed clean.
"""
import glob as _glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import websocket  # websocket-client

# Portable scope resolution: argv files win, then a .cdp_filelist.txt beside
# the script or in the cwd, then a root *.html glob in the cwd. No hardcoded
# drive paths (the azazael repo's anti-pattern scanner rejects them and this
# tool runs from C: root, the azazael repo, G: mirrors, and Kali alike).
_script_dir = os.path.dirname(os.path.abspath(__file__))
_cwd = os.getcwd()


def _load_list(path):
    with open(path, encoding="utf-8") as fh:
        return [os.path.abspath(f) for f in fh.read().splitlines() if f.strip()]


if len(sys.argv) > 1:
    FILES = [os.path.abspath(f) for f in sys.argv[1:]]
else:
    FILES = []
    for cand in (os.path.join(_script_dir, ".cdp_filelist.txt"),
                 os.path.join(_cwd, ".cdp_filelist.txt")):
        if os.path.exists(cand):
            FILES = _load_list(cand)
            break
    if not FILES:
        FILES = sorted(_glob.glob(os.path.join(_cwd, "*.html")))
if not FILES:
    raise SystemExit("no HTML files found")

PORT = 9333
BASE = f"http://127.0.0.1:{PORT}"


def find_chrome():
    # CHROME_BIN wins, then standard Windows program dirs built from env vars
    # (no hardcoded drive letter — the azazael anti-pattern scanner rejects
    # those and this tool must run from C: root, the azazael repo, G: mirrors,
    # and Kali alike), then a PATH lookup for linux chrome/chromium.
    env_chrome = os.environ.get("CHROME_BIN")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome
    prog_dirs = [d for d in (os.environ.get("ProgramFiles"),
                             os.environ.get("ProgramFiles(x86)")) if d]
    for base in prog_dirs:
        cand = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(cand):
            return cand
    for base in prog_dirs:
        for cand in _glob.glob(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")):
            if os.path.exists(cand):
                return cand
    if shutil.which("google-chrome"):
        return shutil.which("google-chrome")
    if shutil.which("chromium"):
        return shutil.which("chromium")
    raise SystemExit("chrome.exe not found (set CHROME_BIN or install Chrome)")


def main():
    chrome = find_chrome()
    prof = tempfile.mkdtemp(prefix="cdp_aoa_")
    proc = subprocess.Popen(
        [chrome, "--headless", "--remote-debugging-port=%d" % PORT,
         f"--user-data-dir={prof}", "--remote-allow-origins=*",
         "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # wait for the debugging endpoint
        for _ in range(60):
            try:
                with urllib.request.urlopen(BASE + "/json/version", timeout=1) as r:
                    json.loads(r.read())
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("CDP endpoint never came up")

        with urllib.request.urlopen(BASE + "/json/list") as r:
            tabs = json.loads(r.read())
        tab = next(t for t in tabs if t.get("type") == "page")
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
        seq = 0

        def send(method, params=None):
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq:
                    return msg

        send("Page.enable")
        send("Runtime.enable")
        # drain any startup messages
        ws.settimeout(0.2)
        try:
            while True:
                ws.recv()
        except Exception:
            pass
        ws.settimeout(15)

        def classify(desc, loc, url):
            """local if the error traces to the doc under test; external if the
            stack/location points only at remote hosts (third-party CDN scripts
            that fail because the doc is opened via file://). Console errors
            carry no stack, so they are conservatively treated as local."""
            text = f"{desc} {loc}"
            if "http://" in text or "https://" in text:
                # Any local file:// frame or the doc's own name => local defect.
                if "file://" in text or os.path.basename(url) in text:
                    return "local"
                return "external"
            return "local"

        def collect(msg, events_, url):
            m = msg.get("method")
            if m == "Runtime.consoleAPICalled":
                p = msg["params"]
                text = " ".join(
                    a.get("value", "") if isinstance(a, dict) else str(a)
                    for a in p.get("args", [])
                )[:300]
                events_.append(("console", p["type"], text, "local"))
            elif m == "Runtime.exceptionThrown":
                d = msg["params"].get("exceptionDetails", {})
                exc = d.get("exception", {}) or {}
                desc = exc.get("description", "") or d.get("text", "")
                loc = f"{d.get('url','?')}:{d.get('lineNumber','?')}"
                events_.append(("exception", "", f"{desc[:200]} @ {loc}",
                                classify(desc, loc, url)))

        def drain(ws_, events_, seconds):
            ws_.settimeout(seconds)
            done = time.time() + seconds
            while time.time() < done:
                try:
                    msg = json.loads(ws_.recv())
                except Exception:
                    break
                collect(msg, events_, url)

        def sweep_one(ws_, send_, url, events_):
            """Navigate to url, capture console/exception events.
            Returns (ready, local_errs, external_errs, name)."""
            send_("Page.navigate", {"url": url})
            deadline = time.time() + 8
            ready = False
            while time.time() < deadline:
                try:
                    msg = json.loads(ws_.recv())
                except Exception:
                    continue
                m = msg.get("method")
                if m == "Page.loadEventFired":
                    ready = True
                    drain(ws_, events_, 0.6)  # settle trailing console events
                    break
                collect(msg, events_, url)
            ws_.settimeout(15)
            local = [e for e in events_ if e[3] == "local" and
                     (e[0] == "exception" or (e[0] == "console" and e[1] == "error"))]
            external = [e for e in events_ if e[3] == "external" and
                        (e[0] == "exception" or (e[0] == "console" and e[1] == "error"))]
            name = os.path.basename(url.replace("file:///", "").replace("/", "\\"))
            return (ready, local, external, name)

        passed = []
        failed = []
        ext_only = []  # files whose only errors are third-party remote-origin
        for path in FILES:
            url = "file:///" + path.replace("\\", "/").replace(" ", "%20")
            events = []
            try:
                ready, local, external, name = sweep_one(ws, send, url, events)
            except Exception as e:
                ready, local, external = False, \
                    [("exception", "", f"harness: {type(e).__name__}: {str(e)[:150]}", "local")], []
                name = os.path.basename(path)
            # Fail on LOCAL errors, or on a silent hang (never ready AND no
            # error signal at all). A not-ready page whose only errors are
            # external is a third-party capture whose CDN scripts block load
            # under file:// — reported as INFO, not a failure.
            if local or (not ready and not external):
                failed.append((name, local, external, not ready))
            else:
                passed.append(name)
                if external:
                    ext_only.append((name, external))
            nl = len([e for e in local if e[0] == "exception"])
            nc = len([e for e in local if e[0] == "console" and e[1] == "error"])
            nx = len(external)
            is_pass = not local and (ready or bool(external))
            print(f"  {'PASS' if is_pass else 'FAIL'}  {name}  "
                  f"{nl} exc, {nc} err"
                  + (f" (+{nx} external-origin, informational)" if nx else ""),
                  flush=True)
        ws.close()
        print()
        print(f"SWEEP: {len(FILES)} files — {len(passed)} passed, {len(failed)} failed")
        for name, ext in ext_only:
            print(f"  INFO {name}: {len(ext)} external-origin error(s) ignored "
                  f"(3rd-party CDN under file://)")
        for name, local, external, notready in failed:
            print(f"  FAIL {name}  ready={not notready}  "
                  f"{len(local)} local + {len(external)} external error(s)")
            for e in local:
                print(f"      -> [{e[1]}] {e[2]}")
        print("VERDICT:", "ALL CLEAN" if not failed else "ISSUES FOUND")
        return 0 if not failed else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())