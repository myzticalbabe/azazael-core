#!/usr/bin/env python3
"""WSL one-shot eBPF sample -> check_ebpf scores -> JSON.

Invoked hourly from Windows (spine_cycle_apply.py) via:
  wsl -d Ubuntu-26.04 -- python3 /mnt/c/Azazael/azazael/scripts/bcc_oneshot.py
Self-heals tracefs mount (doesn't persist across WSL sessions). Reads the
C:-mirrored baseline (WSL cannot see G:). Never raises: prints {} on failure.
"""
import json
import re
import subprocess
import sys

sys.path.insert(0, "/mnt/c/Azazael/azazael/src")

BT = "/tmp/bcc_oneshot.bt"
PROG = """tracepoint:raw_syscalls:sys_enter { @sys[comm] = count(); }
interval:s:8 {
  print("SYS_BEGIN");
  print(@sys);
  print("SYS_END");
  clear(@sys);
  exit();
}
"""
DUR = 8


def main():
    try:
        subprocess.run(["sudo", "-n", "mount", "--types", "tracefs",
                        "nodev", "/sys/kernel/tracing"],
                       capture_output=True, timeout=30)
        open(BT, "w", encoding="utf-8").write(PROG)
        r = subprocess.run(["sudo", "-n", "bpftrace", BT], capture_output=True,
                           text=True, timeout=60, encoding="utf-8", errors="replace")
        counts: dict = {}
        in_sec = False
        for line in r.stdout.splitlines():
            s = line.strip()
            if s == "SYS_BEGIN":
                in_sec = True
                continue
            if s == "SYS_END":
                break
            m = re.match(r"@\w+\[(.+?)\]:\s+(\d+)", s)
            if m and in_sec:
                counts[m.group(1)] = int(m.group(2)) / DUR
        from CORE.WORMS.expansion.immune import ImmuneSentinelWorm
        w = ImmuneSentinelWorm()
        # Observer-effect guard: the collector (bpftrace+python3+sudo) always
        # spikes its own comms during the window — never score those.
        SELF = {"bpftrace", "python3", "python", "sudo"}
        scored = [w.check_ebpf(c, v) for c, v in counts.items() if c not in SELF]
        alerts = [s for s in scored if s.get("baseline_alert")]
        print(json.dumps({"success": True, "comms": len(scored),
                          "alerts": [{"comm": a["comm"], "z": a["baseline_z"]}
                                     for a in alerts]}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)[:150]}))


if __name__ == "__main__":
    main()
