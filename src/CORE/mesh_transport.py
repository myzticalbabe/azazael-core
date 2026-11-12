# coding: utf-8
"""mesh_transport.py — real data plane for the neural mesh.

Length-prefixed JSON envelopes over TCP loopback, one listener per env,
delivery receipts. Stdlib-only. MeshRouterWorm.send() delivers through this;
route() keeps its scoring-only contract for backward compatibility.

Wire protocol for mesh.py's fabric; not the mesh itself.

TOPOLOGY LIMIT (documented 2026-09-27, so the next audit does not re-find it
as a defect): the default bind is 127.0.0.1, so cross-HOST delivery (bare
metal, remote kali) cannot work as deployed -- only envs sharing a loopback
(WSL2 localhost forwarding) can form a seam. Opening beyond loopback requires
``require_auth``: a non-loopback ``bind_addr`` without authenticated
envelopes is refused at listen() by design, because the wire is otherwise
trust-by-port -- anyone reaching the port could inject mesh state.

Hardening (2026-09-27): envelopes are size-capped (a peer-supplied length
prefix must never drive allocation), untrusted envelopes are refused before
the inbox when auth is required, and an undrained inbox spills to disk at
shutdown instead of vanishing with the process.
"""
import json
import socket
import struct
import threading
import time
import uuid
import zlib
from typing import List
import os
from pathlib import Path

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ENV_PORTS = {
    "windows": 18210,
    "wsl2-ubuntu": 18211,
    "wsl2-kali": 18212,
    "bare": 18213,
    "kali": 18214,
}
BASE_PORT = 18220  # fallback digest range start for unknown envs

# Peer-supplied frame cap: the 4-byte length prefix must never drive an
# unbounded allocation (audit 2026-09-27). 1 MiB is ~100x a fat gossip
# envelope; anything larger is a broken or hostile peer.
MAX_ENVELOPE = 1 * 1024 * 1024
DEFAULT_BIND = "127.0.0.1"


def _is_loopback(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "localhost")


def env_host(env: str) -> str:
    """Peer HOST for an env (2026-09-27: cross-host topology).

    Default 127.0.0.1 -- the same-loopback topology. WSL2 NAT does NOT share
    loopback with the Windows host, so a WSL-side sender talking to the
    windows peer must be given the host address explicitly, e.g.
    AZAZAEL_MESH_HOSTS='{"windows": "172.26.16.1"}' (the default gateway
    seen from inside WSL). Mirrored networking does not need this. Unknown
    envs and malformed tables fall back to loopback; the port map is
    unchanged and auth still governs non-loopback binds.
    """
    raw = os.environ.get("AZAZAEL_MESH_HOSTS")
    if raw:
        try:
            table = json.loads(raw)
            host = str(table.get(env) or "") if isinstance(table, dict) else ""
            if host:
                return host
        except (ValueError, TypeError) as exc:
            note_failure("mesh_transport:L68", exc) # a broken table must not break delivery; loopback is the truth
    return "127.0.0.1"


def _spill_path():
    import os
    override = os.environ.get("AZAZAEL_MESH_INBOX")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "state" / "mesh_inbox.jsonl"


def spill_inbox(env: str, messages: list) -> int:
    """Append undelivered envelopes to disk so a restart cannot lose them."""
    if not messages:
        return 0
    path = _spill_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps({"env": env, "msg": msg}, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_spilled(env: str, limit: int = 1000) -> list:
    """Return this env's spilled envelopes and remove them from the file."""
    path = _spill_path()
    if not path.exists():
        return []
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, ValueError) as exc:
        note_failure("mesh_transport:L104", exc)
        return []
    mine = [r.get("msg") for r in rows if r.get("env") == env and isinstance(r.get("msg"), dict)][:limit]
    rest = [r for r in rows if not (r.get("env") == env and isinstance(r.get("msg"), dict))]
    try:
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rest:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError as exc:
        note_failure("mesh_transport:L114", exc) # spill read-back is best-effort; a lost tail is logged by stats
    return mine

