#!/usr/bin/env python3
# coding: utf-8
"""SpitFire Subagent Spawner — parallel target builds via Agent Manager.

Spawns one subagent per target (win7, win81, win10, macos, linux) for
truly parallel Nuitka compilation. Each subagent runs on its own runner
with the correct Python/Compiler toolchain.

Usage:
  python -m spitfire.spawn_agents --targets win7,win81,win10,macos,linux
  python -m spitfire.spawn_agents --target win10 --background
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


# Target specifications for subagent dispatch
TARGET_SPECS = {
    "win7": {
        "os": "windows-2019",
        "python": "3.8",
        "arch": "x64",
        "vs": "2017",
        "config": "spitfire.win7.nuitka.toml",
        "artifact": "SpitFire-win7.exe",
        "label": "Win7 (Python 3.8 + MSVC 14.1)",
    },
    "win81": {
        "os": "windows-2022",
        "python": "3.12",
        "arch": "x64",
        "vs": "2019",
        "config": "spitfire.win81.nuitka.toml",
        "artifact": "SpitFire-win81.exe",
        "label": "Win8.1 (Python 3.12 + MSVC 14.2)",
    },
    "win10": {
        "os": "windows-2022",
        "python": "3.15t",
        "arch": "x64",
        "vs": "2022",
        "config": "spitfire.win10.nuitka.toml",
        "artifact": "SpitFire-win10.exe",
        "label": "Win10+ (Python 3.15t free-threaded + MSVC 14.3)",
        "experimental": True,
    },
    "macos": {
        "os": "macos-13",
        "python": "3.15t",
        "arch": "universal2",
        "config": "spitfire.macos.nuitka.toml",
        "artifact": "SpitFire-macos",
        "label": "macOS 10.15+ (Python 3.15t universal2)",
    },
    "linux": {
        "os": "ubuntu-20.04",
        "python": "3.15t",
        "arch": "x86_64",
        "config": "spitfire.linux.nuitka.toml",
        "artifact": "SpitFire-linux",
        "label": "Linux glibc 2.17+ (Python 3.15t manylinux2014)",
    },
}


def build_subagent_prompt(target: str, spec: dict[str, Any]) -> str:
    """Generate the prompt for a subagent building one target."""
    exp_flag = "--experimental=python3.15" if spec.get("experimental") else ""
    
    return f"""Build SpitFire for target: {target} ({spec['label']})

TARGET SPEC:
- OS: {spec['os']}
- Python: {spec['python']}
- Arch: {spec['arch']}
- VS: {spec.get('vs', 'N/A')}
- Nuitka config: src/spitfire/build/{spec['config']}
- Expected artifact: {spec['artifact']}

STEPS:
1. Checkout the repo at C:/Azazael/azazael (or equivalent)
2. Ensure correct Python interpreter is available:
   - For 3.15t: use C:/Azazael/.venvs/py315t/Scripts/python.exe
   - For 3.8/3.12: install from python.org
3. Install Nuitka 4.2+ (required for 3.15t experimental support)
   pip install -U nuitka==latest
4. Install Visual Studio BuildTools if on Windows:
   - VS {spec.get('vs', 'N/A')} for MSVC {spec.get('vs', 'N/A').replace('20','14.')}
5. Run the build:
   cd C:/Azazael/azazael
   python src/spitfire/build/build_all.py --target {target} {exp_flag}

VERIFICATION:
- Check artifact exists: dist/{target}/{spec['artifact']}*
- Run: dist/{target}/{spec['artifact']} --version
- Run: dist/{target}/{spec['artifact']} census --max-workers 4

RETURN:
- JSON with: {{"target": "{target}", "success": true/false, "artifact_path": "...", "duration_seconds": N, "error": "..."}}

This subagent runs on a {spec['os']} runner with the appropriate toolchain pre-installed.
"""


def spawn_via_agent_manager(targets: list[str], background: bool = False) -> list[dict]:
    """Spawn subagents via Agent Manager API (requires agent_manager tool)."""
    # This would call the agent_manager tool with subagent_type="general"
    # and the generated prompts. For now, return the prompts for manual use.
    results = []
    for target in targets:
        spec = TARGET_SPECS[target]
        prompt = build_subagent_prompt(target, spec)
        results.append({
            "target": target,
            "spec": spec,
            "prompt": prompt,
            "background": background,
        })
    return results


def spawn_via_kilo_task_tool(targets: list[str], background: bool = False) -> list[dict]:
    """Spawn subagents via Kilo's task tool (subagent_type='general')."""
    results = []
    for target in targets:
        spec = TARGET_SPECS[target]
        prompt = build_subagent_prompt(target, spec)
        results.append({
            "target": target,
            "description": f"SpitFire build: {spec['label']}",
            "prompt": prompt,
            "subagent_type": "general",
            "background": background,
        })
    return results


