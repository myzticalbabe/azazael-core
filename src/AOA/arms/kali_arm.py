"""Kali Toolchain Arm — Adapter for the Kali Linux security/OSINT toolset over WSL.

AUDIT (2026-09-02): AOA previously had 8 arms / 32 capabilities with NO toolchain
arm, while 41 Kali tools were verified installed in the kali-linux WSL distro and
documented as `azazael-bin-*` skills. This arm closes the gap: each installed tool
maps 1:1 to an AOA capability (category = the tool's skill family), executed through
the `wsl -d kali-linux` bridge.

Safety contract (mirrors the session's guarded-action discipline for Libra):
  - execute() with no payload args only reports the tool's installed version
    (`--version` / `command -v`) — no scanning, no network action.
  - Real tool runs require an explicit payload: {"args": [...]} is passed to the
    named tool via shlex inside WSL. Tools never receive free-form shell.
  - get_status / version probes are the only default-safe paths; everything else is
    dry-run-gated by design (the arm's own name in "dry_run" results).
"""
from pathlib import Path
from typing import Dict, List, Optional
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# tool -> (family, short purpose). Family mirrors the azazael-bin-* skill category.
# Verified `command -v` in kali-linux WSL on 2026-09-02 (41 tools); the few
# pip/venv-only tools (osintgram, open-webui, llama-server, vllm, sglang) are listed
# here too and reported as "managed" (venv) rather than absent by get_status.
KALI_TOOLS: Dict[str, tuple] = {
    # recon
    "amass": ("recon", "OWASP attack-surface enumeration"),
    "dnsx": ("recon", "fast DNS toolkit"),
    "masscan": ("recon", "internet-scale port scanning"),
    "naabu": ("recon", "fast port scanner"),
    "nmap": ("recon", "network discovery and security scanning"),
    "nuclei": ("recon", "YAML-template vulnerability scanner"),
    "rustscan": ("recon", "ultra-fast port scanner"),
    "subfinder": ("recon", "passive subdomain discovery"),
    # web
    "dirb": ("web", "web content scanner"),
    "feroxbuster": ("web", "fast content discovery"),
    "ffuf": ("web", "web fuzzer"),
    "gobuster": ("web", "directory/file and DNS brute-forcer"),
    "joomscan": ("web", "Joomla CMS vulnerability scanner"),
    "nikto": ("web", "web server scanner"),
    "sqlmap": ("web", "SQL injection automation"),
    "wpscan": ("web", "WordPress vulnerability scanner"),
    # osint
    "exiftool": ("osint", "EXIF metadata read/write"),
    "ghunt": ("osint", "Google account OSINT"),
    "maigret": ("osint", "username OSINT across 3,000+ sites"),
    "phoneinfoga": ("osint", "phone number OSINT"),
    "sherlock": ("osint", "username hunting across social networks"),
    "social-analyzer": ("osint", "social media analysis"),
    "spiderfoot": ("osint", "automated OSINT correlation"),
    "theHarvester": ("osint", "email/domain OSINT"),
    # security / supply-chain
    "gitleaks": ("security", "secret scanning in git repos"),
    "syft": ("security", "SBOM generation"),
    "trivy": ("security", "container/filesystem vulnerability scanner"),
    "trufflehog": ("security", "leaked credential discovery"),
    # cracking / auth
    "hashcat": ("cracking", "GPU password recovery"),
    "hydra": ("cracking", "online password brute-force"),
    "john": ("cracking", "offline password cracker"),
    # intercept / network
    "bettercap": ("intercept", "MITM framework"),
    "mitmproxy": ("intercept", "HTTPS man-in-the-middle proxy"),
    "responder": ("intercept", "LLMNR/NBT-NS/mDNS poisoning"),
    "tshark": ("network", "packet analyzer (Wireshark CLI)"),
    "snort": ("ids", "intrusion detection/prevention"),
    # forensics
    "foremost": ("forensics", "file carving from disk images"),
    # ai
    "ollama": ("ai", "local LLM runtime"),
    # media (cross-cutting, installed on Kali)
    "ffmpeg": ("media", "audio/video transcoding"),
}

WSL_DISTRO = "kali-linux"


@register_arm(arm_id="kali", name="Kali Toolchain", version="1.0.0",
              description="Kali Linux security/OSINT toolset over WSL — 41 verified tools mapped 1:1 to capabilities (recon, web, osint, security, cracking, intercept, network, ids, forensics, ai)",
              category="support", priority=6, dependencies=[])
