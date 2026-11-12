# coding: utf-8
"""
CORE web search — keyless-first retrieval with a citation contract.

Nothing in the tree could read the web: no search provider, no fetch
helper, no researcher. This module is that capability, built to the
verification discipline the survey literature converges on: a claim
without a retrievable source is model output, not knowledge.

Design:

* Transport is stdlib ``urllib`` only — no new dependencies, works on the
  3.14t build and inside Nuitka binaries alike.
* ``fetch_url`` never raises and never returns unbounded bytes: timeout,
  size cap, text-only content gate. Errors come back as data.
* Providers implement two methods (``search``/``fetch``) behind a registry
  dict, so a keyed provider (Tavily, Serper, Brave) slots in later without
  touching callers. Only keyless providers ship: Wikipedia (stable,
  keyless, factual) today.
* Citation contract: every consumed result carries ``url`` +
  ``retrieved_at``. ``cite()`` renders the source lines; anything downstream
  that asserts without them is unverifiable by construction.

What this is NOT: a scraper that executes pages, parses JavaScript, or
defeats access controls. Text responses only, standard User-Agent,
timeouts on everything.
"""

from __future__ import annotations

import io
import json as _json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: CaMeL ingress (2026-10-30). Every fetched body is registered with the taint
#: ledger BEFORE the caller sees it, so a later tool call that reproduces this
#: text is refused by `CORE.camel.guard`. The import is guarded because a tree
#: without the package must still fetch — but the failure is then REPORTED in
#: `FetchResult.taint_id` as UNREGISTERED:<reason>, never silently swallowed:
#: "the guard found nothing" must not be mistakable for "the guard was watching".
try:
    from CORE.camel.guard import register_untrusted as _register_untrusted
except ImportError:                               # pragma: no cover - bare tree
    _register_untrusted = None  # type: ignore[assignment]

#: Hard ceiling per fetch. A search helper that buffers megabytes is how a
#: tap becomes slow enough that someone disables it.
MAX_BYTES = 200_000

#: Network deadine per request. Retrieval must stay faster than the pools
#: it serves or callers route around it.
DEFAULT_TIMEOUT_S = 10.0

USER_AGENT = "azazael-research/1.0 (fact-retrieval; contact: local-operator)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class FetchResult:
    url: str = ""
    final_url: str = ""
    status: Optional[int] = None
    text: str = ""
    truncated: bool = False
    retrieved_at: str = ""
    error: Optional[str] = None
    #: Origin id under which this body was registered with the CaMeL taint ledger
    #: ("quote:<url>"), or "UNREGISTERED:<reason>" when registration failed. Empty
    #: only for responses that carried no text.
    taint_id: str = ""


def _register_body(text: str, source: str) -> str:
    """Register a fetched body as untrusted; return the origin id or a reason."""
    if not text or not source:
        return ""
    if _register_untrusted is None:
        return "UNREGISTERED:camel-import"
    try:
        label = _register_untrusted(text, source=source)
    except Exception as e:  # noqa: BLE001 — taint ledger must not break a fetch
        note_failure("web_search:L98-taint", e)
        return "UNREGISTERED:%s" % type(e).__name__
    return "quote:%s" % source if label.tainted else ""


