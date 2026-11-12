# coding: utf-8
"""
WORMZ_ML - ML-Enhanced BIBL Terminology Crawler

Predictive terminology crawling with machine learning for intelligent
file classification and BIBL lexicon updates.

Features:
- Predictive file prioritization
- ML-based content classification
- Anomaly detection in file patterns
- Intelligent delta updates
- Learned terminology patterns

Usage:
    >>> from WORMZ.WORMZ_ML import WORMZ_ML
    >>> wml = WORMZ_ML()
    >>> wml.predictive_crawl()
    >>> wml.update_learned_patterns()
"""

import json
import time
import hashlib
import pickle
#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# 3.14t port: numpy has no free-threaded build. Guard the import and use
# plain lists everywhere (sklearn calls accept lists; the file already
# degrades without sklearn via SKLEARN_AVAILABLE).
try:
    import numpy as _np  # noqa: F401 (kept for SKU-level compat checks)
    _NUMPY_AVAILABLE = True
except ImportError as exc:
    note_failure("WORMZ_ML:L32", exc)
    _np = None
    _NUMPY_AVAILABLE = False
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
# NOTE (3.14t port): tkinter imports live in run_popup() — a top-level
# import pointlessly hard-requires Tk on headless interpreters.

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    from sklearn.ensemble import RandomForestClassifier

    SKLEARN_AVAILABLE = True
except ImportError as exc:
    note_failure("WORMZ_ML:L49", exc)
    SKLEARN_AVAILABLE = False


@dataclass
class MLEntry:
    """ML-enhanced BIBL entry."""

    term: str
    category: str
    confidence: float
    occurrences: int
    files: list[str]
    last_found: str
    pattern_score: float


@dataclass
class CrawlPrediction:
    """Prediction for crawl priority."""

    file_path: str
    priority_score: float
    predicted_categories: list[str]
    estimated_terms: int
    risk_level: str


class WORMZ_MLConfig:
    """Configuration for WORMZ_ML."""

    CATEGORIES = [
        "orchestration",
        "monitoring",
        "optimization",
        "analysis",
        "error_handling",
        "clustering",
        "ml",
        "integration",
        "utilities",
        "documentation",
    ]

    SKIP_PATTERNS = [
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        "dist",
        "build",
        "*.egg-info",
        "Windows",
        "Program Files",
        "AppData",
    ]

    # Declared once here so the module-level reconfiguration below
    # (_ROOT-derived path strings) binds known class attributes instead of
    # writing undeclared attributes onto the class object.
    TARGET_FOLDERS: list[str] = []
    OUTPUT_PATH: str = ""
    MODEL_PATH: str = ""
    PATTERNS_PATH: str = ""
    BIBL_PATH: str = ""


from CORE.paths import get_project_root
import argparse


_ROOT = get_project_root()
_OUTPUT_DIR = _ROOT / "output" / "wormz_ml"
_SRC_DIR = _ROOT / "src"

