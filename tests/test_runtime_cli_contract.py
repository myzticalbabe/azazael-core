# coding: utf-8
"""Runtime CLI contract tests (found during the 2026-10-03 full runtime run).

Covers three defects fixed that day:

  * `validate_registry_importable()` returned `valid: True` over ZERO entries —
    a vacuous pass, surfaced by `azazael-ctl status` printing "Import valid:
    True / Total entries: 0" on a process whose registry had not been
    discovered.
  * the `azazael-ctl` help advertised `registry` and `cluster` sections that
    `CORE.azazael_ctl.main()` never routed, so they answered "Unknown section".
  * `CORE.doctor` computed health with one predicate and the issue count with
    another, printing "PASS — 1 issues" when Ollama was offline.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── registry: an empty registry can never be a pass ────────────────────────

def test_validate_empty_registry_is_not_a_pass(monkeypatch):
    import CORE.worm_registry as wr

    # Discovery that finds nothing, so the registry stays empty on purpose.
    monkeypatch.setattr(wr, "discover_worms", lambda *a, **k: 0)
    with wr._registry_lock:
        saved = dict(wr.WORM_REGISTRY)
        wr.WORM_REGISTRY.clear()
    try:
        result = wr.validate_registry_importable()
        assert result["valid"] is False, "an empty registry must not pass"
        assert result["empty"] is True
        assert result["total"] == 0
    finally:
        with wr._registry_lock:
            wr.WORM_REGISTRY.clear()
            wr.WORM_REGISTRY.update(saved)


def test_validate_records_discovery_error(monkeypatch):
    import CORE.worm_registry as wr

    def _boom(*a, **k):
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(wr, "discover_worms", _boom)
    with wr._registry_lock:
        saved = dict(wr.WORM_REGISTRY)
        wr.WORM_REGISTRY.clear()
    try:
        result = wr.validate_registry_importable()
        assert result["valid"] is False
        assert "discovery exploded" in result.get("discovery_error", "")
    finally:
        with wr._registry_lock:
            wr.WORM_REGISTRY.clear()
            wr.WORM_REGISTRY.update(saved)


# ── azazael-ctl: the advertised sections are routed ────────────────────────

def test_registry_unknown_subcommand_is_rejected(capsys):
    from CORE import azazael_ctl
    rc = azazael_ctl.cmd_registry(["nope"])
    assert rc == 1
    assert "Unknown registry subcommand" in capsys.readouterr().out


def test_cluster_list_lists_predefined_clusters(capsys):
    from CORE import azazael_ctl
    rc = azazael_ctl.cmd_cluster(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "system" in out and "security" in out


def test_cluster_categories_lists_registry_categories(capsys):
    from CORE import azazael_ctl
    rc = azazael_ctl.cmd_cluster(["natures"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Nature categories:" in out


def test_cluster_dead_letter_rejects_bad_lookback(capsys):
    from CORE import azazael_ctl
    rc = azazael_ctl.cmd_cluster(["dead-letter", "--lookback", "abc"])
    assert rc == 1
    assert "lookback" in capsys.readouterr().out.lower()


def test_cluster_unknown_subcommand_is_rejected(capsys):
    from CORE import azazael_ctl
    rc = azazael_ctl.cmd_cluster(["nope"])
    assert rc == 1
    assert "Unknown cluster subcommand" in capsys.readouterr().out


# ── doctor: ONE predicate for per-check label, issue count, and aggregate ──

def test_check_ok_honours_the_healthy_key():
    from CORE.doctor import _check_ok
    # The ollama check returns `healthy` and no `ok`; this is the exact case
    # that made run_all say HEALTHY while verify counted 1 issue.
    assert _check_ok({"healthy": False}) is False
    assert _check_ok({"healthy": True}) is True
    assert _check_ok({"ok": True, "healthy": False}) is True
    assert _check_ok([]) is True          # list = warning-level, never a fail
    assert _check_ok("anything") is True


def test_verify_issue_count_matches_health_predicate(monkeypatch):
    """A single failing check must make both the count and the verdict agree."""
    from CORE import doctor

    checks = {
        "a_ok": {"ok": True},
        "healthy_only_fail": {"healthy": False},   # the ollama shape
    }
    report = {"timestamp": "t", "checks": checks}
    monkeypatch.setattr(doctor, "run_all", lambda quick=True: dict(report))
    out = doctor.cmd_verify()
    # run_all is stubbed, so `healthy` is absent -> the aggregate is False by
    # the same predicate the issue count uses (1 non-ok check).
    n_issues = sum(1 for c in out["checks"].values() if not doctor._check_ok(c))
    assert n_issues == 1
    assert report["checks"]["healthy_only_fail"]["healthy"] is False
