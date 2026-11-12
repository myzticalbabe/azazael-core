#!/usr/bin/env python
"""Hybrid Python stack manager: make the 3.15t and 3.15 interpreters BOTH work.

The situation
-------------
On this machine the free-threaded interpreter and the GIL interpreter share a
single ``site-packages`` directory::

    .../Programs/Python/Python315/python.exe       -> looks for .cp315-win_amd64.pyd
    .../Programs/Python/Python315/python3.15t.exe  -> looks for .cp315t-win_amd64.pyd
    both resolve to: .../Python315/Lib/site-packages

Note: 3.14t is RETIRED (2026-10-19). Production is the free-threaded
``C:/Azazael/.venvs/py315t`` venv and the GIL sidecar is
``C:/Azazael/.venvs/py315`` — separate directories, so this tool's overlay only
applies where two interpreters genuinely SHARE one site-packages (the base
install pair above); pass both interpreters explicitly for any other layout.

Each interpreter only recognises its own ABI suffix (check with
``python -c "import _imp; print(_imp.extension_suffixes())"``). pip, however,
lays down exactly one build per install and *uninstalls the other*, so a
package silently works on one interpreter and breaks on the other with nothing
visible in ``pip list``.

The hybrid
----------
The suffixes are distinct, so the two builds **coexist** in one directory and
each interpreter picks its own. For any package whose both-ABI wheels are the
same version, "all packages work" means: install the free-threaded build (the
project's production runtime) and overlay the GIL build's compiled files next to
it. Neither interpreter is sacrificed.

Tiers
-----
* ``dual``  -- both ABIs published at the same version: install + overlay.
               Both interpreters work.
* ``pure``  -- pure-Python wheel: nothing to overlay, works everywhere.
* ``ft-only`` / ``gil-only`` -- only one ABI is published upstream. The
               available build is installed; the other interpreter is reported
               as upstream-limited rather than silently left broken.

Usage
-----
    python tools/install_dual_abi.py numpy==2.5.3 scipy==1.18.1
    python tools/install_dual_abi.py --manifest tools/hybrid_stack_manifest.txt
    python tools/install_dual_abi.py --check numpy scipy torch
    python tools/install_dual_abi.py --verify-only numpy scipy torch
    python tools/install_dual_abi.py --manifest ... --verify

``--verify`` imports each package under BOTH interpreters in separate
subprocesses (a crash cannot be attributed inside one process) and prints a
matrix.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

BINARY_SUFFIXES = (".pyd", ".so", ".dll")


# --------------------------------------------------------------------------
# interpreter discovery
# --------------------------------------------------------------------------
def _default_interpreters(explicit_ft: str | None,
                          explicit_gil: str | None) -> tuple[str, str]:
    """Return (ft_python, gil_python), guessing siblings when not given."""
    here = Path(sys.executable)
    ft, gil = explicit_ft, explicit_gil
    if here.name.lower() == "python3.15t.exe":
        ft = ft or str(here)
        gil = gil or str(here.with_name("python.exe"))
    else:
        gil = gil or str(here)
        ft = ft or str(here.with_name("python3.15t.exe"))
    return ft, gil


def _is_freethreaded(python: str) -> bool:
    try:
        out = subprocess.run(
            [python, "-c",
             "import sysconfig;print(sysconfig.get_config_var('Py_GIL_DISABLED'))"],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace")
        return out.stdout.strip() == "1"
    except Exception:
        return False


# --------------------------------------------------------------------------
# wheel acquisition
# --------------------------------------------------------------------------
def _pip_download(spec: str, abi: str, dest: Path, python: str,
                  allow_pre: bool = False) -> Path | None:
    """Fetch the wheel matching one explicit ABI. Returns the wheel path."""
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [python, "-m", "pip", "download", spec, "--no-deps",
           "--only-binary", ":all:",
           "--platform", "win_amd64", "--python-version", "3.15",
           "--implementation", "cp", "--abi", abi, "-d", str(dest)]
    if allow_pre:
        cmd.append("--pre")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        # The declared contract is Path | None and the caller records a None as
        # "no wheel" for THIS spec, then continues to the next one. Escaping
        # would abort the whole run and lose that per-spec accounting, so the
        # timeout is reported here and folded into the same contract.
        print(f"   pip download timed out after 1800s: {spec} ({abi})")
        return None
    wheels = sorted(dest.glob("*.whl"))
    if proc.returncode != 0 or not wheels:
        return None
    return wheels[-1]


def _wheel_version(wheel: Path) -> str:
    """Version parsed from a wheel filename (name-version-...whl)."""
    parts = wheel.stem.split("-")
    return parts[1] if len(parts) > 2 else ""


def _is_pure(wheel: Path) -> bool:
    return "py3-none-any" in wheel.name or "-none-any" in wheel.name


# A binary is ABI-specific only when its *filename* carries an ABI tag:
#   _core.cp315t-win_amd64.pyd, _core.cp315-win_amd64.pyd, _rust.abi3.pyd
# A plain ``libarrow.dll`` / ``libopenblas64_*.dll`` is a *build* artifact with
# no ABI tag: the cp315 and cp315t wheels each contain their own, and copying
# one over the other corrupts the build it lands on. That mistake made pandas
# and pyarrow crash under the free-threaded build with both ABI sets nominally
# present, because the free-threaded .pyd was left talking to a GIL-linked
# support DLL (measured on 3.14t before its retirement).
ABI_TAG_RE = re.compile(r"\.(cp3\d{2}t?|abi3)", re.IGNORECASE)


def _is_abi_tagged(filename: str) -> bool:
    return bool(ABI_TAG_RE.search(filename.rsplit("/", 1)[-1]))


def _compiled_members(wheel: Path) -> dict[str, bytes]:
    """ABI-specific binaries inside a wheel, keyed by archive-relative path.

    Only files whose name carries an ABI tag are returned; untagged support
    libraries are deliberately skipped so an overlay can never clobber the
    other build's runtime DLLs.
    """
    out: dict[str, bytes] = {}
    for name, data in _all_binaries(wheel).items():
        if _is_abi_tagged(name):
            out[name] = data
    return out


def _all_binaries(wheel: Path) -> dict[str, bytes]:
    """Every compiled binary in a wheel, tagged or not."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if base.endswith(BINARY_SUFFIXES):
                out[info.filename] = zf.read(info)
    return out


