# coding: utf-8
"""CORE/paths/__main__.py — Self-test entry point for `python -m CORE.paths`."""
from . import (
    VERSION,
    get_project_root,
    get_src_dir,
    get_output_dir,
    get_desktop_dir,
    get_g_drive,
    get_core_dir,
    get_worms_dir,
    get_meta_dir,
    get_codex_dir,
    get_codex_videos_dir,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

if __name__ == "__main__":
    print(f"CORE/paths/ v{VERSION} (domain-split)")
    print(f"  Project root:  {get_project_root()}")
    print(f"  Src dir:       {get_src_dir()}")
    print(f"  Output dir:    {get_output_dir()}")
    print(f"  Desktop dir:   {get_desktop_dir()}")
    print(f"  G: drive:      {get_g_drive()}")
    print(f"  CORE dir:      {get_core_dir()}")
    print(f"  WORMS dir:     {get_worms_dir()}")
    print(f"  META dir:      {get_meta_dir()}")
    print(f"  CODEX dir:     {get_codex_dir()}")
    print(f"  CODEX/VIDEOS:  {get_codex_videos_dir()}")
