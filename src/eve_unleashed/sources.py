"""Eve Unleashed -- source policy: ultra-loose, tier-graded.

The standard here is the tree's own FLOCK-CANON: *any lawful source; tier
decides grade*. That is deliberately permissive -- a source is not refused
for being informal, old, secondary or machine-generated. It is graded,
logged, and used at the weight its tier earns.

What the tiers mean:

  A  primary published artefact -- government portal, official document,
     an API whose terms publish machine use, a byte-range archive copy.
  B  official index -- sitemap, catalog, registry listing, robots-permitted
     listing page. Identity/universe evidence, not content proof.
  C  credible secondary -- news, NGO, archive snapshot, aggregator with
     provenance. Usable; pairs with A/B before it carries a claim.
  D  snippet / social / search-result fragment -- LEAD ONLY. Still logged;
     never promoted without an A/B/C fetch behind it.

Refused is a narrow list, and each entry is a human-verification boundary
rather than a matter of taste:

  * a gate that asks a human to prove they are human (CAPTCHA / interstitial
    challenge): named, skipped, handed to the operator -- never solved;
  * a paywall or login wall: the wall is reported, the free mirror is tried;
  * an explicit robots refusal for that path;
  * non-public personal data: an address or phone number published by its
    owner is fine, a leaked record is not.

Everything else is allowed, with a tier and a log line. Rate discipline is
part of lawfulness: human-scale pacing, no parallel flood, one fetch per URL
per run unless the caller asks again.
"""

from urllib.parse import urlparse

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

#: Host suffixes that are primary/official by construction.
TIER_A_SUFFIXES = (
    ".gov", ".mil", ".gov.uk", ".gc.ca", ".europa.eu", ".gov.au",
    "who.int", "un.org", "worldbank.org", "imf.org",
)
#: Published machine-use APIs and archives: consented by their own terms.
TIER_A_APIS = (
    "wikipedia.org", "wikidata.org", "openlibrary.org", "crossref.org",
    "stackexchange.com", "archive.org", "loc.gov", "commoncrawl.org",
    "doi.org", "arxiv.org", "pubmed.ncbi.nlm.nih.gov",
)
#: Official indexes / catalogs: identity evidence, not content proof.
TIER_B_HOSTS = (
    "sitemaps.org", "opengovus.com", "opencorporates.com", "guidestar.org",
    "propublica.org", "courtlistener.com", "npi registry".replace(" ", "-"),
)
#: Credible secondary: news, NGOs, archives with provenance.
TIER_C_HOSTS = (
    "reuters.com", "apnews.com", "bbc.co.uk", "npr.org", "patch.com",
    "web.archive.org", "archive.today", "documentcloud.org",
)
#: Snippet-only classes: leads, never proof.
TIER_D_SUFFIXES = ("facebook.com", "x.com", "twitter.com", "instagram.com",
                   "tiktok.com", "reddit.com", "linkedin.com", "youtube.com")

#: Path/host markers that mean a human-verification gate.
CHALLENGE_MARKERS = ("captcha", "challenge", "are-you-a-robot", "turnstile",
                     "cf-chl", "px-captcha", "hcaptcha", "recaptcha")
PAYWALL_MARKERS = ("/subscribe", "/paywall", "premium-content", "/login",
                   "/signin", "/account")


def _host(url):
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception as e:
        note_failure("sources:host", e)
        return ""


def _matches(host, table):
    for entry in table:
        if host == entry or host.endswith("." + entry) or host.endswith(entry):
            return entry
    return None


def classify(url, robots=None, gated=False):
    """Grade one source. Returns tier, whether it is allowed, and why.

    ``robots`` -- optional iterable of path prefixes the site's robots.txt
    disallows; an explicit refusal is honoured, not worked around.
    ``gated`` -- True when the caller already knows the page demands a
    login/subscription; that is a wall to report, not a wall to break.
    """
    url = str(url or "").strip()
    if not url:
        raise ValueError("nothing to classify: empty url")
    host = _host(url)
    path = (urlparse(url).path or "/").lower()
    if not host:
        return {"url": url, "tier": "D", "allowed": False, "wall": "unparseable",
                "reason": "no host to grade"}
    for marker in CHALLENGE_MARKERS:
        if marker in path or marker in url.lower():
            return {"url": url, "tier": None, "allowed": False,
                    "wall": "human-verification",
                    "reason": "a gate that asks a human to prove they are "
                              "human is named and skipped, never solved"}
    if gated or any(m in path for m in PAYWALL_MARKERS):
        return {"url": url, "tier": "D", "allowed": False, "wall": "paywall",
                "reason": "report the wall, try the free mirror"}
    if robots:
        for rule in robots:
            rule = str(rule or "").strip()
            if rule and path.startswith(rule.rstrip("*").lower()):
                return {"url": url, "tier": None, "allowed": False,
                        "wall": "robots-disallow",
                        "reason": "the site declared this path off-limits; "
                                  "an explicit refusal is honoured"}
    if _matches(host, TIER_A_APIS) or any(host.endswith(s)
                                          for s in TIER_A_SUFFIXES):
        tier, why = "A", "primary published artefact or consented API"
    elif _matches(host, TIER_B_HOSTS):
        tier, why = "B", "official index: identity evidence, not content proof"
    elif _matches(host, TIER_D_SUFFIXES):
        tier, why = "D", "snippet/social: lead only, still logged"
    elif _matches(host, TIER_C_HOSTS):
        tier, why = "C", "credible secondary: pairs with A/B before a claim"
    else:
        tier, why = "C", "unlisted host graded as secondary by default"
    return {"url": url, "tier": tier, "allowed": True, "wall": None,
            "reason": why, "host": host}


def grade(tier):
    """What a tier may carry, in one line."""
    return {
        "A": "VERIFIED-eligible: may carry a claim alone",
        "B": "IDENTITY/UNIVERSE only: proves existence, not content",
        "C": "claim-eligible only paired with an A or B fetch",
        "D": "LEAD only: never promoted without an A/B/C behind it",
    }.get(tier, "ungraded")


def policy_report(urls, robots_map=None, gated=None):
    """Classify many sources; counts per tier, refused list named."""
    robots_map = robots_map or {}
    gated = gated or {}
    rows, refused = [], []
    for u in urls:
        r = classify(u, robots=robots_map.get(u), gated=gated.get(u, False))
        r["grade"] = grade(r["tier"])
        rows.append(r)
        if not r["allowed"]:
            refused.append(r)
    counts = {}
    for r in rows:
        key = r["tier"] or "refused"
        counts[key] = counts.get(key, 0) + 1
    return {"rows": rows, "counts": counts, "refused": refused,
            "standard": "ultra-loose: any lawful source; tier decides grade"}
