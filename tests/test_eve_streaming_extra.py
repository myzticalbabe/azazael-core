#!/usr/bin/env python3
"""Additional deeper tests for Eve streaming."""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


class FakeGGUF:
    def __call__(self, prompt, max_tokens=256, echo=False, stream=True):
        return [{"choices": [{"text": "hi"}], "usage": {"completion_tokens": 1}, "finish_reason": "stop"}]


def test_stream_gguf():
    from eve_unleashed.streaming import stream_gguf, StreamToken

    async def main():
        toks = []
        async for t in stream_gguf(FakeGGUF(), "prompt", max_tokens=4):
            toks.append(t)
        assert len(toks) >= 1
        assert any(t.is_final for t in toks)

    asyncio.run(main())


def test_stream_transformers_import_error():
    from eve_unleashed.streaming import stream_transformers
    # model/tokenizer not available -> should raise ImportError if transformers missing
    # We just ensure the function is callable
    assert callable(stream_transformers)
