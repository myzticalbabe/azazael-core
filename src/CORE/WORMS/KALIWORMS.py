# coding: utf-8
"""KALIWORMS — Autonomous White Hat tool worms (authorized use only).

One thin worm per Kali audit tool (34 total). Each exposes scan(), which
probes the tool inside the kali-linux WSL2 distro and returns a structured
result. No targets, no credentials, no writes — capability probes only.
Directed runs set .target/.args explicitly (requires authorization).

Autonomy contract (see CLUSTERWORMS._STANDARD_METHODS + DnsWorm precedent):
- @register_worm applied at import -> auto-discovered into WORM_REGISTRY
- no-arg constructors; NO get_summary/get_status (they would shadow scan())
- scan() is parameterless; cluster machinery invokes it directly
- shared _wsl() bridge: argv list (no shell quoting), per-probe timeout
"""

import subprocess
from typing import List

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
    note_failure("KALIWORMS:L20", exc)
    def register_worm(
        category: str = "utility",
        priority: int = 5,
        cooldown_seconds: float = 30.0,
        description: str = "",
        tags=None,
    ):
        return lambda cls: cls


_WSL = ["wsl.exe", "-d", "kali-linux"]
_PROBE_TIMEOUT = 90


def _wsl(argv, timeout=_PROBE_TIMEOUT):
    """Run argv inside kali-linux. Returns (rc, output_head). No shell."""
    try:
        r = subprocess.run(
            _WSL + list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        text = ((r.stdout or "") + (r.stderr or "")).strip()[:400]
        return r.returncode, text
    except Exception as e:
        note_failure("KALIWORMS:L38", e)
        return -1, str(e)[:200]


_VERSION_FLAGS = (("--version",), ("-V",), ("version",), ("-h",), ("--help",))


class _KaliToolWorm:
    """Base: probe the tool until one flag works. Subclasses set BIN/PROBES."""
    BIN = ""
    PROBES: tuple[tuple[str, ...], ...] | None = None
    TIMEOUT = _PROBE_TIMEOUT

    def scan(self):
        """Probe tool capability. Parameterless so cluster machinery invokes it."""
        if self.PROBES:
            chains = [list(a) for a in self.PROBES]
            label = self.BIN or chains[0][0]
        elif self.BIN:
            chains = [[self.BIN] + list(f) for f in _VERSION_FLAGS]
            label = self.BIN
        else:
            return {"success": False, "error": "no binary configured"}
        last = ""
        for argv in chains:
            rc, out = _wsl(argv, self.TIMEOUT)
            if "command not found" in out or rc == 127:
                last = out[:200]
                continue
            if ("Traceback" in out or "ModuleNotFoundError" in out
                    or "PackageNotFoundError" in out or "ImportError" in out):
                last = out[:200]
                continue
            if out:
                return {"success": True, "tool": label,
                        "probe": " ".join(argv), "output": out[:300]}
            last = out[:200] or f"rc={rc} silent"
        return {"success": False, "tool": label, "error": last or "no output"}


def _make(name, binary, category, desc, probes=None):
    cls = type(name, (_KaliToolWorm,), {"BIN": binary,
                                       "__doc__": desc,
                                       "__module__": __name__})
    if probes:
        cls.PROBES = probes
    return register_worm(category=category, priority=5,
                         cooldown_seconds=300.0,
                         description="White Hat " + desc)(cls)


_TOOLS = [
    # (ClassName, binary, category, description, [custom probes])
    ("NmapWorm", "nmap", "network", "Port/service discovery via nmap", None),
    ("NaabuWorm", "naabu", "network", "Fast port scanning via naabu", None),
    ("RustscanWorm", "rustscan", "network", "Fast port scanning via rustscan", None),
    ("MasscanWorm", "masscan", "network", "Internet-scale port scanning via masscan", None),
    ("NucleiWorm", "nuclei", "security", "Template-based vuln scanning via nuclei", None),
    ("NiktoWorm", "nikto", "security", "Web server scanning via nikto", None),
    ("WpscanWorm", "wpscan", "security", "WordPress scanning via wpscan", None),
    ("WapitiWorm", "wapiti", "security", "Web app scanning via wapiti3", None),
    ("FfufWorm", "ffuf", "network", "Web fuzzing via ffuf (seclists present)", None),
    ("GobusterWorm", "gobuster", "network", "Directory/DNS brute-forcing via gobuster", None),
    ("FeroxbusterWorm", "feroxbuster", "network", "Content discovery via feroxbuster", None),
    ("DirbWorm", "dirb", "network", "Web content scanning via dirb", None),
    ("DnsxWorm", "dnsx", "network", "DNS toolkit via dnsx", None),
    ("SubfinderWorm", "subfinder", "network", "Passive subdomain discovery via subfinder", None),
    ("AmassWorm", "amass", "network", "Attack-surface mapping via amass", None),
    ("DnsgenWorm", "dnsgen", "network", "DNS name generation via dnsgen", None),
    ("TheHarvesterWorm", "theHarvester", "analysis", "Email/subdomain harvest via theHarvester", None),
    ("SherlockWorm", "sherlock", "analysis", "Username search via sherlock", None),
    ("MaigretWorm", "maigret", "analysis", "Username search across 3000+ sites via maigret", None),
    ("PhoneinfogaWorm", "phoneinfoga", "analysis", "Phone-number OSINT via phoneinfoga", None),
    ("HoleheWorm", "holehe", "analysis", "Account/breach checks via holehe", None),
    ("H8mailWorm", "h8mail", "analysis", "Breach hunting via h8mail", None),
    ("TrufflehogWorm", "trufflehog", "security", "Leaked-credential scanning via trufflehog", None),
    ("GitleaksWorm", "gitleaks", "security", "Git secret scanning via gitleaks", None),
    ("SqlmapWorm", "sqlmap", "security", "SQL injection testing via sqlmap", None),
    ("HydraWorm", "hydra", "security", "Online credential testing via hydra", None),
    ("JohnWorm", "john", "security", "Offline password audit via john", None),
    ("HashcatWorm", "hashcat", "security", "Hash recovery via hashcat", None),
    ("MitmproxyWorm", "mitmproxy", "security", "Intercepting proxy via mitmproxy", None),
    ("BettercapWorm", "bettercap", "security", "MITM framework via bettercap", None),
    ("ResponderWorm", "responder", "security", "LLMNR/NBT-NS responder via responder", None),
    ("BloodhoundWorm", "bloodhound-ce-python", "security", "AD graph collection via BloodHound", None),
    ("ImpacketWorm", "impacket", "security", "Network protocol toolkit (impacket lib)",
     (( "python3", "-c", "import impacket; print('impacket-ok')"),)),
    ("CrackmapexecWorm", "crackmapexec", "security", "Network execution via crackmapexec/netexec",
     (("crackmapexec", "--version"), ("nxc", "--version"), ("crackmapexec", "-h"))),
]

for _name, _bin, _cat, _desc, _probes in _TOOLS:
    globals()[_name] = _make(_name, _bin, _cat, _desc, _probes)
del _name, _bin, _cat, _desc, _probes


if __name__ == "__main__":
    import json
    print(json.dumps(globals()["NmapWorm"].scan(), indent=2)[:400])
