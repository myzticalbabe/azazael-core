"""Case files: a hash-chained custody log for everything a run reads and produces.

**Adopted, not decorated.** This is the forensic half of the design: an assessment is
a finding about an applicant, and a finding is only worth what its chain of custody
is worth. So a case records, in order and without gaps:

* the sha256 of every input as it was read (an RFP that changes after the verdict is a
  different RFP, and the case can say so);
* every step's payload digest, with the previous entry's hash folded into its own;
* a recomputation on demand that fails at the first broken link and names it.

The chain is the same idea as the audit ledger this tree already runs (JSONL,
hash-chained), reduced to what one process can verify on its own. It is append-only by
construction: `record` never edits a line, and `verify_case` recomputes every hash from
the stored bytes, so a hand-edited digest breaks the link after it.

What it deliberately does not do: sign anything. A signature would need a key, and a
key in a user's home directory is a lock whose key is taped to it. The claim this
supports is "these bytes, in this order, unaltered since they were written" — and that
claim is checked by recomputation, exactly as `verify_seal` checks an artefact.

The bypass kit is a separate concern, and it already exists in `llm.py`: endpoint
normalisation across `/v1` and bare hosts, both `/models` shapes, retry on transport
and 5xx only, and a truncated reply re-asked with a larger budget. It is documented in
the plan rather than duplicated here, because a second copy of a fallback chain is how
two different behaviours get the same name.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CASE_FILE = "case.json"
LEDGER_FILE = "custody.jsonl"


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def digest(payload):
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def file_digest(path):
    handle = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            handle.update(chunk)
    return handle.hexdigest()


def init_case(case_dir, inputs=None, case_id=None):
    """Create a case, hashing each input as read. Refuses to overwrite a live case."""
    root = Path(case_dir)
    if (root / CASE_FILE).exists():
        raise ValueError("case already exists at %s; refusing to overwrite its chain" % root)
    root.mkdir(parents=True, exist_ok=True)
    inputs = inputs or {}
    inventory = []
    for name, path in sorted(inputs.items()):
        target = Path(path)
        if not target.exists():
            raise ValueError("input %r not found at %s" % (name, target))
        inventory.append({"name": name, "path": str(target),
                          "bytes": target.stat().st_size, "sha256": file_digest(target)})
    manifest = {"case_id": case_id or time.strftime("case-%Y%m%dT%H%M%S"),
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "inputs": inventory,
                "chain": "sha256(prev_hash + payload_digest)"}
    (root / CASE_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    record(root, "case_opened", {"inputs": inventory, "case_id": manifest["case_id"]})
    return manifest


def record(case_dir, kind, payload):
    """Append one link. Returns the entry, including the hash the next one chains to."""
    root = Path(case_dir)
    ledger = root / LEDGER_FILE
    entries = read_ledger(root)
    previous = entries[-1]["hash"] if entries else "0" * 64
    payload_digest = digest(payload)
    entry = {"seq": len(entries), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "kind": kind, "payload_digest": payload_digest, "prev": previous}
    entry["hash"] = digest({"prev": previous, "payload_digest": payload_digest,
                            "seq": entry["seq"], "kind": kind, "ts": entry["ts"]})
    with open(ledger, "a", encoding="utf-8") as out:
        out.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def read_ledger(case_dir):
    ledger = Path(case_dir) / LEDGER_FILE
    if not ledger.exists():
        return []
    entries = []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def verify_case(case_dir):
    """Recompute the chain. The first broken link is named, not summarised away."""
    root = Path(case_dir)
    if not (root / CASE_FILE).exists():
        return {"ok": False, "detail": "no case at %s" % root, "entries": 0}
    entries = read_ledger(root)
    previous = "0" * 64
    for position, entry in enumerate(entries):
        if entry.get("seq") != position:
            return {"ok": False, "entries": len(entries),
                    "detail": "sequence gap at position %d (recorded seq %r)"
                              % (position, entry.get("seq"))}
        if entry.get("prev") != previous:
            return {"ok": False, "entries": len(entries),
                    "detail": "broken link at seq %d: prev %s… is not %s…"
                              % (position, str(entry.get("prev"))[:12], previous[:12])}
        expected = digest({"prev": entry.get("prev"),
                           "payload_digest": entry.get("payload_digest"),
                           "seq": entry.get("seq"), "kind": entry.get("kind"),
                           "ts": entry.get("ts")})
        if expected != entry.get("hash"):
            return {"ok": False, "entries": len(entries),
                    "detail": "hash mismatch at seq %d: entry was altered after it was written"
                              % position}
        previous = entry["hash"]
    return {"ok": True, "entries": len(entries),
            "detail": "chain intact: %d link(s), head %s…" % (len(entries), previous[:12]),
            "head": previous}
