# coding: utf-8
"""tests for tools/bible_ship.py -- the injection block and the hooks.

Two hazards get their own tests because both were observed, not imagined:

* creating a repo-local `AGENTS.md` where a parent already owns the repository's
  instructions, which would silently retire them for any client that resolves the
  nearest file;
* overwriting a hook this tool did not write, which would remove somebody's gate
  and leave the tree looking gated.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Use Git Bash on Windows, /bin/sh on Unix
if sys.platform == "win32":
    SH = r"C:/Program Files/Git/bin/bash.exe"
else:
    SH = "/bin/sh"

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bible_ship", ROOT / "tools" / "bible_ship.py")
ship = importlib.util.module_from_spec(_spec)
sys.modules["bible_ship"] = ship
_spec.loader.exec_module(ship)


def _data(**over):
    data = {
        "schema": "azazael/bible@1",
        "books": [{
            "id": "genesis", "name": "Genesis", "genre": "etiology",
            "chapters": [{"number": 1, "verses": [
                {"number": 1, "text": "A control shall be exercised.",
                 "statute": {"rule": "RULE 0B-D",
                             "check": "python tools/preflight_all.py"}},
            ]}],
        }],
        "configs": [{"id": "floor", "name": "the floor", "value": "0.89",
                     "file": "tools/x.py", "governs": "noise is answered"}],
    }
    data.update(over)
    return data


def test_self_test_passes():
    assert ship.self_test() == 0


def test_block_carries_statutes_config_decision_and_followups():
    block = ship.render_block(_data(), followups=14)
    assert ship.BEGIN in block and ship.END in block
    assert "RULE 0B-D" in block
    assert "python tools/preflight_all.py" in block
    assert "| `0.89` |" in block
    assert "DECISION" in block
    assert "exactly **14** followups" in block


def test_the_block_is_generated_not_hand_written():
    """Nothing in the block may be a second source of truth."""
    corpus = ship.load_corpus()
    block = ship.render_block(corpus)
    statute_rules = {rule for rule, _text, _check in ship._statutes(corpus)}
    for rule in statute_rules:
        assert rule in block
    for config in corpus["configs"]:
        assert "`%s`" % config["value"] in block


def test_inject_is_idempotent_and_preserves_other_prose(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("# House rules\n\ndo not delete me\n", encoding="utf-8")
    block = ship.render_block(_data())
    assert ship.inject(target, block, create=True) == "updated"
    assert ship.inject(target, block, create=True) == "unchanged"
    text = target.read_text(encoding="utf-8")
    assert "do not delete me" in text
    assert text.count(ship.END) == 1


def test_stale_block_is_detected(tmp_path):
    target = tmp_path / "AGENTS.md"
    ship.inject(target, ship.render_block(_data()), create=True)
    changed = _data()
    changed["configs"][0]["value"] = "0.95"
    results = dict(ship.injection_report(changed, tmp_path, targets=["AGENTS.md"]))
    assert results["AGENTS.md"] == "stale"


def test_creating_agents_is_refused_under_a_parent_that_owns_instructions(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "AGENTS.md").write_text("# lane rules\n", encoding="utf-8")
    repo = parent / "repo"
    repo.mkdir()
    results = dict(ship.injection_report(_data(), repo, inject_block=True,
                                         create_agents=True, targets=["AGENTS.md"]))
    assert results["AGENTS.md"].startswith("refused")
    assert not (repo / "AGENTS.md").exists(), "a shadowing AGENTS.md was created anyway"


def test_creating_agents_is_allowed_when_nothing_above_owns_it(tmp_path, monkeypatch):
    """This host has an AGENTS.md above every temp path, so the lookup is stubbed:
    the assertion is about the branch, not about this machine's directory tree."""
    repo = tmp_path / "standalone"
    repo.mkdir()
    monkeypatch.setattr(ship, "parent_instruction_file", lambda _root: None)
    results = dict(ship.injection_report(_data(), repo, inject_block=True,
                                         create_agents=True, targets=["AGENTS.md"]))
    assert results["AGENTS.md"] == "created"


def test_explicit_target_outside_the_default_set(tmp_path):
    target = tmp_path / "elsewhere" / "rules.md"
    target.parent.mkdir()
    target.write_text("# rules\n", encoding="utf-8")
    results = dict(ship.injection_report(_data(), tmp_path, inject_block=True,
                                         targets=[str(target)]))
    assert results[str(target)] == "updated"
    assert ship.END in target.read_text(encoding="utf-8")


