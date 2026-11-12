# coding: utf-8
"""TOOLBELTZ - capability-probe worms for the internal toolbelt.

One thin worm per internal toolbelt module (TOOLS, UTILITIES, SCRIPTS,
AZAZAEL_TOOLKIT, META). Each exposes scan(), which smoke-probes the tool
with py_compile in a subprocess and returns a structured result. No
mutations, no targets, no writes - capability probes only.

Autonomy contract (mirrors KALIWORMS):
- @register_worm applied at import -> auto-discovered into WORM_REGISTRY
- no-arg constructors; NO get_summary/get_status (they would shadow scan())
- scan() is parameterless; cluster machinery invokes it directly
"""

import subprocess
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("TOOLBELTZ:L21", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


_SRC = Path(__file__).resolve().parent.parent.parent
_PROBE_TIMEOUT = 60


class _ToolBeltWorm:
    """Base: smoke-probe one internal toolbelt module. Subclasses set REL."""

    REL = ""
    TIMEOUT = _PROBE_TIMEOUT

    def _probe(self, rel):
        path = _SRC / rel
        if not path.exists():
            return {"status": "missing", "tool": rel}
        try:
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.TIMEOUT,
            )
            head = ((r.stdout or "") + (r.stderr or "")).strip()[:300]
            return {
                "status": "ok" if r.returncode == 0 else "fail",
                "tool": rel,
                "detail": head,
            }
        except Exception as e:
            note_failure("TOOLBELTZ:L52", e)
            return {"status": "error", "tool": rel, "detail": str(e)[:200]}

    def scan(self):
        return self._probe(self.REL)


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="PythonSystemMapper probe (TOOLS)")
class PythonSystemMapperWorm(_ToolBeltWorm):
    REL = "TOOLS/PythonSystemMapper.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="PythonMapper_Quick probe (TOOLS)")
class PythonMapperQuickWorm(_ToolBeltWorm):
    REL = "TOOLS/PythonMapper_Quick.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="PythonSystemMapper_Full probe (TOOLS)")
class PythonSystemMapperFullWorm(_ToolBeltWorm):
    REL = "TOOLS/PythonSystemMapper_Full.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="archive_system probe (TOOLS)")
class ArchiveSystemWorm(_ToolBeltWorm):
    REL = "TOOLS/archive_system.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="debug_runner probe (TOOLS)")
class DebugRunnerWorm(_ToolBeltWorm):
    REL = "TOOLS/debug_runner.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="qwythos_proxy probe (TOOLS)")
class QwythosProxyWorm(_ToolBeltWorm):
    REL = "TOOLS/qwythos_proxy.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="COMPREHENSIVE_FIX probe (AZAZAEL_TOOLKIT)")
class ComprehensiveFixWorm(_ToolBeltWorm):
    REL = "AZAZAEL_TOOLKIT/COMPREHENSIVE_FIX.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="QL_ANALYSIS probe (AZAZAEL_TOOLKIT)")
class QLAnalysisWorm(_ToolBeltWorm):
    REL = "AZAZAEL_TOOLKIT/QL_ANALYSIS.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="environment_manager probe (AZAZAEL_TOOLKIT)")
class EnvironmentManagerWorm(_ToolBeltWorm):
    REL = "AZAZAEL_TOOLKIT/environment_manager.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="TRIMZ probe (META)")
class TrimzWorm(_ToolBeltWorm):
    REL = "META/TRIMZ.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="ERRORZ probe (META)")
class ErrorzWorm(_ToolBeltWorm):
    REL = "META/ERRORZ.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="HEALTHZ probe (META)")
class HealthzWorm(_ToolBeltWorm):
    REL = "META/HEALTHZ.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="METRICZ probe (META)")
class MetriczWorm(_ToolBeltWorm):
    REL = "META/METRICZ.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="monitor_learning probe (SCRIPTS)")
class MonitorLearningWorm(_ToolBeltWorm):
    REL = "SCRIPTS/monitor_learning.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="lsp_diagnosis probe (SCRIPTS)")
class LspDiagnosisWorm(_ToolBeltWorm):
    REL = "SCRIPTS/lsp_diagnosis.py"


@register_worm(category="diagnostic", priority=4, cooldown_seconds=60.0,
               description="environ_updater probe (SCRIPTS)")
class EnvironUpdaterWorm(_ToolBeltWorm):
    REL = "SCRIPTS/environ_updater.py"
