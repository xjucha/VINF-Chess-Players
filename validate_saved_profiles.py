"""Offline audit of saved FIDE HTML profiles and crawl_metadata.tsv.

Run from the directory containing data/ and crawler.py.
No network requests; no Playwright dependency.
"""
import argparse
import csv
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ['fide_id', 'url', 'status', 'size_bytes', 'timestamp', 'error']
PROFILE_RE = re.compile(r"<div\b(?=[^>]*\bid=['\"]playerProfile['\"])[^>]*>(.*?)(?=<h3\b[^>]*\bid=['\"]playerHistoryTitle['\"]|<script\b|$)", re.I | re.S)
NAME_RE = re.compile(r"<h2\b(?=[^>]*\bid=['\"]playerName['\"])[^>]*>\s*([^<]+?)\s*</h2>", re.I | re.S)
RATING_RE = re.compile(r"<[^>]+\bclass=['\"][^'\"]*\bprc-rating\b[^'\"]*['\"][^>]*>\s*\d{3,4}\s*</[^>]+>", re.I)
HISTORY_RE = re.compile(r"<[^>]+\bid=['\"]playerHistoryTable['\"]", re.I)

def validate(html, fid):
    if re.search(r'429 Too Many Requests|\bHTTP 429\b', html, re.I):
        return 'rate_limited', '429 Too Many Requests'
    name = NAME_RE.search(html)
    if not name or name.group(1).strip().lower() in ('not found', 'loading'):
        return 'invalid_html', 'Meno hraca sa nenacitalo'
    profile = PROFILE_RE.search(html)
    if not profile:
        return 'invalid_html', 'Chyba vykresleny #playerProfile'
    body = profile.group(1)
    if re.search(r'Loading complete profile|player-profile-loading', body, re.I):
        return 'invalid_html', 'Profil zostal v stave Loading'
    if not RATING_RE.search(body):
        return 'invalid_html', 'Chyba vykresleny rating'
    if not HISTORY_RE.search(html):
        return 'invalid_html', 'Chyba tabulka historie (element)'
    if not re.search(r'/player/' + re.escape(fid) + r'(?:/|[\'"?&#])', html):
        return 'invalid_html', 'FIDE ID sa nezhoduje s URL profilu'
    return 'success', 'OK'

def main():
    parser = argparse.ArgumentParser(description='Offline audit ulozenych HTML profilov')
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--apply', action='store_true', help='Zapisat opravne zaznamy do metadata (predvolene iba kontrola)')
    args = parser.parse_args()
    players = args.data_dir / 'players'
    metadata = args.data_dir / 'crawl_metadata.tsv'
    files = sorted(players.glob('*.html')) if players.is_dir() else []
    if not files:
        print(f'Nenasli sa ziadne HTML subory v {players.resolve()}')
        return
    rows = []
    if metadata.exists():
        with metadata.open('r', encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f, delimiter='\t'))
    last = {r.get('fide_id'): r for r in rows if r.get('fide_id')}
    urls = {}
    frontier = args.data_dir / 'player_urls.tsv'
    if frontier.exists():
        with frontier.open('r', encoding='utf-8', newline='') as f:
            urls = {r['fide_id']: r.get('url', '') for r in csv.DictReader(f, delimiter='\t') if r.get('fide_id')}
    counts = Counter()
    changes = []
    for file in files:
        fid = file.stem
        try:
            if not fid.isdecimal() or file.stat().st_size < 3000:
                status, reason = 'invalid_html', 'Neplatny nazov alebo prilis maly subor'
            else:
                status, reason = validate(file.read_text(encoding='utf-8'), fid)
        except (OSError, UnicodeError) as exc:
            status, reason = 'invalid_html', f'Nepodarilo sa precitat: {exc}'
        counts[status] += 1
        old = last.get(fid, {}).get('status', '')
        if status != 'success':
            print(f'CHYBA {fid}: {status} - {reason} (metadata: {old or "ziadne"})')
        if status != old:
            # Append correction, preserving the original chronological log.
            changes.append(dict(fide_id=fid, url=urls.get(fid) or last.get(fid, {}).get('url', ''),
                                status=status, size_bytes=file.stat().st_size if status == 'success' else 0,
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                error='' if status == 'success' else f'Offline audit: {reason}'))
    print(f'\nSkontrolovane: {len(files)} | platne: {counts["success"]} | '
          f'429: {counts["rate_limited"]} | ine chybne: {counts["invalid_html"]}')
    print(f'Metadata na aktualizaciu: {len(changes)}')
    if not args.apply:
        print('Iba kontrola. Pre zapis pouzi --apply. HTML subory sa nemenia.')
        return
    if changes:
        metadata.parent.mkdir(parents=True, exist_ok=True)
        if metadata.exists():
            backup = metadata.with_name(metadata.name + '.bak')
            shutil.copy2(metadata, backup)
            print(f'Zaloha povodnych metadat: {backup}')
        new_file = not metadata.exists() or metadata.stat().st_size == 0
        with metadata.open('a', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter='\t', extrasaction='ignore')
            if new_file:
                writer.writeheader()
            writer.writerows(changes)
        print(f'Pridanych {len(changes)} opravnych zaznamov do {metadata}')
    else:
        print('Metadata uz zodpovedaju stavu ulozenych HTML.')

if __name__ == '__main__':
    main()
