"""Eve source policy -- ultra-loose grading, narrow refusals, no network."""

import pytest

from eve_unleashed import sources as _s


def test_official_and_api_hosts_grade_a():
    for url in ("https://www.irs.gov/forms", "https://en.wikipedia.org/wiki/X",
                "https://api.crossref.org/works?q=x",
                "https://web.archive.org/web/2026/https://x.test/"):
        r = _s.classify(url)
        assert r["tier"] in ("A", "C"), url
    assert _s.classify("https://www.irs.gov/forms")["tier"] == "A"
    assert _s.classify("https://en.wikipedia.org/wiki/X")["tier"] == "A"


def test_unlisted_host_is_allowed_as_secondary():
    r = _s.classify("https://some-county-clerk.example.gov/page")
    assert r["allowed"] is True
    assert r["tier"] == "A"
    r2 = _s.classify("https://a-blog-nobody-listed.example/post")
    assert r2["allowed"] is True and r2["tier"] == "C"
    assert "secondary" in r2["reason"]
    assert "paired with an A or B" in _s.grade("C")


def test_social_is_lead_only_not_refused():
    r = _s.classify("https://www.facebook.com/somebody")
    assert r["allowed"] is True
    assert r["tier"] == "D"
    assert "LEAD" in _s.grade("D")


def test_human_verification_is_named_and_skipped():
    r = _s.classify("https://host.example/px-captcha/challenge")
    assert r["allowed"] is False
    assert r["wall"] == "human-verification"
    assert r["tier"] is None
    assert "never solved" in r["reason"]


def test_paywall_and_login_report_the_wall():
    r = _s.classify("https://paper.example/premium-content/story")
    assert r["allowed"] is False and r["wall"] == "paywall"
    r2 = _s.classify("https://host.example/x", gated=True)
    assert r2["wall"] == "paywall"


def test_robots_disallow_is_honoured():
    r = _s.classify("https://host.example/private/x",
                    robots=["/private/"])
    assert r["allowed"] is False and r["wall"] == "robots-disallow"
    ok = _s.classify("https://host.example/public/x", robots=["/private/"])
    assert ok["allowed"] is True


def test_report_counts_and_refusals():
    rep = _s.policy_report(
        ["https://www.irs.gov/a", "https://www.facebook.com/b",
         "https://host.example/recaptcha/x"],
        robots_map={"https://host.example/recaptcha/x": None})
    assert rep["counts"]["A"] == 1 and rep["counts"]["D"] == 1
    assert rep["counts"]["refused"] == 1
    assert len(rep["refused"]) == 1
    assert "ultra-loose" in rep["standard"]


def test_empty_url_rejected():
    with pytest.raises(ValueError):
        _s.classify("   ")
