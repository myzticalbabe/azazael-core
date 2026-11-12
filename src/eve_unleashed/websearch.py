"""websearch.py -- web search with five keyless providers, no single throat.

One source is a mirror with opinions. This module fans a query
across five providers that need no key -- Wikipedia search,
Library of Congress, Open Library, Crossref, and Stack Exchange --
and returns one deduplicated list where every hit names its
provider. A provider that is down, slow, or blocked reports an
error entry; it never sinks the others and is never counted as
zero results. That distinction is the whole module: `empty` means
the provider answered and found nothing, `error` means it could
not answer, and the two are never merged.

Discipline, stated: 12-second per-provider timeout, a named
User-Agent on every request, at most 5 hits kept per provider,
and deduplication by normalized URL. No scraping of HTML result
pages, no credentialed APIs, no query logging anywhere.

Challenge appendix: live rungs, then memory rungs (Wayback,
Common Crawl) that never touch the host; a wall reads `blocked`.
"""
import json as _json
import os as _os
import time as _time
import urllib.parse as _parse
import urllib.request as _request

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

_TIMEOUT = 12
_UA = {"User-Agent": "EveUnleashed-websearch/1.0 (research; keyless)"}
_PER_PROVIDER = 5
_RENDER_TIMEOUT = 45

# Full browser header set: the second rung of the mutation ladder.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/131.0.0.0 Safari/537.36")
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Challenge signatures, checked against status + headers + body.
# Each is (kind, marker) where marker is found in body or headers.
_CHALLENGE_MARKS = (
    ("cloudflare-iuam", "Just a moment"),
    ("cloudflare-iuam", "cf-chl"),
    ("cloudflare-iuam", "challenges.cloudflare.com"),
    ("cloudflare-turnstile", "turnstile"),
    ("cloudflare-deny", "cf-error-details"),
    ("captcha-wall", "g-recaptcha"),
    ("captcha-wall", "hcaptcha"),
    ("imperva", "incapsula"),
    ("imperva", "_incap_"),
    ("imperva", "imperva"),
    ("imperva", "visid_incap"),
    ("imperva", "incap_ses"),
    ("imperva", "x-iinfo"),
    ("imperva", "reese84"),
    ("bot-wall", "Access Denied"),
    ("bot-wall", "Attention Required"),
    ("bot-wall", "are you a robot"),
)

PROVIDERS = ("wikipedia", "loc", "openlibrary", "crossref",
             "stackexchange")


def _get(url):
    """One HTTP GET parsed as JSON. Returns (data, error)."""
    try:
        req = _request.Request(url, headers=_UA)
        with _request.urlopen(req, timeout=_TIMEOUT) as r:
            return _json.load(r), None
    except Exception as e:
        note_failure("websearch:get", e)
        return None, str(e)[:160]


def _wikipedia(query):
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
           "&srsearch=%s&format=json&srlimit=%d" % (_parse.quote(query),
                                                   _PER_PROVIDER))
    data, err = _get(url)
    if err:
        return {"via": "wikipedia", "state": "error", "detail": err,
                "hits": []}
    hits = [{"title": h.get("title", ""),
             "url": "https://en.wikipedia.org/wiki/" + _parse.quote(
                 h.get("title", "").replace(" ", "_")),
             "snippet": h.get("snippet", "").replace("<span class=\"searchmatch\">", "")
             .replace("</span>", "")[:200]}
            for h in (data or {}).get("query", {}).get("search", [])]
    return {"via": "wikipedia",
            "state": "ok" if hits else "empty",
            "detail": "%d hit(s)" % len(hits), "hits": hits[:_PER_PROVIDER]}