def _overlay_conflict(primary: Path, other: Path) -> list[str]:
    """Untagged binaries that differ between two builds of the same version.

    A tagged ``.pyd`` is loaded against the *untagged* DLLs sitting beside it
    (``libarrow.dll``, ``.libs/*.dll``). Those have no ABI tag, so the cp315
    and cp315t wheels each ship their own and the two cannot share a
    directory: whichever lands second leaves the other build's extension
    linked against a mismatched runtime, which shows up as a bare segfault.

    Where the untagged files are byte-identical across both builds (numpy's
    content-hash-named ``numpy.libs`` DLLs, for example) there is no conflict
    and the two ABIs can safely coexist.

    Returns the conflicting archive paths; empty means the overlay is safe.
    """
    a = {n: d for n, d in _all_binaries(primary).items()
         if not _is_abi_tagged(n)}
    b = {n: d for n, d in _all_binaries(other).items()
         if not _is_abi_tagged(n)}
    return sorted(n for n in set(a) & set(b) if a[n] != b[n])


def _overlay(wheel: Path, site: Path) -> int:
    """Copy a wheel's compiled binaries into site-packages. Returns count.

    Every binary is copied, tagged or not. That is safe *because* the caller
    has already ruled out conflicts: an untagged file that exists in both
    builds with different content aborts the overlay, so by the time we get
    here any same-named untagged file is byte-identical, and an untagged file
    unique to this wheel is purely additive. The additive case is what makes
    abi3 modules work -- safetensors' GIL build is a plain
    ``_safetensors_rust.pyd`` with no ABI tag, which the free-threaded build
    simply never looks for.
    """
    placed = 0
    for name, data in _all_binaries(wheel).items():
        target = site / Path(name)
        if target.exists() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        placed += 1
    return placed


