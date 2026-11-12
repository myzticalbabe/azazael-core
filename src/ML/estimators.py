# coding: utf-8
"""Native azazael wrappers for the eight classical estimator families.

Each family is a frozen :class:`EstimatorSpec` (family name, builder, default
params, problem kinds). :func:`make_estimator` builds a fresh unfitted
estimator; :func:`fit_score` fits and returns ``(estimator, score)``.

Defaults encode audit findings (2026-09-20, ``flet_test/ml_audit.py``):
MLP ships as ``StandardScaler + MLPClassifier(solver="lbfgs")`` because the
bare ``adam`` default stalls below tolerance on small dense sets
(``ConvergenceWarning``). All stochastic estimators pin ``random_state=0``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ESTIMATOR_FAMILIES = (
    "linear_regression",
    "logistic_regression",
    "knn",
    "svm",
    "decision_tree",
    "random_forest",
    "gradient_boosting",
    "neural_network",
)


def _linear_regression(**overrides: Any) -> Any:
    from sklearn.linear_model import LinearRegression

    return LinearRegression(**overrides)


def _logistic_regression(**overrides: Any) -> Any:
    from sklearn.linear_model import LogisticRegression

    params = {"max_iter": 1000, **overrides}
    return LogisticRegression(**params)


def _knn(**overrides: Any) -> Any:
    from sklearn.neighbors import KNeighborsClassifier

    return KNeighborsClassifier(**overrides)


def _svm(**overrides: Any) -> Any:
    from sklearn.svm import SVC

    params = {"random_state": 0, **overrides}
    return SVC(**params)


def _decision_tree(**overrides: Any) -> Any:
    from sklearn.tree import DecisionTreeClassifier

    params = {"random_state": 0, **overrides}
    return DecisionTreeClassifier(**params)


def _random_forest(**overrides: Any) -> Any:
    from sklearn.ensemble import RandomForestClassifier

    params = {"random_state": 0, **overrides}
    return RandomForestClassifier(**params)


def _gradient_boosting(**overrides: Any) -> Any:
    from sklearn.ensemble import GradientBoostingClassifier

    params = {"random_state": 0, **overrides}
    return GradientBoostingClassifier(**params)


def _neural_network(**overrides: Any) -> Any:
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    params = {"solver": "lbfgs", "max_iter": 1000, "random_state": 0, **overrides}
    return make_pipeline(StandardScaler(), MLPClassifier(**params))


_BUILDERS: dict[str, Callable[..., Any]] = {
    "linear_regression": _linear_regression,
    "logistic_regression": _logistic_regression,
    "knn": _knn,
    "svm": _svm,
    "decision_tree": _decision_tree,
    "random_forest": _random_forest,
    "gradient_boosting": _gradient_boosting,
    "neural_network": _neural_network,
}

_KINDS: dict[str, str] = {
    "linear_regression": "regression",
    "logistic_regression": "classification",
    "knn": "classification",
    "svm": "classification",
    "decision_tree": "classification",
    "random_forest": "classification",
    "gradient_boosting": "classification",
    "neural_network": "classification",
}


_DEFAULTS: dict[str, dict[str, Any]] = {
    "linear_regression": {},
    "logistic_regression": {"max_iter": 1000},
    "knn": {},
    "svm": {"random_state": 0},
    "decision_tree": {"random_state": 0},
    "random_forest": {"random_state": 0},
    "gradient_boosting": {"random_state": 0},
    "neural_network": {"solver": "lbfgs", "max_iter": 1000, "random_state": 0},
}


@dataclass(frozen=True)
class EstimatorSpec:
    """Frozen description of one estimator family."""

    family: str
    kind: str
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def builder(self) -> Callable[..., Any]:
        return _BUILDERS[self.family]


def describe(family: str) -> EstimatorSpec:
    """Return the spec for a family; raises ``KeyError`` when unknown."""
    if family not in _BUILDERS:
        raise KeyError(f"unknown estimator family: {family!r} (see ESTIMATOR_FAMILIES)")
    return EstimatorSpec(family=family, kind=_KINDS[family], defaults=dict(_DEFAULTS[family]))


def make_estimator(family: str, **overrides: Any) -> Any:
    """Build a fresh unfitted estimator for ``family``.

    Overrides replace individual constructor params; unknown families raise
    ``KeyError``.
    """
    if family not in _BUILDERS:
        raise KeyError(f"unknown estimator family: {family!r} (see ESTIMATOR_FAMILIES)")
    return _BUILDERS[family](**overrides)


def fit_score(family: str, X: Any, y: Any, **overrides: Any) -> tuple[Any, float]:
    """Fit a fresh estimator for ``family`` and return ``(estimator, score)``."""
    est = make_estimator(family, **overrides)
    est.fit(X, y)
    return est, float(est.score(X, y))