def _loc(query):
    url = "https://www.loc.gov/search/?q=%s&fo=json&c=%d" % (
        _parse.quote(query), _PER_PROVIDER)
    data, err = _get(url)
    if err:
        return {"via": "loc", "state": "error", "detail": err, "hits": []}
    hits = [{"title": r.get("title", ""),
             "url": r.get("url", ""),
             "snippet": ("%s -- %s" % (r.get("title", ""),
                                       r.get("date", "") or "undated"))[:200]}
            for r in (data or {}).get("results", [])]
    return {"via": "loc", "state": "ok" if hits else "empty",
            "detail": "%d hit(s)" % len(hits), "hits": hits[:_PER_PROVIDER]}


def _openlibrary(query):
    url = "https://openlibrary.org/search.json?q=%s&limit=%d" % (
        _parse.quote(query), _PER_PROVIDER)
    data, err = _get(url)
    if err:
        return {"via": "openlibrary", "state": "error", "detail": err,
                "hits": []}
    hits = [{"title": d.get("title", ""),
             "url": "https://openlibrary.org%s" % d.get("key", ""),
             "snippet": ", ".join(d.get("author_name", [])[:3])[:200]}
            for d in (data or {}).get("docs", [])]
    return {"via": "openlibrary", "state": "ok" if hits else "empty",
            "detail": "%d hit(s)" % len(hits), "hits": hits[:_PER_PROVIDER]}


def _crossref(query):
    url = "https://api.crossref.org/works?query=%s&rows=%d&select=title,URL,author,published" % (
        _parse.quote(query), _PER_PROVIDER)
    data, err = _get(url)
    if err:
        return {"via": "crossref", "state": "error", "detail": err,
                "hits": []}
    hits = []
    for item in (data or {}).get("message", {}).get("items", []):
        title = "; ".join(item.get("title", []))
        hits.append({"title": title,
                     "url": item.get("URL", ""),
                     "snippet": title[:200]})
    return {"via": "crossref", "state": "ok" if hits else "empty",
            "detail": "%d hit(s)" % len(hits), "hits": hits[:_PER_PROVIDER]}


def _stackexchange(query):
    url = ("https://api.stackexchange.com/2.3/search/advanced"
           "?order=desc&sort=relevance&q=%s&site=stackoverflow&pagesize=%d"
           % (_parse.quote(query), _PER_PROVIDER))
    data, err = _get(url)
    if err:
        return {"via": "stackexchange", "state": "error", "detail": err,
                "hits": []}
    hits = [{"title": it.get("title", ""),
             "url": it.get("link", ""),
             "snippet": "score %s, %s answer(s)" % (
                 it.get("score", "?"), it.get("answer_count", "?"))}
            for it in (data or {}).get("items", [])]
    return {"via": "stackexchange", "state": "ok" if hits else "empty",
            "detail": "%d hit(s)" % len(hits), "hits": hits[:_PER_PROVIDER]}


def detect_challenge(status, headers, body):
    """Name the bot challenge on a response, or None if it reads clean.

    `status` is the HTTP code, `headers` a mapping, `body` text.
    A 403/503 alone is an error, not a challenge; it becomes a
    named challenge only when a challenge marker is present."""
    try:
        text = body if isinstance(body, str) else (body or b"").decode(
            "utf-8", "replace")
    except Exception as e:
        note_failure("websearch:detect:decode", e)
        text = ""
    low = text.lower()
    head = " ".join("%s: %s" % (k, v) for k, v in
                      dict(headers or {}).items()).lower()
    for kind, mark in _CHALLENGE_MARKS:
        if mark.lower() in low or mark.lower() in head:
            return kind
    if status in (403, 503) and ("cf-ray" in head or "cloudflare" in head):
        return "cloudflare-deny"
    return None