WORMZ_MLConfig.TARGET_FOLDERS = [
    str(_SRC_DIR / "ORCHESTRATION"),
    str(_SRC_DIR / "HYDRA"),
    str(_SRC_DIR / "HORUS.py"),
    str(_SRC_DIR / "UTILITIES" / "TARBABY.py"),
    str(_SRC_DIR / "UTILITIES" / "POINDEXTER"),
    str(_SRC_DIR / "UTILITIES" / "PERSEPHONE"),
    str(_SRC_DIR / "CORE" / "WORMS"),
    str(_SRC_DIR / "META"),
    str(_SRC_DIR / "UTILITIES" / "TERENCE"),
]
WORMZ_MLConfig.OUTPUT_PATH = str(_OUTPUT_DIR)
WORMZ_MLConfig.MODEL_PATH = str(_OUTPUT_DIR / "model.pkl")
WORMZ_MLConfig.PATTERNS_PATH = str(_OUTPUT_DIR / "patterns.json")
WORMZ_MLConfig.BIBL_PATH = str(_SRC_DIR / "UTILITIES" / "BIBL")


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("WORMZ_ML:L134", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="ml", priority=5, cooldown_seconds=60.0, description="ML-enhanced BIBL crawler with predictive file prioritization")
class WORMZ_ML:
    """ML-Enhanced WORMZ with predictive crawling."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = WORMZ_MLConfig()
        self.bibl_path = Path(self.config.BIBL_PATH)
        self.output_path = Path(self.config.OUTPUT_PATH)
        self.model_path = Path(self.config.MODEL_PATH)
        self.patterns_path = Path(self.config.PATTERNS_PATH)
        self._ensure_directories()

        self.learned_patterns: dict[str, list[str]] = {}
        self.ml_entries: list[MLEntry] = []
        self.vectorizer: (TfidfVectorizer | None) = None
        self.cluster_model: (KMeans | None) = None
        self.classifier: (RandomForestClassifier | None) = None
        self.file_features: dict[str, list] = {}

        self._load_patterns()
        self._load_model()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.bibl_path.mkdir(parents=True, exist_ok=True)

    def _extract_features(self, content: str) -> list:
        """Extract TF-IDF features from content (plain list, no numpy)."""
        vec = self.vectorizer
        if vec is None:
            vec = TfidfVectorizer(
                max_features=100,
                stop_words="english",
                ngram_range=(1, 2),
            )
            self.vectorizer = vec
        try:
            return list(vec.fit_transform([content]).toarray()[0])
        except Exception as exc:
            note_failure("WORMZ_ML:L177", exc)
            return [0.0] * 100

    def _calculate_pattern_score(self, term: str, category: str) -> float:
        """Calculate pattern match score."""
        patterns = self.learned_patterns.get(category, [])
        if not patterns:
            return 0.5
        matches = sum(1 for p in patterns if p.lower() in term.lower())
        return min(matches / max(len(patterns), 1), 1.0)

    def _predict_category(self, content: str) -> tuple[str, float]:
        """Predict category of content."""
        if not SKLEARN_AVAILABLE or self.classifier is None:
            return "utilities", 0.5

        features = self._extract_features(content)
        if not any(features):
            return "utilities", 0.5

        try:
            proba = list(self.classifier.predict_proba([features])[0])
            category_idx = max(range(len(proba)), key=lambda i: proba[i])
            category = self.config.CATEGORIES[category_idx]
            confidence = float(proba[category_idx])
            return category, confidence
        except Exception as exc:
            note_failure("WORMZ_ML:L203", exc)
            return "utilities", 0.5

    def _prioritize_files(self, files: list[Path]) -> list[CrawlPrediction]:
        """Predict crawl priority for files."""
        predictions = []
        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                category, confidence = self._predict_category(content)

                hashlib.md5(content.encode()).hexdigest()
                recency_weight = 1.0
                try:
                    mtime = file_path.stat().st_mtime
                    age_days = (time.time() - mtime) / 86400
                    recency_weight = max(0.3, 1.0 - (age_days / 30))
                except Exception as exc:
                    note_failure("WORMZ_ML:L220", exc)

                size_weight = (
                    min(file_path.stat().st_size / 10000, 1.0)
                    if file_path.exists()
                    else 0.5
                )

                priority = confidence * 0.4 + recency_weight * 0.3 + size_weight * 0.3

                estimated_terms = (
                    len(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", content)) // 100
                )

                if confidence < 0.3:
                    risk = "high"
                elif confidence < 0.6:
                    risk = "medium"
                else:
                    risk = "low"

                predictions.append(
                    CrawlPrediction(
                        file_path=str(file_path),
                        priority_score=priority,
                        predicted_categories=[category],
                        estimated_terms=estimated_terms,
                        risk_level=risk,
                    )
                )
            except Exception as exc:
                note_failure("WORMZ_ML:L251", exc)
                predictions.append(
                    CrawlPrediction(
                        file_path=str(file_path),
                        priority_score=0.1,
                        predicted_categories=["unknown"],
                        estimated_terms=0,
                        risk_level="high",
                    )
                )

        return sorted(predictions, key=lambda x: -x.priority_score)

    def _extract_terms(self, content: str) -> list[str]:
        """Extract potential BIBL terms from content."""
        patterns = [
            r"\b[A-Z][a-z]+[A-Z]\w+\b",
            r"\b\w+(?:Z|Zz)\b",
            r"\b(?:[A-Z]{2,}[a-z]*\w*)\b",
            r"def\s+(\w+)",
            r"class\s+(\w+)",
        ]
        terms = set()
        for pattern in patterns:
            matches = re.findall(pattern, content)
            terms.update(matches)
        return list(terms)

    def _learn_patterns(self, entries: list[MLEntry]):
        """Learn patterns from successful entries."""
        for entry in entries:
            if entry.confidence > 0.7:
                category = entry.category
                if category not in self.learned_patterns:
                    self.learned_patterns[category] = []
                self.learned_patterns[category].extend(
                    t for t in entry.files if len(t) > 3
                )
        self._save_patterns()

    def scan_file(self, file_path: str) -> list[MLEntry]:
        """Scan file with ML enhancement."""
        path = Path(file_path)
        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8", errors="ignore")
        terms = self._extract_terms(content)
        category, confidence = self._predict_category(content)

        entries = []
        for term in terms:
            pattern_score = self._calculate_pattern_score(term, category)
            ml_entry = MLEntry(
                term=term,
                category=category,
                confidence=confidence * pattern_score,
                occurrences=1,
                files=[file_path],
                last_found=datetime.now().isoformat(),
                pattern_score=pattern_score,
            )
            entries.append(ml_entry)

        return entries

    def predictive_crawl(
        self,
        folders: (list[str] | None) = None,
        limit: int = 100,
        priority_mode: bool = True,
    ) -> dict[str, Any]:
        """Run predictive crawl."""
        folders = folders or self.config.TARGET_FOLDERS
        all_files = []
        all_entries = []

        for folder in folders:
            folder_path = Path(folder)
            if not folder_path.exists():
                continue

            for pattern in self.config.SKIP_PATTERNS:
                all_files.extend(folder_path.rglob(pattern))

            valid_files = [
                f
                for f in folder_path.rglob("*.py")
                if f not in all_files and f.exists()
            ]

            if priority_mode:
                predictions = self._prioritize_files(valid_files[: limit * 2])
                prioritized = [Path(p.file_path) for p in predictions[:limit]]
            else:
                prioritized = valid_files[:limit]

            for file_path in prioritized:
                entries = self.scan_file(str(file_path))
                all_entries.extend(entries)

        self._learn_patterns(all_entries)

        output = {
            "timestamp": datetime.now().isoformat(),
            "files_scanned": len(prioritized),
            "terms_found": len(all_entries),
            "entries": [
                {
                    "term": e.term,
                    "category": e.category,
                    "confidence": e.confidence,
                    "pattern_score": e.pattern_score,
                }
                for e in all_entries
            ],
            "predictions": [
                {
                    "file_path": p.file_path,
                    "priority": p.priority_score,
                    "category": p.predicted_categories[0],
                    "estimated_terms": p.estimated_terms,
                }
                for p in predictions[:10]
            ]
            if priority_mode
            else [],
        }

        output_file = self.output_path / f"wormz_ml_{int(time.time())}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)

        return output

    def train_model(self, training_data: list[dict[str, Any]]):
        """Train ML model on labeled data."""
        if not SKLEARN_AVAILABLE:
            print("sklearn not available")
            return

        if len(training_data) < 10:
            print("Need more training data")
            return

        texts = [d["content"] for d in training_data]
        labels = [d["category"] for d in training_data]

        try:
            vec = TfidfVectorizer(
                max_features=100,
                stop_words="english",
                ngram_range=(1, 2),
            )
            self.vectorizer = vec
            features = vec.fit_transform(texts)

            km = KMeans(n_clusters=len(self.config.CATEGORIES))
            self.cluster_model = km
            km.fit(features)

            rf = RandomForestClassifier(n_estimators=100)
            self.classifier = rf
            rf.fit(features, labels)

            self._save_model()
            print("Model trained successfully")
        except Exception as e:
            print(f"Training failed: {e}")

    def _save_model(self):
        """Persist ML model."""
        if not SKLEARN_AVAILABLE:
            return
        try:
            model_data = {
                "vectorizer": self.vectorizer,
                "cluster_model": self.cluster_model,
                "classifier": self.classifier,
            }
            with open(self.model_path, "wb") as f:
                pickle.dump(model_data, f)
        except Exception as e:
            print(f"Failed to save model: {e}")

    def _load_model(self):
        """Load persisted ML model."""
        if not SKLEARN_AVAILABLE or not self.model_path.exists():
            return
        try:
            with open(self.model_path, "rb") as f:
                model_data = pickle.load(f)
            self.vectorizer = model_data.get("vectorizer")
            self.cluster_model = model_data.get("cluster_model")
            self.classifier = model_data.get("classifier")
        except Exception as exc:
            note_failure("WORMZ_ML:L443", exc)

    def _save_patterns(self):
        """Persist learned patterns."""
        self.patterns_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.patterns_path, "w", encoding="utf-8") as f:
            json.dump(self.learned_patterns, f, indent=2)

    def _load_patterns(self):
        """Load persisted patterns."""
        if self.patterns_path.exists():
            try:
                with open(self.patterns_path, encoding="utf-8") as f:
                    self.learned_patterns = json.load(f)
            except Exception as exc:
                note_failure("WORMZ_ML:L458", exc)
                self.learned_patterns = {}

    def update_learned_patterns(self) -> dict[str, Any]:
        """Update patterns from recent crawl data."""
        output_files = list(self.output_path.glob("wormz_ml_*.json"))
        all_entries = []

        for of in output_files[-10:]:
            try:
                with open(of, encoding="utf-8") as f:
                    data = json.load(f)
                    all_entries.extend(
                        MLEntry(
                            term=e["term"],
                            category=e["category"],
                            confidence=e["confidence"],
                            occurrences=1,
                            files=[],
                            last_found="",
                            pattern_score=e.get("pattern_score", 0.5),
                        )
                        for e in data.get("entries", [])
                    )
            except Exception as exc:
                note_failure("WORMZ_ML:L482", exc)

        self._learn_patterns(all_entries)

        return {
            "patterns_updated": len(self.learned_patterns),
            "total_entries": len(all_entries),
            "timestamp": datetime.now().isoformat(),
        }

    def get_status(self) -> dict[str, Any]:
        """Get current ML status."""
        return {
            "version": self.VERSION,
            "sklearn_available": SKLEARN_AVAILABLE,
            "model_loaded": self.classifier is not None,
            "patterns_count": sum(len(p) for p in self.learned_patterns.values()),
            "categories": self.config.CATEGORIES,
            "output_path": str(self.output_path),
        }

    def run_popup(self):
        import tkinter as tk
        from tkinter import ttk, scrolledtext
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("WORMZ_ML - Predictive BIBL Crawler")
        root.geometry("800x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        crawl_tab = ttk.Frame(notebook)
        notebook.add(crawl_tab, text="Crawl")

        patterns_tab = ttk.Frame(notebook)
        notebook.add(patterns_tab, text="Patterns")

        status_label = ttk.Label(
            crawl_tab, text="Ready for predictive crawl", font=("Arial", 12)
        )
        status_label.pack(pady=10)

        log_text = scrolledtext.ScrolledText(crawl_tab, width=80, height=25)
        log_text.pack(fill="both", expand=True, padx=10, pady=10)

        def run_crawl():
            status_label.config(text="Crawling with ML predictions...")
            result = self.predictive_crawl()
            log_text.insert("end", json.dumps(result, indent=2))
            status_label.config(
                text=f"Crawl complete: {result['files_scanned']} files, {result['terms_found']} terms"
            )

        ttk.Button(
            crawl_tab,
            text="Predictive Crawl",
            command=run_crawl,
        ).pack(pady=10)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="WORMZ_ML - ML-Enhanced BIBL Crawler")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--crawl", action="store_true", help="Run predictive crawl")
    parser.add_argument("--status", action="store_true", help="Show ML status")
    parser.add_argument("--patterns", action="store_true", help="Show learned patterns")
    parser.add_argument("--limit", type=int, default=100, help="Files to scan")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    crawler = WORMZ_ML()

    if args.status:
        print(json.dumps(crawler.get_status(), indent=2))
    elif args.patterns:
        print(json.dumps(crawler.learned_patterns, indent=2))
    elif args.crawl:
        result = crawler.predictive_crawl(limit=args.limit)
        print(f"Files: {result['files_scanned']}, Terms: {result['terms_found']}")
    elif args.popup:
        crawler.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


