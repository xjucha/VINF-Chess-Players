import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

# NASTAVENIA CRAWLERA
DEFAULT_DELAY = 3.0          # zakladny interval medzi poziadavkami (s)
RANDOM_DELAY_MAX = 0.7       # dodatocny nahodny interval 0 az 0.7 s
RATE_LIMIT_PAUSE = 10.0      # minimalna pauza po HTTP 429 (s)
RATE_LIMIT_MULTIPLIER = 2.0  # 3 s -> 6 s; nenásobi sa opakovane
BOOST_SUCCESS_COUNT = 5     # pocet uspesnych profilov pri zvysenom delay
MAX_PROFILE_RETRIES = 1     # opakovania po prvom pokuse (1 = max 2 pokusy)
INCOMPLETE_RETRY_PAUSE = 15.0
NAVIGATION_TIMEOUT_MS = 60000
PROFILE_WAIT_TIMEOUT_MS = 45000
DISCOVERY_RETRIES = 2       # dalsie pokusy pri chybe navigacie
# ============================================================

BASE = 'https://fideratings.com'
ROOT = Path('data')
PLAYERS = ROOT / 'players'
FEDS = ROOT / 'federations'
STATE = ROOT / 'crawler_state.json'
FRONTIER = ROOT / 'player_urls.tsv'
LOG = ROOT / 'crawl_metadata.tsv'
CAP = 2000
MIN_YEAR, MAX_YEAR = 1900, 2030
URL_FIELDS = ['fide_id', 'url', 'federation']
LOG_FIELDS = ['fide_id', 'url', 'status', 'size_bytes', 'timestamp', 'error']
PLAYER_RE = re.compile(r'href=["\'](https?://[^"\']+|/player/\d+/[^"\']+)["\']', re.I)
ID_RE = re.compile(r'/player/(\d+)/')
FED_RE = re.compile(r'href=["\'](?:https?://fideratings\.com)?/federations\?fed=([A-Z]{3})', re.I)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def save_state(state):
    atomic_bytes(STATE, json.dumps(state, ensure_ascii=False, indent=2).encode('utf-8'))


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding='utf-8'))
    return {'federations': {}, 'federation_order': []}


