# coding: utf-8
"""The heredoc lint must fire on the shape that failed and stay quiet on the one
that worked.

Both fixtures below are transcribed from ONE real session (2026-10-15, task #721),
and that is the point: a lint whose threshold were picked from theory would be
free to fire on either. The two commands differ in nothing but payload SIZE --

  * `REAL_WORKING` is the actual ~20-line, triple-quoted heredoc that applied its
    edit correctly on the first try;
  * `REAL_FAILING_SHAPE` reproduces the measured properties of the ~170-line one
    that bash reported as `delimited by end-of-file` and that never applied: a
    payload well over the limit, carrying triple-quoted strings.

So the negative control is not a hypothetical "short heredoc" -- it is the
twenty-line triple-quoted command that demonstrably WORKED, which is exactly the
command a triple-quote-keyed rule would have wrongly rejected.
"""

import io
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from heredoc_pipe_lint import (  # noqa: E402 - after sys.path
    MAX_PAYLOAD_LINES, main, scan_command,
)

#: VERBATIM from the session -- the heredoc that WORKED: payload 20 lines, and it
#: contains Python triple-quoted strings twice. Delimited with single-quote triple
#: quotes precisely BECAUSE the payload contains double-quote triple quotes; a
#: double-quoted delimiter here would be closed by the fixture's own content, which
#: is the very failure this lint exists to name. Raw, so the `\r\n` / `\n` inside
#: stay literal for the scanner to measure.
REAL_WORKING = r'''cd /c/Azazael/azazael && python - <<'PY'
from pathlib import Path
p = Path('src/CORE/NeuralPipeline.py')
t = p.read_bytes().decode('utf-8').replace('\r\n', '\n')
old = """            if not features:
                raise ValueError(
                    "the cutting-edge backend needs at least one feature row")
            if not train_records:
                raise ValueError("No training data found for source=%s" % source)
"""
new = """            if not features:
                raise ValueError(
                    "the cutting-edge backend needs at least one feature row")
"""
assert t.count(old) == 1, t.count(old)
t = t.replace(old, new)
p.write_bytes(t.replace('\n', '\r\n').encode('utf-8'))
print("guard removed")
PY
python -c "import ast; ast.parse(open('src/CORE/NeuralPipeline.py',encoding='utf-8').read())"
'''


def _long_command(payload_lines: int, *, triple_quotes: bool) -> str:
    """Reproduce the measured FAILING properties: an over-limit payload."""
    body = []
    for i in range(payload_lines):
        if triple_quotes and i == 3:
            body.append('    old = """some anchor"""')
        else:
            body.append("    row_%d = %d" % (i, i))
    return "cd /c/Azazael/azazael && python - <<'PY'\n" + "\n".join(body) + "\nPY\n"


# -- the negative controls: the lint must NOT fire here ---------------------

def test_the_real_working_heredoc_is_clean():
    """The ~20-line triple-quoted command that applied correctly must be clean.

    This is the control that makes the threshold meaningful: a rule keyed on
    triple quotes would reject the one command here that is known to work.
    """
    findings = scan_command(REAL_WORKING)
    assert findings == [], (
        "the lint rejected a heredoc that applied successfully; keying on triple "
        "quotes rather than payload size: %r" % (findings,))


def test_a_short_plain_heredoc_is_clean():
    """The everyday form must stay clean, or the lint gets ignored."""
    text = "git commit -m \"$(cat <<'EOF'\nsubject line\nEOF\n)\"\n"
    assert scan_command(text) == []


def test_a_long_heredoc_payload_measured_as_safe_is_clean():
    """The boundary itself: exactly MAX_PAYLOAD_LINES is still allowed."""
    text = "\n".join(["cat <<'EOF'"] + ["line %d" % i
                                        for i in range(MAX_PAYLOAD_LINES)] + ["EOF"])
    assert scan_command(text) == []


# -- the shape that failed --------------------------------------------------

def test_over_long_payload_is_flagged_as_too_long():
    findings = scan_command(_long_command(MAX_PAYLOAD_LINES + 5, triple_quotes=True))
    assert len(findings) == 1, findings
    f = findings[0]
    assert f["kind"] == "heredoc_too_long", f
    assert f["tag"] == "PY", f
    assert f["payload_lines"] == MAX_PAYLOAD_LINES + 5, f
    assert f["contains_triple_quotes"] is True, f


def test_a_long_payload_without_triple_quotes_is_flagged_and_says_only_that():
    """Length alone is the rule, and the report must not invent a triple quote."""
    findings = scan_command(_long_command(MAX_PAYLOAD_LINES + 5, triple_quotes=False))
    assert len(findings) == 1, findings
    f = findings[0]
    assert f["kind"] == "heredoc_too_long", f
    assert f["contains_triple_quotes"] is False, f
    assert "triple" not in f["detail"].lower(), (
        "the report mentions triple quotes on a payload that has none: %r"
        % f["detail"])


def test_an_unterminated_heredoc_is_flagged():
    text = "python - <<'PY'\nprint('no terminator follows')\n"
    findings = scan_command(text)
    assert [f["kind"] for f in findings] == ["heredoc_unterminated"], findings
    assert findings[0]["tag"] == "PY", findings[0]


def test_a_tag_used_inside_the_payload_is_not_mistaken_for_the_terminator():
    """The terminator is a line whose ONLY content is the tag.

    Matching the tag as a substring would end the payload early, under-report its
    length and hide the hazard -- so the payload here is over the limit and the
    word PY appears inside it.
    """
    body = ["    note = 'PY is only mentioned here'"] * (MAX_PAYLOAD_LINES + 3)
    text = "python - <<'PY'\n" + "\n".join(body) + "\nPY\n"
    findings = scan_command(text)
    assert [f["kind"] for f in findings] == ["heredoc_too_long"], findings
    assert findings[0]["payload_lines"] == MAX_PAYLOAD_LINES + 3, findings[0]


def test_crlf_input_is_measured_the_same_as_lf():
    """This host's real hazard: the same command arriving with CRLF endings."""
    lf = _long_command(MAX_PAYLOAD_LINES + 5, triple_quotes=True)
    crlf = lf.replace("\n", "\r\n")
    assert ([f["kind"] for f in scan_command(lf)]
            == [f["kind"] for f in scan_command(crlf)])


# -- the interface a caller actually uses -----------------------------------

def _write(tmp_path, name, text):
    p = Path(tmp_path) / name
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_exit_codes(tmp_path, capsys):
    bad = _write(tmp_path, "bad.sh", _long_command(MAX_PAYLOAD_LINES + 5,
                                                    triple_quotes=True))
    good = _write(tmp_path, "good.sh", REAL_WORKING)
    assert main([str(bad)]) == 1
    assert main([str(good)]) == 0
    assert main([]) == 2, "no arguments must be a usage error, not a silent pass"
    captured = capsys.readouterr()
    assert "finding(s)" in captured.out or "clean" in captured.out


def test_cli_json_is_machine_readable(tmp_path, capsys):
    bad = _write(tmp_path, "bad.sh", _long_command(MAX_PAYLOAD_LINES + 5,
                                                   triple_quotes=True))
    assert main(["--json", str(bad)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1, payload
    assert payload["findings"][0]["kind"] == "heredoc_too_long", payload


def test_cli_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        _long_command(MAX_PAYLOAD_LINES + 5, triple_quotes=True)))
    assert main(["-"]) == 1
    assert "heredoc_too_long" in capsys.readouterr().out


def test_cli_rejects_a_missing_file(tmp_path):
    assert main([str(Path(tmp_path) / "nope.sh")]) == 2
