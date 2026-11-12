"""A live, self-refreshing download dashboard in a real browser window.

The PNG card updates on disk every 20s, but a file on disk is not something a
person can WATCH. The user needs to see the numbers move while they plan, so
this serves HTML generated from the CURRENT file sizes on every request, with a
2-second meta refresh, and it is opened in the default browser.

Run:  python tools/flux_dash.py  [--port 8099] [--no-open]
Then:  http://127.0.0.1:8099/
"""
import argparse
import http.server
import os
import socketserver
import threading
import time
import webbrowser
from pathlib import Path

BASE = Path(r"C:\Users\clint\AppData\Local\Programs\AI Playground\resources\models\ComfyUI")
COMPONENTS = [
    ("UNet", "diffusion_models", "flux1-schnell-fp8-e4m3fn.safetensors", 11891329784, "FLUX.1-schnell FP8"),
    ("Text encoder", "text_encoders", "t5xxl_fp8_e4m3fn_scaled.safetensors", 5157348688, "T5-XXL FP8 scaled"),
    ("CLIP-L", "clip", "clip_l.safetensors", 246144152, ""),
    ("VAE", "vae", "flux-vae-bf16.safetensors", 167664710, "flux-vae bf16"),
]
GB = 1073741824
START = time.time()
HIST = []  # (t, total_bytes)


def sample():
    rows, total, want = [], 0, 0
    for label, sub, fn, size, note in COMPONENTS:
        p = BASE / sub / fn
        try:
            got = p.stat().st_size
        except OSError:
            got = 0
        total += got
        want += size
        rows.append((label, note, got, size, min(100.0, 100 * got / size)))
    return rows, total, want


def rates(total):
    now = time.time()
    HIST.append((now, total))
    del HIST[:-40]
    inst = avg = 0.0
    if len(HIST) >= 2:
        dt = HIST[-1][0] - HIST[-2][0]
        if dt > 0:
            inst = (HIST[-1][1] - HIST[-2][1]) / GB * 1024 / dt
    el = now - START
    if el > 0:
        avg = total / GB * 1024 / el
    return inst, avg, now - START


def hms(s):
    if s is None or s < 0 or s != s or s in (float("inf"),):
        return "--:--:--"
    s = int(round(s))
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def page():
    rows, total, want = sample()
    inst, avg, el = rates(total)
    pct = 100.0 * total / want
    eta = ((want - total) / GB * 1024 / inst) if inst > 50 else None
    done = sum(1 for r in rows if r[2] >= r[3])

    bar = "".join(
        f"""<tr class="{'done' if r[2]>=r[3] else ('dl' if r[2]>0 else 'q')}">
<td class="lbl">{r[0]}<span class="note">{r[1]}</span></td>
<td class="tr"><div class="fill" style="width:{max(0.4, r[4]):.2f}%"></div></td>
<td class="pct">{r[4]:.1f}%</td>
<td class="sz">{r[2]/GB:.2f} / {r[3]/GB:.2f} GB</td></tr>"""
        for r in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>FLUX download</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font:14px/1.5 "Cascadia Mono",Consolas,monospace;
     padding:28px 32px}}
h1{{font-size:21px;letter-spacing:.3px}} h1 span{{color:#58a6ff}}
.sub{{color:#8b949e;font-size:12px;margin-top:6px}}
.big{{font-size:58px;font-weight:700;color:#58a6ff;margin:22px 0 4px;line-height:1}}
.of{{color:#8b949e;font-size:14px}}
.track{{height:15px;background:#161b22;border:1px solid #30363d;border-radius:8px;
       overflow:hidden;margin:12px 0 22px}}
.fillO{{height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff)}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}}
.cell{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 13px}}
.k{{font-size:10px;color:#8b949e;letter-spacing:1px;text-transform:uppercase}}
.v{{font-size:19px;font-weight:700;margin-top:4px}}
table{{width:100%;border-collapse:collapse}}
tr td{{padding:9px 0;border-top:1px solid #21262d;vertical-align:middle}}
.lbl{{width:210px;font-weight:600}} .note{{display:block;font-size:10.5px;color:#6e7681;font-weight:400}}
.tr{{width:auto;padding:0 14px}} .pct{{width:70px;text-align:right;font-weight:700}}
.sz{{width:170px;text-align:right;color:#6e7681;font-size:12px}}
.fill{{height:8px;background:#3fb950;border-radius:4px}}
.dl .fill{{background:linear-gradient(90deg,#1f6feb,#58a6ff)}} .q .fill{{background:#30363d}}
.done .pct{{color:#3fb950}}
.foot{{margin-top:20px;padding-top:12px;border-top:1px solid #21262d;
      font-size:11px;color:#6e7681;line-height:1.8}}
</style></head><body>
<h1>FLUX<span>.1-schnell</span> FP8 &mdash; download</h1>
<div class="sub">Intel Arc 140T &middot; 7.80 GB XPU &middot; headless ComfyUI &middot; auto-refresh 2s</div>
<div class="big">{pct:.1f}%</div>
<div class="of">{total/GB:.2f} of {want/GB:.2f} GB &middot; {done}/4 files complete</div>
<div class="track"><div class="fillO" style="width:{pct:.2f}%"></div></div>
<div class="grid">
<div class="cell"><div class="k">speed now</div><div class="v" style="color:#58a6ff">{inst:.1f}<span style="font-size:11px;color:#8b949e"> MB/s</span></div></div>
<div class="cell"><div class="k">average</div><div class="v">{avg:.1f}<span style="font-size:11px;color:#8b949e"> MB/s</span></div></div>
<div class="cell"><div class="k">remaining</div><div class="v" style="color:#d29922">{hms(eta)}</div></div>
<div class="cell"><div class="k">elapsed</div><div class="v">{hms(el)}</div></div>
</div>
<table>{bar}</table>
<div class="foot">
Link runs ~3 MB/s, so 16.26 GB takes roughly 1.5 h. curl uses <b>-C -</b>: a drop resumes, it does not restart.<br>
UNet is 11.89 GB against a 7.80 GB card, so ComfyUI offloads to system RAM &mdash;
<b>free RAM is the binding constraint, not VRAM</b>. Restart before running.
</div></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        html = page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"live dashboard: {url}   (Ctrl-C to stop)")
    if not a.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.shutdown()


if __name__ == "__main__":
    main()
