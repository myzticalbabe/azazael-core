"""doc_tutor.py -- document processing into tutorial tools.

A manual nobody can follow is a document; a manual with steps a
learner can do is a tutorial. This module converts a document into
a runnable tutorial natively, with no OCR service, no cloud
parser, and no model:

- read_document(path): .md/.txt/.rst (text), .docx (zip +
  WordprocessingML), .pdf (text operators + FlateDecode), .html
  (tag strip). Returns {text, format, words, source_sha}.
- outline(text): headings by markdown/underline/caps heuristics,
  each with its line span.
- tutorial(text, audience): steps with a goal, an action, a
  check you can verify yourself, and a common-mistake note.
  Extraction is mechanical: numbered/bulleted procedures become
  steps verbatim, prose paragraphs become read-and-summarise
  steps. Every step names where it came from, so a learner can
  audit the conversion.
- write_tutorial(path, ...): markdown with provenance header,
  source hash, and an honesty section naming what the converter
  could not read (scans, images, tables flattened).

Honest limits, stated in the output: a scanned PDF has no text
layer and reads as empty rather than guessed; a table becomes
prose; figures are named by caption only. A tutorial that hides
what it lost teaches the loss.
"""
import hashlib
import html
import re
import zipfile
import zlib
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

TEXT_FORMATS = {".md": "markdown", ".markdown": "markdown",
                ".txt": "text", ".rst": "rst", ".csv": "text"}
_STEP_RE = re.compile(r"^\s*(?:(\d+)[.)]|[-*+])\s+(\S.*)$")


def read_document(path):
    """Read any supported document. Returns {text, format, words, sha}."""
    p = Path(path)
    if not p.is_file():
        raise ValueError("no such document: %s" % path)
    raw = p.read_bytes()
    suffix = p.suffix.lower()
    if suffix in TEXT_FORMATS:
        text = raw.decode("utf-8", errors="replace")
        fmt = TEXT_FORMATS[suffix]
    elif suffix == ".docx":
        text, fmt = _read_docx(raw), "docx"
    elif suffix == ".pdf":
        text, fmt = _read_pdf(raw), "pdf"
    elif suffix in (".html", ".htm"):
        text, fmt = _read_html(raw), "html"
    else:
        raise ValueError("unsupported format: %s (md/txt/rst/csv/docx/pdf/html)"
                         % suffix)
    if not text.strip():
        raise ValueError("no text layer in %s -- a scan reads empty, not "
                         "guessed; run OCR first and cite it" % p.name)
    return {"text": text, "format": fmt,
            "words": len(text.split()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "source": p.name}


def _read_docx(raw):
    """WordprocessingML: paragraphs from word/document.xml, in order."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(raw)) as z:
            xml = z.read("word/document.xml").decode("utf-8",
                                                     errors="replace")
    except Exception as e:
        note_failure("doc_tutor:docx", e)
        return ""
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    out = []
    for para in paras:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        line = html.unescape("".join(runs)).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _read_pdf(raw):
    """PDF text operators. Handles uncompressed and FlateDecode streams."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        blob = match.group(1)
        if blob[:2] == b"PK":
            continue
        try:
            blob = zlib.decompress(blob)
        except Exception:
            note_failure("doc_tutor:pdf-stream", None)
        if b"Tj" not in blob and b"TJ" not in blob:
            continue
        for tj in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*Tj", blob):
            chunks.append(_pdf_string(tj.group(0)))
        for arr in re.finditer(rb"\[(.*?)\]\s*TJ", blob, re.S):
            parts = re.findall(rb"\((?:\\.|[^\\()])*\)", arr.group(1))
            chunks.append("".join(_pdf_string(p) for p in parts))
        chunks.append("\n")
    return _tidy(" ".join(chunks))


def _pdf_string(op):
    body = op[op.find(b"(") + 1:op.rfind(b")")] if b"(" in op else b""
    text = body.decode("latin-1", errors="replace")
    text = text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    return text


def _tidy(text):
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _read_html(raw):
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _tidy(html.unescape(text))


def outline(text):
    """Headings by three heuristics, each with its line number."""
    lines = text.splitlines()
    heads = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            heads.append({"line": i + 1, "level": len(s) - len(s.lstrip("#")),
                          "title": s.lstrip("# ").strip()})
        elif (i + 1 < len(lines)
              and re.fullmatch(r"[=\-]{3,}", lines[i + 1].strip())
              and len(s) < 90):
            level = 1 if "=" in lines[i + 1] else 2
            heads.append({"line": i + 1, "level": level, "title": s})
        elif (len(s) < 70 and s.isupper() and len(s.split()) >= 2
              and not s.endswith(".")):
            heads.append({"line": i + 1, "level": 2, "title": s.title()})
    return heads


def tutorial(text, audience="general"):
    """Convert document text into auditable steps."""
    if not text or not text.strip():
        raise ValueError("nothing to convert: empty text")
    lines = text.splitlines()
    heads = {h["line"]: h["title"] for h in outline(text)}
    steps, current = [], ""
    for i, line in enumerate(lines):
        if (i + 1) in heads:
            current = heads[i + 1]
        m = _STEP_RE.match(line)
        if m:
            steps.append({"goal": "complete: %s" % line.strip()[:90],
                          "action": line.strip(),
                          "check": "you can state what changed after "
                                   "doing this step",
                          "mistake": "skipping it because it looks obvious",
                          "from": "line %d" % (i + 1),
                          "section": current})
    if not steps:
        for i, para in enumerate(_paragraphs(text)):
            steps.append({
                "goal": "understand: %s" % para[:70].strip(),
                "action": "read this passage and write one sentence in "
                          "your own words: %s" % para[:200].strip(),
                "check": "your sentence matches the passage without "
                         "copying its words",
                "mistake": "summarising the heading instead of the text",
                "from": "paragraph %d" % (i + 1), "section": None})
    return {"audience": audience, "steps": steps[:60],
            "outline": [h["title"] for h in outline(text)][:24],
            "note": ("%d step(s) extracted mechanically from the "
                     "document's own structure. Nothing was invented; "
                     "nothing was repaired." % len(steps))}


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text)
            if len(p.split()) >= 12]


def write_tutorial(path, doc, tut):
    """Write the tutorial markdown with provenance and limits."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Tutorial from %s" % doc["source"], "",
             "- source sha256: `%s`" % doc["sha256"],
             "- format: %s | words: %d" % (doc["format"], doc["words"]),
             "- audience: %s" % tut["audience"],
             "- steps: %d" % len(tut["steps"]), "",
             "## Outline found in the document", ""]
    lines += ["- %s" % h for h in tut["outline"]] or ["- (no headings found)"]
    lines += ["", "## Steps", ""]
    for n, s in enumerate(tut["steps"], 1):
        lines += ["### Step %d -- %s" % (n, s["goal"]), "",
                  "- **Do:** %s" % s["action"],
                  "- **Check yourself:** %s" % s["check"],
                  "- **Common mistake:** %s" % s["mistake"],
                  "- **Source:** %s%s" % (s["from"],
                                          " (%s)" % s["section"]
                                          if s.get("section") else ""),
                  ""]
    lines += ["## What this conversion lost", "",
              "- Scanned pages with no text layer read empty, not guessed.",
              "- Tables become prose; figures are absent unless captioned.",
              "- Step wording is the document's own lines, not a rewrite.",
              "", tut["note"], ""]
    p.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"path": str(p), "bytes": p.stat().st_size,
            "steps": len(tut["steps"])}