def port_for(env: str) -> int:
    if env in ENV_PORTS:
        return ENV_PORTS[env]
    # Audit 2026-09-27: this was `abs(hash(env)) % 100`. str.__hash__ draws
    # from PYTHONHASHSEED, so two processes disagreed on the port for the
    # same unknown env and could never form a seam. crc32 of the name is
    # deterministic across processes and platforms.
    return BASE_PORT + (zlib.crc32(env.encode("utf-8")) % 100)


def _recvall(conn, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


class MeshTransport:
    """Blocking send + background listener with inbox. One per env."""

    VERSION = "1.0.0"

    def __init__(self, env: str, port: int = 0,
                 bind_addr: str | None = None,
                 require_auth: bool | None = None):
        self.env = env
        self.port = port or port_for(env)
        self.bind_addr = bind_addr or DEFAULT_BIND
        # Auth is mandatory off-loopback: the interlock lives in listen().
        self.require_auth = (not _is_loopback(self.bind_addr)
                             if require_auth is None else bool(require_auth))
        self._inbox: list[dict] = []
        self._lock = threading.Lock()
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._oldest_inbox_ts: float | None = None
        self.sent = 0
        self.received = 0
        self.errors = 0
        self.rejected = 0
        self.spilled = 0
        # A restart must not lose what the last process received but nobody
        # drained (audit 2026-09-27): adopt the spilled backlog first.
        for msg in load_spilled(self.env):
            self._inbox.append(msg)
        if self._inbox:
            self._oldest_inbox_ts = float(self._inbox[0].get("ts") or time.time())

    # -- listener ------------------------------------------------------
    def listen(self) -> int:
        if self._thread and self._thread.is_alive():
            return self.port
        if not _is_loopback(self.bind_addr) and not self.require_auth:
            raise RuntimeError(
                "refusing to bind %s without require_auth: the wire is "
                "trust-by-port and must not be exposed unauthenticated"
                % self.bind_addr)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_addr, self.port))
        srv.listen(16)
        srv.settimeout(0.5)
        self._srv = srv
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name=f"mesh-{self.env}")
        self._thread.start()
        return self.port

    def _serve(self):
        while self._srv:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout as exc:
                note_failure("mesh_transport:L193", exc)
                continue
            except OSError as exc:
                note_failure("mesh_transport:L195", exc)
                break
            try:
                with conn:
                    raw = _recvall(conn, 4)
                    (ln,) = struct.unpack("!I", raw)
                    if ln > MAX_ENVELOPE:
                        # Never allocate what a peer merely claims to send.
                        # Counted as an error; the connection is dropped and
                        # the listener keeps serving.
                        raise ValueError("oversize frame: %d > %d" % (ln, MAX_ENVELOPE))
                    msg = json.loads(_recvall(conn, ln).decode("utf-8"))
                    ok, reason = True, ""
                    if self.require_auth:
                        ok, reason = self._verify_trust(msg)
                    if ok:
                        with self._lock:
                            self._inbox.append(msg)
                            self.received += 1
                            if self._oldest_inbox_ts is None:
                                self._oldest_inbox_ts = float(msg.get("ts") or time.time())
                    else:
                        with self._lock:
                            self.rejected += 1
                    receipt = json.dumps({"id": msg.get("id"),
                                          "ok": ok,
                                          "reason": reason,
                                          "at": time.time()}).encode()
                    conn.sendall(struct.pack("!I", len(receipt)) + receipt)
            except Exception as exc:
                note_failure("mesh_transport:L224", exc)
                with self._lock:
                    self.errors += 1

    @staticmethod
    def _verify_trust(msg: dict) -> tuple[bool, str]:
        """Envelope auth: delegate to the spine's provenance gate, STRICTER.

        Reuses ``spine_registry.check_gossip_trust`` (Ed25519 over the
        canonical payload + monotonic seq + TOFU key pinning) so the transport
        does not invent a second trust model -- but enforces a stricter bar
        than the ambient gossip POLICY, which may be warn/off and accept
        unsigned payloads (measured 2026-09-27: an unsigned envelope passed
        under the default policy). ``require_auth`` means what it says: only
        an actual cryptographic verification (verdict "verified") admits an
        envelope. Skipped / unavailable / missing / invalid all fail closed.
        """
        try:
            from CORE import spine_registry as _spine
            payload = msg.get("payload")
            if not isinstance(payload, dict) or not str(payload.get("signature") or ""):
                return False, "unsigned envelope"
            trust = _spine.check_gossip_trust(payload)
            verdict = str(trust.get("signature") or "")
            if verdict != "verified" or not trust.get("ok"):
                return False, str(trust.get("reason") or verdict or "untrusted")
            return True, ""
        except Exception as exc:
            note_failure("mesh_transport:L251", exc)
            return False, "trust check unavailable: %s" % str(exc)[:80]

    def close(self):
        srv, self._srv = self._srv, None
        if srv:
            try:
                srv.close()
            except Exception as exc:
                note_failure("mesh_transport:L259", exc)
        with self._lock:
            leftover, self._inbox = self._inbox, []
        self.spilled += spill_inbox(self.env, leftover)

    # -- client --------------------------------------------------------
    def send(self, dst_env: str, payload: dict,
             timeout: float = 10.0, port: int | None = None) -> dict:
        """Deliver one envelope; returns receipt or error dict.

        Every result carries a typed ``status`` (audit 2026-09-27) so callers
        can separate route signal from infrastructure noise:

        * ``delivered``     -- receipt matched; the seam worked.
        * ``peer_rejected`` -- bytes moved but the receipt was wrong: the peer
                               (or a stranger on the port) is broken.
        * ``env_error``     -- connect/send/recv failed (refused, timeout,
                               reset). Says nothing about route quality.

        ``learnable`` is True only for the first two. A learner must never
        consume ``env_error`` as a policy outcome -- a missing listener is not
        a bad route. ``port`` overrides the canonical ``port_for(dst_env)``
        lookup (tests, non-standard deployments).
        """
        msg = {"id": uuid.uuid4().hex[:12], "src": self.env, "dst": dst_env,
               "ts": time.time(), "payload": payload}
        body = json.dumps(msg).encode()
        if len(body) > MAX_ENVELOPE:
            # Local policy refusal, not route signal: learnable=False.
            with self._lock:
                self.errors += 1
            return {"success": False, "id": msg["id"],
                    "error": "oversize envelope: %d > %d" % (len(body), MAX_ENVELOPE),
                    "status": "env_error", "learnable": False}
        target_port = port or port_for(dst_env)
        try:
            with socket.create_connection((env_host(dst_env), target_port),
                                          timeout=timeout) as conn:
                conn.settimeout(timeout)
                conn.sendall(struct.pack("!I", len(body)) + body)
                raw = _recvall(conn, 4)
                (ln,) = struct.unpack("!I", raw)
                receipt = json.loads(_recvall(conn, ln).decode("utf-8"))
                with self._lock:
                    self.sent += 1
                ok = receipt.get("id") == msg["id"] and receipt.get("ok")
                return {"success": bool(ok), "id": msg["id"],
                        "receipt": receipt,
                        "status": "delivered" if ok else "peer_rejected",
                        "learnable": True}
        except Exception as e:
            note_failure("mesh_transport:L310", e)
            with self._lock:
                self.errors += 1
            return {"success": False, "id": msg["id"], "error": str(e)[:120],
                    "status": "env_error", "learnable": False}

    def drain(self) -> list[dict]:
        with self._lock:
            msgs, self._inbox = self._inbox, []
            self._oldest_inbox_ts = None  # age is measured from the oldest UNDRAINED
        return msgs

    def stats(self) -> dict:
        with self._lock:
            oldest = None
            if self._inbox and self._oldest_inbox_ts:
                oldest = round(time.time() - self._oldest_inbox_ts, 1)
            return {"env": self.env, "port": self.port,
                    "bind_addr": self.bind_addr,
                    "require_auth": self.require_auth,
                    "sent": self.sent, "received": self.received,
                    "errors": self.errors, "rejected": self.rejected,
                    "spilled": self.spilled,
                    "inbox": len(self._inbox),
                    "oldest_inbox_age_s": oldest}
