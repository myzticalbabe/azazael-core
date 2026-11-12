#!/usr/bin/env python3
"""Deeper tests for Eve CLI."""
import sys
from pathlib import Path
from io import StringIO
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import pytest


def test_main_no_command():
    from eve_unleashed.cli import main
    with patch("sys.argv", ["eve"]):
        rc = main()
    assert rc == 1


def test_cmd_stats():
    from eve_unleashed.cli import cmd_stats
    args = type("A", (), {})()
    rc = cmd_stats(args)
    assert rc == 0


def test_cmd_list_models_no_scan():
    from eve_unleashed.cli import cmd_list_models
    args = type("A", (), {"scan": False})()
    rc = cmd_list_models(args)
    assert rc == 0


def test_cmd_config_show():
    from eve_unleashed.cli import cmd_config
    args = type("A", (), {"config_action": "show", "dest": None, "source": None})()
    rc = cmd_config(args)
    assert rc == 0


def test_cmd_config_backup_restore(tmp_path):
    from eve_unleashed.cli import cmd_config
    dest = tmp_path / "cfg.json"
    args = type("A", (), {"config_action": "backup", "dest": str(dest), "source": None})()
    rc = cmd_config(args)
    assert rc == 0
    assert dest.exists()
    args2 = type("A", (), {"config_action": "restore", "dest": None, "source": str(dest)})()
    rc2 = cmd_config(args2)
    assert rc2 == 0


def test_cmd_config_restore_missing():
    from eve_unleashed.cli import cmd_config
    args = type("A", (), {"config_action": "restore", "dest": None, "source": "/nope.json"})()
    rc = cmd_config(args)
    assert rc == 1


def test_cmd_registry_show():
    from eve_unleashed.cli import cmd_registry
    args = type("A", (), {"registry_action": "show", "verbose": False, "dirs": None})()
    rc = cmd_registry(args)
    assert rc == 0


def test_cmd_infer_bad_model():
    from eve_unleashed.cli import cmd_infer
    args = type("A", (), {"model": "/nope.pt", "input": "[1,2,3]", "framework": "auto", "output": "text"})()
    rc = cmd_infer(args)
    assert rc == 1


def test_cmd_infer_json_output(tmp_path):
    # create a real tiny model file? skip - just test parse path returns 0 (swarm fallback)
    from eve_unleashed.cli import cmd_infer
    args = type("A", (), {"model": "/nope.pt", "input": "hello", "framework": "auto", "output": "json"})()
    rc = cmd_infer(args)
    assert rc == 0  # swarm fallback succeeds


def test_main_stats():
    from eve_unleashed.cli import main
    with patch("sys.argv", ["eve", "stats"]):
        rc = main()
    assert rc == 0


def test_main_list():
    from eve_unleashed.cli import main
    with patch("sys.argv", ["eve", "list"]):
        rc = main()
    assert rc == 0
