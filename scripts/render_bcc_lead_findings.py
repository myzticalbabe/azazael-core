#!/usr/bin/env python3
"""
render_bcc_lead_findings.py -- mirror bcc_lead_inference_evidence.json into the
corpus as a searchable HTML findings doc (BCC_LEAD_FINDINGS.html).

Why: the 7 evidence-grounded leads currently live only in JSON, which is not
discoverable from the HTML corpus (structural audit, browser search, cross-doc
anchors). This renderer keeps the doc in sync with the JSON whenever the lead
pass is re-run:  python3 render_bcc_lead_findings.py

The doc is generated from the JSON -- it adds no facts of its own. Class labels
map to badge colors; each lead carries its basis files, gap cross-refs, action,
and integrity note verbatim. Output is corpus-style (dark GitHub theme) with
the standard as-of board-role banner comment, so it joins the other corpus docs
for the structural audit and CDP render check.
"""

import html
import json
import os
import sys

PYIO = sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

IN_JSON = "scripts/bcc_lead_inference_evidence.enriched.json"  # R5: canonical lead store
OUT_HTML = "BCC_LEAD_FINDINGS.html"

CLASS_BADGE = {
    "NEW": ("badge badge-y", "NEW"),
    "STRENGTHENS-EXISTING": ("badge badge-b", "STRENGTHENS-EXISTING"),
    "CROSS-VALIDATES": ("badge badge-g", "CROSS-VALIDATES"),
    "CORRUPTION-FOUND-AND-FIXED": ("badge badge-r", "CORRUPTION FOUND → FIXED"),
    "CORRUPTION-FOUND": ("badge badge-r", "CORRUPTION FOUND"),
    "RESOLVED": ("badge badge-g", "RESOLVED"),
}
STATUS_BADGE = {
    "OPEN": "badge badge-y",
    "IN-PROGRESS": "badge badge-b",
    "RESOLVED": "badge badge-g",
}
# INVESTIGATION_GAPS.html carries one in-page anchor per numbered gap in its
# machine-readable index (<tr id="gap-NN">, verified 62/62 id==number), so a
# lead's gap cross-refs can back-link straight to that gap's row.
GAPS_DOC = "INVESTIGATION_GAPS.html"


def gap_links(refs):
    if not refs:
        return "<span class='note'>—</span>"
    return " ".join(
        f"<a href=\"{GAPS_DOC}#gap-{esc(g)}\" style=\"color:var(--a);text-decoration:none;"
        f"border-bottom:1px dashed #30363d;\" title=\"Back-link to {GAPS_DOC} row {g}\">"
        f"<span class=\"code\">#{esc(g)}</span></a>" for g in refs)
CLASS_BAR = {
    "NEW": "finding-y",
    "STRENGTHENS-EXISTING": "finding-b",
    "CROSS-VALIDATES": "finding-g",
    "CORRUPTION-FOUND-AND-FIXED": "finding-r",
    "CORRUPTION-FOUND": "finding-r",
    "RESOLVED": "finding-g",
}


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        alt = os.path.join("azazael", "scripts", os.path.basename(path))
        with open(alt, encoding="utf-8") as fh:
            return json.load(fh)


def esc(s):
    return html.escape(str(s), quote=True)


def lead_rows(leads):
    rows = []
    for L in leads:
        badge_cls, badge_txt = CLASS_BADGE.get(L["class"], ("badge badge-b", L["class"]))
        st_cls = STATUS_BADGE.get(L.get("status", "OPEN"), "badge badge-y")
        rows.append(
            f"<tr><td><a href=\"#{esc(L['id'])}\" style=\"color:var(--a);text-decoration:none;"
            f"font-weight:700;\">{esc(L['id'])}</a></td>"
            f"<td><span class=\"{badge_cls}\">{esc(badge_txt)}</span></td>"
            f"<td><span class=\"{st_cls}\">{esc(L.get('status','OPEN'))}</span></td>"
            f"<td>{esc(L['title'])}</td></tr>")
    return "\n".join(rows)