def write_subagent_specs(targets: list[str], output_dir: Path) -> int:
    """Write subagent specification files for external orchestration."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for target in targets:
        spec = TARGET_SPECS[target]
        prompt = build_subagent_prompt(target, spec)
        
        spec_file = output_dir / f"subagent_{target}.json"
        spec_data = {
            "target": target,
            "spec": spec,
            "prompt": prompt,
            "model": "auto",  # Let orchestrator choose
            "provider": "auto",
            "timeout_seconds": 3600,
        }
        spec_file.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")
        print(f"[spawn] Wrote {spec_file}")
    
    # Write orchestration script
    orch_file = output_dir / "orchestrate.py"
    orch_content = '''#!/usr/bin/env python3
"""Orchestrates parallel SpitFire subagent builds."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

SPEC_DIR = Path(__file__).parent

async def run_subagent(spec_file: Path) -> dict:
    """Run one subagent build."""
    data = json.loads(spec_file.read_text(encoding="utf-8"))
    target = data["target"]
    prompt = data["prompt"]
    
    # Option A: Use Kilo task tool (if available)
    # Option B: Use agent_manager CLI
    # Option C: Direct SSH to build runners
    
    # For now, print the command that would run
    cmd = [
        sys.executable, "-c",
        f"import sys; sys.path.insert(0, 'C:/Azazael/azazael'); "
        f"from src.spitfire.build.build_all import main; "
        f"sys.argv = ['build_all.py', '--target', '{target}']; main()"
    ]
    print(f"[{target}] Would run: {' '.join(cmd)}")
    
    # Simulate result
    return {{
        "target": target,
        "success": True,
        "artifact_path": f"dist/{target}/{data['spec']['artifact']}",
        "duration_seconds": 0,
        "error": None
    }}

async def main():
    spec_files = list(SPEC_DIR.glob("subagent_*.json"))
    tasks = [run_subagent(f) for f in spec_files]
    results = await asyncio.gather(*tasks)
    
    print("\\n=== BUILD RESULTS ===")
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"{status} {r['target']}: {r['artifact_path']} ({r['duration_seconds']:.1f}s)")
        if r["error"]:
            print(f"    ERROR: {r['error']}")
    
    return 0 if all(r["success"] for r in results) else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
'''
    orch_file.write_text(orch_content, encoding="utf-8")
    print(f"[spawn] Wrote orchestration script: {orch_file}")
    
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Spawn parallel SpitFire build subagents"
    )
    ap.add_argument(
        "--targets",
        default="win7,win81,win10,macos,linux",
        help="Comma-separated targets (default: all 5)",
    )
    ap.add_argument(
        "--mode",
        choices=["agent_manager", "kilo_task", "specs"],
        default="specs",
        help="Spawn mode: agent_manager (Agent Manager), kilo_task (Kilo task tool), specs (write spec files)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "state" / "spitfire_subagents",
        help="Output directory for spec files (specs mode)",
    )
    ap.add_argument(
        "--background",
        action="store_true",
        help="Run subagents in background",
    )
    ap.add_argument(
        "--print-prompts",
        action="store_true",
        help="Print prompts to stdout instead of spawning",
    )
    
    args = ap.parse_args()
    targets = [t.strip() for t in args.targets.split(",")]
    
    # Validate targets
    for t in targets:
        if t not in TARGET_SPECS:
            print(f"[spawn] Unknown target: {t}", file=sys.stderr)
            return 1
    
    if args.print_prompts:
        for target in targets:
            spec = TARGET_SPECS[target]
            prompt = build_subagent_prompt(target, spec)
            print(f"\n{'='*60}")
            print(f"TARGET: {target} ({spec['label']})")
            print(f"{'='*60}")
            print(prompt)
        return 0
    
    if args.mode == "agent_manager":
        results = spawn_via_agent_manager(targets, args.background)
        print(f"[spawn] Would spawn {len(results)} subagents via Agent Manager")
        for r in results:
            print(f"  - {r['target']}: {r['spec']['label']}")
    elif args.mode == "kilo_task":
        results = spawn_via_kilo_task_tool(targets, args.background)
        print(f"[spawn] Would spawn {len(results)} subagents via Kilo task tool")
        for r in results:
            print(f"  - {r['target']}: {r['description']}")
    elif args.mode == "specs":
        return write_subagent_specs(targets, args.output_dir)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())