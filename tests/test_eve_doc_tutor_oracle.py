"""G1 tests for doc_tutor.py, oracle.py, swarm_oracle.py.

Document conversion is read-only and mechanical: a numbered list
becomes steps, a prose document becomes read-and-summarise steps,
a scan reads empty rather than guessed. Oracles must name skipped
and errored signals instead of counting them as support. The swarm
must report failures rather than dropping them.
"""
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import doc_tutor as D
from eve_unleashed import oracle as O
from eve_unleashed import swarm_oracle as S


def test_read_markdown_and_outline(tmp_path):
    p = tmp_path / "manual.md"
    p.write_text("# Setup\n\nIntro words here.\n\n## Install\n\n1. Run the thing\n"
                 "2. Check the log\n", encoding="utf-8")
    doc = D.read_document(str(p))
    assert doc["format"] == "markdown" and doc["words"] > 5
    titles = [h["title"] for h in D.outline(doc["text"])]
    assert "Setup" in titles and "Install" in titles


def test_numbered_lines_become_steps(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text("1. Unplug it\n2. Wait ten seconds\n3. Plug it in\n",
                 encoding="utf-8")
    doc = D.read_document(str(p))
    tut = D.tutorial(doc["text"])
    assert len(tut["steps"]) == 3
    assert tut["steps"][0]["action"] == "1. Unplug it"
    assert all(s["check"] and s["mistake"] for s in tut["steps"])


def test_prose_becomes_read_steps(tmp_path):
    p = tmp_path / "essay.txt"
    p.write_text("This paragraph carries enough words to count as a real "
                 "paragraph of prose for the converter to treat as one "
                 "readable unit of text.\n\nA second paragraph also long "
                 "enough to be treated as separate prose by the very same "
                 "rule applied again here.\n", encoding="utf-8")
    tut = D.tutorial(D.read_document(str(p))["text"])
    assert tut["steps"][0]["action"].startswith("read this passage")


def test_docx_and_html_and_empty_pdf(tmp_path):
    docx = tmp_path / "d.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:body><w:p><w:r><w:t>Hello mars</w:t>"
                   "</w:r></w:p></w:body></w:document>")
    assert "Hello mars" in D.read_document(str(docx))["text"]
    page = tmp_path / "p.html"
    page.write_text("<p>Alpha <b>beta</b></p><script>x=1</script>",
                    encoding="utf-8")
    assert "Alpha beta" in D.read_document(str(page))["text"]
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4\nstream\nimageonly\nendstream\n")
    with pytest.raises(ValueError):
        D.read_document(str(scan))


def test_write_tutorial_records_losses(tmp_path):
    tut = {"audience": "general", "outline": ["Setup"],
           "steps": [{"goal": "g", "action": "a", "check": "c",
                      "mistake": "m", "from": "line 1", "section": "Setup"}],
           "note": "one step"}
    doc = {"source": "m.md", "sha256": "ab" * 32, "format": "markdown",
           "words": 3}
    out = D.write_tutorial(str(tmp_path / "t.md"), doc, tut)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "What this conversion lost" in text
    assert "ab" * 32 in text


def test_oracle_supported_and_refused():
    with patch.object(O, "_live_signal",
                      return_value={"signal": "live", "verdict": "CORROBORATED",
                                    "detail": "x", "refutes": False}):
        ok = O.consult("The Hoover Dam exists.")
    assert ok["verdict"] == "SUPPORTED" and ok["confidence"] == 1.0
    bad = O.consult("This always works except when it fails.")
    assert bad["verdict"] == "REFUSED"
    assert bad["disagreeing"] >= 1


def test_oracle_names_skipped_signal():
    with patch.object(O, "_live_signal",
                      return_value={"signal": "live", "verdict": "SKIPPED",
                                    "detail": "x", "refutes": False}):
        res = O.consult("A plain checkable sentence.")
    skipped = [s for s in res["signals"] if s["verdict"] == "SKIPPED"]
    assert skipped
    assert res["agreeing"] == 1


def test_swarm_reports_tally_and_failures():
    res = S.swarm(["This always works except never.",
                   "The river flows north."], workers=2)
    assert res["answered"] == 2
    assert res["tally"].get("REFUTED") == 1
    assert res["failed"] == []
    with pytest.raises(ValueError):
        S.swarm([])


def test_swarm_worker_cap():
    out = S.swarm(["a b c"] * 20, workers=99)
    assert out["workers"] <= S.MAX_WORKERS
