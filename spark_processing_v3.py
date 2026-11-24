import argparse
import re
import unicodedata
from pathlib import Path
from datetime import datetime
from pyspark.sql import SparkSession, Window, functions as F, types as T

WIKI_DIR = Path('data/wiki')
OUTPUT_DIR = Path('data/spark_output_v3')
PLAYERS_FILE = 'data/players.tsv'

# Wikipedia wikitext: the infobox key may contain spaces or underscores.
FIDE_REGEX = (r'(?im)^[ \t]*\|[ \t]*fide[ \t_-]*id[ \t]*=[ \t]*'
              r'(?:\[\[[^\]|]*\|[ \t]*)?(\d{5,10})')
BIRTH_DATE_REGEX = r'(?im)^[ \t]*\|[ \t]*birth[ \t_]*date[ \t]*=[ \t]*([^\r\n]*)'
BIRTH_PLACE_REGEX = r'(?im)^[ \t]*\|[ \t]*birth[ \t_]*place[ \t]*=[ \t]*([^\r\n]*)'
DEATH_DATE_REGEX = r'(?im)^[ \t]*\|[ \t]*death[ \t_]*date[ \t]*=[ \t]*([^\r\n]*)'
DEATH_PLACE_REGEX = r'(?im)^[ \t]*\|[ \t]*death[ \t_]*place[ \t]*=[ \t]*([^\r\n]*)'

# A level-2 section includes all level-3 subsections until the next level-2 heading.
SECTION_END = r'(?=^[ \t]*==[ \t]*[^=\r\n][^\r\n]*?==[ \t]*\r?$|\Z)'
def section_regex(names):
    return r'(?ims)^[ \t]*==[ \t]*(?:' + names + r')[ \t]*==[ \t]*\r?\n(.*?)' + SECTION_END

EARLY_REGEX = section_regex(r'Early[ \t]+life(?:[ \t]+and[ \t]+education)?|Childhood|Early[ \t]+years|Early[ \t]+life[ \t]+and[ \t]+career')
CAREER_REGEX = section_regex(r'Chess[ \t]+career|Career|Playing[ \t]+career|Professional[ \t]+career')
TOURNAMENT_REGEX = section_regex(r'Tournament(?:[ \t]+and[ \t]+match)?[ \t]+results|Notable[ \t]+tournaments|Tournament[ \t]+career|Major[ \t]+tournaments|World[ \t]+championships?|Competitions')
ACHIEVEMENTS_REGEX = section_regex(r'Achievements|Honours|Honors|Notable[ \t]+results|Major[ \t]+achievements|Awards(?:[ \t]+and[ \t]+honours)?')
# Lead = content before the first level-2 section. Strip the infobox before saving.
LEAD_REGEX = r'(?ms)\A(.*?)(?=^[ \t]*==[ \t]*[^=\r\n][^\r\n]*?==[ \t]*\r?$|\Z)'
CHESS_REGEX = r'(?i)\b(?:chess|grandmaster|international[ \t]+master|world[ \t]+champion|fide|chessboard|chessplayer)\b'

WIKI_SCHEMA = T.StructType([
    T.StructField('id', T.LongType()),
    T.StructField('title', T.StringType()),
    T.StructField('ns', T.LongType()),
    T.StructField('revision', T.StructType([
        T.StructField('text', T.StructType([T.StructField('_VALUE', T.StringType())]))
    ]))
])

MONTHS = {m.lower(): i for i, m in enumerate([
    'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'], 1)}


def normalize_name(value):
    if not value:
        return None
    value = re.sub(r'\s*\([^)]*\)\s*$', '', value)
    value = value.replace('_', ' ')
    if ',' in value:
        a, b = value.split(',', 1)
        value = b.strip() + ' ' + a.strip()
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = value.casefold()
    value = re.sub(r'[^a-z0-9 ]', ' ', value)
    return ' '.join(value.split()) or None


