"""Run all 8 compliance self-tests and print results."""
import sys
sys.path.insert(0, '.')

from CORE.compliance.orchestrator import self_test
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

print('Running self_test()...')
sys.stdout.flush()
success = self_test()
sys.stdout.flush()
print()
print('Result:', success)
sys.exit(0 if success else 1)