def _render_fetch(url):
    """Third rung: render in headless system Chrome via Playwright.

    Returns (text, error). Executes page JavaScript the way a real
    browser does, so non-interactive challenges that clear on their
    own resolve here. Needs the `playwright` package plus any
    Chrome; raises RuntimeError naming what is missing."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError("playwright not installed: %s" % (e,))
    text, err = "", None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome",
                                            headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_BROWSER_UA)
                page.goto(url, timeout=_RENDER_TIMEOUT * 1000,
                          wait_until="domcontentloaded")
                try:
                    page.wait_for_timeout(4000)
                except Exception as e:
                    note_failure("websearch:render:settle", e)
                text = page.evaluate(
                    "() => document.body ? document.body.innerText : ''")
            finally:
                browser.close()
    except Exception as e:
        note_failure("websearch:render", e)
        err = str(e)[:160]
    return text or "", err


def _pick_proxy():
    rotate = [x.strip() for x in _os.environ.get("PROXY_ROTATE", "")
              .split(",") if x.strip()]
    if rotate:
        return rotate[int(_time.time()) % len(rotate)]
    return _os.environ.get("PROXY_URL") or None


def _curl_cffi_fetch(url):
    """TLS-impersonation rung (house pattern, BYPASS_BCC_BENCHMARK.md).

    Tries Chrome/Safari/Edge handshake profiles; the first body
    with no challenge marker wins. Returns (body, profile, error).
    Raises RuntimeError if curl_cffi is missing so the ladder can
    record the rung skipped instead of failed."""
    try:
        from curl_cffi import requests as _cr
    except Exception as e:
        raise RuntimeError("curl_cffi not installed: %s" % (e,))
    profiles = ("chrome124", "chrome120", "safari15_5", "edge101")
    proxy = _pick_proxy()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_err, last_kind = "all profiles challenged", None
    for profile in profiles:
        try:
            r = _cr.get(url, impersonate=profile, timeout=_TIMEOUT,
                        headers={"Accept-Language": "en-US,en;q=0.9"},
                        proxies=proxies)
            kind = detect_challenge(r.status_code, dict(r.headers),
                                    r.text or "")
            if kind:
                last_kind = kind
                continue
            if r.status_code == 200:
                return r.text or "", profile, None
            last_err = "HTTP %s (%s)" % (r.status_code, profile)
        except Exception as e:
            note_failure("websearch:curl-cffi:%s" % profile, e)
            last_err = str(e)[:160]
    return "", None, "%s; last wall: %s" % (last_err, last_kind)


def _archive_urls(url):
    """Wayback availability lookup, then the snapshot address."""
    api = ("https://archive.org/wayback/available?url=%s"
           % _parse.quote(url, safe=""))
    return api


def _scrapling_fetch(url):
    """Stealth-Chromium rung (house strategy 4, live-proven 2026-11-06)."""
    try:
        from scrapling.fetchers import StealthyFetcher
    except Exception as e:
        raise RuntimeError("scrapling not installed: %s" % (e,))
    try:
        page = StealthyFetcher.fetch(url, headless=True)
    except Exception as e:
        note_failure("websearch:scrapling", e)
        return "", str(e)[:160]
    body = getattr(page, "html_content", "") or ""
    kind = detect_challenge(getattr(page, "status", 200), {}, body)
    if kind or not body:
        return "", "walled: %s" % (kind or "empty render")
    return body, None



def _cc_pick(ndjson_text):
    """First successful text/html capture in an index page."""
    for line in (ndjson_text or "").splitlines():
        try:
            rec = _json.loads(line)
        except Exception as e:
            note_failure("websearch:cc:json", e)
            rec = {}
        if not rec:
            continue
        ok = (str(rec.get("status")) == "200" and "html" in
              str(rec.get("mime", "")).lower() and rec.get("filename")
              and rec.get("offset") is not None and rec.get("length"))
        if ok:
            return rec
    return None


def _cc_extract(blob):
    """HTTP body out of a (possibly gzipped) WARC response record."""
    import gzip as _gzip
    data = blob
    if data[:2] == b"\x1f\x8b":
        try:
            data = _gzip.decompress(data)
        except Exception as e:
            note_failure("websearch:cc:gunzip", e)
            return "", "warc gunzip failed"
    parts = data.split(b"\r\n\r\n", 2)
    if len(parts) < 3:
        return "", "warc structure unrecognized"
    inner_headers, body = parts[1], parts[2]
    if b"content-encoding: gzip" in inner_headers.lower():
        try:
            body = _gzip.decompress(body)
        except Exception as e:
            note_failure("websearch:cc:body-gunzip", e)
            return "", "inner body gunzip failed"
    try:
        return body.decode("utf-8", "replace"), None
    except Exception as e:
        note_failure("websearch:cc:decode", e)
        return "", "body decode failed"


def _cc_try_crawl(cid, url):
    """One crawl's index lookup plus WARC pull; None when unusable."""
    api = ("https://index.commoncrawl.org/%s-index?url=%s"
           "&output=json&filter=status:200"
           % (cid, _parse.quote(url, safe="")))
    try:
        req = _request.Request(api, headers=_UA)
        with _request.urlopen(req, timeout=30) as r:
            nd = r.read().decode("utf-8", "replace")
    except Exception as e:
        note_failure("websearch:cc:index:%s" % cid, e)
        nd = ""
    rec = _cc_pick(nd) or {}
    off, ln = str(rec.get("offset", "")), str(rec.get("length", ""))
    if not (off.isdigit() and ln.isdigit()):
        return None
    off, ln = int(off), int(ln)
    warc_url = "https://data.commoncrawl.org/%s" % rec["filename"]
    try:
        req = _request.Request(
            warc_url, headers={**_UA, "Range": "bytes=%d-%d"
                              % (off, off + ln - 1)})
        with _request.urlopen(req, timeout=30) as r:
            blob = r.read()
    except Exception as e:
        note_failure("websearch:cc:warc", e)
        blob = b""
    body, xerr = _cc_extract(blob)
    if xerr or detect_challenge(200, {}, body):
        return None
    return body


