import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from CORE.compliance.governance import RealtimeAuditPipeline
from CORE.audit_trail import verify_head_hash
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
p = RealtimeAuditPipeline()
for rel in ['.audit/session_20260612_201134_019e.jsonl',
            '.audit/session_20260612_201206_019e.jsonl']:
    r = p.process(rel)
    l14 = next((lr for lr in r.get('layer_results', []) if lr['layer']==14), {})
    v, stored, cur = verify_head_hash(rel)
    print(f"  {os.path.basename(rel):30s} L14={l14.get('passed')!s:5} valid={v} failed={r.get('failed_layers')} cur={cur[:12] if cur else ''} stored={stored[:12] if stored else ''}")
