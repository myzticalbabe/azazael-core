#!/usr/bin/env python3
"""Q4 alert-weighted selection pressure: default is identity, weight steers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evolution_cycle as ec


def test_default_weight_is_identity():
    assert ec.QD_ALERT_WEIGHT == 1.0
    assert ec._qd_weighted_total(10.0, 5.0) == 15.0


def test_weight_two_doubles_alert_half():
    assert ec._qd_weighted_total(10.0, 5.0, weight=2.0) == 20.0


def test_weight_zero_drops_alert():
    assert ec._qd_weighted_total(10.0, 5.0, weight=0.0) == 10.0


def test_eval_salt_uses_weighted_total():
    import inspect
    src = inspect.getsource(ec._eval_salt)
    assert "_qd_weighted_total" in src