def fetch_url(url: str, *,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              max_bytes: int = MAX_BYTES,
              opener: Optional[Callable] = None) -> FetchResult:
    """Fetch one URL as text. Never raises; failures come back as data."""
    ts = _now()
    try:
        scheme = urllib.parse.urlparse(str(url)).scheme.lower()
    except Exception as exc:
        note_failure("web_search:L84", exc)
        scheme = ""
    if scheme not in ("http", "https"):
        return FetchResult(url=str(url), retrieved_at=ts,
                           error="refused scheme: %r" % scheme)
    req = urllib.request.Request(
        str(url), headers={"User-Agent": USER_AGENT,
                           "Accept": "text/html,text/plain,*/*;q=0.8"})
    try:
        open_fn = opener or urllib.request.urlopen
        resp = open_fn(req, timeout=float(timeout_s))
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            ctype = ""
            try:
                ctype = str(resp.headers.get("Content-Type", ""))
            except Exception as exc:
                note_failure("web_search:L100", exc)
                ctype = ""
            if ctype and not ctype.split(";")[0].strip().startswith(
                    ("text/", "application/json", "application/xml",
                     "application/rss", "application/atom")):
                return FetchResult(url=str(url), status=status,
                                   retrieved_at=ts,
                                   error="refused content-type: %s" % ctype[:80])
            raw = resp.read(int(max_bytes) + 1)
            truncated = len(raw) > int(max_bytes)
            raw = raw[:int(max_bytes)]
            charset = "utf-8"
            try:
                charset = resp.headers.get_content_charset() or charset
            except Exception as exc:
                note_failure("web_search:L114", exc)
            try:
                text = raw.decode(charset, errors="replace")
            except Exception as exc:
                note_failure("web_search:L118", exc)
                text = raw.decode("utf-8", errors="replace")
            try:
                final_url = resp.geturl()
            except Exception as exc:
                note_failure("web_search:L122", exc)
                final_url = str(url)
            return FetchResult(url=str(url), final_url=str(final_url),
                               status=status, text=text,
                               truncated=truncated, retrieved_at=ts,
                               taint_id=_register_body(text,
                                                       str(final_url or url)))
        finally:
            try:
                resp.close()
            except Exception as exc:
                note_failure("web_search:L130", exc)
    except urllib.error.HTTPError as e:
        note_failure("web_search:L132", e)
        return FetchResult(url=str(url), status=getattr(e, "code", None),
                           retrieved_at=ts,
                           error="http %s" % getattr(e, "code", "?"))
    except Exception as e:  # noqa: BLE001 — transport boundary reports
        note_failure("web_search:L136", e)
        return FetchResult(url=str(url), retrieved_at=ts,
                           error="%s: %s" % (type(e).__name__, str(e)[:200]))


class WikipediaProvider:
    """Keyless factual search + summary fetch via the MediaWiki API."""

    NAME = "wikipedia"
    API = "https://en.wikipedia.org/w/api.php"

    def __init__(self, opener: Optional[Callable] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self._opener = opener
        self._timeout = float(timeout_s)

    def _get_json(self, params: Dict[str, Any]) -> Dict[str, Any]:
        qs = urllib.parse.urlencode(params)
        res = fetch_url("%s?%s" % (self.API, qs), timeout_s=self._timeout,
                        opener=self._opener)
        if res.error or not res.text:
            return {"_error": res.error or "empty response"}
        try:
            data = _json.loads(res.text)
            return data if isinstance(data, dict) else {"_error": "non-dict"}
        except Exception as e:
            note_failure("web_search:L161", e)
            return {"_error": "bad json: %s" % str(e)[:120]}

    def search(self, query: str, limit: int = 5) -> List[SearchResult]:
        """Full-text search. Returns [] (never raises) on any failure."""
        ts = _now()
        if not str(query or "").strip():
            return []
        try:
            data = self._get_json({"action": "query", "format": "json",
                                   "list": "search", "srsearch": str(query),
                                   "srlimit": max(1, min(int(limit), 20)),
                                   "srprop": "snippet"})
        except Exception as exc:
            note_failure("web_search:L174", exc)
            return []
        out = []
        try:
            for item in (data.get("query", {}) or {}).get("search", []) or []:
                title = str(item.get("title", ""))
                url = ("https://en.wikipedia.org/wiki/"
                       + urllib.parse.quote(title.replace(" ", "_")))
                out.append(SearchResult(
                    title=title, url=url,
                    snippet=_strip_tags(str(item.get("snippet", ""))),
                    source=self.NAME, retrieved_at=ts))
        except Exception as exc:
            note_failure("web_search:L186", exc)
            return out
        return out

    def fetch(self, result: SearchResult) -> FetchResult:
        """Plain-text article extract for a search result. Never raises."""
        ts = _now()
        try:
            title = result.title
        except Exception as exc:
            note_failure("web_search:L195", exc)
            return FetchResult(retrieved_at=ts, error="bad result")
        if not title:
            return FetchResult(retrieved_at=ts, error="empty title")
        try:
            data = self._get_json(
                {"action": "query", "format": "json", "prop": "extracts",
                 "explaintext": 1, "exsectionformat": "plain",
                 "titles": title})
        except Exception as e:
            note_failure("web_search:L204", e)
            return FetchResult(url=result.url, retrieved_at=ts,
                               error=str(e)[:200])
        if "_error" in data:
            return FetchResult(url=result.url, retrieved_at=ts,
                               error=str(data["_error"])[:200])
        try:
            pages = (data.get("query", {}) or {}).get("pages", {}) or {}
            text = next(iter(pages.values())).get("extract", "") if pages else ""
            text = str(text)[:MAX_BYTES]
            return FetchResult(url=result.url, final_url=result.url,
                               status=200, text=text,
                               truncated=len(str(text)) >= MAX_BYTES,
                               retrieved_at=ts)
        except Exception as e:
            note_failure("web_search:L218", e)
            return FetchResult(url=result.url, retrieved_at=ts,
                               error=str(e)[:200])


def _strip_tags(html: str) -> str:
    out = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out).replace("&quot;", '"').replace("&amp;", "&").strip()


