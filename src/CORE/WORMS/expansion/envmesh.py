# coding: utf-8
"""
NEURAL MESH — Category: ENVMESH (6 worms)
Per-env adapters. Split WSL2 was conflated: now wsl2-ubuntu vs wsl2-kali distinct.
Bare metal is only reachable via passthrough (SSH/vsock/mount) from WSL2/Win.

WinBridgeWorm, WslUbuntuWorm, WslKaliWorm, BarePassthroughWorm, GDriveLoomWorm, EnvBeaconV2Worm

Per-env adapters for the mesh fabric (mesh.py); not inference serving
(servemesh.py).
"""

import os
import platform
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("envmesh:L21", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

try:
    from CORE.freethreading import is_free_threaded
except ImportError as exc:
    note_failure("envmesh:L27", exc)
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
        except Exception as exc:
            note_failure("envmesh:L32", exc)
            return False


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as exc:
        note_failure("envmesh:L40", exc)
        return ""


def env_id_v2() -> str:
    """5-way env id: windows | wsl2-ubuntu | wsl2-kali | bare | kali.

    This name is the historical origin of the 5-way split (it fixed
    mesh._env_id() conflating Ubuntu-26.04 and Kali under WSL2 into one
    'wsl2' bucket). As of the 2026-09-15 audit it delegates to
    CORE.env_id.env_id() — the single canonical implementation — so the
    name survives for callers but the logic exists exactly once.

    - wsl2-ubuntu: /proc/version has microsoft, os-release NOT kali
    - wsl2-kali:   /proc/version has microsoft AND os-release has kali
    - kali:        bare (non-WSL) Kali
    - bare:        generic non-WSL non-Kali Linux (true bare metal OR vm;
                   use BarePassthroughWorm.probe() to split bare vs vm)
    """
    try:
        from CORE.env_id import env_id
        return env_id()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("envmesh:L62", exc)
        if os.name == "nt":
            return "windows"
        ver = _read("/proc/version").lower()
        is_kali = "kali" in _read("/etc/os-release").lower()
        if "microsoft" in ver:
            return "wsl2-kali" if is_kali else "wsl2-ubuntu"
        return "kali" if is_kali else "bare"


@register_worm(category="envmesh", priority=8, cooldown_seconds=20.0,
    description="Windows 11 host bridge: tasklist, G: mount, wsl -l -v")
class WinBridgeWorm:
    """W11 control plane: process + WSL distro + G: checks."""

    VERSION = "1.0.0"

    def __init__(self):
        self._lock = threading.Lock()

    def wsl_distros(self) -> dict[str, Any]:
        try:
            r = subprocess.run(["wsl", "-l", "-v"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            out = (r.stdout or "") + (r.stderr or "")
            has_ubuntu = "ubuntu-26.04" in out.lower() or "ubuntu" in out.lower()
            has_kali = "kali" in out.lower()
            return {"success": True, "raw_lines": len(out.splitlines()),
                    "ubuntu2604": has_ubuntu, "kali": has_kali}
        except Exception as e:
            note_failure("envmesh:L90", e)
            return {"success": False, "error": str(e)[:200]}

    def g_mount(self) -> dict[str, Any]:
        ok = os.path.exists(r"G:\source-of-truth")
        return {"success": True, "mounted": ok, "path": r"G:\source-of-truth"}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute",
                "result": {"env": env_id_v2(), "g": self.g_mount(),
                           "wsl": self.wsl_distros() if os.name == "nt" else "n/a"}}


@register_worm(category="envmesh", priority=8, cooldown_seconds=20.0,
    description="WSL2 Ubuntu-26.04 worker: 3.14t FT training, /mnt/g bridge")
class WslUbuntuWorm:
    """Ubuntu-26.04 training worker profile."""

    VERSION = "1.0.0"

    def profile(self) -> dict[str, Any]:
        return {"success": True, "env": env_id_v2(), "expect": "wsl2-ubuntu",
                "match": env_id_v2() == "wsl2-ubuntu",
                "ft": is_free_threaded(), "cpu": os.cpu_count(),
                "mnt_g": os.path.exists("/mnt/g/source-of-truth"),
                "role": "trainer", "at": datetime.now().isoformat()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.profile()}


@register_worm(category="envmesh", priority=7, cooldown_seconds=20.0,
    description="WSL2 Kali worker: recon-gated, quiet by default (arousal=recon)")
class WslKaliWorm:
    """Kali-under-WSL2 scanner profile; respects thalamus ArousalSwitch."""

    VERSION = "1.0.0"
    TOOLS = ("nmap", "masscan", "nikto", "sqlmap", "nuclei")

    def profile(self, arousal: str = "recon") -> dict[str, Any]:
        import importlib.util as _iu
        avail = {}
        for t in ("nmap",):
            try:
                avail[t] = _iu.find_spec(t) is not None
            except Exception as exc:
                note_failure("envmesh:L135", exc)
                avail[t] = False
        for b in ("nmap", "masscan", "nikto", "sqlmap"):
            avail[b + ":bin"] = any(
                os.path.exists(p + b) for p in ("/usr/bin/", "/bin/"))
        quiet = arousal != "recon"
        return {"success": True, "env": env_id_v2(), "expect": "wsl2-kali",
                "match": env_id_v2() == "wsl2-kali", "arousal": arousal,
                "active_scan_allowed": not quiet or arousal == "recon",
                "tools": avail, "at": datetime.now().isoformat()}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.profile()}


@register_worm(category="envmesh", priority=9, cooldown_seconds=30.0,
    description="Bare-metal passthrough: local kernel unreachable from WSL2/Win, use SSH/vsock/mount chain")
class BarePassthroughWorm:
    """Bare-metal kernel passthrough.

    WHY PASSTHROUGH: WSL2 (both Ubuntu-26.04 and Kali) runs on the Microsoft
    virtual kernel — /proc/version says microsoft, /dev/kmsg, eBPF, io_uring
    queue depth, /dev/dri GPU nodes are the VIRTUAL set, not the physical
    host's. True bare metal (physical Ubuntu-26.04) kernel is only reachable
    via: (1) SSH to the bare host, (2) vsock/hvsock from W11, (3) bind-mount
    of /mnt/g as the shared loom. This worm probes in that order and reports
    which path actually reaches a non-microsoft kernel.
    """

    VERSION = "1.0.0"

    def __init__(self, bare_ssh: str = ""):
        self._bare_ssh = bare_ssh or os.environ.get("AZAZAEL_BARE_SSH", "")
        self._lock = threading.Lock()

    def probe_local(self) -> dict[str, Any]:
        if os.name == "nt":
            return {"success": True, "path": "local", "virt": "hyperv_host",
                    "kernel": platform.platform()[:120],
                    "feats": {"kmsg": False, "dri": False, "ebpf_bpffs": False},
                    "is_bare_kernel": False,
                    "note": "Windows host kernel, not Linux bare metal"}
        ver = _read("/proc/version")
        cgroup = _read("/proc/1/cgroup").lower()
        virt = "wsl2" if "microsoft" in ver.lower() else (
            "container" if ("docker" in cgroup or os.path.exists("/.dockerenv")) else "bare_or_vm")
        feats = {
            "kmsg": os.path.exists("/dev/kmsg"),
            "dri": os.path.exists("/dev/dri"),
            "ebpf_bpffs": os.path.exists("/sys/fs/bpf"),
        }
        return {"success": True, "path": "local", "virt": virt,
                "kernel": ver.strip()[:120], "feats": feats,
                "is_bare_kernel": virt == "bare_or_vm" and "microsoft" not in ver.lower()}

    def probe_ssh(self, host: str | None = None) -> dict[str, Any]:
        target = host or self._bare_ssh
        if not target:
            return {"success": False, "path": "ssh", "error": "no AZAZAEL_BARE_SSH set"}
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                target, "cat /proc/version; echo ---; ls /dev/dri 2>&1 | head -3"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
            out = (r.stdout or "") + (r.stderr or "")
            is_bare = "microsoft" not in out.lower() and r.returncode == 0
            return {"success": r.returncode == 0, "path": f"ssh:{target}",
                    "is_bare_kernel": is_bare, "out": out[:400]}
        except Exception as e:
            note_failure("envmesh:L202", e)
            return {"success": False, "path": "ssh", "error": str(e)[:200]}

    def probe_vsock(self) -> dict[str, Any]:
        # hvsock from W11 to bare via TCP fallback probe on common daemon port
        try:
            s = socket.create_connection(("127.0.0.1", 8420), timeout=2)
            s.close()
            return {"success": True, "path": "vsock:8420", "pams_local": True}
        except Exception as e:
            note_failure("envmesh:L211", e)
            return {"success": False, "path": "vsock:8420", "error": str(e)[:120]}

    def probe(self) -> dict[str, Any]:
        local = self.probe_local()
        if local.get("is_bare_kernel"):
            return {"success": True, "via": "local", "local": local}
        ssh = self.probe_ssh()
        if ssh.get("is_bare_kernel"):
            return {"success": True, "via": ssh["path"], "local": local, "ssh": ssh}
        vsock = self.probe_vsock()
        return {"success": True, "via": "none-bare (confined to virtual kernel)",
                "local": local, "ssh": ssh, "vsock": vsock,
                "hint": "set AZAZAEL_BARE_SSH=user@bare-host to enable passthrough"}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.probe()}


