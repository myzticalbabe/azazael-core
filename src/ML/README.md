# ML — native azazael machine-learning package

Owns the classical estimator families plus the supporting numerical
machinery the skills catalog describes. Stateless factories only (no
module-level mutable state): safe under Python 3.14t free-threading.

## Contents

| Module | What | Source |
|---|---|---|
| `estimators` | 8 families: linear/logistic regression, kNN, SVM, decision tree, random forest, gradient boosting, neural network (MLP). `make_estimator` / `fit_score` / `describe` | sklearn 1.9.1 (verified both interpreters) |
| `autodiff` | Reverse-mode via mygrad, dual-number + complex-step forward-mode, SGD/Momentum/Adam from first principles, finite-difference HVP | mygrad + numpy |
| `nsga` | NSGA-II: SBX crossover, polynomial mutation, non-dominated sort, crowding distance, `NSGA2` driver | numpy, from scratch |
| `gosp` | Online session-outcome learner (`DictVectorizer + GradientBoosting`, warm-start refits, lock-guarded). Semantics defined here: rows are trace-stat feature dicts + caller-supplied `label` | sklearn |
| `torch_bridge` | Lazy optional torch (`torch_available`, `require_torch`, `to_torch`, `to_numpy`). torch is absent/broken on this box and in WSL (no CUDA libs) — never a hard dep | — |

## Defaults that encode audit findings

- MLP ships as `StandardScaler + MLPClassifier(solver="lbfgs")`: bare `adam`
  stalls below tolerance on small dense sets (`ConvergenceWarning`).
- Stochastic estimators pin `random_state=0` for determinism.

## Verify

```powershell
python3.14t.exe -m pytest src/tests/test_ml_estimators.py src/tests/test_ml_autodiff.py src/tests/test_ml_nsga.py src/tests/test_ml_gosp.py
python3.14t.exe -m ruff check src/ML src/tests/test_ml_*.py
# --explicit-package-bases is required by the repo's src layout:
python3.14t.exe -m mypy --explicit-package-bases ML   # run from src/
```

## Platform notes

- Windows 3.14t: everything above passes (25 tests). mygrad/ruff/mypy present.
- WSL Ubuntu (3.14t): sklearn 1.8.0 + mygrad present; torch broken (no
  libcublas) — `torch_bridge` reports this instead of crashing.
- No native estimator/autodiff/evolution code existed anywhere in the tree
  (Windows or WSL mirror) before this package; `ml_platforms` remains
  inference/analysis infra, untouched.