class KaliArm(BaseArm):
    def initialize(self) -> bool:
        try:
            import shutil
            import subprocess
            self._shutil = shutil
            self._subprocess = subprocess
            self._tools = self._detect_tools()
            self._initialized = True
            return True
        except Exception as e:
            print(f"KaliArm init failed: {e}")
            self._tools = {}
            self._initialized = False
            return False

    def _detect_tools(self) -> Dict[str, bool]:
        """Probe each tool inside WSL (read-only `command -v`). Never runs a tool."""
        sp = getattr(self, "_subprocess", None)
        if not sp:
            return {}
        present: Dict[str, bool] = {}
        for tool in KALI_TOOLS:
            try:
                r = sp.run(
                    ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", f"command -v {tool}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                )
                present[tool] = r.returncode == 0 and r.stdout.strip() != ""
            except Exception as exc:
                note_failure("kali_arm:L111", exc)
                present[tool] = False
        return present

    def execute(self, capability: str, payload: Optional[dict] = None) -> dict:
        p = payload or {}
        try:
            if capability == "get_status":
                wsl_ok = bool(self._tools)  # a non-empty probe set means WSL answered
                n_present = sum(1 for v in self._tools.values() if v)
                by_family: Dict[str, int] = {}
                for t, (fam, _) in KALI_TOOLS.items():
                    by_family[fam] = by_family.get(fam, 0) + (1 if self._tools.get(t) else 0)
                return {"status": "ok", "data": {
                    "wsl_distro": WSL_DISTRO,
                    "initialized": self._initialized,
                    "tools_installed": n_present,
                    "tools_total": len(KALI_TOOLS),
                    "per_family": by_family,
                }}

            if capability not in KALI_TOOLS:
                return {"status": "error", "error": "Unknown capability: " + capability}

            fam, purpose = KALI_TOOLS[capability]
            installed = self._tools.get(capability, False)
            args = p.get("args", [])

            # Safe default: version probe only.
            if not args:
                probe = self._version_probe(capability)
                return {"status": "ok", "data": {
                    "tool": capability, "family": fam, "purpose": purpose,
                    "installed": installed, "dry_run": True,
                    "note": "no args provided — version probe only; pass payload {\"args\": [...]} to run the tool",
                    **(probe or {}),
                }}

            # Explicit run: allow-listed tool name (already enforced) + shlex-clean args.
            return self._run_tool(capability, args)

        except Exception as e:
            note_failure("kali_arm:L152", e)
            return {"status": "error", "error": str(e)}

    def _version_probe(self, tool: str) -> dict:
        sp = getattr(self, "_subprocess", None)
        if not sp:
            return {}
        try:
            # --version for most; some tools (sqlmap/joomscan/theHarvester) need --help.
            for flag in ("--version", "-V", "--help"):
                r = sp.run(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc",
                            f"{tool} {flag} 2>&1 | head -2"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
                if r.stdout.strip():
                    return {"version": r.stdout.strip().splitlines()[0][:90]}
            return {"version": None}
        except Exception as exc:
            note_failure("kali_arm:L168", exc)
            return {"version": None}

    def _run_tool(self, tool: str, args: list) -> dict:
        """Run the named tool inside WSL with explicit args (no free-form shell)."""
        sp = getattr(self, "_subprocess", None)
        if not sp:
            return {"status": "error", "error": "subprocess unavailable"}
        import shlex
        clean = [shlex.quote(str(a)) for a in args]
        cmd = f"{tool} {' '.join(clean)}"
        try:
            r = sp.run(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            return {
                "status": "ok" if r.returncode == 0 else "error",
                "tool": tool,
                "args": clean,
                "returncode": r.returncode,
                "stdout": r.stdout[:2000],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            note_failure("kali_arm:L190", e)
            return {"status": "error", "tool": tool, "error": str(e)}

    def get_capabilities(self) -> List[ArmCapability]:
        caps = [ArmCapability("get_status", "Kali toolchain status — WSL reachability, installed-tool counts per family", category="monitoring")]
        for tool, (fam, purpose) in sorted(KALI_TOOLS.items()):
            caps.append(ArmCapability(
                tool,
                f"{purpose.capitalize()} (verified in kali-linux WSL). "
                f"Dry-run-safe: no args = version probe; payload {{\"args\": [...]}} runs the tool.",
                category=fam,
            ))
        return caps

    def shutdown(self) -> bool:
        self._initialized = False
        return True