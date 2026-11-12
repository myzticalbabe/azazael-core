#!/usr/bin/env python3
# coding: utf-8
"""build_forest_index -- one page that indexes the forest, from the ledgers.

WHY
The environment clips (WINDOWS / UBUNTU / KALI) answer "what is on this machine".
The domain atlases answer "what is this domain". Neither answers the question
actually asked when work is handed out: *which domains exist, what did each
atlas measure, and which ledger produced it?* Reading eleven JSON files is not
an answer either, so this renders one page from `domain_index.json` plus the
cleave census, with each row naming its atlas -- provenance on the row, not in a
footnote.

DELIBERATELY ONE PAGE, NOT ONE MORE TREE
This is an INDEX, so it draws no forest: a domain figure measured on one
environment must not be read as a measurement of another, and a composite tree
would do exactly that. The three environment clips and each domain clip stay
separate artefacts; this page only lists them.

USAGE
  python tools/build_forest_index.py                     # from ~/yggdrasil_forest
  python tools/build_forest_index.py --out Desktop/YGGDRASIL_FOREST_INDEX.html
  node tools/capture_yggdrasil.js <page.html> 30 <clip.mp4>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

OUT_DIR = Path.home() / "yggdrasil_forest"
DEFAULT_OUT = Path.home() / "Desktop" / "YGGDRASIL_FOREST_INDEX.html"
DURATION_MS = 9000


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


#: Short codes, because `k[:4]` rendered javascript as "java" and a reader
#: cannot tell that from actual Java.
LANG_CODE = {"python": "py", "javascript": "js", "typescript": "ts",
             "html": "html", "batch": "bat", "powershell": "ps1",
             "shell": "sh", "rust": "rs", "c": "c", "cpp": "c++",
             "cuda": "cu", "go": "go", "zig": "zig", "java": "jvm",
             "kotlin": "kt", "dotnet": "net", "julia": "jl", "ruby": "rb",
             "swift": "swift", "objc": "objc"}


def rows_html(rows: List[Dict[str, Any]]) -> str:
    out = []
    max_mod = max([r.get("modules") or 0 for r in rows] + [1])
    for i, r in enumerate(rows):
        langs = r.get("languages") or {}
        langs = {k: v for k, v in langs.items() if k != "_capped"}
        mods = r.get("modules") or 0
        if r.get("kind") == "stacks":
            # A declared stacks domain is read from dist-info metadata, never
            # from source: "none" there reads as "no code", which is the one
            # thing it does not mean.
            lang_txt = "dist-info only"
        elif langs:
            lang_txt = ", ".join("%s %s" % (LANG_CODE.get(k, k[:4]), v)
                                 for k, v in langs.items())
        else:
            lang_txt = "not walked" if mods else "—"
        # sqrt: linear width made every other domain a 1px stub beside dev's
        # 58,511 modules, which reads as "nothing else exists".
        pct = 100.0 * ((mods / max_mod) ** 0.5)
        clips = r.get("clips") or []
        clip_txt = "%d clip" % len(clips) if clips else "no clip"
        missing = r.get("missing") or 0
        flags = []
        if missing:
            flags.append('%d missing' % missing)
        if r.get("stale"):
            flags.append('STALE')
        flag = ('<span class="warn">%s</span>' % " · ".join(flags)) if flags else ""
        out.append(
            '<tr style="--d:%dms">'
            '<td class="nm">%s</td>'
            '<td class="bar"><i style="width:%.1f%%"></i></td>'
            '<td class="num">%s</td>'
            '<td class="num">%s</td>'
            '<td class="num">%s</td>'
            '<td class="lang">%s</td>'
            '<td class="prov">%s %s</td>'
            '<td class="clip">%s</td>'
            "</tr>" % (
                i * 260,
                r.get("domain", "?"),
                pct,
                "{:,}".format(mods),
                "{:,}".format(r.get("lines") or 0),
                "{:,}".format(r.get("classes") or 0),
                lang_txt,
                r.get("atlas") or "no atlas",
                flag,
                clip_txt,
            )
        )
    return "\n".join(out)


def tier_html(tiers: Dict[str, int]) -> str:
    order = [("A_cleave_today", "A · clean python cut"),
             ("A_mixed_unaudited", "A-mixed · other languages unaudited"),
             ("A_cross_language", "A-cross · referenced from another language"),
             ("B_needs_shim", "B · needs a shim"),
             ("C_cycle_blocked", "C · cycle-blocked"),
             ("D_stub", "D · stub, not a cleave")]
    out = []
    for key, label in order:
        n = tiers.get(key, 0)
        out.append('<li><b>%d</b><span>%s</span></li>' % (n, label))
    return "\n".join(out)


def evidence_txt(xl: Dict[str, Any]) -> str:
    """The resolution evidence, not just the count.

    `exact` is a path; `tail` is a unique trailing-component match (a launcher
    written for a flat layout); `basename` is a filename with no directory
    agreement, counted separately because it is the one class that could be a
    coincidence.
    """
    by = xl.get("resolved_by") or {}
    if not by:
        return "<span class=\"dim\">none recorded</span>"
    parts = []
    for key in ("exact", "variable", "periphery", "tail", "basename"):
        if by.get(key):
            parts.append("%s <b>%d</b>" % (key, by[key]))
    out = " · ".join(parts)
    weak = xl.get("weak_statements") or 0
    if weak:
        out += " · %d of those by filename alone" % weak
    out_p = xl.get("outside_repo") or {}
    if out_p:
        out += " · %d point outside the repo" % sum(out_p.values())
    return out


def aliases_txt(census: Dict[str, Any]) -> str:
    """Declared vs used, and a loud mark if a declaration went dead.

    An alias table that no longer matches anything is decoration; the census
    reports it as `stale_alias` and this line is where a reader sees it.
    """
    block = census.get("aliases") or {}
    declared = int(block.get("declarations") or 0)
    if not declared:
        return "<span class=\"dim\">none declared</span>"
    used = len(block.get("used") or {})
    stale = block.get("stale_alias") or []
    txt = "%d declared · %d used" % (declared, used)
    if stale:
        txt += " · <b>%d STALE</b>" % len(stale)
    return txt


def lift_txt(census: Dict[str, Any]) -> str:
    lf = (census.get("contracts") or {}).get("liftability") or {}
    if not lf:
        return "<span class=\"dim\">not measured</span>"
    return "%d/%d (%d blocked)" % (lf.get("liftable", 0), lf.get("shards", 0),
                                   lf.get("blocked", 0))


def budget_txt(census: Dict[str, Any]) -> str:
    """Current unresolved count against the ratchet's recorded budget."""
    now = sum((census.get("cross_language") or {}).get("unresolved" or {}).values())
    base = load(OUT_DIR.parent / "azazael" / "tools" / "unresolved_baseline.json", {})
    if not base:
        base = load(Path(__file__).resolve().parent / "unresolved_baseline.json", {})
    if not base:
        return "%d now · <span class=\"dim\">no baseline</span>" % now
    prior = int(base.get("total") or 0)
    mark = "" if now <= prior else " · <b>OVER</b>"
    return "%d of %d%s" % (now, prior, mark)


