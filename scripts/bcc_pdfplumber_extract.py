"""
bcc_pdfplumber_extract.py — pdfplumber table/text extraction over the
investigation PDF set (Desktop/FACEBOOK/pdfs + root PDFs).

The 19 eviction docket files on disk are .txt docket-sheet texts (already
extracted); the underlying complaint PDFs (which carry unit street addresses)
are not yet obtained — this harness is the extraction path for when they arrive.
It runs over the PDFs that DO exist, characterizing the pattern: tables, pages,
and extracted rows per document.

Run:  C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bcc_pdfplumber_extract.py
      (deps belong in py315t; .venvs/py314m is the sanctioned 3.14 fallback)
Out:  BCC_PDFPLUMBER_EXTRACT.html + bcc_pdfplumber_extract.json
"""
import glob
import html
import json
import os

import pdfplumber


def scan(pdf_path):
    out = {"path": pdf_path, "pages": 0, "tables": 0, "rows": 0,
           "text_chars": 0, "first_table_rows": []}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            out["pages"] = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                out["text_chars"] += len(text)
                for tbl in page.extract_tables():
                    out["tables"] += 1
                    for r in tbl:
                        clean = [str(c).replace("\n", " ").strip()
                                 if c is not None else "" for c in r]
                        out["rows"] += 1
                        if len(out["first_table_rows"]) < 6:
                            out["first_table_rows"].append(clean)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def main():
    pdfs = sorted(
        glob.glob("Desktop/FACEBOOK/pdfs/*.pdf") +
        glob.glob("Desktop/FACEBOOK/*.pdf"))
    print(f"{len(pdfs)} PDFs found")
    results = [scan(p) for p in pdfs]

    total_pages = sum(r["pages"] for r in results)
    total_tables = sum(r["tables"] for r in results)
    total_rows = sum(r["rows"] for r in results)

    out = {"generated": "2026-09-05", "n_pdfs": len(pdfs),
           "total_pages": total_pages, "total_tables": total_tables,
           "total_rows": total_rows, "docs": results}
    with open("bcc_pdfplumber_extract.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    rows = []
    for r in results:
        err = f'<span style="color:#f78166">{html.escape(r.get("error", ""))}</span>' \
            if r.get("error") else ""
        preview = "".join(
            f'<li>{" | ".join(html.escape(c)[:60] for c in tr)}</li>'
            for tr in r["first_table_rows"]) or "<li>(no tables)</li>"
        rows.append(
            f'<tr><td>{os.path.basename(r["path"])}</td>'
            f'<td>{r["pages"]}</td><td>{r["tables"]}</td>'
            f'<td>{r["rows"]}</td><td>{r["text_chars"]}</td>'
            f'<td><ul>{preview}</ul>{err}</td></tr>')

    report = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BCC PDFplumber Extraction — September 5, 2026</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9;
          --h:#f0f6fc; --a:#58a6ff; --g:#3fb950; --y:#ffa657; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }}
  header {{ padding:28px 32px; border-bottom:1px solid var(--border);
           background:linear-gradient(180deg,#10151d,#0d1117); }}
  h1 {{ color:var(--h); font-size:1.35rem; }}
  .sub {{ color:#8b949e; font-size:.9rem; margin-top:6px; }}
  main {{ max-width:1200px; margin:0 auto; padding:24px 32px; }}
  .stat {{ display:inline-block; background:var(--card); border:1px solid var(--border);
          border-radius:10px; padding:14px 20px; margin:0 10px 18px 0; }}
  .stat b {{ color:var(--h); font-size:1.5rem; display:block; }}
  .stat span {{ color:#8b949e; font-size:.78rem; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--border);
          vertical-align:top; font-size:.9rem; }}
  th {{ color:var(--a); font-size:.78rem; text-transform:uppercase;
       letter-spacing:.06em; }}
  ul {{ list-style:none; }}
  li {{ margin:4px 0; font-size:.82rem; color:#8b949e; }}
  .note {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
          padding:14px 18px; margin:18px 0; color:#8b949e; font-size:.88rem; }}
</style></head><body>
<header><h1>BCC PDFplumber Extraction</h1>
<div class="sub">Table/text extraction over the investigation PDF set ·
generated 2026-09-05</div></header>
<main>
<div class="stat"><b>{len(pdfs)}</b><span>PDFs</span></div>
<div class="stat"><b>{total_pages}</b><span>pages</span></div>
<div class="stat"><b>{total_tables}</b><span>tables</span></div>
<div class="stat"><b>{total_rows}</b><span>rows</span></div>
<div class="note"><b>Eviction-docket note:</b> the 19 landlord-tenant docket
files on disk are .txt docket-sheet texts (town-level party addresses only —
no unit street addresses). The underlying complaint PDFs (which carry unit
addresses) are not yet obtained. This harness is the ready extraction path:
run against the complaint PDFs when the records arrive to pull rent amounts,
unit addresses, and property identities for the housing-tenure evidence thread.
</div>
<table><thead><tr><th>PDF</th><th>Pages</th><th>Tables</th><th>Rows</th>
<th>Chars</th><th>First table rows</th></tr></thead><tbody>{''.join(rows)}
</tbody></table>
</main></body></html>"""
    with open("BCC_PDFPLUMBER_EXTRACT.html", "w", encoding="utf-8") as f:
        f.write(report)
    print(f"wrote BCC_PDFPLUMBER_EXTRACT.html + bcc_pdfplumber_extract.json "
          f"({len(pdfs)} PDFs, {total_tables} tables, {total_rows} rows)")


if __name__ == "__main__":
    main()