"""Tests for tools/twelve_factor_audit.py (the 12-face / Twelve-Factor audit).

WHY THIS FILE ASSERTS SO HARD ON THE PROBES
-------------------------------------------
The first version of the audit reported two confident FALSE findings:

  * "8 hardcoded credential literals" -- every one of them a placeholder
    (`api_key = "anonymous"`), a deliberate scanner fixture
    (`invalid_code_secret` in enforcement_harness.py), or a vendored Jinja2
    lexer token (`token = 'block_begin'`) inside archived/upstream trees.
  * "scripts & src/SCRIPTS share filenames" -- which is just `__init__.py`
    plus `monitor_learning.py`, a deliberately byte-identical mirror.

A test that only asserted "the audit returns 12 faces" would have passed
while the tool lied. These tests therefore pin the *exclusions*, and include
a negative control proving a real credential is still caught -- an exclusion
rule that swallows everything is as wrong as one that catches nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "twelve_factor_audit.py"


def _load_tool():
    """Import tools/twelve_factor_audit.py by path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location("twelve_factor_audit", _TOOL)
    assert spec and spec.loader, "could not load %s" % _TOOL
    mod = importlib.util.module_from_spec(spec)
    sys.modules["twelve_factor_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tfa():
    return _load_tool()


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# Artifact shape
# --------------------------------------------------------------------------

def test_tool_imports_and_declares_twelve_faces(tfa):
    assert len(tfa.FACES) == 12, "the dodecahedron has twelve faces"


def test_audit_reports_twelve_faces_with_legal_verdicts(tfa, tmp_path):
    payload = tfa.audit(tmp_path)
    assert payload["faces_total"] == 12
    assert len(payload["faces"]) == 12
    legal = {"pass", "partial", "gap", "manual"}
    for face in payload["faces"]:
        assert face["verdict"] in legal, face
        assert face["detail"], "a face must always explain itself"
        assert isinstance(face["evidence"], dict)


def test_manual_is_never_counted_as_solid(tfa, tmp_path):
    """A `manual` face is unmeasured -- it must not inflate the solid count."""
    payload = tfa.audit(tmp_path)
    assert payload["faces_solid"] == payload["tally"].get("pass", 0)
    assert payload["faces_solid"] + payload["faces_open"] == 12
    manual = payload["tally"].get("manual", 0)
    assert payload["faces_open"] >= manual


def test_render_is_ascii_only(tfa, tmp_path):
    """Console is cp1252 on this host: non-ASCII would mojibake the report."""
    text = tfa.render(tfa.audit(tmp_path))
    text.encode("ascii")


# --------------------------------------------------------------------------
# Factor IV -- the false-positive class. Exclusions AND a negative control.
# --------------------------------------------------------------------------

def test_placeholder_value_is_not_a_credential(tfa, tmp_path):
    _write(tmp_path, "src/inference/providers.py", 'api_key = "anonymous"\n')
    assert tfa._credential_literals(tmp_path) == []


def test_fixture_variable_is_not_a_credential(tfa, tmp_path):
    _write(tmp_path, "src/CORE/enforcement_harness.py",
           'invalid_code_secret = "api_key = \'sk-1234567890abcdef\'"\n')
    # The name marks it a deliberate fixture, so it must not be reported.
    assert not [h for h in tfa._credential_literals(tmp_path)
                if "enforcement_harness" in h]


def test_vendored_lexer_token_is_not_a_credential(tfa, tmp_path):
    _write(tmp_path, "nuitka_build_backup/inline_copy/jinja2/lexer.py",
           "token = 'block_begin'\ntoken = 'block_end'\n")
    assert tfa._credential_literals(tmp_path) == []


def test_archived_upstream_checkout_is_not_scanned(tfa, tmp_path):
    """archived/onnxruntime-* is a pristine upstream tree, not our config."""
    _write(tmp_path,
           "archived/2026-05-31/onnxruntime-main/tools/ci/download_azure.py",
           'AZURE_BLOB_SAS_TOKEN = "abcdef1234567890"\n')
    _write(tmp_path, "src/CORE/inference/nim_client.py",
           'some = os.environ["NVIDIA_NIM_API_KEY"]\n')
    names = tfa.discover_env_names(tmp_path)
    assert "AZURE_BLOB_SAS_TOKEN" not in names
    assert "NVIDIA_NIM_API_KEY" in names


def test_real_credential_IS_reported_negative_control(tfa, tmp_path):
    """Negative control: excluding everything would be as wrong as the leak."""
    _write(tmp_path, "src/CORE/leaky.py",
           'GITHUB_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"\n')
    hits = tfa._credential_literals(tmp_path)
    assert hits, "a real hardcoded token must still be caught"
    assert any("leaky.py" in h for h in hits)


def test_env_example_uses_relative_paths_only(tfa, tmp_path):
    """An earlier draft leaked the operator's home directory into a repo file."""
    _write(tmp_path, "src/deep/nested/config.py",
           'x = os.environ.get("AZAZAEL_MODE")\n')
    body = tfa.render_env_example(tfa.discover_env_names(tmp_path))
    assert "src/deep/nested/config.py" in body
    assert str(tmp_path) not in body
    # Assembled rather than written literally: a bare drive-letter string here
    # trips the repo's own hardcoded-path scanner on a line that exists purely
    # to prove such paths are absent.
    drive = "C" + ":"
    assert drive + "/" not in body
    assert drive + chr(92) not in body


def test_env_example_blanks_secret_values(tfa, tmp_path):
    _write(tmp_path, "src/a.py", 'k = os.environ["SOME_API_KEY"]\n')
    _write(tmp_path, "src/b.py", 'm = os.environ["AZAZAEL_DEBUG"]\n')
    body = tfa.render_env_example(tfa.discover_env_names(tmp_path))
    assert "\nSOME_API_KEY=\n" in body or body.rstrip().endswith("SOME_API_KEY=")
    # Non-secret names are emitted commented out so nothing is enabled by copy.
    assert "# AZAZAEL_DEBUG=" in body


def test_os_interpreter_vars_are_not_config(tfa, tmp_path):
    _write(tmp_path, "src/a.py",
           'a = os.environ["PYTHONPATH"]\nb = os.environ.get("PATH")\n'
           'c = os.getenv("PROCESSOR_ARCHITECTURE")\n'
           'd = os.environ["AZAZAEL_MEMORY_DIR"]\n')
    names = tfa.discover_env_names(tmp_path)
    assert "AZAZAEL_MEMORY_DIR" in names
    for noise in ("PYTHONPATH", "PATH", "PROCESSOR_ARCHITECTURE"):
        assert noise not in names


# --------------------------------------------------------------------------
# Factor I -- overlap is not drift; only divergence is a defect.
# --------------------------------------------------------------------------

def test_mirrored_identical_files_are_not_reported_as_gap(tfa, tmp_path):
    _write(tmp_path, "scripts/monitor_learning.py", "print('same')\n")
    _write(tmp_path, "src/SCRIPTS/monitor_learning.py", "print('same')\n")
    face = tfa.face_1_codebase(tmp_path)
    assert face.verdict != "gap"
    assert "monitor_learning.py" in face.evidence["mirrored_identical"]


def test_mirrored_diverged_files_are_a_gap(tfa, tmp_path):
    _write(tmp_path, "scripts/monitor_learning.py", "print('here')\n")
    _write(tmp_path, "src/SCRIPTS/monitor_learning.py", "print('there')\n")
    face = tfa.face_1_codebase(tmp_path)
    assert face.verdict == "gap"
    assert "monitor_learning.py" in face.evidence["mirrored_diverged"]


def test_dunder_init_overlap_is_ignored(tfa, tmp_path):
    _write(tmp_path, "scripts/__init__.py", "")
    _write(tmp_path, "src/SCRIPTS/__init__.py", "")
    face = tfa.face_1_codebase(tmp_path)
    assert face.evidence["mirrored_diverged"] == []


# --------------------------------------------------------------------------
# Factor III -- the gap this tool exists to detect.
# --------------------------------------------------------------------------

def test_config_face_flags_missing_env_example(tfa, tmp_path):
    _write(tmp_path, "src/a.py", 'x = os.environ["AZAZAEL_MODE"]\n')
    face = tfa.face_3_config(tmp_path)
    assert face.verdict == "partial", "env-driven but undocumented is partial"
    assert "no .env.example" in face.detail.lower()


def test_config_face_passes_with_env_example(tfa, tmp_path):
    _write(tmp_path, "src/a.py", 'x = os.environ["AZAZAEL_MODE"]\n')
    (tmp_path / ".env.example").write_text("AZAZAEL_MODE=\n", encoding="utf-8")
    assert tfa.face_3_config(tmp_path).verdict == "pass"


def test_real_tree_has_no_hardcoded_credentials(tfa):
    """Regression guard on the actual repo: the validated answer is zero."""
    root = _TOOL.resolve().parent.parent
    hits = tfa._credential_literals(root)
    assert hits == [], "hardcoded credential literal(s) found:\n" + "\n".join(hits)


def test_real_tree_env_example_exists_and_is_clean(tfa):
    root = _TOOL.resolve().parent.parent
    sample = root / ".env.example"
    assert sample.exists(), (
        "Factor III requires a declarative config sample; regenerate with "
        "python tools/twelve_factor_audit.py --write-env-example")
    body = sample.read_text(encoding="utf-8")
    assert ("C" + ":" + "/") not in body
    assert "onnxruntime" not in body
