# coding: utf-8
"""Tests for `tools/agent_orders_sync.py` -- the standing-order drift gate.

Why this file exists
--------------------
`tools/preflight_all.py --self-test` requires every BLOCKING row in the registry
to name a control artifact, because a gate nobody has seen fail is an assertion,
not a control (RULE 0B-C). This is that control for the `agent-orders` gate,
which `.githooks/pre-commit` runs as check 10.

What it is guarding
-------------------
On 2026-10-30 the MCP gate standing order existed as four hand-maintained copies
in four instruction files. The gate it described was rewritten the same day and
every copy went stale within hours, so each session opened on instructions
describing a check that no longer existed -- and nothing raised. The fix was one
source per block plus a generator; this file proves the generator's `--check`
actually fails when a copy is edited, which is the only property that makes the
pre-commit stanza worth having.

Two behaviours are controls in their own right: `--apply` must repair a tampered
block WITHOUT moving it, and it must refuse to guess where a missing block
belongs (an appended precedence statement at the bottom of a file is useless, so
silence would be worse than an error).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import agent_orders_sync as orders  # noqa: E402

KEY = "session-order"
SRC = "00_session_order.md"
BODY = "## SESSION ORDER\n\none source, many copies.\n"
MARKED = (
    "intro paragraph\n"
    "\n"
    "<!-- AO:session-order START -->\n"
    "a stale hand edit\n"
    "<!-- AO:session-order END -->\n"
    "\n"
    "tail paragraph\n"
)


def _workspace(tmp_path, monkeypatch, targets):
    """Point the module at a throwaway source dir and target list."""
    src_dir = tmp_path / "orders"
    src_dir.mkdir()
    (src_dir / SRC).write_text(BODY, encoding="utf-8")
    monkeypatch.setattr(orders, "ORDERS", src_dir)
    monkeypatch.setattr(orders, "TARGETS", {KEY: (SRC, list(targets))})
    return src_dir


def _target(tmp_path, text=MARKED, name="lane.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_apply_rewrites_the_block_and_leaves_position(tmp_path, monkeypatch, capsys):
    target = _target(tmp_path)
    _workspace(tmp_path, monkeypatch, [target])

    assert orders.check() == 1, "a stale block must be reported, not tolerated"
    assert orders.check(apply=True) == 0
    text = target.read_text(encoding="utf-8")

    assert "one source, many copies." in text
    assert "a stale hand edit" not in text
    assert text.startswith("intro paragraph"), "the block moved instead of being rewritten"
    assert text.rstrip().endswith("tail paragraph")


def test_clean_tree_passes_and_check_is_idempotent(tmp_path, monkeypatch, capsys):
    target = _target(tmp_path)
    _workspace(tmp_path, monkeypatch, [target])
    orders.check(apply=True)
    capsys.readouterr()

    assert orders.check() == 0
    assert "in sync" in capsys.readouterr().out


def test_hand_edit_inside_a_block_is_the_negative_control(tmp_path, monkeypatch, capsys):
    """The property the pre-commit stanza depends on: tamper MUST fail."""
    target = _target(tmp_path)
    _workspace(tmp_path, monkeypatch, [target])
    orders.check(apply=True)
    capsys.readouterr()

    tampered = target.read_text(encoding="utf-8").replace(
        "one source, many copies.", "an edit somebody made instead of using --apply")
    target.write_text(tampered, encoding="utf-8")

    assert orders.check() == 1
    out = capsys.readouterr().out
    assert "DRIFT" in out
    assert str(target) in out, "the report must name the file to fix"


def test_missing_markers_are_reported_and_the_file_is_untouched(tmp_path, monkeypatch, capsys):
    bare = _target(tmp_path, text="no markers in here at all\n", name="bare.md")
    _workspace(tmp_path, monkeypatch, [bare])
    before = bare.read_bytes()

    assert orders.check(apply=True) == 1
    assert "MARKERS ABSENT" in capsys.readouterr().out
    assert bare.read_bytes() == before, "--apply guessed a position instead of refusing"


def test_missing_source_is_reported(tmp_path, monkeypatch, capsys):
    target = _target(tmp_path)
    _workspace(tmp_path, monkeypatch, [target])
    (tmp_path / "orders" / SRC).unlink()

    assert orders.check() == 1
    assert "SOURCE MISSING" in capsys.readouterr().out


def test_missing_target_is_reported(tmp_path, monkeypatch, capsys):
    _workspace(tmp_path, monkeypatch, [tmp_path / "absent.md"])

    assert orders.check() == 1
    assert "TARGET MISSING" in capsys.readouterr().out


def test_hook_mode_skips_absent_targets_but_still_fails_on_drift(tmp_path, monkeypatch, capsys):
    """The commit path must not block a host that does not carry every lane file."""
    present = _target(tmp_path)
    _workspace(tmp_path, monkeypatch, [present, tmp_path / "absent.md"])

    assert orders.check(hook_mode=True) == 1, "drift in a present file must still block"
    out = capsys.readouterr().out
    assert "DRIFT" in out and "absent on this host" in out

    assert orders.check(apply=True, hook_mode=True) == 0
    capsys.readouterr()
    assert orders.check(hook_mode=True) == 0
    assert "absent on this host" in capsys.readouterr().out, "a skip must be visible, not silent"


def test_hook_mode_with_no_targets_is_not_a_pass(tmp_path, monkeypatch, capsys):
    """Nothing verified is not a pass -- the reason this mode needs its own test."""
    _workspace(tmp_path, monkeypatch, [tmp_path / "a-absent.md", tmp_path / "b-absent.md"])

    assert orders.check(hook_mode=True) == 1
    assert "NO TARGETS ON THIS HOST" in capsys.readouterr().out


# --------------------------------------------------------------------------
# render_for_wsl -- the translation layer that had NO control until 2026-10-31
# --------------------------------------------------------------------------
def test_wsl_block_has_no_windows_paths_and_no_bare_python():
    """The Linux lanes cannot run a Windows block.

    Measured before this control existed: neither WSL distro carried a single
    AO block, so both lanes started without the protocol every Windows lane
    had. Copying the block verbatim would have been worse than leaving it out --
    `C:/Users/clint/_board.py` does not resolve there and a stock Kali has no
    `python` alias, so the lane would run a command that fails and read the
    failure as a broken board.
    """
    text = orders.render_for_wsl("lane-queue", "03_lane_queue.md")

    assert "C:/" not in text, "a Windows drive path survived the rewrite"
    assert "/mnt/c/Users/clint" in text, "the rewritten path is missing entirely"
    assert "python C:/" not in text and "python /mnt/c" not in text, \
        "a bare `python` invocation survived -- the Linux lanes have no such alias"
    assert "python3 /mnt/c/Users/clint/_board.py" in text


def test_wsl_rewrite_is_idempotent():
    """Rewriting an already-rewritten block must be a no-op.

    This matters because an apply that is retried after a partial write feeds
    the rewritten text back through the same rules. A rule that is not
    idempotent turns a retry into `/mnt/mnt/c/...` or `python33`, which reads
    as a broken board rather than a broken rewrite.
    """
    once = orders.render_for_wsl("lane-queue", "03_lane_queue.md")
    twice = orders.rewrite_for_wsl(once)

    assert once == twice, "a second rewrite changed the block -- not idempotent"
    assert "mnt/mnt" not in twice and "python33" not in twice
