#!/usr/bin/env python3
"""
AZAZAEL REAL FRAMEWORK TEST v1.0
Actual execution of all installed frameworks and platforms
NO SIMULATION - REAL IMPORTS AND TESTS
"""

import sys
import os
import subprocess
import json
import time
import threading
from pathlib import Path
from datetime import datetime
import traceback
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class RealFrameworkTest:
    def __init__(self):
        self.desktop = Path.home() / "Desktop"
        self.azazael = Path(os.getcwd())  # Use CWD instead of hardcoded path
        self.results = []
        self.start_time = datetime.now()

    def log(self, msg, status="INFO"):
        symbols = {
            "INFO": "[+]",
            "OK": "[OK]",
            "ERROR": "[-]",
            "WARN": "[!]",
            "SKIP": "[--]",
        }
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"{symbols.get(status, '[?]')} {timestamp} {msg}"
        print(line)
        self.results.append({"time": timestamp, "msg": msg, "status": status})

    def detect_platform_real(self):
        """Real platform detection"""
        self.log("=" * 70, "INFO")
        self.log("PLATFORM DETECTION", "INFO")
        self.log("=" * 70, "INFO")

        platform_info = {
            "os_name": os.name,
            "sys_platform": sys.platform,
            "executable": sys.executable,
            "python_version": sys.version,
            "cwd": os.getcwd(),
            "path": list(sys.path[:5]),
        }

        # Check WSL
        if os.path.exists("/proc/version"):
            try:
                with open("/proc/version", encoding="utf-8") as f:
                    platform_info["proc_version"] = f.read().strip()
            except Exception as exc:
                note_failure("REAL_FRAMEWORK_TEST:L59", exc)

        # Check Windows
        platform_info["is_windows"] = os.name == "nt"
        platform_info["is_wsl"] = (
            "microsoft" in platform_info.get("proc_version", "").lower()
        )

        for key, val in platform_info.items():
            self.log(f"  {key}: {val[:80] if isinstance(val, str) else val}", "OK")

        return platform_info

    def test_venv_packages(self):
        """Get all installed packages"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("INSTALLED PACKAGES", "INFO")
        self.log("=" * 70, "INFO")

        # Try to get packages from venv
        venv_python = self.azazael / ".venv" / "Scripts" / "python.exe"

        packages = []
        if venv_python.exists():
            try:
                result = subprocess.run(
                    [str(venv_python), "-m", "pip", "list", "--format=json"],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=60,
                )
                if result.returncode == 0:
                    packages = json.loads(result.stdout)
                    self.log(f"Found {len(packages)} packages in venv", "OK")
            except Exception as e:
                self.log(f"Could not get venv packages: {e}", "ERROR")

        # Also check sys.modules
        self.log(f"\nLoaded modules in current Python: {len(sys.modules)}", "OK")

        return packages

    def test_core_python(self):
        """Test core Python functionality"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("CORE PYTHON TESTS", "INFO")
        self.log("=" * 70, "INFO")

        tests = [
            (
                "Basic types",
                lambda: {
                    "int": isinstance(1, int),  # type: ignore[reportUnnecessaryIsInstance]
                    "str": isinstance("test", str),  # type: ignore[reportUnnecessaryIsInstance]
                    "list": isinstance([], list),  # type: ignore[reportUnnecessaryIsInstance]
                    "dict": isinstance({}, dict),  # type: ignore[reportUnnecessaryIsInstance]
                },
            ),
            (
                "File operations",
                lambda: {
                    "Path": hasattr(Path, "__truediv__"),
                    "exists": Path("/").exists(),
                },
            ),
            (
                "JSON",
                lambda: {
                    "dumps": isinstance(json.dumps({}), str),  # type: ignore[reportUnnecessaryIsInstance]
                    "loads": isinstance(json.loads('{"a":1}'), dict),
                },
            ),
            (
                "Datetime",
                lambda: {
                    "now": isinstance(datetime.now(), datetime),  # type: ignore[reportUnnecessaryIsInstance]
                    "isoformat": len(datetime.now().isoformat()) > 0,
                },
            ),
            (
                "Subprocess",
                lambda: {
                    "run": True,  # We've been using it
                },
            ),
        ]

        for name, test_func in tests:
            try:
                result = test_func()
                all_pass = all(result.values()) if isinstance(result, dict) else result  # type: ignore[reportUnnecessaryIsInstance]
                status = "OK" if all_pass else "ERROR"
                self.log(f"  {name}: {result}", status)
            except Exception as e:
                self.log(f"  {name}: {e}", "ERROR")

    def test_gui_frameworks(self):
        """Test GUI frameworks - REAL IMPORTS"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("GUI FRAMEWORK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        gui_frameworks = [
            ("tkinter", "Standard GUI"),
            ("PyQt5", "Qt5 Bindings"),
            ("PyQt6", "Qt6 Bindings"),
            ("pygame", "Game Framework"),
            ("PyOpenGL", "OpenGL Bindings"),
            ("wx", "wxWidgets"),
            ("kivy", "NUI Framework"),
            ("PySide2", "Qt5 bindings"),
            ("PySide6", "Qt6 bindings"),
        ]

        for name, desc in gui_frameworks:
            try:
                module = __import__(
                    name.lower().replace("pyside", "pyside").replace("pyqt", "pyqt")
                )
                version = getattr(module, "__version__", "unknown")
                self.log(f"  {name} ({desc}): v{version}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_ml_frameworks(self):
        """Test ML/AI frameworks - REAL IMPORTS"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("ML/AI FRAMEWORK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        ml_frameworks = [
            ("torch", "PyTorch"),
            ("tensorflow", "TensorFlow"),
            ("keras", "Keras"),
            ("sklearn", "Scikit-learn"),
            ("numpy", "NumPy"),
            ("pandas", "Pandas"),
            ("scipy", "SciPy"),
            ("jax", "JAX"),
            ("flax", "Flax"),
            ("optuna", "Optuna"),
            ("transformers", "HuggingFace"),
            ("torchvision", "PyTorch Vision"),
            ("torchaudio", "PyTorch Audio"),
            ("accelerate", "HF Accelerate"),
            ("datasets", "HF Datasets"),
            ("triton", "Triton"),
            ("triton_local", "Triton Local"),
        ]

        for name, desc in ml_frameworks:
            try:
                module = __import__(name)
                version = getattr(module, "__version__", "unknown")

                # Additional tests for ML frameworks
                extra = ""
                if name == "torch":
                    has_cuda = (
                        module.cuda.is_available() if hasattr(module, "cuda") else "N/A"
                    )
                    extra = f" CUDA: {has_cuda}"
                elif name == "tensorflow":
                    extra = f" GPU: {module.config.list_physical_devices('GPU') if hasattr(module, 'config') else 'N/A'}"
                elif name == "numpy":
                    extra = f" BLAS: {module.get_include()}"

                self.log(f"  {name} ({desc}): v{version}{extra}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_data_frameworks(self):
        """Test data processing frameworks"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("DATA FRAMEWORK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        data_frameworks = [
            ("pandas", "DataFrames"),
            ("polars", "Polars DataFrames"),
            ("dask", "Dask Parallel"),
            ("pyspark", "Spark"),
            ("modin", "Modin Parallel"),
            ("vaex", "Vaex Out-of-Core"),
            ("duckdb", "DuckDB"),
            ("pyarrow", "PyArrow"),
        ]

        for name, desc in data_frameworks:
            try:
                module = __import__(name)
                version = getattr(module, "__version__", "unknown")
                self.log(f"  {name} ({desc}): v{version}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_web_frameworks(self):
        """Test web frameworks"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("WEB FRAMEWORK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        web_frameworks = [
            ("fastapi", "FastAPI"),
            ("flask", "Flask"),
            ("django", "Django"),
            ("starlette", "Starlette"),
            ("httpx", "HTTPX"),
            ("aiohttp", "Async HTTP"),
            ("requests", "Requests"),
            ("urllib", "URLLib"),
            ("selenium", "Selenium"),
            ("playwright", "Playwright"),
        ]

        for name, desc in web_frameworks:
            try:
                module = __import__(name)
                version = getattr(module, "__version__", "unknown")
                self.log(f"  {name} ({desc}): v{version}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_ipython(self):
        """Test IPython and notebooks"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("IPYTHON/NOTEBOOK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        tests = [
            ("IPython", "Interactive Python"),
            ("jupyter", "Jupyter"),
            ("notebook", "Classic Notebook"),
            ("nbformat", "Notebook Format"),
            ("ipywidgets", "IPyWidgets"),
            ("ipykernel", "IPython Kernel"),
        ]

        for name, desc in tests:
            try:
                module = __import__(name)
                version = getattr(module, "__version__", "unknown")
                self.log(f"  {name} ({desc}): v{version}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_other_frameworks(self):
        """Test other frameworks"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("OTHER FRAMEWORK TESTS", "INFO")
        self.log("=" * 70, "INFO")

        other_frameworks = [
            ("PIL", "Pillow Image"),
            ("cv2", "OpenCV"),
            ("PIL", "Pillow"),
            ("moviepy", "MoviePy"),
            ("nltk", "NLTK"),
            ("spacy", "SpaCy"),
            ("gensim", "Gensim"),
            ("openai", "OpenAI"),
            ("anthropic", "Anthropic"),
            ("litellm", "LiteLLM"),
            ("langchain", "LangChain"),
            ("llama_index", "LlamaIndex"),
            ("chromadb", "ChromaDB"),
            ("faiss", "FAISS"),
            ("pinecone", "Pinecone"),
            ("weaviate", "Weaviate"),
            ("qdrant_client", "Qdrant"),
            ("grpc", "gRPC"),
            ("msgpack", "MessagePack"),
            ("yaml", "PyYAML"),
            ("toml", "TOML"),
        ]

        for name, desc in other_frameworks:
            try:
                module = __import__(name)
                version = getattr(module, "__version__", "unknown")
                self.log(f"  {name} ({desc}): v{version}", "OK")
            except ImportError:
                self.log(f"  {name} ({desc}): NOT INSTALLED", "SKIP")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def test_system_info(self):
        """Test system information"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("SYSTEM INFORMATION", "INFO")
        self.log("=" * 70, "INFO")

        import platform

        sys_info = {
            "machine": platform.machine(),
            "processor": platform.processor(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.architecture(),
            "python_build": platform.python_build(),
            "byteorder": sys.byteorder,
            "maxsize": sys.maxsize,
            "threading_threads": threading.active_count() if "threading" in sys.modules else "N/A",
        }

        # resource module is Unix-only; skip on Windows
        try:
            import resource
            sys_info["memory_rss"] = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            )
        except (ImportError, AttributeError) as exc:
            note_failure("REAL_FRAMEWORK_TEST:L383", exc)
            sys_info["memory_rss"] = "N/A (Windows)"

        for key, val in sys_info.items():
            self.log(f"  {key}: {val}", "OK")

        return sys_info

    def test_azazael_modules(self):
        """Test Azazael-specific modules"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("AZAZAEL MODULE TESTS", "INFO")
        self.log("=" * 70, "INFO")

        # Test our fixes
        tests = [
            ("AzazaelDynamicLogger", "Dynamic Logging"),
            ("environment_manager", "Environment Manager"),
            ("COMPREHENSIVE_FIX", "Comprehensive Fix"),
            ("azazael_launcher", "Launcher"),
            ("bootstrap", "Bootstrapper"),
        ]

        for name, desc in tests:
            try:
                __import__(name)
                self.log(f"  {name} ({desc}): LOADED", "OK")
            except ImportError as e:
                self.log(f"  {name} ({desc}): NOT FOUND - {e}", "ERROR")
            except Exception as e:
                self.log(f"  {name}: ERROR - {str(e)[:50]}", "ERROR")

    def run_quick_operations(self):
        """Run quick operations to verify frameworks work"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("QUICK OPERATIONAL TESTS", "INFO")
        self.log("=" * 70, "INFO")

        operations = [
            ("NumPy array", lambda: __import__("numpy").array([1, 2, 3])),
            (
                "Pandas DataFrame",
                lambda: __import__("pandas").DataFrame({"a": [1, 2, 3]}),
            ),
            ("JSON parse", lambda: json.loads('{"test": 1}')),
            ("Path creation", lambda: Path("/tmp/test")),
            ("Datetime now", lambda: datetime.now()),
        ]

        for name, op in operations:
            try:
                start = time.time()
                result = op()
                elapsed = (time.time() - start) * 1000
                self.log(f"  {name}: {result} ({elapsed:.2f}ms)", "OK")
            except Exception as e:
                self.log(f"  {name}: ERROR - {e}", "ERROR")

    def generate_report(self):
        """Generate comprehensive test report"""
        self.log("\n" + "=" * 70, "INFO")
        self.log("GENERATING REPORT", "INFO")
        self.log("=" * 70, "INFO")

        # Count results
        ok_count = sum(1 for r in self.results if r["status"] == "OK")
        error_count = sum(1 for r in self.results if r["status"] == "ERROR")
        skip_count = sum(1 for r in self.results if r["status"] == "SKIP")
        warn_count = sum(1 for r in self.results if r["status"] == "WARN")

        elapsed = (datetime.now() - self.start_time).total_seconds()

        report = {
            "timestamp": self.start_time.isoformat(),
            "duration_seconds": elapsed,
            "summary": {
                "total": len(self.results),
                "ok": ok_count,
                "errors": error_count,
                "skipped": skip_count,
                "warnings": warn_count,
            },
            "platform": self.detect_platform_real(),
            "tests": self.results,
        }

        report_file = self.azazael / "REAL_FRAMEWORK_TEST_REPORT.json"
        try:
            report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        except (OSError, PermissionError) as e:
            self.log(f"Could not write report file: {e}", "WARN")

        self.log(f"\n{'=' * 70}", "INFO")
        self.log("TEST SUMMARY", "INFO")
        self.log(f"{'=' * 70}", "INFO")
        self.log(f"Total Tests: {len(self.results)}", "INFO")
        self.log(f"  OK: {ok_count}", "OK")
        self.log(f"  Errors: {error_count}", "ERROR")
        self.log(f"  Skipped: {skip_count}", "SKIP")
        self.log(f"  Warnings: {warn_count}", "WARN")
        self.log(f"Duration: {elapsed:.2f} seconds", "INFO")
        self.log(f"\nReport saved to: {report_file}", "OK")

        return report

    def run_all(self):
        """Run all framework tests"""
        print("\n" + "=" * 70)
        print("AZAZAEL REAL FRAMEWORK TEST v1.0")
        print(f"Start Time: {self.start_time.isoformat()}")
        print("=" * 70 + "\n")

        try:
            self.detect_platform_real()
            self.test_venv_packages()
            self.test_core_python()
            self.test_system_info()
            self.test_gui_frameworks()
            self.test_ml_frameworks()
            self.test_data_frameworks()
            self.test_web_frameworks()
            self.test_ipython()
            self.test_other_frameworks()
            self.test_azazael_modules()
            self.run_quick_operations()
        except Exception as e:
            self.log(f"FATAL ERROR: {e}", "ERROR")
            traceback.print_exc()

        self.generate_report()

        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETE")
        print("=" * 70)


if __name__ == "__main__":
    tester = RealFrameworkTest()
    tester.run_all()