def lead_cards(leads):
    cards = []
    for L in leads:
        bar = CLASS_BAR.get(L["class"], "finding-b")
        badge_cls, badge_txt = CLASS_BADGE.get(L["class"], ("badge badge-b", L["class"]))
        basis = "<br>".join(f"<span class=\"code\">{esc(b)}</span>" for b in L.get("basis", []))
        gaps = gap_links(L.get("gap_refs", []))
        st_cls = STATUS_BADGE.get(L.get("status", "OPEN"), "badge badge-y")
        st_txt = L.get("status", "OPEN")
        cards.append(f"""
<div class="card" id="{esc(L['id'])}">
  <div class="lead-head">
    <span class="lead-id">{esc(L['id'])}</span>
    <span class="{badge_cls}">{esc(badge_txt)}</span>
    <span class="{st_cls}">STATUS: {esc(st_txt)}</span>
  </div>
  <div class="finding {bar}"><strong>Finding.</strong> {esc(L['title'])}</div>
  <table class="meta">
    <tr><th>Evidence basis</th><td>{basis}</td></tr>
    <tr><th>Gap cross-refs</th><td>{gaps}</td></tr>
    <tr><th>Next public-record action</th><td>{esc(L['action'])}</td></tr>
    <tr><th>Integrity note</th><td class="note">{esc(L['integrity_note'])}</td></tr>
  </table>
</div>""")
    return "\n".join(cards)


