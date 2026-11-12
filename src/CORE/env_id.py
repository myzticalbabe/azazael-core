# coding: utf-8
"""Canonical environment identity for the neural mesh — ONE implementation.

Why this module exists (2026-09-15 audit):
    Environment id is a *primary key* in at least six mesh subsystems —
    spine-map nodes ("env:worm"), gossip drop filenames ("<env>.json"),
    Kademlia node ids, the gossip anti-replay table, route Q-keys
    ("src>dst"), and beacon peer sets. Before this module there were FOUR
    independent copies of the detection logic:

        CORE.WORMS.expansion.envmesh.env_id_v2()      (5-way, canonical)
        CORE.WORMS.expansion.mesh._env_id()           (delegated to envmesh)
        CORE.WORMS.expansion.xenomorph._local_env()   (3-way, stale)
        CORE.WORMS.expansion.spine._local_env()       (3-way, stale)
        CORE.spine_registry._local_env_name()         (3-way, stale)

    The three "stale" copies each carried a docstring claiming they were
    mirrors of mesh._env_id(), which stopped being true when envmesh
    split WSL2 into wsl2-ubuntu/wsl2-kali. The result was one host writing
    state under two different keys depending on which module wrote it, and
    gossip filenames that could never be matched by Kademlia peer lookup.

    All five call sites now delegate here. Add new envs HERE, not there.

Design constraints:
    - stdlib only, no imports from CORE (importable from any CORE submodule
      without a cycle; spine_registry is documented stdlib-only)
    - deterministic: same host + same env always yields the same id
    - AZAZAEL_ENV overrides everything (the supported way to run two logical
      envs on one machine, e.g. gossip testing)
"""

import os
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["env_id", "describe_env", "is_known_env", "VALID_ENVS",
           "WSL_ENVS", "canonical_env"]

# The five hosted identity buckets the mesh recognises.
VALID_ENVS = ("windows", "wsl2-ubuntu", "wsl2-kali", "bare", "kali")

# Both WSL2 flavours are real Linux kernels (Ubuntu 26.04 / Kali on
# 6.18-microsoft-standard-WSL2), so callers that ask "am I on a full
# Linux kernel?" should treat these as yes.
WSL_ENVS = ("wsl2-ubuntu", "wsl2-kali")


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as exc:
        note_failure("env_id:L51", exc)
        return ""


def canonical_env(raw: str) -> str:
    """Map a legacy/partial env name onto a canonical id.

    Accepts the pre-2026-09-13 3-way ids ("wsl2", "bare") and the
    "local"/"" placeholders used by older gossip payloads, so state
    written by a previous version still correlates with new state
    instead of silently forking into a second key space.
    """
    name = (raw or "").strip().lower()
    if not name or name == "local":
        return "windows" if os.name == "nt" else "bare"
    if name in VALID_ENVS:
        return name
    # Legacy conflation: "wsl2" could have been either distro. Prefer the
    # generic Linux bucket only as a last resort; callers that need the
    # distro ask env_id() on the host that actually knows.
    if name == "wsl2":
        return "wsl2-ubuntu"
    return name


def env_id() -> str:
    """Canonical 5-way env id: windows | wsl2-ubuntu | wsl2-kali | bare | kali.

    - wsl2-ubuntu: /proc/version has microsoft, /etc/os-release is NOT kali
    - wsl2-kali:   both microsoft and kali
    - kali:        bare (non-WSL) Kali
    - bare:        generic non-WSL non-Kali Linux (true bare metal OR VM;
                   use BarePassthroughWorm.probe() to split bare vs vm)

    AZAZAEL_ENV overrides the detection (truncated to 40 chars, matching
    the gossip filename budget).
    """
    override = os.environ.get("AZAZAEL_ENV")
    if override:
        return canonical_env(override)[:40]
    if os.name == "nt":
        return "windows"
    ver = _read("/proc/version").lower()
    rel = _read("/etc/os-release").lower()
    is_wsl = "microsoft" in ver
    is_kali = "kali" in rel
    if is_wsl and is_kali:
        return "wsl2-kali"
    if is_wsl:
        return "wsl2-ubuntu"
    if is_kali:
        return "kali"
    return "bare"


def is_known_env(name: str) -> bool:
    """True when `name` is a reachable mesh env id (exact or prefix form).

    Prefix-tolerant so "wsl2" (the pre-split bucket) and "wsl2-ubuntu"
    both answer True — the route table's `hops` field must not report a
    live target as unreachable just because the caller used the legacy
    spelling.
    """
    if not name:
        return False
    if name in VALID_ENVS:
        return True
    return any(v.startswith(name) or name.startswith(v) for v in VALID_ENVS)


def describe_env() -> dict:
    """Richer host description: virt, container flavour, memory, FT.

    Distinguishes TRUE bare metal (no hypervisor) from VMs/WSL2 so callers
    (e.g. Lilith, placement) can prefer bare without bricking when absent.

    Windows hypervisor detection is real here (this replaced a dead
    try/except in mesh.describe_env() that hard-coded "hyperv_host"
    without ever checking Hyper-V — audit finding #14, 2026-09-15).
    """
    env = env_id()
    virt = "unknown"
    if env == "windows":
        virt = "hyperv_host" if _windows_hypervisor_present() else "windows_native"
    else:
        ver = _read("/proc/version").lower()
        cgroup = _read("/proc/1/cgroup").lower()
        if "microsoft" in ver:
            virt = "wsl2"
        elif os.path.exists("/.dockerenv") or "docker" in cgroup or "kubepods" in cgroup:
            virt = "container"
        elif "hypervisor" in ver or "kvm" in ver or "vmware" in ver:
            virt = "vm"
        else:
            virt = "bare_metal_or_unknown"
    mem_gb = 0.0
    try:
        import psutil
        mem_gb = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception as exc:
        note_failure("env_id:L150", exc)
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                mem_kb = int(f.readline().split()[1])
            mem_gb = round(mem_kb / 1e6, 1)
        except Exception as exc:
            note_failure("env_id:L155", exc)
            mem_gb = 0.0
    return {"env": env, "virt": virt, "cpu": os.cpu_count() or 0,
            "mem_gb": mem_gb, "bare_likely": env == "bare" and virt == "bare_metal_or_unknown"}


def _windows_hypervisor_present() -> bool:
    """Best-effort Hyper-V presence check (never raises).

    Same signal WSL/`systeminfo` reports: the "HypervisorPresent" value
    under the Hyper-V hypervisor key. Absent key => no Hyper-V.
    """
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Virtualization",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "HypervisorPresent")
        return bool(val)
    except Exception as exc:
        note_failure("env_id:L175", exc)
        return False
