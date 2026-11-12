# coding: utf-8
"""Round 4 live repro (Engram #538): ClusterWORMZ production-scale run through
the SparseMoE remediation loop (#524).

Read-only on source: the REAL DataPipelineWorm runs against the live store
(the store's real contents are the point — including how many rows the
retention passes left behind). No durable writes: versioner store off,
persist noop, DLQ in a tempdir. New file; nothing under src/ is edited.
"""

import hashlib
import json
import os
import sys
import sysconfig
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

CUTTING_EDGE = REPO / "src/CORE/WORMS/expansion/cutting_edge_model.py"
WORK_STEALING = REPO / "src/CORE/parallel/work_stealing.py"

REPORT_PATH = Path("C:/Temp/round4_report.json")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    gil = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    print("interpreter: %s gil_disabled=%s" % (sys.version.split()[0], gil),
          flush=True)
    if not gil:
        print("REFUSED: run with $env:GIL_DISABLED='1' on py315t", flush=True)
        return 2
    hashes_before = {
        "cutting_edge_model.py": sha256_of(CUTTING_EDGE),
        "work_stealing.py": sha256_of(WORK_STEALING),
    }
    print("hashes-before: %s" % json.dumps(hashes_before), flush=True)

    from CORE.NeuralPipeline import NeuralPipeline
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue

    tmpdir = tempfile.mkdtemp(prefix="round4-dlq-")
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False,
                              use_cutting_edge=True)
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None
    pipeline.dlq = DeadLetterQueue(
        storage_path=Path(tmpdir) / "dead_letters.json")

    started = time.perf_counter()
    try:
        run = pipeline.run_end_to_end(source="ClusterWORMZ", limit=5000,
                                      register_model=True)
    except Exception as exc:
        print("RUN-FAILED: %s: %s" % (type(exc).__name__, exc), flush=True)
        traceback.print_exc()
        return 3
    elapsed = time.perf_counter() - started

    hashes_after = {
        "cutting_edge_model.py": sha256_of(CUTTING_EDGE),
        "work_stealing.py": sha256_of(WORK_STEALING),
    }
    void = hashes_before != hashes_after
    print("hashes-after: %s" % json.dumps(hashes_after), flush=True)
    print("void (source moved mid-run): %s" % void, flush=True)

    serializable = json.loads(json.dumps(run, default=str))
    report = {
        "interpreter": sys.version.split()[0],
        "gil_disabled": gil,
        "hashes_before": hashes_before,
        "hashes_after": hashes_after,
        "void": void,
        "wall_seconds": round(elapsed, 2),
        "run_top_level_keys": sorted(serializable.keys()),
        "run": serializable,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")

    get = serializable.get
    print("rows_loaded: %s" % (get("rows_loaded", get("n_rows", get("rows")))),
          flush=True)
    print("wall_seconds: %.2f" % elapsed, flush=True)
    for key in ("routing", "rebalance", "rebalance_report", "experts",
                "entropy_normalised", "max_skew", "loss_before",
                "loss_after", "loss_delta", "status"):
        if key in serializable:
            print("%s: %s" % (key, json.dumps(serializable[key],
                                              default=str)[:600]), flush=True)
    print("report_written: %s" % REPORT_PATH, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
