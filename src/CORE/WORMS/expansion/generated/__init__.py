# coding: utf-8
"""PROMOTED morphogenesis specialists.

This package exists so that `worm_registry.discover_worms()` descends into
it: the walk only enters a subdirectory that holds an `__init__.py`.

Files in here are *generated* source that passed the containment gate in
`expansion/spine.py::MorphogenesisWorm.promote()` — quarantine, then a
"promote" vote from distinct envs (or an explicit operator `force`).

Candidates awaiting that gate live in `_staging/` next door, which has no
`__init__.py` and an `_`-prefixed name, so discovery skips it twice over and
a staged file can never be imported as a worm.

Do not hand-edit the generated modules; they are overwritten by signature.
"""

__all__: list[str] = []
