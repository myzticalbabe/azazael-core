"""evidence.py -- native evidence locker with chain of custody.

Loose file paths rot: files move, edits go unnoticed, and a bundle
that points at a missing path proves nothing. The locker ingests a
file, a URL receipt, or a written note into content-addressed
storage (sha256-named), and every touch is appended to a
chain-of-custody log: who, when, what hash, what action.

- ingest_file(path): copy into the locker, return the locker id.
- ingest_text(note, label): store a written note (intake memo).
- ingest_receipt(receipt): store a factcheck-style receipt dict.
- verify(locker_id): rehash and compare; report INTACT vs TAMPERED.
- custody(locker_id): the ordered touch log for one item.
- resolve(ref): locker ids (ev:...) resolve to locker paths so
  redress.prove_case can check them like files.

Eve keeps; only you file. Tampering the locker is outside Eve's
threat model and stated here: the log detects, it does not prevent.
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

LOCKER_DIR = Path.home() / ".azazael" / "evidence"
_CUSTODY_LOG = "custody.jsonl"


def _ldir():
    LOCKER_DIR.mkdir(parents=True, exist_ok=True)
    return LOCKER_DIR


def _log(action, locker_id, digest, detail=""):
    entry = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "action": action, "id": locker_id, "sha256": digest,
             "detail": detail}
    with open(_ldir() / _CUSTODY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _store_bytes(data, label):
    digest = hashlib.sha256(data).hexdigest()
    lid = "ev:%s" % digest[:16]
    dest = _ldir() / (digest + ".bin")
    if not dest.is_file():
        dest.write_bytes(data)
        _log("ingest", lid, digest, label)
    else:
        _log("ingest_duplicate", lid, digest, label)
    meta = {"id": lid, "sha256": digest, "bytes": len(data),
            "label": label,
            "stored_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime())}
    with open(_ldir() / (digest + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def ingest_file(path):
    """Ingest a file. Returns the locker meta (with id)."""
    p = Path(path)
    if not p.is_file():
        raise ValueError("no such file: %s" % path)
    return _store_bytes(p.read_bytes(), "file:%s" % p.name)


def ingest_text(note, label="intake-note"):
    """Ingest a written note. Returns the locker meta."""
    if not note or not note.strip():
        raise ValueError("nothing to ingest: empty note")
    return _store_bytes(note.encode("utf-8"), label)


def ingest_receipt(receipt):
    """Ingest a factcheck-style receipt dict. Returns the locker meta."""
    if not isinstance(receipt, dict) or "sha256" not in receipt:
        raise ValueError("not a receipt: need a dict with sha256")
    return _store_bytes(
        json.dumps(receipt, sort_keys=True).encode("utf-8"),
        "receipt:%s" % receipt.get("url", "?")[:80])


def verify(locker_id):
    """Rehash and compare. Returns INTACT / TAMPERED / MISSING."""
    meta = _meta_for(locker_id)
    if not meta:
        return {"id": locker_id, "state": "MISSING"}
    blob = _ldir() / (meta["sha256"] + ".bin")
    if not blob.is_file():
        _log("verify_missing", locker_id, meta["sha256"])
        return {"id": locker_id, "state": "MISSING"}
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    state = "INTACT" if digest == meta["sha256"] else "TAMPERED"
    _log("verify_%s" % state.lower(), locker_id, digest)
    return {"id": locker_id, "state": state, "sha256": digest}


def custody(locker_id):
    """Ordered custody touches for one locker id."""
    out = []
    log = _ldir() / _CUSTODY_LOG
    if not log.is_file():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception as exc:
            note_failure("evidence:custody-parse", exc)
            continue
        if e.get("id") == locker_id:
            out.append(e)
    return out


def list_items():
    """All locker metas, newest first."""
    metas = []
    if not LOCKER_DIR.is_dir():
        return metas
    for p in sorted(LOCKER_DIR.glob("*.json")):
        if p.name == _CUSTODY_LOG:
            continue
        try:
            metas.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            note_failure("evidence:list", exc)
    return metas


def resolve(ref):
    """Locker ids (ev:...) resolve to locker blob paths; else the ref."""
    if isinstance(ref, str) and ref.startswith("ev:"):
        meta = _meta_for(ref)
        if meta:
            blob = _ldir() / (meta["sha256"] + ".bin")
            if blob.is_file():
                return str(blob)
    return ref


def _meta_for(locker_id):
    if not isinstance(locker_id, str) or not locker_id.startswith("ev:"):
        return None
    if not LOCKER_DIR.is_dir():
        return None
    for p in LOCKER_DIR.glob("*.json"):
        if p.name == _CUSTODY_LOG:
            continue
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("evidence:meta", exc)
            continue
        if meta.get("id") == locker_id:
            return meta
    return None
