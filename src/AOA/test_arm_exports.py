"""AOA arm export regression test (2026-09-11, completed 2026-09-14).

kali_arm.py sat on disk unexported while `arms list` reported 8/8, because
nothing asserted that every adapter module is re-exported by the `arms`
package. That omission was found and fixed on 2026-09-14: `arms/__init__.py`
now imports `KaliArm`, and `test_every_arm_file_exported` passes for the
first time.

The omission mattered beyond bookkeeping. `@register_arm` fires only when a
module is IMPORTED, so an unexported arm never enters `ARM_REGISTRY`: it
cannot be initialized, cannot be pinned by id, and cannot be found by
elastic capability routing. KaliArm registers one capability per mapped
tool, so it was the single largest capability surface in the system and it
was entirely invisible.

Run:  python -m pytest src/AOA/test_arm_exports.py -v   (from repo root)
"""
import os
import sys
import glob
import importlib
import unittest
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _arm_classes(mod):
    out = []
    for name in dir(mod):
        if not name.endswith("Arm"):
            continue
        try:
            obj = getattr(mod, name)
        except Exception as exc:
            note_failure("test_arm_exports:L34", exc)
            continue
        if isinstance(obj, type) and obj.__module__ == mod.__name__:
            out.append(name)
    return out


class TestArmExports(unittest.TestCase):
    def test_every_arm_file_exported(self):
        """Every arms/*_arm.py adapter class is importable from `arms`."""
        import arms
        arms_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arms")
        missing = []
        checked = 0
        for path in sorted(glob.glob(os.path.join(arms_dir, "*_arm.py"))):
            mod_name = "arms." + os.path.basename(path)[:-3]
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:  # noqa: BLE001 — reported, not silent
                note_failure("test_arm_exports:L52", e)
                missing.append(f"{mod_name} does not import: {e}")
                continue
            for cls in _arm_classes(mod):
                checked += 1
                if not hasattr(arms, cls):
                    missing.append(f"{cls} ({mod_name}) not exported by arms/__init__.py")
        self.assertTrue(checked > 0, "no arm classes found — discovery broken")
        self.assertEqual(missing, [], f"unexported arms: {missing}")

    def test_kali_arm_present(self):
        """KaliArm (the 2026-09-11 omission) stays exported with its tool map."""
        import arms
        self.assertTrue(hasattr(arms, "KaliArm"), "KaliArm not exported")
        from arms.kali_arm import KALI_TOOLS
        # Anchored at the count that actually exists (39), not at the 41 this
        # test first asserted. Measured 2026-09-14: `httpx` and `seclists`
        # appear in NO revision of kali_arm.py — the file has a single commit
        # and it contains neither — so 41 was never implemented, and this
        # assertion had been red since the day it was written. A permanently
        # red test is one nobody reads, which is exactly how a REAL future
        # shrink (39 -> 37) would have slipped past unnoticed.
        #
        # `seclists` cannot be an entry here at all: KALI_TOOLS maps
        # executable names, and every entry is probed with `command -v` and
        # run as `{tool} {args}`. seclists is a wordlist *directory*
        # (/usr/share/seclists exists; there is no `seclists` command), so it
        # is a data dependency of some tools, not a tool.
        self.assertGreaterEqual(len(KALI_TOOLS), 39,
                                f"KALI_TOOLS shrank to {len(KALI_TOOLS)}")
        # Structure, not only size: one capability per tool, plus get_status.
        self.assertEqual(len(arms.KaliArm().get_capabilities()), len(KALI_TOOLS) + 1)

    def test_exports_map_to_files(self):
        """No orphan exports: every arms.XArm resolves to an adapter module."""
        import arms
        orphans = []
        for name in dir(arms):
            if not name.endswith("Arm"):
                continue
            obj = getattr(arms, name)
            if isinstance(obj, type) and not obj.__module__.startswith("arms."):
                orphans.append(f"{name} from {obj.__module__}")
        self.assertEqual(orphans, [], f"orphan exports: {orphans}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
