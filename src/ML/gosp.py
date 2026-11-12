# coding: utf-8
"""Online session-outcome learner (GOSP).

Assumed semantics (no prior implementation exists anywhere in the tree, so
this module defines them): rows are feature dicts describing an agent
session — e.g. trace-stat counters from ``ML``-audited mindwalk traces —
plus a caller-supplied outcome label. :class:`SessionOutcomeLearner` keeps a
``DictVectorizer + GradientBoostingClassifier`` pair and refits with
``warm_start`` on every :meth:`update`, which makes repeated online updates
cheap and keeps a single instance safe to share across free-threads (one
instance-level lock guards refits).
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

LABEL_KEY = "label"


class SessionOutcomeLearner:
    """Incremental classifier over session feature rows."""

    def __init__(self, random_state: int = 0) -> None:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.feature_extraction import DictVectorizer

        self._vectorizer = DictVectorizer(sparse=False)
        self._model = GradientBoostingClassifier(random_state=random_state)
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._labels: list[Any] = []
        self._fitted = False

    def update(self, rows: Iterable[Mapping[str, Any]]) -> "SessionOutcomeLearner":
        """Add labeled rows (each mapping must carry ``LABEL_KEY``) and refit."""
        new_rows = [dict(r) for r in rows]
        labels = []
        feats = []
        for row in new_rows:
            if LABEL_KEY not in row:
                raise KeyError(f"row missing {LABEL_KEY!r}: {sorted(row)}")
            labels.append(row.pop(LABEL_KEY))
            feats.append(row)
        with self._lock:
            self._rows.extend(feats)
            self._labels.extend(labels)
            if not self._fitted:
                # The vocabulary is fixed at first fit; later rows with
                # unseen keys have those keys ignored by transform. This
                # keeps warm-start refits dimensionally stable.
                X = self._vectorizer.fit_transform(self._rows)
                self._model.fit(X, self._labels)
                self._fitted = True
            else:
                X = self._vectorizer.transform(self._rows)
                self._model.set_params(warm_start=True)
                self._model.fit(X, self._labels)
        return self

    @property
    def fitted(self) -> bool:
        with self._lock:
            return self._fitted

    def predict(self, rows: Iterable[Mapping[str, Any]]) -> list[Any]:
        """Predict labels for unlabeled feature rows."""
        with self._lock:
            if not self._fitted:
                raise RuntimeError("SessionOutcomeLearner is not fitted yet")
            X = self._vectorizer.transform([dict(r) for r in rows])
            return list(self._model.predict(X))  # type: ignore[reportArgumentType]

    def predict_proba(self, rows: Iterable[Mapping[str, Any]]) -> list[list[float]]:
        """Positive-class probabilities for unlabeled feature rows."""
        with self._lock:
            if not self._fitted:
                raise RuntimeError("SessionOutcomeLearner is not fitted yet")
            X = self._vectorizer.transform([dict(r) for r in rows])
            return [list(map(float, p)) for p in self._model.predict_proba(X)]
