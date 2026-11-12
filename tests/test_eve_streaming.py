#!/usr/bin/env python3
"""Deeper tests for Eve streaming helpers."""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_stream_token_and_result():
    from eve_unleashed.streaming import StreamToken, StreamResult
    t = StreamToken(text="hi", index=0)
    assert t.text == "hi"
    r = StreamResult()
    r.append(StreamToken(text="hello", is_final=False))
    r.append(StreamToken(text=" world", is_final=True, latency_ms=100.0))
    assert r.full_text == "hello world"
    assert r.token_count == 2
    assert r.total_time_ms == 100.0
    assert r.tokens_per_second == 20.0


def test_stream_result_no_final():
    from eve_unleashed.streaming import StreamResult
    r = StreamResult()
    r.append(__import__("eve_unleashed.streaming", fromlist=["StreamToken"]).StreamToken(text="x"))
    assert r.tokens_per_second == 0.0


def test_stream_native():
    from eve_unleashed.streaming import stream_native, StreamToken

    class R:
        predictions = [0.1, 0.2]
        latency_ms = 5.0

    async def main():
        toks = []
        async for t in stream_native(R()):
            toks.append(t)
        assert len(toks) == 1
        assert toks[0].is_final is True
        assert "0.1" in toks[0].text

    asyncio.run(main())


def test_stream_native_no_attrs():
    from eve_unleashed.streaming import stream_native

    async def main():
        toks = []
        async for t in stream_native("raw"):
            toks.append(t)
        assert toks[0].text == "raw"
        assert toks[0].latency_ms == 0

    asyncio.run(main())