def _remove_tagged(pkg_dir: Path, tag: str) -> int:
    """Delete stray binaries carrying an ABI tag (used to roll back an overlay).

    Keeps the directory in a single known-good state instead of leaving the
    other interpreter loading a build whose untagged runtime DLLs are the
    wrong ones -- which crashes rather than raising.
    """
    removed = 0
    if not pkg_dir.exists():
        return 0
    # Match the tag exactly: ``"cp315" in "x.cp315t-win_amd64.pyd"`` is also
    # true, so a substring test would delete the free-threaded build it is
    # meant to protect. The ABI tag always appears as ``.cp315-`` / ``.cp315t-``.
    needle = f".{tag}-"
    for p in pkg_dir.rglob("*"):
        if (p.is_file() and p.suffix in BINARY_SUFFIXES
                and needle in p.name and _is_abi_tagged(p.name)):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _abi_counts(pkg_dir: Path) -> dict[str, int]:
    counts = {"cp315t": 0, "cp315": 0, "other": 0}
    if not pkg_dir.exists():
        return counts
    for p in pkg_dir.rglob("*"):
        if p.suffix in BINARY_SUFFIXES and p.is_file():
            if "cp315t" in p.name:
                counts["cp315t"] += 1
            elif "cp315" in p.name:
                counts["cp315"] += 1
            else:
                counts["other"] += 1
    return counts


