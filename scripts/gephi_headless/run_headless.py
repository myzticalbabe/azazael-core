#!/usr/bin/env python
"""run_headless.py — build & run the headless Gephi engine CLI.

Compiles GephiHeadless.java against the jars of an installed Gephi 0.11.x
(flat classpath via a Java @argfile — ~1,600 jars exceed the cmdline limit),
then runs the driver as a reusable CLI:

  import GraphML -> layout (ForceAtlas2 default; any registered layout by name)
  -> optional node-attribute CSV export -> PNG render.

No GUI, no window focus, fully deterministic. Verified 2026-09-04 (Sep-4(57)),
CLI extension Sep-4(60).

Usage:
  python run_headless.py [--gephi DIR] [--input graphml] [--output png]
                         [--layout NAME] [--iterations N]
                         [--attributes out.csv] [--width W] [--height H]
                         [--margin M] [--no-labels] [--list-layouts]
                         [--no-compile] [--keep]

Examples:
  python run_headless.py                                      # FA2, bcc graph
  python run_headless.py --layout fruchterman --iterations 200
  python run_headless.py --layout yifanhu --attributes nodes.csv
  python run_headless.py --list-layouts
"""
import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.normpath(os.path.join(HERE, "..", "bcc_network.graphml"))
DEFAULT_OUTPUT = os.path.join(HERE, "bcc_network_layout.png")

GEPHI_HINTS = [
    os.path.join(os.environ.get("LOCALAPPDATA", "C:/"), "Programs", "Gephi-0.11.2"),
    os.path.join(os.environ.get("PROGRAMFILES", "C:/Program Files"), "Gephi-0.11.2"),
]
JDK_HINTS = [
    r"C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot",
]

#: Authoritative return code when a bounded Java step does not return. Same
#: value as scripts/chain20.py's RC_TIMEOUT: the caller gets a code it can
#: branch on, instead of a traceback out of main().
RC_TIMEOUT = 124
COMPILE_TIMEOUT_S = 900
DRIVER_TIMEOUT_S = 1800


def find_gephi_dir():
    for base in GEPHI_HINTS:
        if os.path.isdir(base):
            return base
    for root in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", "")):
        if not root:
            continue
        for cand in glob.glob(os.path.join(root, "Programs", "Gephi*")):
            if os.path.isdir(os.path.join(cand, "bin")):
                return cand
    return None


def find_jdk():
    for base in JDK_HINTS:
        exe = os.path.join(base, "bin", "java.exe")
        if os.path.isfile(exe):
            return exe
    for root in (os.environ.get("LOCALAPPDATA", ""), "C:\\Program Files\\Eclipse Adoptium"):  # scanner:ignore deliberate: env var first, literal is a last-resort candidate
        if not root:
            continue
        for cand in glob.glob(os.path.join(root, "**", "bin", "java.exe"), recursive=True):
            return cand
    return None


def collect_jars(gephi_dir):
    """All jars under the install (platform/*, gephi/modules, extra/modules)."""
    jars = set()
    for root_dir in ("platform", "gephi", "extra"):
        base = os.path.join(gephi_dir, root_dir)
        for sub, _, files in os.walk(base):
            if "update_tracking" in sub or "locale" in sub:
                continue
            for fn in files:
                if fn.lower().endswith(".jar"):
                    jars.add(os.path.join(sub, fn))
    return sorted(jars)


def arg_escape(s):
    """Java @argfiles treat backslash as escape -> double them."""
    return s.replace("\\", "\\\\")


