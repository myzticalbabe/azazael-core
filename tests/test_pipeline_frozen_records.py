# coding: utf-8
"""NeuralPipeline frozen-records seam (#854 acceptance).

Proves `run_end_to_end(records=...)` skips store extraction and threads
the frozen rows to the training dispatch, and that `records=None` keeps
the extraction path. Training itself is stubbed at the dispatch
boundary; featurization runs for real. No live store, no GPU.
"""

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.NeuralPipeline import NeuralPipeline  # noqa: E402
from CORE.WORMS.dead_letter_queue import DeadLetterQueue  # noqa: E402


FROZEN = [
    {"event_type": "hook:probe", "severity": "warning", "source": "probe",
     "data": {"ok": False, "hook": "probe"}},
    {"event_type": "hook:probe", "severity": "info", "source": "probe",
     "data": {"ok": True, "hook": "probe"}},
]


class _ExplodingDataPipe:
    """Raises on any extraction: proves the frozen path never extracts."""

    def split_memories(self, **kwargs):
        raise AssertionError("extraction must be skipped")

    def extract_from_memory(self, **kwargs):
        raise AssertionError("extraction must be skipped")


class _RowDataPipe:
    """Returns fixed rows and records that extraction ran."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def split_memories(self, **kwargs):
        self.calls.append("split")
        return {"train": list(self.rows), "test": [],
                "stats": {"total": len(self.rows)}}

    def extract_from_memory(self, **kwargs):
        self.calls.append("extract")
        return list(self.rows)


def _pipeline(tmpdir, data_pipe):
    pipeline = NeuralPipeline(max_workers=4, llm_augmentation=False)
    pipeline.data_pipe = data_pipe
    pipeline.dlq = DeadLetterQueue(
        storage_path=Path(tmpdir) / "dead_letters.json")
    pipeline.versioner._store = False
    pipeline._persist_run = lambda metrics: None
    return pipeline


def _stub_dispatch(agent):
    seen = {}

    def fake_train_agent(agent_type, source, event_type, train_records,
                         test_records, features, labels, feat_meta,
                         worm_features, register_model=False, **kwargs):
        seen["agent_type"] = agent_type
        seen["train_records"] = train_records
        return {"trained": True, "stubbed": True}

    agent._train_agent = fake_train_agent
    return seen


def test_frozen_records_skip_extraction():
    with tempfile.TemporaryDirectory() as td:
        agent = _pipeline(td, _ExplodingDataPipe())
        seen = _stub_dispatch(agent)
        out = agent.run_end_to_end(source="probe", agent_type="SB3",
                                   records=list(FROZEN),
                                   register_model=False)
        assert seen["train_records"] == FROZEN
        assert seen["agent_type"] == "SB3"
        assert out.get("agent_type") == "SB3"


def test_none_records_still_extracts():
    with tempfile.TemporaryDirectory() as td:
        pipe = _RowDataPipe(FROZEN)
        agent = _pipeline(td, pipe)
        seen = _stub_dispatch(agent)
        out = agent.run_end_to_end(source="probe", agent_type="SB3",
                                   records=None, register_model=False)
        assert pipe.calls == ["split"]
        assert seen["train_records"] == FROZEN
        assert out.get("agent_type") == "SB3"


if __name__ == "__main__":
    test_frozen_records_skip_extraction()
    print("frozen: PASS")
    test_none_records_still_extracts()
    print("none: PASS")
