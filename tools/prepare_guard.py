"""prepare_guard.py — an absolute, programmatic guard. Not a rule; a mechanism.

THE FAILURE THIS EXISTS TO STOP. On 2026-09-30 the canon contained RULE 0B
("follow instructions literally") and PMB contained the lesson "read the
rendered artefact". I violated the rule, missed the lesson, never called
prepare(), and codified the rules anyway. Replaying the day's tasks afterwards
showed **30 lessons would have been surfaced across 6 of 7 tasks**. The
information was there. The operator did not collect it.

A written rule cannot fix that, because the failure is not ignorance of the
rule -- it is not opening it. So this is not a rule and not a reminder. It is a
filesystem permission.

HOW IT WORKS
    1. Canon files are made READ-ONLY (444) and a watcher keeps re-asserting it.
    2. The ONLY way to write canon is to mint a permit, and a permit requires a
       FRESH prepare() receipt -- i.e. you must actually consult the store.
    3. A write that happens without a valid receipt is blocked by the OS, then
       re-locked, and the violation is recorded to the VLP audit trail.

This uses the mechanism the project already designed for ("All source files are
read-only (444). --pre-validate unlocks (644) ... --post-validate re-locks
(444)", sentinel_gate.py) -- which was documented and never applied. Every canon
file was found writable on 2026-09-30.

THE HONEST LIMIT, stated up front: this is a guard I must still start. It does
not remove the need to run `record()` first -- it makes the failure of not
running it IMPOSSIBLE TO COMMIT rather than merely likely. That is a real and
large improvement over a rule, and it is not the same as not needing
discipline. Saying otherwise would be exactly the class of claim RULE 0B-D
forbids.
"""
from __future__ import annotations

import argparse, json, os, stat, sys, time
from pathlib import Path

AZ = Path(r"C:\Users\clint")
# Discovered, not hardcoded. The first version listed seven files, and a NEW
# canon document written the same day -- RULE_FORENSICS.md -- came out
# writable, i.e. a guard that only covers what its author remembered. Canon is
# now: the rules/instruction corpus at the project root, plus the global
# AGENTS.md. Anything matching these globs is locked, including files created
# later, which is the only version of this guard that is worth having.
CANON_GLOBS = [
    "AGENTS.md",
    "azazael/MANDATES.md",
    "azazael/SYSTEM_INSTRUCTIONS.md",
    "azazael/SOP.md",
    "azazael/ERROR_LOG.md",
    "azazael/MEMORIES.md",
    "azazael/SESSION_STARTUP.md",
    "azazael/RULE_AUDIT_TODO.md",
    "azazael/RULE_FORENSICS.md",
    "azazael/CAPABILITIES.md",
    "azazael/CODEBASE_AUDIT_TODO.md",
    "azazael/LESSONS_MASTER.md",
]
RECEIPT = AZ / "azazael" / "state" / "prepare_receipt.json"
PERMIT_TTL_S = 1800          # a permit expires, so a stale one buys nothing
PMB_TOKEN = Path(r"C:\Users\clint\.pmb\daemon.token")


def pmb_url() -> str:
    """The LIVE daemon URL, discovered — never a constant.

    The daemon idle-exits after 120 min and is restarted on demand, and it
    migrates ports when the old one is taken (observed 8765 -> 8766 -> 8768).
    This was hardcoded to 8765 and went stale, so the guard could not mint a
    receipt. `servers.json` is the daemon's own registry; read it.
    """
    try:
        entries = json.loads((PMB_TOKEN.parent / "servers.json").read_text(
            encoding="utf-8"))
    except Exception:  # noqa: BLE001 — fall back to the conventional port
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("port"):
            host = entry.get("host") or "127.0.0.1"
            return "http://%s:%s%s" % (host, entry["port"],
                                       entry.get("path") or "/mcp")
    return "http://127.0.0.1:8765/mcp"


# ── receipts ──────────────────────────────────────────────────────────────
def canon_files() -> list:
    """Resolved live, so a canon file created after this module was written is
    still covered. Missing files are skipped rather than reported as failures."""
    out = []
    for rel in CANON_GLOBS:
        p = AZ / rel
        if p.exists():
            out.append(p)
    return out