def write_argfile(path, *tokens):
    with open(path, "w", encoding="utf-8") as f:
        for t in tokens:
            f.write(arg_escape(t) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Headless Gephi engine CLI")
    ap.add_argument("--gephi", default=None)
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--layout", default=None,
                    help="layout name (default forceatlas2); see --list-layouts")
    ap.add_argument("--iterations", type=int, default=None,
                    help="layout iterations (default 300)")
    ap.add_argument("--attributes", default=None,
                    help="export node attributes CSV (id,label,x,y,degree,...)")
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--margin", type=int, default=None)
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--color-by", default=None,
                    help="color nodes by column values, e.g. Community")
    ap.add_argument("--label-size", type=float, default=None,
                    help="explicit node-label font size (overrides proportional)")
    ap.add_argument("--label-by", default=None,
                    help="column whose values become display labels (e.g. Label)")
    ap.add_argument("--list-layouts", action="store_true",
                    help="print registered layouts and exit")
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep temp classpath files")
    args = ap.parse_args()

    gephi_dir = args.gephi or find_gephi_dir()
    if not gephi_dir:
        print("ERROR: Gephi install not found")
        return 2
    java = find_jdk()
    if not java:
        print("ERROR: JDK not found")
        return 2
    jars = collect_jars(gephi_dir)
    print(f"[run] Gephi: {gephi_dir}")
    print(f"[run] JDK:   {java}")
    print(f"[run] jars:  {len(jars)}")

    cp = HERE + os.pathsep + os.pathsep.join(jars)
    argfile = os.path.join(HERE, "_cp.args")
    compile_args = os.path.join(HERE, "_compile.args")
    write_argfile(argfile, "-cp", '"' + cp + '"')
    with open(compile_args, "w", encoding="utf-8") as f:
        f.write('"-cp"\n')
        f.write('"' + arg_escape(cp) + '"\n')
        f.write('"' + arg_escape(os.path.join(HERE, "GephiHeadless.java")) + '"\n')
        f.write('"-d"\n')
        f.write('"' + arg_escape(HERE) + '"\n')

    src = os.path.join(HERE, "GephiHeadless.java")
    cls = os.path.join(HERE, "GephiHeadless.class")
    if not args.no_compile and (not os.path.isfile(cls)
                                or os.path.getmtime(src) > os.path.getmtime(cls)):
        print("[run] compiling...")
        try:
            r = subprocess.run([java.replace("java.exe", "javac.exe"), "@" + compile_args],
                               timeout=COMPILE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            print(f"ERROR: javac exceeded its {COMPILE_TIMEOUT_S}s bound")
            return RC_TIMEOUT
        if r.returncode != 0:
            print("ERROR: compile failed")
            return r.returncode

    driver_args = [java, "-Xmx1g", "@" + argfile, "GephiHeadless"]
    if args.list_layouts:
        driver_args.append("--list-layouts")
    else:
        driver_args += ["--input", args.input, "--output", args.output]
        if args.layout:
            driver_args += ["--layout", args.layout]
        if args.iterations is not None:
            driver_args += ["--iterations", str(args.iterations)]
        if args.attributes:
            driver_args += ["--attributes", args.attributes]
        if args.width:
            driver_args += ["--width", str(args.width)]
        if args.height:
            driver_args += ["--height", str(args.height)]
        if args.margin:
            driver_args += ["--margin", str(args.margin)]
        if args.no_labels:
            driver_args.append("--no-labels")
        if args.color_by:
            driver_args += ["--color-by", args.color_by]
        if args.label_size:
            driver_args += ["--label-size", str(args.label_size)]
        if args.label_by:
            driver_args += ["--label-by", args.label_by]

    print("[run] executing driver...")
    rc = RC_TIMEOUT
    try:
        r = subprocess.run(driver_args, timeout=DRIVER_TIMEOUT_S)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        # An authoritative code (scripts/chain20.py uses the same RC_TIMEOUT)
        # rather than a traceback escaping main().
        print(f"ERROR: java driver exceeded its {DRIVER_TIMEOUT_S}s bound")
    finally:
        # Runs on every path, the timeout included: otherwise a timed-out run
        # leaks both temp argfiles.
        if not args.keep:
            leftovers = []
            for f in (argfile, compile_args):
                try:
                    os.remove(f)
                except OSError as exc:
                    leftovers.append(f"{f} ({type(exc).__name__}: {exc})")
            if leftovers:
                print("[run] note: could not remove " + ", ".join(leftovers))
    return rc


if __name__ == "__main__":
    sys.exit(main())
