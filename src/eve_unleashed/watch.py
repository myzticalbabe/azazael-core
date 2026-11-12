"""watch.py -- docket watch: poll public feeds, diff, alert.

Civil justice moves on dockets and rules, and nobody can watch
them by hand. Eve keeps a watchlist of search terms, polls the
keyless-or-DEMO-key read endpoints on demand, snapshots what it
saw, and reports only what is new since the last snapshot.

No daemon, no background thread: `eve watch check` is the poll,
and the operator's scheduler (cron, Task Scheduler) is the clock.
Eve watches; only you file, comment, or sue.

Sources today: Regulations.gov document search (read-only GET,
DEMO_KEY works at low volume; set REGULATIONS_GOV_API_KEY for
real use). A source that needs a secret is prepared, not faked.
"""
import json
import time
from pathlib import Path

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

WATCH_DIR = Path.home() / ".azazael" / "watch"
_WATCHLIST = "watchlist.json"


def _wdir():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    return WATCH_DIR


def _load_watchlist():
    p = _wdir() / _WATCHLIST
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        note_failure("watch:load", exc)
        return []


def _save_watchlist(items):
    with open(_wdir() / _WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def add_watch(term):
    """Watch a search term. Returns the watch entry."""
    if not term or not term.strip():
        raise ValueError("nothing to watch: empty term")
    items = _load_watchlist()
    if term not in [w["term"] for w in items]:
        items.append({"term": term, "added_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _save_watchlist(items)
    return {"term": term, "watches": len(items)}


def remove_watch(term):
    """Stop watching a term. Returns remaining count."""
    items = [w for w in _load_watchlist() if w["term"] != term]
    _save_watchlist(items)
    return {"term": term, "watches": len(items)}


def _snapshot_path(term):
    safe = "".join(c if c.isalnum() else "_" for c in term)[:40]
    return _wdir() / ("snap_%s.json" % safe)


def check_watches():
    """Poll every watched term, diff against snapshots. Returns news."""
    from .redress import search_dockets
    news = []
    for w in _load_watchlist():
        term = w["term"]
        try:
            current = search_dockets(term)
        except Exception as e:
            note_failure("watch:poll", e)
            news.append({"term": term, "error": str(e)[:200]})
            continue
        ids = sorted(d.get("id", "") for d in current
                     if d.get("id"))
        snap = _snapshot_path(term)
        before = []
        if snap.is_file():
            try:
                before = json.loads(snap.read_text(encoding="utf-8"))
            except Exception as exc:
                note_failure("watch:snap", exc)
        new = [i for i in ids if i not in before]
        try:
            snap.write_text(json.dumps(ids, indent=2), encoding="utf-8")
        except Exception as exc:
            note_failure("watch:write", exc)
        news.append({"term": term, "seen": len(ids),
                     "new": new,
                     "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.gmtime())})
    return news
