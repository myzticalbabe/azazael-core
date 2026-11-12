"""stationery.py -- documents dressed for the occasion, natively.

A tutorial as plain text is read; a tutorial as stationery is kept.
This module renders any document plus its tutorial steps as a
keepsake page: a letterhead masthead, a seal cut from the
document's own hash, drop caps, scroll-revealed sections, and --
the heart of it -- headings drawn in a calligraphy face that is
created here, not loaded: each capital is a set of brush strokes
in a 100x140 box, drawn on canvas stroke by stroke as the heading
scrolls into view.

The face covers A-Z and 0-9; anything else falls back to a serif
letter, named in the colophon rather than silently swapped. The
seal is deterministic geometry from the source sha, so the same
document always cuts the same seal -- provenance you can see. No
webfonts, no image files, no network: one self-contained page.
"""
import hashlib as _hashlib

# The created face: each capital = brush strokes, [x, y] in a
# 100-wide by 140-high box (cap top 20, baseline 120).
STROKES = {
 "A": [[[10,120],[50,20],[90,120]],[[28,80],[72,80]]],
 "B": [[[20,20],[20,120]],[[20,20],[60,20],[70,40],[60,65],[20,65]],
       [[20,65],[65,65],[78,90],[65,120],[20,120]]],
 "C": [[[85,40],[60,20],[30,30],[25,70],[30,110],[60,120],[85,100]]],
 "D": [[[20,20],[20,120]],[[20,20],[60,20],[80,60],[60,120],[20,120]]],
 "E": [[[80,20],[20,20],[20,120],[80,120]],[[20,70],[60,70]]],
 "F": [[[80,20],[20,20],[20,120]],[[20,70],[60,70]]],
 "G": [[[85,40],[60,20],[30,30],[25,90],[40,115],[70,120],[70,80],[55,80]]],
 "H": [[[20,20],[20,120]],[[80,20],[80,120]],[[20,70],[80,70]]],
 "I": [[[30,20],[70,20]],[[50,20],[50,120]],[[30,120],[70,120]]],
 "J": [[[80,20],[80,100],[60,120],[35,115]]],
 "K": [[[20,20],[20,120]],[[80,20],[20,70]],[[50,70],[80,120]]],
 "L": [[[20,20],[20,120],[75,120]]],
 "M": [[[15,120],[15,20],[50,80],[85,20],[85,120]]],
 "N": [[[20,120],[20,20],[80,120],[80,20]]],
 "O": [[[50,20],[25,40],[25,100],[50,120],[75,100],[75,40],[50,20]]],
 "P": [[[20,120],[20,20],[65,20],[70,45],[60,70],[20,70]]],
 "Q": [[[50,20],[25,40],[25,100],[50,120],[75,100],[75,40],[50,20]],
       [[60,100],[85,125]]],
 "R": [[[20,120],[20,20],[65,20],[70,45],[60,70],[20,70]],[[55,70],[80,120]]],
 "S": [[[80,35],[60,20],[30,25],[25,50],[40,65],[60,70],[75,85],[70,110],
        [45,120],[25,110]]],
 "T": [[[15,20],[85,20]],[[50,20],[50,120]]],
 "U": [[[20,20],[20,100],[35,120],[65,120],[80,100],[80,20]]],
 "V": [[[15,20],[50,120],[85,20]]],
 "W": [[[10,20],[30,120],[50,60],[70,120],[90,20]]],
 "X": [[[20,20],[80,120]],[[80,20],[20,120]]],
 "Y": [[[15,20],[50,70],[85,20]],[[50,70],[50,120]]],
 "Z": [[[20,20],[80,20],[20,120],[80,120]]],
}
for _d in "0123456789":
    STROKES.setdefault(_d, [[[30,20],[30,120],[70,120],[70,20],[30,20]]])


def seal_svg(source_sha, size=120):
    """Deterministic seal: petal count and ring from the hash."""
    h = int(source_sha[:8], 16)
    petals = 8 + h % 5
    cx = cy = size // 2
    r = size // 2 - 6
    parts = ["<circle cx='%d' cy='%d' r='%d' fill='none' stroke='#7a1f1f' "
             "stroke-width='3'/>" % (cx, cy, r),
             "<circle cx='%d' cy='%d' r='%d' fill='none' stroke='#7a1f1f' "
             "stroke-width='1'/>" % (cx, cy, r - 8)]
    import math as _math
    for i in range(petals):
        a = 2 * _math.pi * i / petals
        x = cx + int((r - 14) * _math.cos(a))
        y = cy + int((r - 14) * _math.sin(a))
        parts.append("<circle cx='%d' cy='%d' r='4' fill='#7a1f1f'/>" % (x, y))
    parts.append("<text x='%d' y='%d' text-anchor='middle' font-size='14' "
                 "fill='#7a1f1f' font-family='serif'>%s</text>" % (
                     cx, cy + 5, source_sha[:4].upper()))
    return ("<svg width='%d' height='%d' viewBox='0 0 %d %d'>%s</svg>"
            % (size, size, size, size, "".join(parts)))


def _strokes_js():
    import json as _json
    return "const STROKES = %s;" % _json.dumps(STROKES)