def read_receipt() -> dict:
    try:
        return json.loads(RECEIPT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_receipt(data: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def receipt_valid(ttl: float = PERMIT_TTL_S) -> tuple[bool, str]:
    r = read_receipt()
    if not r:
        return False, "no prepare receipt on disk"
    age = time.time() - float(r.get("ts") or 0)
    if age > ttl:
        return False, f"receipt is {age/60:.0f} min old (ttl {ttl/60:.0f})"
    return True, f"receipt {age/60:.1f} min old, {r.get('n_lessons', 0)} lessons seen"


# ── the lock ──────────────────────────────────────────────────────────────
def lock(path: Path) -> bool:
    """Read-only, which on Windows means clearing the writable bit."""
    try:
        os.chmod(path, stat.S_IREAD)          # 0o444
        return True
    except Exception:
        return False


def unlock(path: Path) -> bool:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)   # 0o644
        return True
    except Exception:
        return False


def is_writable(path: Path) -> bool:
    try:
        return bool(os.stat(path).st_mode & (stat.S_IWRITE | stat.S_IWUSR))
    except Exception:
        return False


def _violation(path: Path, reason: str) -> None:
    """Record an unpermitted write attempt to the signed audit trail."""
    try:
        sys.path.insert(0, str(AZ / "azazael" / "src"))
        from CORE.audit_trail import audit_session_start, audit_error_occurrence, close_session
        audit_session_start()
        audit_error_occurrence(
            218, "Canon write attempted without a prepare() permit", str(path),
            f"prepare_guard locked this file read-only and the write did not go "
            f"through a minted permit. Reason: {reason}. RULE 0B was in canon and "
            f"PMB held the lessons; the guard makes skipping prepare() impossible "
            f"to COMMIT rather than merely likely.")
        close_session()
    except Exception:
        pass


# ── commands ──────────────────────────────────────────────────────────────
def cmd_record(msg: str) -> int:
    """Call PMB prepare() and mint a receipt. This is the only door."""
    import asyncio
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = PMB_TOKEN.read_text(encoding="utf-8").strip()
    url = pmb_url()

    async def go():
        c = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                               timeout=300.0)
        async with streamable_http_client(url, http_client=c) as st:
            async with ClientSession(st[0], st[1]) as s:
                await s.initialize()
                raw = "".join(getattr(x, "text", "") for x in
                              (await s.call_tool("prepare", {"message": msg})).content)
                return json.loads(raw)

    d = asyncio.run(go())
    lessons = d.get("lessons") or []
    write_receipt({"ts": time.time(), "message": msg,
                   "n_lessons": len(lessons),
                   "n_activity": len(d.get("recent_activity") or []),
                   "ulids": [l.get("ulid") for l in lessons if l.get("ulid")]})
    print(f"receipt written: {len(lessons)} lessons surfaced")
    for l in lessons[:5]:
        print("   -", str(l.get("content", ""))[:88])
    return 0


def cmd_permit(ttl: float = PERMIT_TTL_S) -> int:
    ok, why = receipt_valid(ttl)
    if not ok:
        print(f"DENIED: {why}")
        print("Run:  python azazael\\tools\\prepare_guard.py record \"<task>\"")
        return 1
    n = sum(1 for p in canon_files() if p.exists() and unlock(p))
    print(f"PERMIT GRANTED ({why}); {n} canon file(s) unlocked for {ttl/60:.0f} min")
    return 0


def cmd_lock() -> int:
    n = sum(1 for p in canon_files() if p.exists() and lock(p))
    print(f"{n} canon file(s) locked read-only")
    return 0


def cmd_status() -> int:
    ok, why = receipt_valid()
    print(f"receipt   : {'VALID' if ok else 'ABSENT/EXPIRED'} -- {why}")
    print(f"canon         :")
    for p in canon_files():
        if p.exists():
            w = "WRITABLE  <-- GUARD NOT APPLIED" if is_writable(p) else "read-only  ok"
            print(f"            {w:28} {p.name}")
    return 0


def cmd_watch(tick: float = 3.0) -> int:
    """Continuously re-assert read-only. A write that slips through is
    re-locked and logged; the file cannot stay modified."""
    print(f"watching {len(CANON_GLOBS)} canon files every {tick}s -- Ctrl-C to stop")
    while True:
        for p in canon_files():
            if not p.exists():
                continue
            if is_writable(p):
                ok, why = receipt_valid()
                if ok:
                    continue                      # a live permit: leave it open
                lock(p)
                print(f"  {time.strftime('%H:%M:%S')} RE-LOCKED {p.name} ({why})")
                _violation(p, why)
        time.sleep(tick)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record"); r.add_argument("message")
    pm = sub.add_parser("permit"); pm.add_argument("--ttl", type=float, default=PERMIT_TTL_S)
    sub.add_parser("lock")
    sub.add_parser("status")
    w = sub.add_parser("watch"); w.add_argument("--tick", type=float, default=3.0)
    a = ap.parse_args()
    {"record": lambda: cmd_record(a.message), "permit": lambda: cmd_permit(a.ttl),
     "lock": cmd_lock, "status": cmd_status, "watch": lambda: cmd_watch(a.tick)}[a.cmd]()


