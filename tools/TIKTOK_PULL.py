#!/usr/bin/env python3
"""TIKTOK_PULL.py — TikTok downloader bypass (verified 2026-10-26).

Why it exists: TikTok IP-blocks anonymous fetches ("Your IP address is
blocked"), and Chrome cookies are unreadable (app-bound DPAPI). Firefox
cookies.sqlite is plain SQLite — this tool clones it via the SQLite backup
API (no lock issues), exports TikTok session cookies to a temp Netscape
file, and hands it to yt-dlp.

Nothing secret is written to the repo: the cookies file lives in %TEMP%
and is deleted after the pull.

Usage:
    python TIKTOK_PULL.py <tiktok_url> [outdir]
Example:
    python TIKTOK_PULL.py https://www.tiktok.com/@user/video/123
"""
import os
import sqlite3
import subprocess
import sys
import tempfile


def find_firefox_cookies():
    base = os.path.expandvars(r'%APPDATA%\Mozilla\Firefox\Profiles')
    if not os.path.isdir(base):
        raise SystemExit('No Firefox profiles dir: ' + base)
    cands = []
    for prof in os.listdir(base):
        p = os.path.join(base, prof, 'cookies.sqlite')
        if os.path.isfile(p):
            cands.append((os.path.getmtime(p), p))
    if not cands:
        raise SystemExit('No cookies.sqlite in any Firefox profile')
    cands.sort(reverse=True)
    return cands[0][1]


def export_tiktok_cookies(src, dest):
    s = sqlite3.connect('file:%s?mode=ro' % src, uri=True)
    d = sqlite3.connect(dest)
    s.backup(d)
    d.close()
    s.close()
    d = sqlite3.connect(dest)
    rows = d.execute(
        "SELECT host, path, isSecure, expiry, name, value FROM moz_cookies "
        "WHERE host LIKE '%tiktok%'").fetchall()
    d.close()
    if not rows:
        raise SystemExit('No TikTok cookies in Firefox profile (log into TikTok in Firefox first)')
    cookie_file = os.path.join(tempfile.gettempdir(), 'tiktok_cookies.txt')
    with open(cookie_file, 'w') as f:
        f.write('# Netscape HTTP Cookie File\n')
        for host, path, sec, exp, name, val in rows:
            f.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (
                host, 'TRUE' if host.startswith('.') else 'FALSE',
                path, 'TRUE' if sec else 'FALSE', exp, name, val or ''))
    return cookie_file, len(rows)


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python TIKTOK_PULL.py <tiktok_url> [outdir]')
    url = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.expandvars('%USERPROFILE%'), 'Desktop', 'HTML_MP4')
    os.makedirs(outdir, exist_ok=True)
    src = find_firefox_cookies()
    print('Firefox cookies: ' + src)
    tmpdb = os.path.join(tempfile.gettempdir(), 'ff_cookies_pull.sqlite')
    if os.path.exists(tmpdb):
        os.remove(tmpdb)
    export_tiktok_cookies(src, tmpdb)
    os.remove(tmpdb)
    cookie_file = os.path.join(tempfile.gettempdir(), 'tiktok_cookies.txt')
    print('Exported TikTok session cookies, pulling...')
    try:
        r = subprocess.run(
            ['yt-dlp', '--cookies', cookie_file, '--no-playlist',
             '-o', os.path.join(outdir, 'TIKTOK_%(id)s.%(ext)s'), url],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        print((r.stdout or '')[-800:])
        if r.returncode != 0:
            print((r.stderr or '')[-800:])
            raise SystemExit('yt-dlp failed (rc=%d)' % r.returncode)
    finally:
        if os.path.exists(cookie_file):
            os.remove(cookie_file)
    print('DONE -> ' + outdir)


if __name__ == '__main__':
    main()