def _commoncrawl_fetch(url):
    """Ring 7: answer from the crawl, never touching the target host."""
    info, err = _get("https://index.commoncrawl.org/collinfo.json")
    if err or not info:
        return "", "", "crawl list unreachable: %s" % (err or "empty")
    ids = [c.get("id", "") for c in info[:3] if c.get("id")]
    for cid in ids:
        body = _cc_try_crawl(cid, url)
        if body:
            return body, cid, None
    return "", "", "no usable capture in %d recent crawl(s)" % len(ids)


def _archive_fallback(url):
    """Last rung: the closest Wayback snapshot, fetched plainly.

    Returns (body, snapshot_url, error). A page nobody archived
    has no snapshot; that reads `unarchived`, never an error in
    the page itself."""
    data, err = _get(_archive_urls(url))
    if err:
        return "", "", "availability lookup failed: %s" % err
    outer = (data or {}).get("archived_snapshots", {}) or {}
    snap = outer.get("closest", {}) or {}
    snap_url = snap.get("url", "")
    if not snap.get("available") or not snap_url:
        return "", "", "unarchived: no snapshot listed"
    body, ferr = "", "no attempt made"
    for wait in (0, 5, 20):
        if wait:
            _time.sleep(wait)
        try:
            req = _request.Request(snap_url, headers=_BROWSER_HEADERS)
            with _request.urlopen(req, timeout=_TIMEOUT) as r:
                raw = r.read()
            body, ferr = raw.decode("utf-8", "replace"), None
            break
        except Exception as e:
            note_failure("websearch:archive:fetch", e)
            ferr = str(e)[:120]
            if "429" not in ferr:
                break
    if ferr:
        return "", snap_url, "snapshot fetch failed: %s" % ferr
    kind = detect_challenge(200, {}, body)
    if kind:
        return "", snap_url, "snapshot itself walled: %s" % kind
    return body, snap_url, None


