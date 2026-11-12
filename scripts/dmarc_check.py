"""
dmarc_check.py — Check DMARC/SPF/DKIM for BCC domains.
Resolves gap: 28
"""

import json
import time
import dns.resolver
import dns.exception
from pathlib import Path

BCC_DOMAINS = [
    'bcc-cares.org',
    'berkscounselingcenter.org',
    'cocaberks.org',
    'communitycarepa.org',
    'tascpa.org',
]

OUTPUT_DIR = Path(r"C:\Azazael\azazael\research\bcc_analysis\dmarc_check")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def check_dmarc(domain):
    """Check DMARC record for domain."""
    try:
        answers = dns.resolver.resolve(f'_dmarc.{domain}', 'TXT')
        for rdata in answers:
            txt = ' '.join([s.decode() if isinstance(s, bytes) else s for s in rdata.strings])
            if txt.startswith('v=DMARC1'):
                return {'domain': domain, 'record': txt, 'found': True}
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
        pass
    return {'domain': domain, 'record': None, 'found': False}


def check_spf(domain):
    """Check SPF record."""
    try:
        answers = dns.resolver.resolve(domain, 'TXT')
        for rdata in answers:
            txt = ' '.join([s.decode() if isinstance(s, bytes) else s for s in rdata.strings])
            if txt.startswith('v=spf1'):
                return {'domain': domain, 'record': txt, 'found': True}
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
        pass
    return {'domain': domain, 'record': None, 'found': False}


def check_dkim(domain, selectors=['default', 'google', 'k1', 'k2', 'mail', 'selector1', 'selector2']):
    """Check DKIM records for common selectors."""
    results = []
    for selector in selectors:
        try:
            answers = dns.resolver.resolve(f'{selector}._domainkey.{domain}', 'TXT')
            for rdata in answers:
                txt = ' '.join([s.decode() if isinstance(s, bytes) else s for s in rdata.strings])
                if 'v=DKIM1' in txt or 'p=' in txt:
                    results.append({'selector': selector, 'record': txt, 'found': True})
                    break
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
            continue
    return {'domain': domain, 'dkim': results, 'found': len(results) > 0}


def check_domain(domain):
    """Run all checks for a domain."""
    print(f"  Checking {domain}...")
    return {
        'domain': domain,
        'dmarc': check_dmarc(domain),
        'spf': check_spf(domain),
        'dkim': check_dkim(domain),
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S')
    }


def check_all():
    """Check all BCC domains."""
    results = []
    for domain in BCC_DOMAINS:
        result = check_domain(domain)
        results.append(result)
        time.sleep(0.5)  # Be nice to DNS
    
    output = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'domains': results,
        'gaps_targeted': [28]
    }
    
    (OUTPUT_DIR / 'dmarc_check.json').write_text(json.dumps(output, indent=2, default=str))
    return output


if __name__ == '__main__':
    check_all()