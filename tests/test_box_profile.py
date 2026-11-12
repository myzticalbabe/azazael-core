#!/usr/bin/env python3
# coding: utf-8
"""Tests for the box profiler, with the negative control that matters.

The defect this file exists to prevent is collapsing THREE states into two:
"the library is not installed" quietly becoming "this box has no GPU". On a
machine where torch was never installed, a profiler that prints
`cuda: unavailable` is describing its own import list, and the next reader
documents the hardware from it.

So the negative control here is a box with **no torch at all**, and the
assertion is that every probed backend reads `unknown` -- never `unavailable`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import box_profile  # noqa: E402


def _no_torch() -> dict:
    """Stand-in for `_torch_accelerators()` on a box where torch is absent."""
    return {"_library": {"torch": "not importable: ModuleNotFoundError"}}


def test_absent_library_is_unknown_never_unavailable(monkeypatch):
    monkeypatch.setattr(box_profile, "_torch_accelerators", _no_torch)
    box = box_profile.profile()
    statuses = {r["backend"]: r["status"] for r in box["accelerators"]}
    for backend in ("xpu", "cuda", "mps"):
        assert statuses[backend] == "unknown", (
            "%s read %r with no torch installed; unknown and unavailable are "
            "different statements" % (backend, statuses[backend]))
    assert box["preferred_accelerator"] == "cpu"


def test_unknown_rows_carry_a_remedy(monkeypatch):
    """An unknown with no remedy is a dead end for the reader."""
    monkeypatch.setattr(box_profile, "_torch_accelerators", _no_torch)
    box = box_profile.profile()
    unknown = [r for r in box["accelerators"] if r["status"] == "unknown"]
    assert unknown
    for record in unknown:
        assert record.get("remedy") or record.get("note"), record


def test_cpu_is_always_available(monkeypatch):
    monkeypatch.setattr(box_profile, "_torch_accelerators", _no_torch)
    box = box_profile.profile()
    cpu = next(r for r in box["accelerators"] if r["backend"] == "cpu")
    assert cpu["status"] == "available"


def test_present_library_without_device_is_unavailable(monkeypatch):
    """The middle state: importable, reports nothing. That IS a negative reading."""
    monkeypatch.setattr(box_profile, "_torch_accelerators",
                        lambda: {"_library": {"torch": "2.x"},
                                 "xpu": {"available": False},
                                 "cuda": {"available": False},
                                 "mps": {"available": False}})
    box = box_profile.profile()
    statuses = {r["backend"]: r["status"] for r in box["accelerators"]}
    assert statuses["xpu"] == "unavailable"
    assert statuses["cuda"] == "unavailable"


def test_available_backend_wins_the_preference(monkeypatch):
    monkeypatch.setattr(box_profile, "_torch_accelerators",
                        lambda: {"_library": {"torch": "2.x"},
                                 "xpu": {"available": True, "name": "Arc 140T",
                                         "total_memory_gb": 7.8, "device_count": 1},
                                 "cuda": {"available": False},
                                 "mps": {"available": False}})
    box = box_profile.profile()
    assert box["preferred_accelerator"] == "xpu"


def test_preference_order_is_xpu_then_cuda_then_mps(monkeypatch):
    monkeypatch.setattr(box_profile, "_torch_accelerators",
                        lambda: {"_library": {"torch": "2.x"},
                                 "xpu": {"available": False},
                                 "cuda": {"available": True, "name": "RTX"},
                                 "mps": {"available": True}})
    assert box_profile.profile()["preferred_accelerator"] == "cuda"


def test_render_labels_unknown_as_not_measured(monkeypatch):
    """The human table must not let an unknown read as a clean negative."""
    monkeypatch.setattr(box_profile, "_torch_accelerators", _no_torch)
    text = box_profile.render(box_profile.profile())
    assert "NOT MEASURED" in text
    assert "not the same as absent" in text


def test_drift_names_the_backend_that_moved(monkeypatch):
    stored = {"os": {"system": "Windows", "machine": "AMD64"},
              "accelerators": [{"backend": "cuda", "status": "unavailable"}],
              "preferred_accelerator": "cpu"}
    fresh = {"os": {"system": "Windows", "machine": "AMD64"},
             "accelerators": [{"backend": "cuda", "status": "available"}],
             "preferred_accelerator": "cuda"}
    problems = box_profile.drift(stored, fresh)
    assert any("cuda" in line and "available" in line for line in problems)
    assert any("preferred_accelerator" in line for line in problems)


def test_drift_is_silent_when_nothing_changed(monkeypatch):
    box = box_profile.profile()
    assert box_profile.drift(json.loads(json.dumps(box)), box) == []


def test_profile_is_json_serialisable(monkeypatch):
    """It is written to disk and read back by --check; a non-JSON value breaks that."""
    monkeypatch.setattr(box_profile, "_torch_accelerators", _no_torch)
    json.dumps(box_profile.profile())
