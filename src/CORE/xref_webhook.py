#!/usr/bin/env python3
"""xref_webhook.py - utility functions (notify_new_links).

Webhook notifier for new cross-reference links.
"""


__all__ = [
    "notify_new_links",
]

import json
import urllib.request
import urllib.error
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Optional  # removed — all names have native equivalents

def notify_new_links(new_links: int, total: int, webhook_url: str) -> bool:
    payload = json.dumps({
        "text": f"🔗 New cross-reference links created: +{new_links} (total: {total})",
        "new_links": new_links,
        "total_mappings": total,
    }).encode()
    try:
        req = urllib.request.Request(webhook_url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as exc:
        note_failure("xref_webhook:L26", exc)
        return False

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("Webhook URL: ")
    test = notify_new_links(5, 100, url)
    print(f"Notification {'sent' if test else 'failed'}")
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
