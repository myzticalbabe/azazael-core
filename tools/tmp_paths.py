# coding: utf-8
r"""Resolve POSIX-looking temp paths onto the directory bash and Python actually share.

The defect, MEASURED on this host (2026-10-15)
---------------------------------------------
git-bash and CPython disagree about what `/tmp` names, and BOTH SIDES SUCCEED:

  * bash `echo "written-by-bash" > /tmp/az732probe.txt` leaves the file at
    bash's `/tmp`, which is `%LOCALAPPDATA%\Temp`
    (`C:\Users\clint\AppData\Local\Temp`);
  * Python `os.path.exists('/tmp/az732probe.txt')` is False, because
    `os.path.abspath('/tmp/az732probe.txt')` is `C:\tmp\az732probe.txt`;
  * `C:\tmp` EXISTS, so Python `open('/tmp/az732py.txt', 'w')` does not raise --
    it writes into a DIFFERENT directory, and bash then reports
    `ls /tmp/az732py.txt: No such file or directory`.

That is the dangerous shape: a writer and a reader can each report success and
never touch the same file. Not a visible crash -- a silently empty comparison,
the failure mode already paid for once (task #502: a diff compared against an
empty reference and reported the whole file as changed).

The resolution
--------------
There is exactly one point the two sides agree on: bash's `/tmp` IS Python's
`tempfile.gettempdir()`. So a POSIX temp path is translated onto that directory
and everything else is passed through untouched. This module maps a name; it does
not create directories, and it does not rewrite paths it was not asked about.

Usage
-----
    from tmp_paths import resolve_tmp_path
    p = resolve_tmp_path("/tmp/change.diff")   # -> <tempdir>/change.diff

Stdlib only.
"""
from __future__ import annotations

import os
import tempfile

#: Prefixes that mean "the system temp directory" to a POSIX shell. Longest first,
#: so `/var/tmp` is not shadowed by a shorter match, and a bare `/tmp` is matched by
#: EQUALITY only -- otherwise `/tmpfoo` would be silently rewritten.
_POSIX_TEMP_PREFIXES = ("/var/tmp/", "/var/tmp", "/tmp/", "/tmp")


def bash_tmp_dir() -> str:
    r"""The directory git-bash's `/tmp` maps to on this host.

    Measured equal to ``tempfile.gettempdir()`` here
    (``C:\Users\clint\AppData\Local\Temp``), which is *why* that function is the
    translation target rather than an arbitrary choice.
    """
    return tempfile.gettempdir()


def resolve_tmp_path(path: str) -> str:
    """Map a POSIX temp path onto the real temp directory; pass anything else through.

    ``/tmp/a/b.txt`` -> ``<tempdir>/a/b.txt``, so a nested remainder survives with
    separators normalised for this platform. A path that is not a POSIX temp path
    (a relative name, a ``C:`` path, ``%TEMP%``) is returned UNCHANGED: this is a
    name mapping, not a normaliser, and rewriting inputs it was not asked about is
    how a helper starts breaking its callers.

    ``str`` in, ``str`` out. A non-str is returned as given rather than guessed at.
    """
    if not isinstance(path, str):
        return path
    normalised = path.replace("\\", "/")
    for prefix in _POSIX_TEMP_PREFIXES:
        if normalised == prefix:
            return tempfile.gettempdir()
        if prefix.endswith("/") and normalised.startswith(prefix):
            remainder = normalised[len(prefix):]
            parts = [p for p in remainder.split("/") if p not in ("", ".")]
            if not parts:
                return tempfile.gettempdir()
            if ".." in parts:
                return path
            return os.path.join(tempfile.gettempdir(), *parts)
    return path