# WHY THIS TEST RUNS THE HOOK IN ITS OWN REPOSITORY (2026-11-04)
#
# It used to source each installed hook with no `cwd`, so the hook inherited the
# pytest working directory -- this repository -- and every gate below the bypass
# line fired for real: 92 s for the pre-push template, 197 s for all three
# (measured 2026-11-02), so the harness killed the file before its own assertion
# ran. Raising the bound (120 -> 240 -> 330 s) bought nothing, because the cost
# was the environment the hook inherited, not the hook.
#
# `cwd` is now a fresh repository of its own, where `tools/bible_ship.py` and
# `tools/bible_check.py` are absent and each stanza leaves at its own "not here"
# guard. That is also the behaviour worth pinning: a hook installed for somebody
# else's repo must not gate this one.
#
# The early exit costs coverage, though, because a shell does NOT report a bad
# substitution on a line it never reaches -- measured: `exit 0` above `x=${{B}}`
# prints nothing and exits 0, and `sh -n` calls that same file clean. So every
# `${...}` in the installed text is expanded on its own below, which is 100%
# token coverage for milliseconds.
def _expansions(text):
    """Every `${...}` in `text`, brace-matched. A malformed `${{X}}` closes at
    the first `}`, so the token handed back is the broken one, not a truncation."""
    tokens = []
    start = text.find("${")
    while start != -1:
        depth = 0
        end = -1
        for i in range(start + 1, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            tokens.append(text[start:])       # unterminated: let the shell judge it
            break
        tokens.append(text[start:end + 1])
        start = text.find("${", end + 1)
    return tokens


def test_no_hook_template_ships_a_bad_substitution(tmp_path):
    """`sh -n` passes on `${X}` doubled into `${{X}}` and the hook then dies at
    runtime with "bad substitution" -- measured here, on all three templates."""
    for name, template in ship.HOOKS.items():
        assert "${{" not in template, "%s carries a doubled brace" % name
        assert "${" in template, "%s cannot read its bypass variable" % name
    ship.install_hooks(tmp_path)
    # A repository of its own: without this the temp dir is nested inside the
    # C:/Users/clint checkout on this host, and the hooks would be sourced against
    # whatever repo happens to contain it.
    subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    for name in ship.HOOKS:
        text = (tmp_path / ".githooks" / name).read_text(encoding="utf-8")
        assert "${{" not in text
        # One token per one-line script, which is the shape the hook itself is.
        # NOT `sh -c 'eval ..."$1"...'`: that form was measured returning rc=0
        # with empty stderr for `${{B}}`, i.e. a check that passes whatever it is
        # given, which is the failure this whole test is about.
        probe = tmp_path / "expansion-probe.sh"
        for token in _expansions(text):
            probe.write_text("x=%s\n" % token, encoding="utf-8")
            expanded = subprocess.run([SH, str(probe)], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=60)
            assert expanded.returncode == 0, "%s: %s -> %s" % (name, token,
                                                                (expanded.stderr or "").strip())
            assert "bad substitution" not in (expanded.stderr or ""), "%s: %s" % (name, token)
        completed = subprocess.run([SH, "-c", "set -e; . \"$1\" </dev/null || true",
                                    "sh", str(tmp_path / ".githooks" / name)],
                                   capture_output=True, text=True, cwd=str(tmp_path),
                                   timeout=60)
        assert "bad substitution" not in (completed.stderr or "")


def test_install_writes_executable_hooks_and_refuses_occupied_ones(tmp_path):
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    states = dict(ship.install_hooks(tmp_path))
    assert states["pre-commit"] == "occupied"
    assert "mine" in (hooks / "pre-commit").read_text(encoding="utf-8")
    for name in ("pre-push", "commit-msg"):
        assert states[name] == "created"
        path = hooks / name
        assert ship.HOOK_MARK in path.read_text(encoding="utf-8")
        completed = subprocess.run([SH, "-n", str(path)], capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr


def test_hook_subset_selects_only_what_was_asked_for(tmp_path):
    states = dict(ship.install_hooks(tmp_path, only=["pre-push"]))
    assert states == {"pre-push": "created"}
    assert not (tmp_path / ".githooks" / "commit-msg").exists()


def test_commit_msg_hook_refuses_a_claim_without_evidence(tmp_path):
    hook = tmp_path / "msg"
    ship.install_hooks(tmp_path, only=["commit-msg"])
    script = tmp_path / ".githooks" / "commit-msg"

    # "Fix the parser" is not a completion claim, so it must pass untouched:
    # a gate that fires on the word "fix" teaches people to bypass it.
    hook.write_text("Fix the parser\n\nthe parser now rejects empty input\n",
                    encoding="utf-8")
    completed = subprocess.run([SH, str(script), str(hook)], capture_output=True,
                               text=True, timeout=60)
    assert completed.returncode == 0

    hook.write_text("Fix the parser\n\nfixed, all tests pass\n", encoding="utf-8")
    completed = subprocess.run([SH, str(script), str(hook)], capture_output=True,
                               text=True, timeout=60)
    assert completed.returncode == 1
    assert "Verified:" in completed.stderr

    hook.write_text("Fix the parser\n\nfixed.\nVerified: pytest -q -> 12 passed\n",
                    encoding="utf-8")
    completed = subprocess.run([SH, str(script), str(hook)], capture_output=True,
                               text=True, timeout=60)
    assert completed.returncode == 0


def test_hooks_path_wiring_refuses_to_steal_an_existing_setting(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=60)
    subprocess.run(["git", "config", "core.hooksPath", "other-dir"],
                   cwd=str(tmp_path), check=True, timeout=60)
    assert ship.wire_hooks_path(tmp_path).startswith("refused")


def test_hooks_path_wiring_sets_it_when_unset(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True, timeout=60)
    assert ship.wire_hooks_path(tmp_path) == "wired"
    value, _ = ship.hooks_path(tmp_path)
    assert value == ".githooks"


def test_plugin_parses_or_says_it_could_not_be_checked(tmp_path):
    outcome = ship.install_plugin(tmp_path)
    assert "SYNTAX ERROR" not in outcome
    target = tmp_path / ".opencode" / "plugin" / ship.PLUGIN_NAME
    assert target.exists()
    node = __import__("shutil").which("node")
    if node:
        completed = subprocess.run([node, "--check", str(target)],
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0
    else:
        assert "unchecked" in outcome


def test_check_reports_absent_as_none_not_stale(tmp_path, capsys):
    """`absent` must not read as a failure: a repo with no CLAUDE.md is fine."""
    assert ship.cmd_check(_data(), tmp_path) == 0
    out = capsys.readouterr().out
    assert "[none]" in out
