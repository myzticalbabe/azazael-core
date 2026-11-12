# coding: utf-8
"""gguf_census.py — fingerprint a GGUF BEFORE first load.

Reads only the header/KV metadata (no tensor load): magic, arch, context,
rope dimension_sections, quant mix. Verdicts a file against known backend
limits so a malformed quant can never cost a 400s hang again.
(Eve-V2 incident: 3-section rope where 4 required.)
"""
import struct
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

KNOWN = {
    # arch -> {rope_sections: expected_len or None (don't care)}
    "qwen35": {"rope_sections": 4},
    "qwen3": {"rope_sections": None},
    "qwen2": {"rope_sections": None},
    "llama": {"rope_sections": None},
    "gemma4": {"rope_sections": None},
}


def fingerprint(path: str) -> dict:
    """Parse GGUF header + KV store. Never loads tensors."""
    rec = {"path": path, "magic_ok": False}
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            rec["magic_ok"] = magic == b"GGUF"
            if not rec["magic_ok"]:
                rec.update(loadable=False, reason="bad-magic")
                return rec
            _ver, _nt, _nk = struct.unpack("<IQQ", f.read(20))
            n_tensors, n_kv = _nt, _nk
            rec["n_tensors"] = n_tensors
            rec["n_kv"] = n_kv
            kv = {}
            for _ in range(n_kv):
                ln = struct.unpack("<Q", f.read(8))[0]
                key = f.read(ln).decode("utf-8", "replace")
                typ = struct.unpack("<I", f.read(4))[0]
                kv[key] = _skip_value(f, typ)
            arch = kv.get("general.architecture", "?")
            rec["arch"] = arch
            for k in ("general.name",):
                if k in kv:
                    rec["name"] = str(kv[k])[:60]
            rope_key = f"{arch}.rope.dimension_sections"
            if rope_key in kv:
                rec["rope_sections"] = list(kv[rope_key])
    except Exception as e:
        note_failure("gguf_census:L49", e)
        rec.update(loadable=False, reason="header-read-fail:" + str(e)[:80])
        return rec
    exp = (KNOWN.get(rec.get("arch", ""), {}) or {}).get("rope_sections")
    if exp is not None and "rope_sections" in rec and len(rec["rope_sections"]) != exp:
        rec.update(loadable=False,
                   reason=(f"rope_mismatch: got {len(rec['rope_sections'])}, "
                           f"need {exp} (Eve-class malformed quant)"))
        return rec
    rec.update(loadable=True, reason="header-ok")
    return rec


def _skip_value(f, typ):
    import array
    if typ == 0:
        return struct.unpack("<B", f.read(1))[0]
    if typ == 1:
        return struct.unpack("<b", f.read(1))[0]
    if typ in (2, 3):
        return struct.unpack("<H" if typ == 2 else "<h", f.read(2))[0]
    if typ in (4, 5):
        return struct.unpack("<I" if typ == 4 else "<i", f.read(4))[0]
    if typ == 6:
        return struct.unpack("<f", f.read(4))[0]
    if typ == 7:
        return bool(struct.unpack("<B", f.read(1))[0])
    if typ == 8:
        ln = struct.unpack("<Q", f.read(8))[0]
        return f.read(ln).decode("utf-8", "replace")
    if typ == 10:
        return struct.unpack("<Q", f.read(8))[0]
    if typ == 11:
        return struct.unpack("<q", f.read(8))[0]
    if typ == 12:
        return struct.unpack("<d", f.read(8))[0]
    if typ == 9:  # array
        atype, n = struct.unpack("<IQ", f.read(12))
        if atype == 8:
            out = []
            for _ in range(n):
                ln = struct.unpack("<Q", f.read(8))[0]
                out.append(f.read(ln).decode("utf-8", "replace"))
            return out
        sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4,
                 7: 1, 10: 8, 11: 8, 12: 8}
        if atype in (4, 5):
            return list(struct.unpack("<%dI" % n if atype == 4 else "<%di" % n,
                                      f.read(4 * n)))
        if atype in (10, 11):
            return list(struct.unpack("<%dQ" % n if atype == 10 else "<%dq" % n,
                                      f.read(8 * n)))
        if atype == 12:
            return list(struct.unpack("<%dd" % n, f.read(8 * n)))
        f.read(n * sizes.get(atype, 8))
        return []
    return None