def main():
    data = load(IN_JSON)
    if isinstance(data, list):  # enriched store is a bare list of 11-field records
        data = {"leads": data, "n_leads": len(data),
                "generated": "2026-10-27 (enriched reprocessing)", "layers_loaded": []}
    leads = data.get("leads", [])
    n = data.get("n_leads", len(leads))
    gen = data.get("generated", "2026-09-05")
    n_actual = sum(1 for L in leads if L["id"] != "LEAD-08")
    n_total = len(leads)
    layers = "<br>".join(f"<span class=\"code\">{esc(l)}</span>" for l in data.get("layers_loaded", []))

    counts = {}
    for L in leads:
        counts[L["class"]] = counts.get(L["class"], 0) + 1
    count_spans = " ".join(
        f"<span class=\"badge {CLASS_BADGE.get(c, ('badge badge-b', c))[0]}\">{esc(c)} × {v}</span>"
        for c, v in sorted(counts.items()))

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC Lead Findings — {n_actual} evidence-grounded investigation leads + name-consistency watchdog (cross-layer pass) — September 5, 2026</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9; --h:#f0f6fc; --a:#58a6ff; --r:#f78166; --g:#3fb950; --y:#ffa657; --p:#d2a8ff; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }}
  .container {{ max-width:1080px; margin:0 auto; padding:26px 30px 90px; }}
  h1 {{ color:var(--y); font-size:1.55rem; border-bottom:2px solid var(--y); padding-bottom:12px; margin-bottom:8px; }}
  h2 {{ color:var(--a); font-size:1.15rem; margin:30px 0 12px; border-bottom:1px solid var(--border); padding-bottom:6px; }}
  .subtitle {{ color:#8b949e; font-size:.88rem; margin-bottom:22px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px 18px; margin-bottom:16px; }}
  .lead-head {{ display:flex; align-items:center; gap:12px; margin-bottom:12px; }}
  .lead-id {{ font-weight:800; color:var(--h); font-size:1.02rem; letter-spacing:.02em; }}
  table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
  th,td {{ text-align:left; padding:7px 10px; border:1px solid var(--border); vertical-align:top; }}
  th {{ background:#1c2128; color:var(--a); font-size:.72rem; text-transform:uppercase; width:150px; }}
  tr:nth-child(even) td {{ background:#1a1f26; }}
  .badge {{ display:inline-block; padding:1px 9px; border-radius:10px; font-size:.7rem; font-weight:700; white-space:nowrap; }}
  .badge-r {{ background:#3a1a1a; color:var(--r); }} .badge-y {{ background:#3a2e12; color:var(--y); }}
  .badge-g {{ background:#1b3826; color:var(--g); }} .badge-b {{ background:#0d2a3d; color:var(--a); }}
  .finding {{ padding:12px 16px; margin:8px 0 12px; border-radius:0 8px 8px 0; font-size:.9rem; }}
  .finding-r {{ background:#2d1414; border-left:4px solid var(--r); }}
  .finding-y {{ background:#2d2212; border-left:4px solid var(--y); }}
  .finding-g {{ background:#12281a; border-left:4px solid var(--g); }}
  .finding-b {{ background:#0e1c2b; border-left:4px solid var(--a); }}
  .code {{ font-family:'Cascadia Code',Consolas,monospace; font-size:.78rem; background:#1c2128; padding:1px 6px; border-radius:4px; }}
  .note {{ color:#8b949e; font-size:.8rem; }}
  .counts {{ margin:14px 0; }}
  .footer {{ text-align:center; color:#6e7681; font-size:.78rem; margin-top:36px; border-top:1px solid var(--border); padding-top:14px; }}
  .filterbar {{ margin:0 0 16px; }}
  .filterbar input {{ width:100%; background:#1c2128; color:var(--text); border:1px solid var(--border);
                     border-radius:8px; padding:9px 13px; font-size:.9rem; outline:none; }}
  .filterbar input:focus {{ border-color:var(--a); }}
</style>
</head>
<body>
<div class="container">

<h1>BCC LEAD FINDINGS</h1>
<div class="subtitle">{n_actual} evidence-grounded investigation leads ({n_total} entries incl. the LEAD-08 name-consistency watchdog) from the reproducible cross-layer pass · {esc(gen)} · source: <span class="code">bcc_lead_inference_evidence.json</span> (generated by <span class="code">azazael/scripts/bcc_lead_inference.py</span>)</div>

<!-- board-role-asof-sep2026 -->
<div id="board-role-asof-sep2026" style="border:1px solid #ffa657;background:#1c1305;color:#f0e6d2;border-radius:8px;padding:10px 14px;margin:10px 0 22px 0;font-size:0.85rem;line-height:1.5;"><strong style="color:#ffa657;">⚠ AS-OF BOARD ROLES (addendum Sep 3, 2026):</strong> Officer roles cited in this document are as of the FY2025 Form 990 (filed May 8, 2026) and reflect a <strong>post-filing board reorganization</strong> first observed on the live site Sep 2, 2026: <strong>Plageman Jr. is no longer Board Chair</strong> and <strong>Robinson is no longer Treasurer</strong> (both remain board members); current roles per berkscc.org — <strong>Seitz Chairperson, Gaul Vice President, Ralph Treasurer</strong>. Full audit: INVESTIGATION_GAPS.html Sep 3 annex.</div>

<div class="filterbar"><input id="leadfilter" type="text" placeholder="Filter leads — type a name, gap #, file, or keyword (e.g. Plageman, #31, Herbein, 990)…"></div>

<h2>All leads at a glance</h2>
<div class="card" style="padding:6px 10px;">
<table>
  <tr><th>Lead</th><th>Class</th><th>Status</th><th>Finding</th></tr>
  {lead_rows(leads)}
</table>
</div>
<div class="counts">{count_spans}</div>

<h2>Lead details</h2>
{lead_cards(leads)}

<h2>Method &amp; layers</h2>
<div class="card">
  <p style="font-size:.88rem;margin-bottom:10px;">{esc(data.get('method',''))}</p>
  <table>
    <tr><th>Layers loaded</th><td>{layers}</td></tr>
    <tr><th>Regenerate</th><td><span class="code">python3 azazael/scripts/bcc_lead_inference.py</span> → then <span class="code">python3 azazael/scripts/render_bcc_lead_findings.py</span> to refresh this doc.</td></tr>
  </table>
</div>

<div class="footer">Berks Counseling Center accountability investigation · documented public-record edges only · {esc(gen)}</div>

<script>
(function () {{
  var inp = document.getElementById('leadfilter');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var meta = Array.prototype.slice.call(document.querySelectorAll('.card .finding, .card .meta'));
  function apply() {{
    var q = (inp.value || '').trim().toLowerCase();
    cards.forEach(function (card) {{
      if (!card.querySelector('.lead-id')) return; // skip summary/method cards
      var txt = (card.textContent || '').toLowerCase();
      card.style.display = (!q || txt.indexOf(q) !== -1) ? '' : 'none';
    }});
  }}
  inp.addEventListener('input', apply);
}})();
</script>
</div>
</body>
</html>
"""
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    print(f"wrote {OUT_HTML} ({n} leads, {len(leads)} rendered)")


if __name__ == "__main__":
    main()