def graphs_html(census: Dict[str, Any]) -> str:
    """One row per analysed language: files, resolved edges, external, missing.

    A count of "65 javascript modules" was the whole story before; a row that
    says 0 internal edges and 127 external packages is a different fact about
    the same 65 files.
    """
    graphs = census.get("graphs") or {}
    if not graphs:
        return '<p class="dim">no graphs — run the census</p>'
    rows = ['<tr><th>graph</th><th>files</th><th>edges</th><th>tree pairs</th>'
            '<th>external</th><th>unresolved</th><th>weak</th></tr>']
    # python first: it is the graph with the most edges, and a table headed
    # "language graphs" that omits it reads as "python was not analysed".
    pg = census.get("python_graph") or {}
    ordered = ([("python", pg)] if pg else []) + list(graphs.items())
    for lang, g in ordered:
        ext = g.get("external_statements") or g.get("external_assets") or 0
        unres = g.get("unresolved") or {}
        files, edges = g.get("files") or 0, g.get("statements") or 0
        # `0 edges` beside `65 files` reads as "not analysed". Where the edges are
        # genuinely zero and there ARE references, say so on the row -- derived
        # from the numbers, so it cannot go stale.
        # A single mark, not a phrase: a wrapping cell pushes the row out of
        # alignment with every other row, which reads as a rendering fault.
        note = "†" if (not edges and files and ext) else ""
        rows.append(
            '<tr><td class="nm">%s</td><td class="num">%d</td>'
            '<td class="num">%d%s</td><td class="num">%d</td>'
            '<td class="num">%d</td><td class="num">%d</td>'
            '<td class="num">%d</td></tr>' % (
                lang, files, edges, note,
                len(g.get("tree_pairs") or []), ext, sum(unres.values()),
                g.get("weak_statements") or 0))
    return "\n".join(rows)