PROVIDERS: Dict[str, Any] = {
    WikipediaProvider.NAME: WikipediaProvider,
}


def search(query: str, provider: str = "wikipedia",
           limit: int = 5, **kw) -> Dict[str, Any]:
    """Search via a registered provider. Envelope, never raises.

    Returns ``{"results": [...], "provider": ..., "error": ...}`` — error
    is None on success. Unknown provider is a loud error, never a silent
    fallback to a different backend.
    """
    cls = PROVIDERS.get(str(provider or "").lower())
    if cls is None:
        return {"results": [], "provider": str(provider),
                "error": "unknown provider %r (registered: %s)"
                         % (provider, sorted(PROVIDERS))}
    try:
        inst = cls(**kw) if kw else cls()
        results = inst.search(query, limit=limit)
        return {"results": [asdict(r) for r in results],
                "provider": str(provider), "error": None}
    except Exception as e:  # noqa: BLE001 — provider boundary reports
        note_failure("web_search:L259", e)
        return {"results": [], "provider": str(provider),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def cite(results: List[Any]) -> str:
    """Render source lines for consumed results. No sources, no claims."""
    lines = []
    for r in results or []:
        d = asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r or {})
        title = d.get("title") or d.get("url") or "untitled"
        lines.append("- %s <%s> retrieved %s via %s"
                     % (title, d.get("url", "?"),
                        d.get("retrieved_at", "?"), d.get("source", "?")))
    return "\n".join(lines)


def search_and_cite(query: str, provider: str = "wikipedia",
                    limit: int = 5, fetch_first: bool = False) -> Dict[str, Any]:
    """Search + optionally fetch the top hit + render citations. One call."""
    out = search(query, provider=provider, limit=limit)
    fetched = None
    if fetch_first and not out["error"] and out["results"]:
        try:
            cls = PROVIDERS[str(provider).lower()]
            top = SearchResult(**{k: out["results"][0].get(k, "")
                                   for k in ("title", "url", "snippet",
                                             "source", "retrieved_at")})
            fetched = asdict(cls().fetch(top))
        except Exception as e:  # noqa: BLE001 — fetch is optional detail
            note_failure("web_search:L288", e)
            fetched = {"error": str(e)[:200]}
    out["fetched"] = fetched
    out["citations"] = cite(out["results"])
    return out