@register_worm(category="envmesh", priority=6, cooldown_seconds=60.0,
    description="G: loom bridge: W11 G:/ <-> WSL /mnt/g shared state")
class GDriveLoomWorm:
    """Shared-state mount resolver + write probe (no destructive writes)."""

    VERSION = "1.0.0"

    def resolve(self) -> dict[str, Any]:
        env = env_id_v2()
        if env == "windows":
            root = Path(r"G:\source-of-truth")
        else:
            root = Path("/mnt/g/source-of-truth") if os.path.exists("/mnt/g/source-of-truth") \
                else Path("/media/g/source-of-truth")
        exists = root.exists()
        return {"success": True, "env": env, "root": str(root), "mounted": exists}

    def execute(self) -> dict[str, Any]:
        return {"success": True, "method": "execute", "result": self.resolve()}


@register_worm(category="envmesh", priority=7, cooldown_seconds=15.0,
    description="5-way capability beacon: windows/wsl2-ubuntu/wsl2-kali/bare/kali + FT + GPU")
class EnvBeaconV2Worm:
    """Replaces mesh:EnvBeaconWorm 3-way id with env_id_v2() 5-way id."""

    VERSION = "1.0.0"

    def __init__(self):
        self._beacons: dict = {}
        self._lock = threading.Lock()

    def beacon(self) -> dict[str, Any]:
        cap = {"env": env_id_v2(), "ft": is_free_threaded(),
               "platform": platform.platform()[:80], "cpu": os.cpu_count(),
               "dri": os.path.exists("/dev/dri"), "kmsg": os.path.exists("/dev/kmsg"),
               "mnt_g": os.path.exists("/mnt/g/source-of-truth") or os.path.exists(r"G:\source-of-truth"),
               "at": datetime.now().isoformat()}
        with self._lock:
            self._beacons[cap["env"]] = cap
        return {"success": True, "beacon": cap, "known_envs": sorted(self._beacons.keys())}

    def execute(self) -> dict[str, Any]:
        r = self.beacon()
        return {"success": True, "method": "execute", "result": r["beacon"]}