def load_frontier():
    result = {}
    if FRONTIER.exists():
        with FRONTIER.open(encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                if row.get('fide_id') and row.get('url'):
                    result[row['fide_id']] = row
    return result


def append_row(path, fields, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open('a', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
        if new:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def links(html):
    result = {}
    for match in PLAYER_RE.finditer(html):
        url = urljoin(BASE, match.group(1))
        if not url.startswith(BASE + '/player/'):
            continue
        m = ID_RE.search(url)
        if m:
            result[m.group(1)] = url
    return result


def polite_sleep(seconds):
    time.sleep(seconds + random.uniform(0, RANDOM_DELAY_MAX))


def goto(page, url, delay):
    for attempt in range(DISCOVERY_RETRIES + 1):
        try:
            polite_sleep(delay)
            response = page.goto(url, wait_until='domcontentloaded', timeout=NAVIGATION_TIMEOUT_MS)
            if response and response.status >= 400:
                raise RuntimeError(f'HTTP {response.status}')
            return
        except Exception as exc:
            if attempt == DISCOVERY_RETRIES:
                raise
            print(f'  Pokus {attempt + 1} zlyhal: {exc}; opakujem')
            time.sleep((attempt + 1) * 5)


def stable_links(page, max_wait=20):
    deadline = time.monotonic() + max_wait
    previous = None
    same = 0
    while time.monotonic() < deadline:
        current = links(page.content())
        signature = (len(current), tuple(list(current)[:3]), tuple(list(current)[-3:]))
        if current and signature == previous:
            same += 1
            if same >= 3:
                return current
        else:
            same = 0
        previous = signature
        time.sleep(0.6)
    return links(page.content())


def discover_federations(page, delay):
    goto(page, BASE + '/federations', delay)
    page.locator('a[href*="fed="]').first.wait_for(timeout=30000)
    html = page.content()
    return sorted(set(FED_RE.findall(html)))


def save_federation_html(page, fed, force=False):
    target = FEDS / f'{fed}.html'
    if target.exists() and target.stat().st_size > 0 and not force:
        return False
    atomic_bytes(target, page.content().encode('utf-8'))
    print(f'{fed}: uložené HTML federácie: {target} ({target.stat().st_size} B)')
    return True


def federation_links(page, fed, delay, save_snapshot=False):
    goto(page, f'{BASE}/federations?fed={fed}', delay)
    page.locator('#compareCountryLimit').wait_for(timeout=30000)
    page.locator('#compareCountryLimit').select_option('2000')
    found = stable_links(page)
    if save_snapshot:
        save_federation_html(page, fed)
    return found


def apply_birth_filter(page, start, end, delay):
    polite_sleep(delay)
    page.locator('#compareCountryBirthFrom').fill(str(start))
    page.locator('#compareCountryBirthTo').fill(str(end))
    page.locator('#compareCountryBirthApplyBtn').click()
    time.sleep(1.5)
    return stable_links(page)


def store_links(found, fed, frontier):
    new = 0
    for fid, url in found.items():
        if fid not in frontier:
            row = {'fide_id': fid, 'url': url, 'federation': fed}
            append_row(FRONTIER, URL_FIELDS, row)
            frontier[fid] = row
            new += 1
    return new


def expected_federation_count(page):
    return page.evaluate(r"""() => {
        const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const root = document.querySelector('#compareCountries') ||
                     document.querySelector('#compareCountry') ||
                     document.querySelector('[id*=compareCountr]')?.closest('section') ||
                     document.body;
        const elements = [...root.querySelectorAll('*')].filter(visible);
        const candidates = [];
        for (const e of elements) {
            if (e.children.length > 3) continue;
            const own = (e.innerText || '').trim();
            if (!/^PLAYERS$/i.test(own)) continue;
            let parent = e.parentElement;
            for (let level = 0; level < 3 && parent; level++, parent = parent.parentElement) {
                const t = (parent.innerText || '').trim();
                if (t.length > 100) continue;
                const m = t.match(/(?:^|\n)\s*PLAYERS\s*(?:\n|:|\s)\s*([\d,\s]+)(?:\n|$)/i) ||
                          t.match(/(?:^|\n)\s*([\d,]+)\s*(?:\n|\s)\s*PLAYERS(?:\n|$)/i);
                if (m) {
                    const n = Number(m[1].replace(/[,\s]/g, ''));
                    if (Number.isInteger(n) && n >= 0) candidates.push(n);
                }
            }
        }
        const unique = [...new Set(candidates)];
        return unique.length === 1 ? unique[0] : null;
    }""")


def verify_federation(page, fed, info, frontier, delay, already_open=False):
    if not already_open:
        federation_links(page, fed, delay, save_snapshot=True)
    expected = expected_federation_count(page)
    discovered = sum(row.get('federation') == fed for row in frontier.values())
    info['expected_players'] = expected
    info['discovered_players'] = discovered
    info['known_player_urls'] = discovered
    info['missing_players'] = max(0, expected - discovered) if expected is not None else None
    info['verified_at'] = timestamp()
    if expected is None:
        verification = 'unverified'
    elif expected == discovered:
        verification = 'matched'
    elif discovered < expected:
        verification = 'incomplete'
    else:
        verification = 'mismatch'
    info['verification'] = verification
    if verification in ('incomplete', 'mismatch'):
        info['status'] = verification
    elif verification == 'matched' and not info.get('unresolved_ranges') and not info.get('pending_ranges'):
        info['status'] = 'completed'
    elif verification == 'unverified' and info.get('status') == 'completed':
        info['status'] = 'unverified'
    print(f'{fed}: na stranke={expected if expected is not None else "nezistene"}, '
          f'objavených={discovered}, rozdiel={info["missing_players"]}, '
          f'kontrola={verification}, stav={info["status"]}')


def discover_one(page, fed, state, frontier, delay):
    info = state['federations'].setdefault(fed, {'status': 'pending', 'pending_ranges': [], 'done_ranges': []})
    if info['status'] in ('completed', 'unverified') and info.get('initialized'):
        print(f'{fed}: federacia uz objavena, kontrolujem pocet hracov')
        federation_links(page, fed, delay)
        current_count = expected_federation_count(page)
        previous_count = info.get('expected_players')
        snapshot = FEDS / f'{fed}.html'
        if current_count is not None and previous_count is not None and current_count != previous_count:
            print(f'{fed}: zmena poctu hracov {previous_count} -> {current_count}; obnovujem objavovanie')
            save_federation_html(page, fed, force=True)
            info['status'] = 'pending'
            info['initialized'] = False
            info['pending_ranges'] = []
            info['done_ranges'] = []
            info.pop('unresolved_ranges', None)
            save_state(state)
            # Run the existing discovery algorithm again. Previously discovered
            # URLs remain in the frontier; this is an additive refresh.
            discover_one(page, fed, state, frontier, delay)
            return
        if not snapshot.exists() or snapshot.stat().st_size == 0:
            save_federation_html(page, fed)
        verify_federation(page, fed, info, frontier, delay, already_open=True)
        save_state(state)
        return
    print(f'\n=== Federacia {fed} ===')
    base_links = federation_links(page, fed, delay, save_snapshot=True)
    new = store_links(base_links, fed, frontier)
    print(f'Nefiltrovane: {len(base_links)} URL, novych: {new}')
    info['status'] = 'in_progress'
    if not info.get('initialized'):
        info['initialized'] = True
        info['base_count'] = len(base_links)
        info['pending_ranges'] = [[MIN_YEAR, MAX_YEAR]] if len(base_links) >= CAP else []
        info['done_ranges'] = []
        save_state(state)
    # A federation with fewer than CAP links does not need year filters.
    while info['pending_ranges']:
        start, end = info['pending_ranges'][0]
        # Reload federation for each range to avoid stale filter state.
        federation_links(page, fed, delay)
        found = apply_birth_filter(page, start, end, delay)
        added = store_links(found, fed, frontier)
        print(f'{fed} {start}-{end}: {len(found)} URL, novych {added}')
        if len(found) >= CAP and start < end:
            middle = (start + end) // 2
            info['pending_ranges'] = [[start, middle], [middle + 1, end]] + info['pending_ranges'][1:]
        elif len(found) >= CAP:
            # A single year still exceeds CAP; cannot claim completeness.
            info.setdefault('unresolved_ranges', []).append([start, end])
            info['pending_ranges'].pop(0)
        else:
            info['done_ranges'].append([start, end])
            info['pending_ranges'].pop(0)
        save_state(state)
    info['status'] = 'completed' if not info.get('unresolved_ranges') else 'partial'
    info['last_checked'] = timestamp()
    verify_federation(page, fed, info, frontier, delay)
    save_state(state)


class RateLimitedError(RuntimeError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def retry_after_seconds(value):
    if not value:
        return None
    try:
        return max(0, min(86400, int(value)))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            return max(0, min(86400, int((dt - datetime.now(timezone.utc)).total_seconds())))
        except (TypeError, ValueError, OverflowError):
            return None


def profile_goto(page, url, delay):
    polite_sleep(delay)

    response = page.goto(
        url,
        wait_until='domcontentloaded',
        timeout=NAVIGATION_TIMEOUT_MS
    )

    if response is not None:
        if response.status == 429:
            raise RateLimitedError(
                'HTTP 429',
                retry_after_seconds(response.headers.get('retry-after'))
            )

        if response.status >= 400:
            raise RuntimeError(f'HTTP {response.status}')

    return response



# Validation uses rendered HTML elements, never window.__PLAYER_DATA__.
PROFILE_RE = re.compile(r"<div\b(?=[^>]*\bid=['\"]playerProfile['\"])[^>]*>(.*?)(?=<h3\b[^>]*\bid=['\"]playerHistoryTitle['\"]|<script\b|$)", re.I | re.S)
NAME_RE = re.compile(r"<h2\b(?=[^>]*\bid=['\"]playerName['\"])[^>]*>\s*([^<]+?)\s*</h2>", re.I | re.S)
RATING_RE = re.compile(r"<[^>]+\bclass=['\"][^'\"]*\bprc-rating\b[^'\"]*['\"][^>]*>\s*\d{3,4}\s*</[^>]+>", re.I)
HISTORY_RE = re.compile(r"<[^>]+\bid=['\"]playerHistoryTable['\"]", re.I)


def validate_profile(html, fid):
    if re.search(r'429 Too Many Requests|\bHTTP 429\b', html, re.I):
        return False, '429 Too Many Requests'
    name = NAME_RE.search(html)
    if not name or name.group(1).strip().lower() in ('not found', 'loading'):
        return False, 'Meno hraca sa nenacitalo'
    profile = PROFILE_RE.search(html)
    if not profile:
        return False, 'Chyba vykresleny #playerProfile'
    body = profile.group(1)
    if re.search(r'Loading complete profile|player-profile-loading', body, re.I):
        return False, 'Profil zostal v stave Loading'
    if not RATING_RE.search(body):
        return False, 'V HTML profile chyba vykresleny rating'
    if not HISTORY_RE.search(html):
        return False, 'Chyba tabulka historie (element)'
    # URL must refer to the requested FIDE ID, not merely mention it in a script.
    if not re.search(r"/player/" + re.escape(str(fid)) + r"(?:/|['\"?&#])", html):
        return False, 'FIDE ID sa nezhoduje s URL profilu'
    return True, 'OK'


def valid_saved(path, fid):
    if not path.exists() or path.stat().st_size < 3000:
        return False
    try:
        return validate_profile(path.read_text(encoding='utf-8'), fid)[0]
    except (OSError, UnicodeError):
        return False


def download_profiles(page, frontier, max_players, delay, federation='ALL'):
    run_started = time.monotonic()
    downloaded = 0
    skipped = 0
    current_delay = delay
    boosted_remaining = 0
    rate_limit_streak = 0
    rows = ((fid, row) for fid, row in frontier.items()
            if federation == 'ALL' or row.get('federation', '').upper() == federation)
    for fid, row in rows:
        if max_players is not None and downloaded >= max_players:
            break
        target = PLAYERS / f'{fid}.html'
        if valid_saved(target, fid):
            skipped += 1
            continue
        profile_started = time.monotonic()
        try:
            for attempt in range(MAX_PROFILE_RETRIES + 1):
                try:
                    profile_goto(page, row['url'], current_delay)
                    try:
                        page.locator('#playerProfile .prc-rating').first.wait_for(state='attached', timeout=PROFILE_WAIT_TIMEOUT_MS)
                    except Exception:
                        pass
                    html = page.content()
                    ok, reason = validate_profile(html, fid)
                    if '429' in reason:
                        raise RateLimitedError(reason)
                    if ok:
                        break
                    if attempt < MAX_PROFILE_RETRIES:
                        print(f'Profil {fid}: {reason}; opakujem po pauze')
                        time.sleep(INCOMPLETE_RETRY_PAUSE)
                except RateLimitedError:
                    raise
            else:
                raise ValueError(reason)
            atomic_bytes(target, html.encode('utf-8'))
            append_row(LOG, LOG_FIELDS, dict(fide_id=fid, url=row['url'], status='success',
                                             size_bytes=target.stat().st_size, timestamp=timestamp(), error=''))
            downloaded += 1
            if boosted_remaining > 0:
                boosted_remaining -= 1
                if boosted_remaining == 0:
                    current_delay = delay
                    rate_limit_streak = 0
                    print(f'  {BOOST_SUCCESS_COUNT} uspesnych profilov po 429; '
                          f'navrat na delay {delay:.1f} s')
            elapsed_profile = time.monotonic() - profile_started
            elapsed_run = time.monotonic() - run_started
            average = elapsed_run / downloaded
            remaining = (max_players - downloaded) * average if max_players is not None else None
            eta = f'; odhad zostava {remaining / 60:.1f} min' if remaining is not None else ''
            print(f'Profil {fid}: {target.stat().st_size} B '
                  f'({downloaded}/{max_players if max_players is not None else "bez limitu"}); '
                  f'cas {elapsed_profile:.1f} s; priemer {average:.1f} s/profil{eta}')
        except KeyboardInterrupt:
            raise
        except RateLimitedError as exc:
            rate_limit_streak += 1

            current_delay = delay * RATE_LIMIT_MULTIPLIER
            boosted_remaining = BOOST_SUCCESS_COUNT

            append_row(
                LOG,
                LOG_FIELDS,
                dict(
                    fide_id=fid,
                    url=row['url'],
                    status='rate_limited',
                    size_bytes=0,
                    timestamp=timestamp(),
                    error=str(exc)
                )
            )

            if rate_limit_streak >= 3:
                print(
                    f'Profil {fid}: opakovany HTTP 429 '
                    f'({rate_limit_streak} pocas zvyseneho delay). '
                    f'Zastavujem stahovanie.'
                )
                break

            wait = max(
                RATE_LIMIT_PAUSE if rate_limit_streak == 1 else 30.0,
                exc.retry_after or 0.0
            )

            print(
                f'Profil {fid}: HTTP 429; '
                f'pauza {wait:.0f} s; '
                f'delay {current_delay:.1f} s; '
                f'preskakujem profil'
            )

            time.sleep(wait)
            continue
        except Exception as exc:
            print(f'Profil {fid}: CHYBA {exc}')
            append_row(LOG, LOG_FIELDS, dict(fide_id=fid, url=row['url'], status='failed',
                                             size_bytes=0, timestamp=timestamp(), error=str(exc).replace('\t', ' ')[:300]))
    total_elapsed = time.monotonic() - run_started
    print(f'Nove profily v tomto spusteni: {downloaded}; preskocene platne: {skipped}')
    print(f'Celkovy cas download rezimu: {total_elapsed / 60:.2f} min '
          f'({total_elapsed:.1f} s); '
          f'priemer {total_elapsed / downloaded:.1f} s/novy profil' if downloaded else
          f'Celkovy cas download rezimu: {total_elapsed / 60:.2f} min; ziadny novy profil')


def main():
    parser = argparse.ArgumentParser(description='Resumable FIDE Ratings crawler')
    parser.add_argument('--federation', default='POL', help='kod federacie alebo ALL')
    parser.add_argument('--max-players', type=int, default=None, help='volitelny limit novych profilov; bez parametra neobmedzene')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--discover-only', action='store_true')
    mode.add_argument('--download-only', action='store_true')
    parser.add_argument('--delay', type=float, default=DEFAULT_DELAY, help=f'zakladny interval medzi poziadavkami v sekundach (predvolene {DEFAULT_DELAY})')
    parser.add_argument('--resume', action='store_true', help='pokracovanie je automaticke, prepinac aby bolo jasne')
    args = parser.parse_args()
    if (args.max_players is not None and args.max_players < 0) or args.delay < 1:
        parser.error('max-players musi byt >= 0 a delay >= 1')
    PLAYERS.mkdir(parents=True, exist_ok=True)
    FEDS.mkdir(parents=True, exist_ok=True)
    state = load_state()
    frontier = load_frontier()
    if args.download_only and not frontier:
        parser.error('Chyba data/player_urls.tsv alebo neobsahuje URL. Najprv spusti --discover-only.')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                extra_http_headers={
                    "From": "xjuchat@stuba.sk",
                    "X-Project": "University student project"
                }
            )
            page = context.new_page()

            if not args.download_only:
                if args.federation.upper() == 'ALL':
                    if not state['federation_order']:
                        state['federation_order'] = discover_federations(page, args.delay)
                        save_state(state)
                    federations = state['federation_order']
                else:
                    federations = [args.federation.upper()]
                for fed in federations:
                    if not re.fullmatch('[A-Z]{3}', fed):
                        raise ValueError(f'Neplatny kod federacie: {fed}')
                    discover_one(page, fed, state, frontier, args.delay)
            if not args.discover_only:
                federation = args.federation.upper()
                if federation != 'ALL' and not re.fullmatch('[A-Z]{3}', federation):
                    raise ValueError(f'Neplatny kod federacie: {federation}')
                download_profiles(page, frontier, args.max_players, args.delay, federation)
        except KeyboardInterrupt:
            print('\nPrerusene. Ulozene HTML, frontier a checkpoint zostavaju zachovane.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()

