import argparse
import csv
import html as html_lib
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAYER_FIELDS = [
    'fide_id', 'name', 'federation', 'birth_year', 'gender', 'title',
    'standard', 'rapid', 'blitz',
    'standard_world_rank', 'standard_national_rank', 'standard_peak', 'standard_peak_date',
    'rapid_world_rank', 'rapid_national_rank', 'rapid_peak', 'rapid_peak_date',
    'blitz_world_rank', 'blitz_national_rank', 'blitz_peak', 'blitz_peak_date',
]
HISTORY_FIELDS = ['fide_id', 'list_date', 'rating', 'world_rank', 'national_rank',
                  'change', 'games', 'title', 'country', 'status']
FLAGS = re.I | re.S


def match(pattern, source, group=1):
    found = re.search(pattern, source, FLAGS)
    return found.group(group) if found else ''


def clean(value):
    value = re.sub(r'<[^>]*>', ' ', value)
    return re.sub(r'\s+', ' ', html_lib.unescape(value)).strip()


def class_tag(source, tag, class_name):
    # Match a complete element whose class attribute contains the requested class.
    pattern = (rf'<{tag}\b(?=[^>]*\bclass\s*=\s*["\'][^"\']*\b{re.escape(class_name)}\b)'
               rf'[^>]*>(.*?)</{tag}\s*>')
    return match(pattern, source)


def meta_value(source, label):
    return clean(match(r'<span\b[^>]*class=["\'][^"\']*\bprofile-label\b[^"\']*["\'][^>]*>\s*'
                       + re.escape(label) + r'\s*</span>\s*'
                       r'<(?:span|a)\b[^>]*class=["\'][^"\']*\bprofile-value\b[^"\']*["\'][^>]*>(.*?)</(?:span|a)>', source))