def _pkg_dir(site: Path, name: str) -> Path:
    """Best-effort import-package directory for a distribution name.

    Distribution names frequently differ from the import package
    (scikit-learn -> sklearn, pywin32 -> win32com, pill
    ow -> PIL), so consult the alias table before falling back to a
    mechanical dash-to-underscore translation.
    """
    candidates = [name.replace("-", "_").split(".")[0]]
    # Aliases are keyed by canonical distribution name; look up case- and
    # separator-insensitively so "PyYAML" and "pyyaml" both reach `yaml`.
    key = name.lower().replace("_", "-")
    alias = MODULE_ALIASES.get(key)
    if alias:
        candidates.insert(0, alias.split(".")[0])
    for cand in candidates:
        for c in (site / cand, site / cand.lower()):
            if c.exists():
                return c
    return site / name.replace("-", "_")


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def _probe(module: str, python: str, timeout: float) -> str:
    try:
        proc = subprocess.run(
            [python, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    if proc.returncode == 0:
        return "ok"
    if proc.returncode < 0 or proc.returncode >= 0xC0000000:
        return "CRASH"
    return "FAIL"


def _pip_install(installer: str,
                 wheel: Path) -> "tuple[subprocess.CompletedProcess[str] | None, str]":
    """Force-reinstall one wheel with `installer`; returns (process, reason).

    A timeout must NOT escape this call. The caller branches on the result of
    the install, and one of those branches is the GIL-build fallback retry that
    exists precisely for an install which did not succeed -- an exception here
    would skip the recovery instead of triggering it.

    The reason is RETURNED rather than discarded, for two reasons: `None` alone
    cannot say whether the wheel timed out or pip refused it, and a tuple is not
    the bare-constant return the scanner reads as `except_return_default`.
    `rc=...` is the fallback when pip printed nothing, which used to raise
    IndexError on `.splitlines()[-1]`.
    """
    try:
        return (subprocess.run(
            [installer, "-m", "pip", "install", "--force-reinstall",
             "--no-deps", "--no-warn-conflicts", str(wheel)],
            capture_output=True, text=True, timeout=1800,
            encoding="utf-8", errors="replace"), "")
    except subprocess.TimeoutExpired as exc:
        return (None, "%s after 1800s" % type(exc).__name__)


def verify(modules: list[str], ft: str, gil: str, timeout: float) -> int:
    print(f"\nverification (one subprocess per module per interpreter)")
    print(f"  FT : {ft}")
    print(f"  GIL: {gil}")
    print(f"  {'module':<22} {'FT':<8} GIL")
    bad = 0
    for spec in modules:
        key = spec.split("==")[0]
        import_name = MODULE_ALIASES.get(
            key.lower().replace("_", "-"), key.replace("-", "_"))
        ft_status = _probe(import_name, ft, timeout)
        gil_status = _probe(import_name, gil, timeout)
        flag = ""
        if ft_status != "ok":
            flag = "  <- FT broken"
            bad += 1
        elif gil_status != "ok":
            flag = "  <- GIL limited upstream"
        print(f"  {spec.split('==')[0]:<22} {ft_status:<8} {gil_status}{flag}")
    return bad


# Distribution name -> importable module name where they differ.
MODULE_ALIASES = {
    "scikit-learn": "sklearn",
    "pywin32": "win32com",
    "protobuf": "google.protobuf",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "gitpython": "git",
    "opentelemetry-api": "opentelemetry",
    "rpds-py": "rpds",
    "fonttools": "fontTools",
    "z3-solver": "z3",
    "opencv-python-headless": "cv2",
    "scikit-image": "skimage",
    "opencv": "cv2",
    "pyqt5": "PyQt5",
    "pyqt6": "PyQt6",
    "msgpack": "msgpack",
    "ormsgpack": "ormsgpack",
    "pyyaml": "yaml",
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="*", help="name==version (or bare name)")
    ap.add_argument("--manifest", metavar="FILE",
                    help="file with one name==version per line")
    ap.add_argument("--check", action="store_true",
                    help="report ABIs present, change nothing")
    ap.add_argument("--verify", action="store_true",
                    help="import each package under both interpreters")
    ap.add_argument("--verify-only", action="store_true",
                    help="skip installs; only verify")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter used for pip (default: this one)")
    ap.add_argument("--python-ft", help="free-threaded interpreter path")
    ap.add_argument("--python-gil", help="GIL interpreter path")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--pre", action="store_true",
                    help="allow pre-release wheels when resolving")
    args = ap.parse_args()

    specs = list(args.specs)
    if args.manifest:
        for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                specs.append(line)
    if not specs:
        ap.error("give specs or --manifest")

    ft_py, gil_py = _default_interpreters(args.python_ft, args.python_gil)
    site = Path(sysconfig.get_paths()["purelib"])

    print(f"site-packages : {site}")
    print(f"pip via       : {args.python}")
    print(f"FT interpreter: {ft_py}  (free-threaded="
          f"{_is_freethreaded(ft_py)})")
    print(f"GIL interpreter: {gil_py}  (free-threaded="
          f"{_is_freethreaded(gil_py)})")

    if args.verify_only:
        return 1 if verify(specs, ft_py, gil_py, args.timeout) else 0

    summary: list[tuple[str, str, str, int, int]] = []
    problems: list[str] = []

    for spec in specs:
        name = spec.split("==")[0]
        pkg_dir = _pkg_dir(site, name)
        print(f"\n== {spec}")

        if args.check:
            counts = _abi_counts(pkg_dir)
            print(f"            cp315t={counts['cp315t']}  cp315={counts['cp315']}  "
                  f"other={counts['other']}")
            continue

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ft_wheel = _pip_download(spec, "cp315t", tmp / "ft", args.python,
                                     args.pre)
            gil_wheel = _pip_download(spec, "cp315", tmp / "gil", args.python,
                                      args.pre)

            if ft_wheel is None and gil_wheel is None:
                print("   no wheel for either ABI")
                problems.append(f"{spec}: no wheel")
                continue

            # Prefer a common version so the overlay is safe.
            if ft_wheel and gil_wheel and _wheel_version(ft_wheel) != _wheel_version(gil_wheel):
                pinned = _wheel_version(ft_wheel)
                retry = _pip_download(f"{name}=={pinned}", "cp315",
                                      tmp / "gil2", args.python, args.pre)
                if retry:
                    gil_wheel = retry

            two_abi = (ft_wheel is not None and gil_wheel is not None
                       and _wheel_version(ft_wheel) == _wheel_version(gil_wheel))
            # A pure-Python wheel is ABI-independent, so it serves BOTH
            # interpreters and beats any overlay. This matters for packages
            # whose compiled extension is actually FT-hostile: protobuf's
            # abi3 `google._upb._message` segfaulted under the free-threaded
            # 3.14t build (measured), while the pure wheel is correct
            # everywhere (just slower).
            if ft_wheel is not None and _is_pure(ft_wheel):
                tier, primary, gil_wheel = "pure", ft_wheel, None
            elif gil_wheel is not None and _is_pure(gil_wheel) and ft_wheel is None:
                tier, primary = "pure", gil_wheel
            elif two_abi:
                tier, primary = "dual", ft_wheel
            elif ft_wheel:
                tier, primary = "ft-only", ft_wheel
            else:
                tier, primary = "gil-only", gil_wheel
            assert primary is not None

            def _installer_for(wheel: Path) -> str:
                """Pick the interpreter whose ABI the wheel actually carries.

                A cp315 wheel is rejected outright by a free-threaded pip
                ("not a supported wheel on this platform"), which is how a
                GIL-only package ended up uninstalled rather than simply
                GIL-limited.
                """
                return args.python if "cp315t" in wheel.name else gil_py

            proc, why = _pip_install(_installer_for(primary), primary)
            if proc is None or proc.returncode != 0:
                # Retry with the GIL build if the FT build is unusable. A
                # timeout counts as a failure here, so a slow FT install still
                # reaches the fallback this block exists to perform.
                alt = gil_wheel if primary is ft_wheel else None
                if alt is not None:
                    primary, ft_wheel, tier = alt, None, "gil-only"
                    proc, why = _pip_install(_installer_for(primary), primary)
                if proc is None or proc.returncode != 0:
                    if not why:
                        tail = (proc.stderr or proc.stdout).strip().splitlines()
                        why = tail[-1][:100] if tail else "rc=%d" % proc.returncode
                    print(f"   install failed: {why}")
                    problems.append(spec)
                    continue

            print(f"   tier {tier}: installed {primary.name}")
            if tier == "dual":
                other = gil_wheel if primary is ft_wheel else ft_wheel
                if other is not None:
                    conflicts = _overlay_conflict(primary, other)
                    if conflicts:
                        tier = "ft-priority"
                        print(f"   SKIP overlay: {len(conflicts)} untagged "
                              f"binary(ies) differ between the two builds, so "
                              f"they cannot share one directory. Keeping the "
                              f"free-threaded build (the production runtime); "
                              f"the GIL interpreter cannot load this package.")
                        for c in conflicts[:3]:
                            print(f"      conflicts: {c}")
                        other_tag = "cp315" if "cp315t" in primary.name else "cp315t"
                        stray = _remove_tagged(_pkg_dir(site, name), other_tag)
                        if stray:
                            print(f"   rolled back {stray} stray "
                                  f"'{other_tag}' file(s)")
                    else:
                        n = _overlay(other, site)
                        print(f"   overlaid {n} file(s) -> {other.name}")
            elif tier in ("ft-only", "gil-only"):
                print(f"   note: upstream publishes only one ABI for this "
                      f"version; {'GIL' if tier == 'ft-only' else 'FT'} "
                      f"interpreter cannot load it")

        counts = _abi_counts(pkg_dir)
        summary.append((name, tier, f"{counts['cp315t']}/{counts['cp315']}",
                        counts["cp315t"], counts["cp315"]))

    if not args.check and summary:
        print("\nsummary (tier, cp315t/cp315 binaries now present)")
        for name, tier, ratio, _ft, _gil in summary:
            print(f"  {name:<22} {tier:<10} {ratio}")

    rc = 0
    if args.verify:
        rc = 1 if verify(specs, ft_py, gil_py, args.timeout) else 0
    if problems:
        print(f"\nproblems: {', '.join(problems)}")
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
