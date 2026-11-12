"""Parse batch06 technique note parts -> consolidated TECHNIQUES.md + manifest JSON.

Input : state/sota_batches/batch06/technique_notes_part{1,2,3,4}.md
Output: state/sota_batches/batch06/TECHNIQUES.md   (batch05-compatible format)
        state/sota_batches/batch06/techniques_manifest.json  (machine-readable)
"""
import json
import re
from pathlib import Path

B = Path(r"C:\Azazael\state\sota_batches\batch06")
PARTS = [B / f"technique_notes_part{i}.md" for i in (1, 2, 3, 4)]

HDR = re.compile(r"^##\s+EP(?P<ep>\d+)\s+—\s+(?P<name>.+?)\s+\((?P<arxiv>[0-9.]+)\)\s*$")
FIELD = re.compile(r"^-\s*(Mechanism|Mixer steal)\s*:\s*(.*)$")

entries = []
for part in PARTS:
    text = part.read_text(encoding="utf-8")
    cur = None
    for line in text.splitlines():
        m = HDR.match(line.strip())
        if m:
            if cur:
                entries.append(cur)
            cur = {
                "ep": int(m.group("ep")),
                "name": m.group("name").strip(),
                "arxiv": m.group("arxiv"),
                "mechanism": "",
                "mixer": "",
                "part": part.name,
            }
            continue
        if cur is None:
            continue
        f = FIELD.match(line.strip())
        if f:
            key = "mechanism" if f.group(1) == "Mechanism" else "mixer"
            cur[key] = f.group(2).strip()
    if cur:
        entries.append(cur)

print("parsed entries:", len(entries))
print("with mechanism:", sum(1 for e in entries if e["mechanism"]))
print("with mixer    :", sum(1 for e in entries if e["mixer"]))
print("unique EPs    :", len({e["ep"] for e in entries}))

# 1) consolidated TECHNIQUES.md (match batch05 style)
out = ["# SOTA batch06 — technique notes (%d entries)" % len(entries), ""]
out.append("Sources: `technique/<yt_id>.txt` (YT description + arXiv abstract each); "
           "consolidated from `technique_notes_part{1..4}.md`.")
out.append("")
for e in entries:
    out.append("## EP%d — %s (%s)" % (e["ep"], e["name"], e["arxiv"]))
    out.append("- Mechanism: %s" % (e["mechanism"] or "(not captured)"))
    out.append("- Mixer steal: %s" % (e["mixer"] or "(not captured)"))
    out.append("")
(B / "TECHNIQUES.md").write_text("\n".join(out), encoding="utf-8")
print("wrote:", B / "TECHNIQUES.md")

# 2) manifest JSON for the memory-ingest step
(B / "techniques_manifest.json").write_text(
    json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote:", B / "techniques_manifest.json")