def extract_profile(raw_html):
    # In-memory only: do not modify the source HTML file.
    source = re.sub(r'<script\b[^>]*>.*?</script\s*>', '', raw_html, flags=FLAGS)
    profile = match(r'<section\b[^>]*id=["\']view-player["\'][^>]*>(.*?)</section\s*>', source)
    # The section contains nested sections, so use the whole script-free HTML for scoped IDs.
    if not profile:
        raise ValueError('Missing view-player section')
    name = clean(match(r'<h2\b[^>]*id=["\']playerName["\'][^>]*>(.*?)</h2>', source))
    details = match(r'<div\b[^>]*class=["\'][^"\']*\bprofile-info-line\b[^"\']*["\'][^>]*>(.*?)</div>', source)
    fide_id = clean(match(r'<a\b[^>]*href=["\']https?://ratings\.fide\.com/profile/\d+["\'][^>]*>(\d+)</a>', details))
    if not fide_id:
        fide_id = clean(match(r'<span\b[^>]*class=["\'][^"\']*\bprofile-label\b[^"\']*["\'][^>]*>\s*FIDE ID\s*</span>\s*<a[^>]*>(\d+)</a>', details))
    if not name or not fide_id:
        raise ValueError('Missing player name or FIDE ID in rendered HTML')
    federation = match(r'<a\b[^>]*href=["\']/federations\?fed=([A-Za-z]{3})', details).upper()
    row = dict.fromkeys(PLAYER_FIELDS, '')
    row.update(fide_id=fide_id, name=name, federation=federation,
               birth_year=meta_value(details, 'Born'), gender=meta_value(details, 'Gender'),
               title=meta_value(details, 'Title'))
    # Rating cards are before the mobile duplicate, and the rating type is explicit.
    grid = match(r'<div\b[^>]*class=["\'][^"\']*\bprofile-ratings-grid\b[^"\']*["\'][^>]*>(.*?)<section\b[^>]*class=["\'][^"\']*\bprofile-mobile-ratings\b', source)
    if not grid:
        # The grid can be closed before the mobile section; fall back to the span between markers.
        grid = match(r'<div\b[^>]*class=["\'][^"\']*\bprofile-ratings-grid\b[^"\']*["\'][^>]*>(.*?)<section\b[^>]*class=["\'][^"\']*\bprofile-mobile-ratings\b', source)
    for rating_type in ('standard', 'rapid', 'blitz'):
        # Each card starts at prc-type and ends at the next prc-type / mobile section.
        card = match(r'<div\b[^>]*class=["\'][^"\']*\bprc-type\b[^"\']*["\'][^>]*>\s*'
                     + rating_type + r'\s*</div>(.*?)(?=<div\b[^>]*class=["\'][^"\']*\bprc-type\b|<section\b[^>]*class=["\'][^"\']*\bprofile-mobile-ratings\b|\Z)', grid)
        row[rating_type] = clean(match(r'<span\b[^>]*class=["\'][^"\']*\bprc-rating\b[^"\']*["\'][^>]*\bdata-type=["\']'
                                       + rating_type + r'["\'][^>]*>(.*?)</span>', card))
        ranks = re.findall(r'<div\b[^>]*class=["\'][^"\']*\bprc-rank\b[^"\']*["\'][^>]*>(.*?)</div>', card, FLAGS)
        for rank in ranks:
            rank_number = clean(class_tag(rank, 'span', 'prc-rank-n')).lstrip('#')
            label = clean(class_tag(rank, 'span', 'prc-rank-l')).lower()
            if label in ('world', 'national'):
                row[f'{rating_type}_{label}_rank'] = rank_number
        peak = match(r'<div\b[^>]*class=["\'][^"\']*\bprc-peak\b(?![^"\']*\bprc-peak-pos\b)[^"\']*["\'][^>]*>(.*?)</div>', card)
        row[f'{rating_type}_peak'] = clean(class_tag(peak, 'span', 'prc-peak-rating'))
        row[f'{rating_type}_peak_date'] = clean(class_tag(peak, 'a', 'prc-peak-date'))
    if not any(row[k] for k in ('standard', 'rapid', 'blitz')):
        raise ValueError('No rendered ratings found')

    history = []
    tbody = match(r'<tbody\b[^>]*id=["\']playerHistoryTable["\'][^>]*>(.*?)</tbody>', source)
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', tbody, FLAGS):
        cells = [clean(cell) for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', tr, FLAGS)]
        if len(cells) < 12 or not re.fullmatch(r'\d{4}-\d{2}', cells[0]):
            continue
        history.append(dict(zip(HISTORY_FIELDS, [fide_id, cells[0], cells[1], cells[2], cells[3],
                                                      cells[6], cells[7], cells[8], cells[10], cells[11]])))
    return row, history


def existing_ids(path):
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if reader.fieldnames != PLAYER_FIELDS:
            raise ValueError(f'Unexpected columns in {path}; use --rebuild to recreate it')
        return {r['fide_id'] for r in reader if r.get('fide_id')}


def existing_history_keys(path):
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if reader.fieldnames != HISTORY_FIELDS:
            raise ValueError(f'Unexpected columns in {path}; use --rebuild')
        return {(r['fide_id'], r['list_date']) for r in reader}


def open_tsv(path, fields, rebuild):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 'w' if rebuild or not path.exists() or path.stat().st_size == 0 else 'a'
    handle = path.open(mode, encoding='utf-8', newline='')
    writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t', lineterminator='\n')
    if mode == 'w':
        writer.writeheader()
        handle.flush()
    return handle, writer


def main():
    parser = argparse.ArgumentParser(description='Extract rendered HTML chess profiles using regex')
    parser.add_argument('--html-dir', type=Path, default=BASE / 'data' / 'players')
    parser.add_argument('--output-dir', type=Path, default=BASE / 'data')
    parser.add_argument('--rebuild', action='store_true', help='Recreate TSV files from all HTML files')
    parser.add_argument('--limit', type=int, default=0, help='Process at most N new profiles (0 = all)')
    args = parser.parse_args()
    if not args.html_dir.is_dir():
        parser.error(f'HTML folder does not exist: {args.html_dir}')
    players_file = args.output_dir / 'players.tsv'
    history_file = args.output_dir / 'rating_history.tsv'
    done = set() if args.rebuild else existing_ids(players_file)
    if not args.rebuild and players_file.exists() and not history_file.exists() and done:
        parser.error('rating_history.tsv missing while players.tsv exists; use --rebuild to synchronize')
    history_keys = set() if args.rebuild else existing_history_keys(history_file)
    player_handle, player_writer = open_tsv(players_file, PLAYER_FIELDS, args.rebuild)
    history_handle, history_writer = open_tsv(history_file, HISTORY_FIELDS, args.rebuild)
    added = skipped = failed = histories = 0
    try:
        for path in sorted(args.html_dir.glob('*.html')):
            if path.stem in done:
                skipped += 1
                continue
            try:
                player, history = extract_profile(path.read_text(encoding='utf-8'))
                if player['fide_id'] != path.stem:
                    raise ValueError(f'FIDE ID {player["fide_id"]} differs from filename {path.stem}')
                # Write history first, then player as completion marker.
                # On restart, avoid duplicates in history after an interrupted write.
                fresh_history = [h for h in history if (h['fide_id'], h['list_date']) not in history_keys]
                history_writer.writerows(fresh_history)
                history_handle.flush()
                history_keys.update((h['fide_id'], h['list_date']) for h in fresh_history)
                player_writer.writerow(player)
                player_handle.flush()
                done.add(player['fide_id'])
                added += 1
                histories += len(fresh_history)
                if added % 100 == 0:
                    print(f'New: {added}, skipped: {skipped}, errors: {failed}', flush=True)
                if args.limit and added >= args.limit:
                    break
            except (OSError, UnicodeError, ValueError, csv.Error) as exc:
                failed += 1
                print(f'ERROR {path.name}: {exc}', flush=True)
    finally:
        player_handle.close()
        history_handle.close()
    print(f'Done. New profiles: {added}; skipped: {skipped}; errors: {failed}; history rows: {histories}')
    print(f'Files: {players_file} | {history_file}')


if __name__ == '__main__':
    main()