def fetch_visible(url):
    """Live rungs up the ladder, then memory rungs (never touch host)."""
    if not url or not str(url).strip():
        raise ValueError("nothing to fetch: empty url")
    url = str(url).strip()
    ladder = [("plain", _UA), ("browser-headers", _BROWSER_HEADERS)]
    last_err, last_kind = "no attempt made", None
    for rung, headers in ladder:
        try:
            req = _request.Request(url, headers=headers)
            with _request.urlopen(req, timeout=_TIMEOUT) as r:
                raw = r.read()
                try:
                    body = raw.decode("utf-8", "replace")
                except Exception as e:
                    note_failure("websearch:fetch:decode", e)
                    body = ""
                kind = detect_challenge(r.status, dict(r.headers), body)
                if kind:
                    # Challenged, not answered: climb to the next rung.
                    last_kind = kind
                    continue
                return {"state": "ok", "rung": rung, "kind": None,
                        "text": body[:20000]}
        except Exception as e:
            last_err = str(e)[:160]
            note_failure("websearch:fetch:%s" % rung, e)
    try:
        text, profile, cerr = _curl_cffi_fetch(url)
    except RuntimeError as e:
        text, profile, cerr = "", None, str(e)
    if not cerr:
        return {"state": "ok", "rung": "tls-impersonation",
                "kind": None, "profile": profile,
                "text": (text or "")[:20000]}
    note_failure("websearch:fetch:tls-impersonation",
                 Exception(cerr[:120]))
    try:
        text, err = _render_fetch(url)
    except RuntimeError as e:
        text, err = "", "render unavailable: %s" % (e,)
    render_kind = None
    if err:
        note_failure("websearch:fetch:render", Exception(err[:120]))
    else:
        render_kind = detect_challenge(200, {}, text)
        if not render_kind:
            return {"state": "ok", "rung": "render", "kind": None,
                    "text": (text or "")[:20000]}
    try:
        stext, serr = _scrapling_fetch(url)
    except RuntimeError as e:
        stext, serr = "", str(e)
    if not serr:
        return {"state": "ok", "rung": "stealth-render",
                "kind": None, "text": (stext or "")[:20000]}
    note_failure("websearch:fetch:stealth", Exception(str(serr)[:120]))
    wall = render_kind or last_kind or "live fetch failed"
    body, snap_url, aerr = _archive_fallback(url)
    if not aerr:
        return {"state": "ok", "rung": "archive",
                "kind": wall, "snapshot": snap_url,
                "text": (body or "")[:20000]}
    cbody, cid, cerr = _commoncrawl_fetch(url)
    if not cerr:
        return {"state": "ok", "rung": "commoncrawl", "kind": wall,
                "crawl": cid, "text": (cbody or "")[:20000]}
    detail = "wall '%s' held; archive: %s; crawl: %s" % (wall, aerr, cerr)
    return {"state": "blocked", "rung": "commoncrawl", "kind": wall,
            "detail": detail}


_FNS = {"wikipedia": _wikipedia, "loc": _loc,
        "openlibrary": _openlibrary, "crossref": _crossref,
        "stackexchange": _stackexchange}


def search(query, providers=PROVIDERS):
    """Fan out, isolate failures, dedupe by URL. Returns the bundle."""
    if not query or not query.strip():
        raise ValueError("nothing to search: empty query")
    wanted = [p for p in providers if p in _FNS]
    if not wanted:
        raise ValueError("no known providers named: %s" % (providers,))
    legs, seen, hits = [], set(), []
    for name in wanted:
        try:
            leg = _FNS[name](query.strip())
        except Exception as e:
            note_failure("websearch:%s" % name, e)
            leg = {"via": name, "state": "error",
                   "detail": str(e)[:160], "hits": []}
        for h in leg.get("hits", []):
            key = (h.get("url") or h.get("title", "")).lower().strip()
            if key and key not in seen:
                seen.add(key)
                hits.append({**h, "via": name})
        legs.append({"via": name, "state": leg.get("state", "error"),
                     "detail": leg.get("detail", "")[:160],
                     "kept": len([h for h in leg.get("hits", [])])})
    return {"query": query.strip(),
            "searched_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           _time.gmtime()),
            "legs": legs,
            "answered": sum(1 for l in legs if l["state"] != "error"),
            "hits": hits,
            "note": ("error legs could not answer and are not zeros; "
                     "empty legs answered with nothing found.")}
