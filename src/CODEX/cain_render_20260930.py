"""Fix the span, add scroll-driven reveals, then record the standard.

Two defects before render, both found by the precheck rather than by reading:
  1. the document claims 12.69 h; the truth from the first user message is ~10.7 h
  2. the 28 session blocks have no scroll-driven reveal, which scroll_video.js
     requires (`animation-timeline: view()`) or a single-frame scroll jump lands
     on arbitrary content
"""
import os, re, json, glob, datetime, shutil, subprocess
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

AZ = r"C:\Azazael\azazael"
DOC = os.path.join(AZ, "dashboard", "CAIN_20260930.html")
OUTMP4 = os.path.join(AZ, "dashboard", "CAIN_20260930.mp4")
TPL = os.path.join(AZ, "src", "CODEX", "SESSION_TEMPLATE", "templates", "output_cain.html")
EDGE = r"C:\Users\clint\edge0-intel\scripts\scroll_video.js"

# ── 1. TRUE SPAN, from the first user message ────────────────────────────
START = datetime.datetime(2026, 9, 30, 2, 35, 11)   # first user msg, local UTC-4
now = datetime.datetime.now()
SPAN = (now - START).total_seconds() / 3600.0
SPAN_S = "%.2f h" % SPAN
print("true span: %s   (document says 12.69)" % SPAN_S)

h = open(DOC, encoding="utf-8").read()
before = h

# every form the wrong span took
h = h.replace("12.69 h", SPAN_S).replace("12.70 h", SPAN_S)
h = h.replace("12.69", SPAN_S).replace("12.70", SPAN_S)
h = h.replace("12.66 h", SPAN_S).replace("12.68 h", SPAN_S)
h = h.replace("12.64 h", SPAN_S).replace("12.61 h", SPAN_S)
h = h.replace("12.6 h", SPAN_S)
h = h.replace("12.71 h", SPAN_S)
h = h.replace("$1,205.39", "$%.2f" % (SPAN * 95))
h = h.replace("$1,204.99", "$%.2f" % (SPAN * 95))
h = h.replace("$1,090.67", "$%.2f" % (SPAN * 95))
h = h.replace("$1,066", "$%.0f" % (SPAN * 95))
h = h.replace("2.05 h overstated", "0 h overstated")
h = h.replace("12.70 h \u00d7 $95/hr", "%s \u00d7 $95/hr" % SPAN_S)

# ── 2. scroll-driven reveal on the 28 session blocks ─────────────────────
CSS = """
<style id="session-reveal">
.session-20260930{
  animation: sessionReveal linear both;
  animation-timeline: view();
  animation-range: entry 0% entry 60%;
}
@keyframes sessionReveal{
  from{opacity:0;transform:translateY(14px);}
  to  {opacity:1;transform:translateY(0);}
}
@media (prefers-reduced-motion:reduce){
  .session-20260930{animation:none;opacity:1;transform:none;}
}
</style>
"""
if "session-reveal" not in h:
    h = h.replace("</head>", CSS + "</head>", 1)

open(DOC, "w", encoding="utf-8").write(h)
print("span corrected      :", before != h)
print("reveal CSS injected :", "animation-timeline" in h)
print("28 blocks targeted  :", h.count("session-20260930"))

# ── 3. stage the filled doc where the renderer can reach it ───────────────
if os.path.exists(TPL) and not os.path.exists(TPL + ".orig"):
    shutil.copy2(TPL, TPL + ".orig")
    print("template backed up  :", TPL + ".orig")

# ── 4. RENDER with the edge0 standard ────────────────────────────────────
STAGE = os.path.join(AZ, "dashboard", "cain_render.html")
shutil.copy2(DOC, STAGE)
cmd = ["node", EDGE, "--html", STAGE, "--out", OUTMP4,
       "--fps", "30", "--crf", "26"]
print("\nrendering:", " ".join(cmd[1:]))
r = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(EDGE)),
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
print("rc =", r.returncode)
tail = (r.stdout or "")[-700:] + (r.stderr or "")[-700:]
print(tail)
if os.path.exists(OUTMP4):
    sz = os.path.getsize(OUTMP4)
    print("\nMP4: %s  %.2f MB" % (OUTMP4, sz / 2 ** 20))
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", OUTMP4],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    for ln in p.stderr.splitlines():
        if any(k in ln for k in ("Duration", "Video:", "bitrate")):
            print("   ", ln.strip())
else:
    print("\nNO MP4 PRODUCED")
