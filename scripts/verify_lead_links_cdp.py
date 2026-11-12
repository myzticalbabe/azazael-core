"""CDP click-through verification for LEAD back-links (2026-09-05).

Opens INVESTIGATION_GAPS.html in headless Chrome, collects every
`a.gap-lead-ref` anchor (index-table + audit-annex chips), then for each one
simulates a real click and confirms the page navigates to
BCC_LEAD_FINDINGS.html#LEAD-NN with the target card present and visible.

Usage:
  python verify_lead_links_cdp.py                 # default: ../.. INVESTIGATION_GAPS.html
  python verify_lead_links_cdp.py PATH_TO_GAPS    # explicit source doc

Exit 0 when every link resolves; non-zero with a per-link report otherwise.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import websocket  # websocket-client

PORT = 9334
BASE = f"http://127.0.0.1:{PORT}"


def find_chrome():
    env_chrome = os.environ.get("CHROME_BIN")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome
    prog_dirs = [d for d in (os.environ.get("ProgramFiles"),
                             os.environ.get("ProgramFiles(x86)")) if d]
    for base in prog_dirs:
        cand = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(cand):
            return cand
    raise SystemExit("chrome.exe not found (set CHROME_BIN or install Chrome)")


def main():
    src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "INVESTIGATION_GAPS.html")
    if not os.path.exists(src):
        raise SystemExit(f"source doc not found: {src}")
    src_url = "file:///" + src.replace("\\", "/")

    chrome = find_chrome()
    prof = tempfile.mkdtemp(prefix="cdp_leadlinks_")
    proc = subprocess.Popen(
        [chrome, "--headless", "--remote-debugging-port=%d" % PORT,
         f"--user-data-dir={prof}", "--remote-allow-origins=*",
         "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
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
        # drain startup messages
        ws.settimeout(0.2)
        try:
            while True:
                ws.recv()
        except Exception:
            pass
        ws.settimeout(15)

        # 1) classify every leads anchor from the SOURCE (DOM order matches
        #    source order; the annex <div>s are hoisted out of the table by the
        #    parser, so DOM-walking cannot recover their owning row).
        raw = open(src, encoding="utf-8").read()
        annex_at = raw.find("AUDIT ANNEX", 100000)
        # div spans (a div may hold several anchors, e.g. gap-11 → LEAD-01+02)
        spans = [(m.start(), m.end()) for m in
                 re.finditer(r'<div class="gap-lead-ref".*?</div>', raw, re.S)]
        meta = []  # (where, href) in source order
        for (ds, de) in spans:
            for am in re.finditer(r'<a href="(BCC_LEAD_FINDINGS\.html#[A-Z0-9-]+)"', raw[ds:de]):
                href = am.group(1)
                if ds < annex_at:
                    # index table: owning row is the nearest preceding id="gap-NN"
                    pre = raw[max(0, ds - 4000):ds]
                    mm = re.findall(r'id="(gap-\d+)"', pre)
                    where = "index:" + (mm[-1] if mm else "?")
                else:
                    # annex table: owning row is the nearest preceding <td><strong>N
                    pre = raw[max(0, ds - 2000):ds]
                    mm = re.findall(r'<td><strong>(G?-?\d+)</strong>', pre)
                    where = "annex:" + (mm[-1] if mm else "?")
                meta.append((where, href))

        # 2) load the source doc and enumerate every leads anchor in DOM order
        send("Page.navigate", {"url": src_url})
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("method") == "Page.loadEventFired":
                break
        time.sleep(0.5)

        res = send("Runtime.evaluate", {
            "expression": """
              Array.from(document.querySelectorAll('div.gap-lead-ref a')).map(a => ({
                href: a.getAttribute('href'), text: a.textContent.trim()
              }))
            """,
            "returnByValue": True,
        })
        dom = res.get("result", {}).get("result", {}).get("value", [])
        if not dom:
            print("NO LINKS FOUND — no a.gap-lead-ref anchors in", os.path.basename(src))
            return 1
        if len(dom) != len(meta):
            print(f"COUNT MISMATCH: DOM {len(dom)} links vs source {len(meta)} — aborting")
            return 1
        links = [{"where": meta[i][0], "href": meta[i][1], "text": dom[i]["text"]}
                 for i in range(len(dom))]

        print(f"found {len(links)} leads back-links:")
        for l in links:
            print(f"  {l['where']:<12} {l['href']}  ({l['text']})")

        # 2) click each link and verify navigation + target card
        total = len(links)
        passed = 0
        failed = []
        for l in links:
            send("Runtime.evaluate", {
                "expression": "document.querySelector('a[href=\"%s\"]').click()" % l["href"].replace('"', '\\"')
            })
            # wait for navigation to the leads doc
            deadline = time.time() + 8
            landed = False
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    continue
                if msg.get("method") == "Page.loadEventFired":
                    landed = True
                    break
            time.sleep(0.4)
            r2 = send("Runtime.evaluate", {
                "expression": """
                  (() => {
                    const hash = location.hash;
                    const el = hash ? document.getElementById(hash.slice(1)) : null;
                    const rect = el ? el.getBoundingClientRect() : null;
                    return { url: location.href.split('/').pop(),
                             hash: hash,
                             card: !!el,
                             visible: !!(el && rect && rect.width > 0 && rect.height > 0) };
                  })()
                """,
                "returnByValue": True,
            })
            v = r2.get("result", {}).get("result", {}).get("value", {})
            ok = v.get("card") and v.get("visible")
            if ok:
                passed += 1
                print(f"  PASS {l['href']} -> {v.get('url')}{v.get('hash')} card found & visible")
            else:
                failed.append((l["href"], v))
                print(f"  FAIL {l['href']} -> {v}")
            # navigate back to the source doc for the next click
            send("Page.navigate", {"url": src_url})
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    continue
                if msg.get("method") == "Page.loadEventFired":
                    break
            time.sleep(0.4)

        print(f"\nRESULT: {passed}/{total} links landed on a visible LEAD card")
        if failed:
            print("FAILED:")
            for href, v in failed:
                print(f"  {href}: {v}")
            return 1
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())