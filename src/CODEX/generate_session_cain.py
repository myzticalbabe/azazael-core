#!/usr/bin/env python
# coding: utf-8
"""DEPRECATED — use `inject_session_cain.py`.

This module used to build a CAIN report from scratch: it carried its own copy of
the template's CSS as a Python string literal and never read the template file.
That is the defect the `azazael-cain-template` skill names under CRITICAL —
"never generate CAIN HTML from scratch" — and it had a concrete cost: the
template was later restyled to real glyphs (⚠️ ❌ ✅ ⚡ →) while the copy in here
kept the old ones, and the two drifted apart.

Retired 2026-11-01. The original 34,321-byte implementation is in git history
(last real change 255752be, 2026-10-09) if any of it is ever needed again.

Every call now delegates to `inject_session_cain`, so a stale caller still gets
a correct report rather than a from-scratch reconstruction:

    python generate_session_cain.py --output report.html

    -> python inject_session_cain.py -o report.html
"""

import sys

# Top-level rather than inside main(): an import in a function body is the
# `import_inside_function` anti-pattern, and hiding an ImportError until the
# first call is what makes a retired entry point look live. Failing at import
# with a message that names the fix is the honest shape for a delegator.
try:
    import inject_session_cain
except ImportError as _exc:  # pragma: no cover - only outside src/CODEX
    raise SystemExit(
        "generate_session_cain delegates to inject_session_cain; run it from "
        "src/CODEX (or put that directory on sys.path): %s" % _exc) from _exc

WARNING = ("generate_session_cain is retired: it rebuilt the template from "
           "scratch and its embedded CSS had drifted. Delegating to "
           "inject_session_cain.py instead.")


def main(argv=None) -> int:
    print(WARNING, file=sys.stderr)
    return inject_session_cain.main(argv)


if __name__ == "__main__":
    sys.exit(main())