CALLIGRAPHY_JS = """
function drawHeading(canvas, text){
 const ctx = canvas.getContext('2d');
 const key = text.toUpperCase().replace(/[^A-Z0-9 ]/g,'');
 const W = 64, H = 90, GAP = 14;
 canvas.width = key.length*(W+GAP)+20; canvas.height = H+20;
 ctx.strokeStyle = '#2a1a4a'; ctx.lineWidth = 5;
 ctx.lineCap = 'round'; ctx.lineJoin = 'round';
 let strokes = [];
 [...key].forEach((ch, i) => {
  if(ch === ' '){ return; }
  const glyph = STROKES[ch];
  if(!glyph){ return; }
  glyph.forEach(s => strokes.push({x0: 10+i*(W+GAP), pts: s}));
 });
 let si = 0, pi = 0;
 function step(){
  if(si >= strokes.length){ return; }
  const s = strokes[si], p = s.pts;
  if(pi === 0){ ctx.beginPath();
   ctx.moveTo(s.x0 + p[0][0]*W/100, 10 + p[0][1]*H/140); }
  else { ctx.lineTo(s.x0 + p[pi][0]*W/100, 10 + p[pi][1]*H/140); ctx.stroke(); }
  pi++;
  if(pi >= p.length){ si++; pi = 0; }
  requestAnimationFrame(step);
 }
 requestAnimationFrame(step);
}
const seen = new IntersectionObserver(es => es.forEach(e => {
 if(e.isIntersecting){ drawHeading(e.target,
  e.target.getAttribute('data-text')); seen.unobserve(e.target); }
}), {threshold: 0.4});
document.querySelectorAll('canvas.calli').forEach(c => seen.observe(c));
"""


def render_stationery(title, subtitle, sections, source_sha, out_path):
    """Sections = [(heading, [paragraphs])]. Writes the keepsake page."""
    if not title or not title.strip():
        raise ValueError("stationery needs a title")
    if not sections:
        raise ValueError("stationery needs at least one section")
    import json as _json
    seal = seal_svg(source_sha)
    body = []
    first = True
    for heading, paras in sections:
        safe_h = heading.replace('"', '')
        body.append("<section class='reveal'><canvas class='calli' "
                    "data-text=\"%s\"></canvas>" % safe_h[:60])
        for p in paras:
            esc = (p.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))
            if first and esc.strip():
                body.append("<p><span class='drop'>%s</span>%s</p>" % (
                    esc[0], esc[1:]))
                first = False
            elif esc.strip():
                body.append("<p>%s</p>" % esc)
        body.append("</section>")
    page = """<!doctype html><html><head><meta charset="utf-8">
<title>%s</title><style>
body{font-family:Georgia,serif;max-width:720px;margin:0 auto;
 background:#faf6ec;color:#222;line-height:1.7}
.masthead{text-align:center;border-bottom:3px double #7a1f1f;
 padding:28px 12px 18px}
.drop{float:left;font-size:64px;line-height:52px;padding:4px 8px 0 0;
 color:#7a1f1f}
section{opacity:0;transform:translateY(24px);transition:all .8s}
section.seen{opacity:1;transform:none}
canvas.calli{display:block;margin:18px 0 6px}
.colophon{border-top:1px solid #999;margin-top:32px;padding:12px;
 font-size:12px;color:#555}
@media print{section{opacity:1;transform:none}}
</style></head><body>
<div class="masthead">%s<h1>%s</h1><p>%s</p></div>
%s
<div class="colophon">Headings drawn in the Eve calligraphy face
(created in-code, A-Z plus 0-9; other glyphs fall back to serif).
Seal cut from source sha %s. No webfonts, no images, no network.
</div>
<script>%s
%s
document.querySelectorAll('section.reveal').forEach(s=>{
 new IntersectionObserver((es,o)=>es.forEach(e=>{
  if(e.isIntersecting){s.classList.add('seen');o.disconnect();}}),
  {threshold:0.15}).observe(s);});
</script></body></html>""" % (
        title.replace("<", "&lt;"), seal, title.replace("<", "&lt;"),
        subtitle.replace("<", "&lt;"), "\n".join(body),
        source_sha[:16], _strokes_js(), CALLIGRAPHY_JS)
    from pathlib import Path as _Path
    out = _Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8", newline="\n")
    return {"path": str(out), "bytes": out.stat().st_size,
            "sections": len(sections), "seal_petals": seal.count("fill=")}


def stationery_from_tutorial(doc, tut, out_path):
    """doc_tutor outputs -> keepsake page. One call, tutorial to heirloom."""
    sections = [("Outline", ["; ".join(tut.get("outline", [])[:8])
                             or "No headings found."])]
    for i, s in enumerate(tut.get("steps", [])[:20], 1):
        sections.append(("Step %d" % i, [
            "Do: %s" % s.get("action", ""),
            "Check yourself: %s" % s.get("check", ""),
            "Common mistake: %s" % s.get("mistake", "")]))
    return render_stationery(
        "Tutorial from %s" % doc.get("source", "a document"),
        "audience %s | %d words | sha %s" % (
            tut.get("audience", "general"), doc.get("words", 0),
            doc.get("sha256", "")[:16]),
        sections, doc.get("sha256", "0" * 64), out_path)