def parse_date(value):
    """Return (ISO date if complete, year if present). Never invent month/day."""
    if not value:
        return (None, None)
    s = str(value).strip()
    # Common Wikipedia templates: {{birth date|1990|11|30}} or {{birth date and age|1990|11|30}}
    m = re.search(r'(?i)\{\{\s*(?:birth date(?: and age)?|birth-date(?: and age)?|bda|birth date and age2)\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})', s)
    if m:
        try:
            d = datetime(int(m[1]), int(m[2]), int(m[3]))
            return (d.strftime('%Y-%m-%d'), d.year)
        except ValueError:
            pass
    # ISO format.
    m = re.search(r'(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)', s)
    if m:
        try:
            d = datetime(int(m[1]), int(m[2]), int(m[3]))
            return (d.strftime('%Y-%m-%d'), d.year)
        except ValueError:
            pass
    # English date: 30 June 1957.
    m = re.search(r'(?i)\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\b', s)
    if m:
        try:
            d = datetime(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
            return (d.strftime('%Y-%m-%d'), d.year)
        except ValueError:
            pass
    # English date: June 30, 1957.
    m = re.search(r'(?i)\b(' + '|'.join(MONTHS) + r')\s+(\d{1,2}),?\s+(\d{4})\b', s)
    if m:
        try:
            d = datetime(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
            return (d.strftime('%Y-%m-%d'), d.year)
        except ValueError:
            pass
    # Some templates and articles contain only a year.
    m = re.search(r'(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)', s)
    return (None, int(m[1]) if m else None)


# The fallback is restricted to the opening prose: dates elsewhere in an article
# could refer to opponents, tournament years or other people.
LEAD_BORN_REGEX = (r'(?i)\b(?:born|b\.\s*)\s*(?:on\s+)?'
                   r'((?:\d{1,2}\s+[A-Za-z]+\s+\d{4})|'
                   r'(?:[A-Za-z]+\s+\d{1,2},?\s+\d{4}))')

DATE_SCHEMA = T.StructType([
    T.StructField('date', T.StringType()), T.StructField('year', T.IntegerType())
])
parse_date_udf = F.udf(parse_date, DATE_SCHEMA)
normalize_udf = F.udf(normalize_name, T.StringType())


def strip_balanced_templates(value, leading_only=False):
    """Remove balanced {{...}} blocks, including nested templates, without truncation."""
    if not value:
        return ''
    if leading_only:
        # Drop infoboxes and other metadata templates before introductory prose.
        s = value
        while True:
            m = re.match(r'(?s)^\s*(?:<!--.*?-->\s*)?\{\{', s)
            if not m:
                return s
            end = balanced_end(s, m.end() - 2)
            if end is None:
                return s
            s = s[end:]
    result = []
    pos = 0
    while True:
        start = value.find('{{', pos)
        if start < 0:
            result.append(value[pos:])
            break
        result.append(value[pos:start])
        end = balanced_end(value, start)
        if end is None:
            result.append(value[start:])
            break
        pos = end
    return ''.join(result)


def balanced_end(s, start):
    depth = 0
    i = start
    while i < len(s) - 1:
        if s[i:i+2] == '{{':
            depth += 1
            i += 2
        elif s[i:i+2] == '}}':
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return None


def clean_wikitext(value):
    """Readable text; preserve all narrative text and paragraph length."""
    if not value:
        return ''
    s = re.sub(r'(?s)<!--.*?-->', '', value)
    s = re.sub(r'(?is)<ref\b[^>]*>.*?</ref\s*>|<ref\b[^>]*/\s*>', '', s)
    s = re.sub(r'(?is)<(?:gallery|timeline|math)\b[^>]*>.*?</(?:gallery|timeline|math)\s*>', '', s)
    s = strip_balanced_templates(s)
    # Wikilinks: [[target|label]] -> label, [[target]] -> target.
    s = re.sub(r'\[\[(?:File|Image|Category):[^\]]*\]\]', '', s, flags=re.I)
    for _ in range(3):
        s = re.sub(r'\[\[([^\[\]]+)\]\]',
                   lambda m: m.group(1).rsplit('|', 1)[-1].split('#')[0], s)
    s = re.sub(r'\[(?:https?://)[^\s\]]+\s+([^\]]+)\]', r'\1', s)
    s = re.sub(r"'{2,5}", '', s)
    s = re.sub(r'(?i)</?(?:small|big|sup|sub|span|div|blockquote|br|nowiki|i|b)\b[^>]*>', ' ', s)
    s = re.sub(r'(?m)^[ \t]*\{\|.*$', '', s)
    s = re.sub(r'(?m)^[ \t]*\|[-+}].*$', '', s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', s)
    return s.strip()


def clean_lead(value):
    return clean_wikitext(strip_balanced_templates(value, leading_only=True))

clean_lead_udf = F.udf(clean_lead, T.StringType())
clean_text_udf = F.udf(clean_wikitext, T.StringType())

def create_spark():
    spark = (SparkSession.builder.appName('VINF_Chess_Wikipedia_v3')
             .master('local[4]')
             .config('spark.driver.memory', '6g')
             .config('spark.sql.shuffle.partitions', '8')
             .getOrCreate())
    spark.sparkContext.setLogLevel('ERROR')
    return spark


def load_players(spark):
    return (spark.read.option('header', True).option('sep', '\t').csv(PLAYERS_FILE)
            .withColumn('fide_id', F.col('fide_id').cast('long'))
            .withColumn('name_key', normalize_udf('name'))
            .withColumn('_player_date', parse_date_udf('birth_year'))
            .withColumn('player_birth_date', F.col('_player_date.date'))
            .withColumn('player_birth_year', F.col('_player_date.year'))
            .drop('_player_date'))


def process_wiki_file(spark, file, name_keys):
    output = OUTPUT_DIR / 'wiki_parts' / file.stem
    if (output / '_SUCCESS').exists():
        print(f'PRESKAKUJEM: {file.name}', flush=True)
        return
    print(f'\nSPRACUVAM: {file.name}', flush=True)
    wiki = (spark.read.format('xml').option('rowTag', 'page')
            .schema(WIKI_SCHEMA).load(str(file)))
    base = (wiki.filter(F.col('ns') == 0)
            .select(F.col('id').alias('wiki_page_id'),
                    F.col('title').alias('wiki_title'),
                    F.col('revision.text._VALUE').alias('wiki_text'))
            .filter(F.col('wiki_text').isNotNull())
            .withColumn('fide_id_string', F.regexp_extract('wiki_text', FIDE_REGEX, 1))
            .withColumn('name_key', normalize_udf('wiki_title')))
    # Diagnostics: article totals, FIDE-ID hits, exact-name hits and chess context.
    # Counts describe the selection funnel; they are not all chess articles in Wikipedia.
    base = (base.join(F.broadcast(name_keys), 'name_key', 'left')
            .withColumn('name_hit', F.col('_name_candidate').isNotNull())
            .drop('_name_candidate')
            .withColumn('chess_context', F.col('wiki_text').rlike(CHESS_REGEX))
            .withColumn('fide_hit', F.col('fide_id_string') != ''))
    stats = base.agg(
        F.count('*').alias('articles_ns0_with_text'),
        F.sum(F.col('fide_hit').cast('long')).alias('fide_id_hits'),
        F.sum(F.col('name_hit').cast('long')).alias('name_hits'),
        F.sum((F.col('name_hit') & F.col('chess_context')).cast('long')).alias('name_and_chess'),
        F.sum((F.col('name_hit') & ~F.col('chess_context') & ~F.col('fide_hit')).cast('long')).alias('name_without_chess_excluded'),
        F.sum((F.col('fide_hit') | (F.col('name_hit') & F.col('chess_context'))).cast('long')).alias('selected')
    ).first().asDict()
    print('DIAGNOSTIKA FILTROV:', stats, flush=True)
    candidates = base.filter(F.col('fide_hit') | (F.col('name_hit') & F.col('chess_context')))
    extracted = (candidates
        .withColumn('wiki_birth_date_raw', F.trim(F.regexp_extract('wiki_text', BIRTH_DATE_REGEX, 1)))
        .withColumn('_wiki_date', parse_date_udf('wiki_birth_date_raw'))
        .withColumn('_born_lead', F.regexp_extract(F.regexp_extract('wiki_text', LEAD_REGEX, 1), LEAD_BORN_REGEX, 1))
        .withColumn('_lead_date', parse_date_udf('_born_lead'))
        .withColumn('wiki_birth_date', F.coalesce(F.col('_wiki_date.date'), F.col('_lead_date.date')))
        .withColumn('wiki_birth_year', F.coalesce(F.col('_wiki_date.year'), F.col('_lead_date.year')))
        .withColumn('wiki_birth_place', F.trim(F.regexp_extract('wiki_text', BIRTH_PLACE_REGEX, 1)))
        .withColumn('wiki_death_date', F.trim(F.regexp_extract('wiki_text', DEATH_DATE_REGEX, 1)))
        .withColumn('wiki_death_place', F.trim(F.regexp_extract('wiki_text', DEATH_PLACE_REGEX, 1)))
        .withColumn('wiki_introduction', clean_lead_udf(F.regexp_extract('wiki_text', LEAD_REGEX, 1)))
        .withColumn('wiki_early_life', clean_text_udf(F.regexp_extract('wiki_text', EARLY_REGEX, 1)))
        .withColumn('wiki_career_section', clean_text_udf(F.regexp_extract('wiki_text', CAREER_REGEX, 1)))
        .withColumn('wiki_tournaments', clean_text_udf(F.regexp_extract('wiki_text', TOURNAMENT_REGEX, 1)))
        .withColumn('wiki_achievements_section', clean_text_udf(F.regexp_extract('wiki_text', ACHIEVEMENTS_REGEX, 1)))
        .withColumn('wiki_url', F.concat(F.lit('https://en.wikipedia.org/wiki/'),
                                        F.regexp_replace('wiki_title', ' ', '_')))
        .select('wiki_page_id', 'wiki_title',
                F.when(F.col('fide_id_string') != '', F.col('fide_id_string').cast('long')).alias('wiki_fide_id'),
                'name_key', 'chess_context', 'wiki_birth_date_raw', 'wiki_birth_date',
                'wiki_birth_year', 'wiki_birth_place', 'wiki_death_date', 'wiki_death_place',
                'wiki_introduction', 'wiki_early_life', 'wiki_career_section',
                'wiki_tournaments', 'wiki_achievements_section', 'wiki_url')
        .dropDuplicates(['wiki_page_id']))
    extracted.write.mode('overwrite').parquet(str(output))
    print(f'ULOZENE: {output}', flush=True)


def join_players(spark, players):
    parts = OUTPUT_DIR / 'wiki_parts'
    completed = sorted(str(p) for p in parts.iterdir()
                       if p.is_dir() and (p / '_SUCCESS').exists()) if parts.exists() else []
    if not completed:
        print('Zatial nemame spracovane wiki data vo v3.', flush=True)
        return
    wiki = spark.read.parquet(*completed).dropDuplicates(['wiki_page_id'])
    # Tier 1: FIDE ID; a wiki ID must be unique on both sides for an automatic match.
    fide_counts = wiki.filter(F.col('wiki_fide_id').isNotNull()).groupBy('wiki_fide_id').count()
    fide_wiki = (wiki.join(fide_counts.filter('count = 1').select('wiki_fide_id'), 'wiki_fide_id')
                 .withColumn('match_method', F.lit('fide_id')))
    fide_matches = players.select('fide_id').join(
        fide_wiki, F.col('fide_id') == F.col('wiki_fide_id'), 'inner').drop('wiki_fide_id')
    matched_ids = fide_matches.select('fide_id').distinct()
    remaining = players.join(matched_ids, 'fide_id', 'left_anti')

    # Tier 2: exact normalized title + exact full birth date + chess context.
    # Reject conflicting FIDE IDs, ambiguous names and multiple candidate articles.
    name_wiki = wiki.filter(F.col('chess_context') & F.col('wiki_birth_date').isNotNull())
    name_matches = (remaining.alias('p').join(name_wiki.alias('w'),
        (F.col('p.name_key') == F.col('w.name_key')) &
        (F.col('p.player_birth_date') == F.col('w.wiki_birth_date')) &
        (F.col('p.player_birth_date').isNotNull()) &
        (F.col('w.wiki_fide_id').isNull() | (F.col('p.fide_id') == F.col('w.wiki_fide_id'))),
        'inner')
        .select(F.col('p.fide_id').alias('fide_id'),
                *[F.col('w.' + c).alias(c) for c in wiki.columns if c != 'name_key'],
                F.lit('name_birth_chess').alias('match_method')))
    # Reject any one-to-many or many-to-one name matching.
    by_player = name_matches.groupBy('fide_id').count().filter('count = 1').select('fide_id')
    by_page = name_matches.groupBy('wiki_page_id').count().filter('count = 1').select('wiki_page_id')
    name_matches = name_matches.join(by_player, 'fide_id').join(by_page, 'wiki_page_id')
    # Do not assign the same wiki page to a different player via name if it was already used by FIDE.
    name_matches = name_matches.join(fide_matches.select('wiki_page_id').distinct(), 'wiki_page_id', 'left_anti')

    wiki_columns = [c for c in wiki.columns if c not in ('name_key', 'wiki_fide_id')]
    f = fide_matches.select('fide_id', *wiki_columns, 'match_method')
    n = name_matches.select('fide_id', *wiki_columns, 'match_method')
    links = f.unionByName(n)
    # Extra protection against duplicate matches per player.
    w = Window.partitionBy('fide_id').orderBy(
        F.when(F.col('match_method') == 'fide_id', 0).otherwise(1), 'wiki_page_id')
    links = links.withColumn('_rn', F.row_number().over(w)).filter('_rn = 1').drop('_rn')
    result = players.drop('name_key', 'player_birth_date', 'player_birth_year').join(links, 'fide_id', 'left')
    output = OUTPUT_DIR / 'joined_players'
    result.write.mode('overwrite').parquet(str(output))
    saved = spark.read.parquet(str(output))
    print('\n=== VYSLEDKY LEFT JOIN v3 ===', flush=True)
    print('Vsetky zaznamy:', saved.count(), flush=True)
    print('Unikatni hraci:', saved.select('fide_id').distinct().count(), flush=True)
    print('Prepojeni hraci:', saved.filter('wiki_page_id IS NOT NULL').count(), flush=True)
    print('Unikatne Wikipedia stranky:', saved.filter('wiki_page_id IS NOT NULL').select('wiki_page_id').distinct().count(), flush=True)
    saved.groupBy('match_method').count().show(truncate=False)
    saved.filter('wiki_page_id IS NOT NULL').select(
        'fide_id', 'name', 'birth_year', 'wiki_title', 'wiki_birth_date', 'match_method').show(30, truncate=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Spracovat vsetky Wikipedia dumpy')
    parser.add_argument('--join-only', action='store_true', help='Len JOIN z hotovych v3 Parquet casti')
    parser.add_argument('--no-join', action='store_true', help='Len extrakcia, bez JOIN')
    args = parser.parse_args()
    if args.join_only and args.no_join:
        parser.error('--join-only a --no-join sa nedaju kombinovat')
    spark = create_spark()
    try:
        players = load_players(spark)
        if not args.join_only:
            files = sorted(WIKI_DIR.glob('*.xml.bz2'))
            if not files:
                raise FileNotFoundError('Chybaju Wikipedia dumpy v data/wiki/')
            if not args.all:
                files = files[:1]
            # Broadcast only the distinct names, not the entire player dataset.
            name_keys = players.select('name_key').filter('name_key IS NOT NULL').distinct().withColumn('_name_candidate', F.lit(1))
            print(f'Pocet suborov: {len(files)}', flush=True)
            for file in files:
                process_wiki_file(spark, file, name_keys)
        if not args.no_join:
            join_players(spark, players)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
