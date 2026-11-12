#!/usr/bin/env python3
"""Deeper tests for Eve predictive worms."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_linear_regression():
    from eve_unleashed.predictive_worms import _linear_regression, _forecast_value
    pts = [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0)]
    slope, intercept = _linear_regression(pts)
    assert abs(slope - 2.0) < 1e-9
    assert abs(intercept - 1.0) < 1e-9
    # single point
    s, i = _linear_regression([(5.0, 7.0)])
    assert s == 0.0
    assert i == 7.0
    # empty
    s2, i2 = _linear_regression([])
    assert s2 == 0.0
    assert i2 == 0.0
    # zero denom
    s3, i3 = _linear_regression([(1.0, 2.0), (1.0, 4.0)])
    assert s3 == 0.0
    val = _forecast_value(2.0, 1.0, 0.0, 3.0)
    assert val == 7.0


def test_predictive_worm_lifecycle():
    from eve_unleashed.predictive_worms import SYSTEMZ
    w = SYSTEMZ(recorder=None)
    assert w.start() is True
    assert w.is_running() is True
    # calling start again returns True
    assert w.start() is True
    # forecast may be None initially
    import time
    time.sleep(0.1)
    w.stop()
    assert w.is_running() is False


def test_forecast_dataclass():
    from eve_unleashed.predictive_worms import Forecast
    f = Forecast(worm="SYSTEMZ", metric="cpu", current=1.0, trend_slope=0.1,
                 predicted=2.0, horizon_s=300.0, anomaly=False, anomaly_score=0.0)
    d = f.to_dict()
    assert d["worm"] == "SYSTEMZ"
    assert d["anomaly"] is False


def test_all_worms_instantiable():
    from eve_unleashed.predictive_worms import SYSTEMZ, DISKZ, NETWORXZ, PROCESSZ, CLOCKZ
    for cls in (SYSTEMZ, DISKZ, NETWORXZ, PROCESSZ, CLOCKZ):
        w = cls(recorder=None)
        assert w.NAME
        w.stop()
