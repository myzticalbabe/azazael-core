#!/usr/bin/env python3
"""fast_json.py — Rust-backed JSON utilities using msgspec/orjson.

Drop-in replacements for json.loads/dumps that are 5-10x faster.
Uses msgspec (Rust-backed) as primary, orjson as fallback, json as last resort.

Usage:
    from fast_json import loads, dumps, encode, decode
    data = loads('{"key": "value"}')
    text = dumps(data)
"""
from __future__ import annotations

try:
    import msgspec
    _BACKEND = "msgspec"
    def loads(s):
        if isinstance(s, (bytes, bytearray)):
            return msgspec.json.decode(s)
        return msgspec.json.decode(s.encode() if isinstance(s, str) else s)
    def dumps(obj):
        return msgspec.json.encode(obj).decode()
    def encode(obj):
        return msgspec.json.encode(obj)
    def decode(s):
        return msgspec.json.decode(s)
except ImportError:
    try:
        import orjson
        _BACKEND = "orjson"
        def loads(s):
            if isinstance(s, str):
                s = s.encode()
            return orjson.loads(s)
        def dumps(obj):
            return orjson.dumps(obj).decode()
        def encode(obj):
            return orjson.dumps(obj)
        def decode(s):
            if isinstance(s, str):
                s = s.encode()
            return orjson.loads(s)
    except ImportError:
        import json
        _BACKEND = "json"
        loads = json.loads
        dumps = json.dumps
        encode = lambda obj: json.dumps(obj).encode()
        decode = json.loads

def benchmark():
    import time
    import json
    data = {"nodes": [{"id": i, "label": f"node_{i}", "val": i%10} for i in range(10000)]}
    text = json.dumps(data)
    # Encode
    t0 = time.time()
    for _ in range(100):
        dumps(data)
    enc_time = time.time() - t0
    # Decode
    t0 = time.time()
    for _ in range(100):
        loads(text)
    dec_time = time.time() - t0
    print(f"Backend: {_BACKEND}")
    print(f"Encode 10K nodes x100: {enc_time:.3f}s")
    print(f"Decode 10K nodes x100: {dec_time:.3f}s")

if __name__ == "__main__":
    benchmark()
    print("fast_json: OK")