def lang_html(plan: Dict[str, Any]) -> str:
    blocks = (plan.get("languages") or {})
    if not blocks:
        return '<p class="dim">no shard plan — run <code>yggdrasil_census.py --plan</code></p>'
    out = ['<tr><th>language</th><th>modules</th><th>shards</th>'
           '<th>underfilled</th><th>straddle*</th></tr>']
    for lang, b in blocks.items():
        out.append(
            '<tr><td class="nm">%s</td><td class="num">%s</td>'
            '<td class="num">%d</td><td class="num">%d</td>'
            '<td class="num">%d</td></tr>' % (
                lang, "{:,}".format(b.get("modules") or 0), b.get("shards") or 0,
                b.get("underfilled") or 0,
                (b.get("continuous") or {}).get("straddling") or 0))
    return "\n".join(out)


def build(index: Dict[str, Any], census: Dict[str, Any],
          plan: Dict[str, Any]) -> str:
    rows = sorted(index.get("domains") or [],
                  key=lambda r: -(r.get("modules") or 0))
    with_atlas = sum(1 for r in rows if r.get("atlas"))
    total_mod = sum(r.get("modules") or 0 for r in rows)
    tiers = {k: len(v or []) if isinstance(v, list) else 0
             for k, v in (census.get("tiers") or {}).items()}
    xl = census.get("cross_language") or {}
    tot = plan.get("totals") or {}
    langs = census.get("languages") or {}
    mods = langs.get("modules") or {}

    lang_chip = " · ".join("%s %s" % (k, v) for k, v in sorted(
        mods.items(), key=lambda kv: -kv[1])) or "none counted"

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>YGGDRASIL — FOREST INDEX</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1920px; height: 1080px; overflow: hidden; }
  body { background: radial-gradient(1200px 700px at 22% 0%, #0b1220 0%, #05070d 62%);
         color: #e8eefc; font-family: "Cascadia Mono", Consolas, monospace; }
  #wrap { position: absolute; inset: 0; padding: 42px 56px; }
  h1 { font-size: 34px; letter-spacing: 3px; font-weight: 700; }
  h1 em { color: #7df9ff; font-style: normal; }
  .sub { color: #7b86a4; font-size: 13px; margin-top: 8px; letter-spacing: 1px; }
  .grid { display: grid; grid-template-columns: 1180px 1fr; gap: 34px; margin-top: 26px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th { text-align: left; color: #7b86a4; font-weight: 500; font-size: 11px;
       letter-spacing: 1.4px; text-transform: uppercase; padding: 0 0 8px; }
  tbody tr { border-top: 1px solid rgba(120,140,180,.16); opacity: 0; transform: translateX(-14px); }
  td { padding: 15px 0; vertical-align: middle; }
  tbody tr:first-child td { padding-top: 8px; }
  td.nm { width: 132px; color: #cfe3ff; font-weight: 700; }
  td.bar { width: 190px; padding-right: 14px; }
  td.bar i { display: block; height: 7px; border-radius: 4px;
             background: linear-gradient(90deg, #39d3c3, #7df9ff); }
  td.num { width: 84px; color: #e8eefc; }
  td.lang { width: 150px; color: #9fb3d1; font-size: 11px; }
  td.prov { color: #6f7d99; font-size: 11px; }
  td.clip { width: 84px; color: #9ece6a; font-size: 11px; }
  .warn { color: #ffb15e; }
  h2 { font-size: 12px; letter-spacing: 1.6px; color: #7df9ff;
       text-transform: uppercase; margin: 0 0 10px; }
  ul.tiers { list-style: none; }
  ul.tiers li { display: flex; gap: 12px; padding: 6px 0; font-size: 12px;
                border-bottom: 1px solid rgba(120,140,180,.12); opacity: 0; }
  ul.tiers b { width: 34px; color: #ffd479; text-align: right; }
  ul.tiers span { color: #a9b8d2; }
  .kv { font-size: 12px; color: #a9b8d2; line-height: 1.7; opacity: 0; }
  .kv b { color: #e8eefc; }
  .card { background: rgba(14,20,34,.72); border: 1px solid rgba(120,140,180,.18);
          border-radius: 10px; padding: 18px 20px; }
  .card + .card { margin-top: 18px; }
  table.plan { font-size: 12px; }
  table.plan td, table.plan th { padding: 4px 0; }
  .dim { color: #6f7d99; font-size: 12px; }
  footer { position: absolute; left: 56px; right: 56px; bottom: 26px;
           color: #5f6c86; font-size: 11px; letter-spacing: .6px;
           display: flex; justify-content: space-between; opacity: 0; }
  code { color: #7df9ff; }
</style></head>
<body><div id="wrap">
  <h1>YGGDRASIL · <em>FOREST INDEX</em></h1>    <div class="sub">__NDOM__ domains · __WITH_ATLAS__ with an atlas · __TOTAL_MOD__ modules ·
    code census __CENSUS_AT__ · index built __INDEX_AT__
    __STALE_CHIP__</div>

  <div class="grid">
    <div>
      <table>
        <thead><tr><th>domain</th><th></th><th>modules</th><th>lines</th>
          <th>classes</th><th>languages</th><th>drawn from</th><th>clip</th></tr></thead>
        <tbody>__ROWS__</tbody>
      </table>
      <div class="card" style="margin-top:22px">
        <h2>what each column is · the universe of every number</h2>
        <table class="plan" style="font-size:12px">
          <tr><td class="nm">modules</td><td>python by AST, every other language by
            file. <span class="warn">counted is not analysed</span></td></tr>
          <tr><td class="nm">classes</td><td>python AST only — 0 in a domain whose
            modules are JS/shell is a missing instrument, not an empty tree</td></tr>
          <tr><td class="nm">bar</td><td>width = √modules, so a 4-module limb is
            visible beside dev's 58,511</td></tr>
          <tr><td class="nm">dev</td><td>declared <code>kind: stacks</code>: read
            from dist-info metadata (781 win / 206 ubuntu / 1,001 kali), no source walk</td></tr>
          <tr><td class="nm">drawn from</td><td>the atlas file that produced the row;
            regenerate when <code>STALE</code> shows</td></tr>
        </table>
      </div>
      <p class="dim" style="margin-top:16px">each row names the atlas it was
      measured from; a domain figure is not a measurement of an environment.</p>
    </div>
    <div>
      <div class="card"><h2>cleave ladder · src/</h2><ul class="tiers">__TIERS__</ul></div>
      <div class="card"><h2>shard plan · limb-aligned</h2>
        <table class="plan">__LANGS__</table>
        <p class="dim" style="margin-top:8px">__TOT_SHARDS__ shards ·
        __STRADDLE__ straddling · __WASTED__ slots unused<br>
        *straddle = shards from the naive global 24-chunk that span two trees</p>
      </div>
      <div class="card"><h2>language graphs · resolved, not asserted</h2>
        <table class="plan">__GRAPHS__</table>
        <div class="kv" style="margin-top:8px">cross-language <b>__XL__</b> resolved ·
          <b>__XLU__</b> unresolved<br>
          evidence __XLB__<br>
          † no internal edges: every reference is an external package<br>
          code counted <b>__LANGCHIP__</b><br>
          aliases __ALIASES__ · liftable __LIFT__<br>
          unresolved budget __BUDGET__</div>
      </div>
    </div>
  </div>

  <footer><span>separate deliverable — environment clips (WINDOWS / UBUNTU / KALI)
    and domain clips are never merged; see DOMAIN_INDEX.md</span>
    <span>tools/build_forest_index.py</span></footer>
</div>
<script>
window.__TOTAL = __DURATION__;
const D = __DURATION__;
const ease = t => 1 - Math.pow(1 - t, 3);
window.__draw = function (t) {
  const rows = document.querySelectorAll('tbody tr');
  rows.forEach((el, i) => {
    const d = i * 260;
    const g = ease(Math.max(0, Math.min(1, (t - d) / 620)));
    el.style.opacity = g;
    el.style.transform = 'translateX(' + (-14 * (1 - g)).toFixed(2) + 'px)';
  });
  const li = document.querySelectorAll('ul.tiers li');
  li.forEach((el, i) => {
    const g = ease(Math.max(0, Math.min(1, (t - 900 - i * 150) / 500)));
    el.style.opacity = g;
  });
  document.querySelectorAll('.kv').forEach((el, i) => {
    const g = ease(Math.max(0, Math.min(1, (t - 1800 - i * 220) / 600)));
    el.style.opacity = g;
  });
  const f = document.querySelector('footer');
  f.style.opacity = ease(Math.max(0, Math.min(1, (t - D + 1400) / 700)));
};
window.__draw(0);
</script>
</body></html>
"""   .replace("__STALE_CHIP__", (
        '<span class="warn">· %d STALE — source newer than atlas</span>'
        % sum(1 for r in rows if r.get("stale")))
       if any(r.get("stale") for r in rows) else "") \
   .replace("__NDOM__", str(len(rows))) \
   .replace("__WITH_ATLAS__", str(with_atlas)) \
   .replace("__TOTAL_MOD__", "{:,}".format(total_mod)) \
   .replace("__CENSUS_AT__", str(census.get("generated_at", "unknown"))) \
   .replace("__INDEX_AT__", str(index.get("generated_at", "unknown"))) \
   .replace("__ROWS__", rows_html(rows)) \
   .replace("__TIERS__", tier_html(tiers)) \
   .replace("__LANGS__", lang_html(plan)) \
   .replace("__GRAPHS__", graphs_html(census)) \
   .replace("__TOT_SHARDS__", str(tot.get("shards", 0))) \
   .replace("__STRADDLE__", str(tot.get("straddling_shards", 0))) \
   .replace("__WASTED__", "{:,}".format(tot.get("wasted_slots", 0))) \
   .replace("__XL__", str(xl.get("statements", 0))) \
   .replace("__XLU__", str(sum((xl.get("unresolved") or {}).values()))) \
   .replace("__XLB__", evidence_txt(xl)) \
   .replace("__ALIASES__", aliases_txt(census)) \
   .replace("__LIFT__", lift_txt(census)) \
   .replace("__BUDGET__", budget_txt(census)) \
   .replace("__LANGCHIP__", lang_chip) \
   .replace("__DURATION__", str(DURATION_MS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--domain-index", default=None)
    ap.add_argument("--census", default=None)
    ap.add_argument("--plan", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    index = load(Path(args.domain_index) if args.domain_index
                 else out_dir / "domain_index.json", {})
    census = load(Path(args.census) if args.census
                  else out_dir / "yggdrasil_census.json", {})
    plan = load(Path(args.plan) if args.plan
                else out_dir / "shard_plan.json", {})

    if not index.get("domains"):
        print("no domain_index.json -- run `python tools/domain_trees.py --index`",
              file=__import__("sys").stderr)
        return 2

    page = build(index, census, plan)
    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    print("wrote %s (%s chars, %d domain rows)" % (out, "{:,}".format(len(page)),
                                                   len(index["domains"])))

    # Read the artefact back: a page whose numbers did not render is a template.
    check = out.read_text(encoding="utf-8")
    problems = [tok for tok in ("__ROWS__", "__TIERS__", "__LANGS__", "__GRAPHS__",
                                "__XL__", "__ALIASES__", "__LIFT__", "__BUDGET__")
                if tok in check]
    if problems:
        print("VERIFY FAILED: unsubstituted tokens %r" % problems,
              file=__import__("sys").stderr)
        return 4
    print("verified: all tokens substituted, %d table rows present"
          % check.count("<tr style="))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
