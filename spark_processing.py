import argparse
from pathlib import Path

from pyspark.sql import SparkSession, functions as F, types as T

WIKI_DIR = Path('data/wiki')
OUTPUT_DIR = Path('data/spark_output')

FIDE_REGEX = (
    r'(?im)^[ \t]*\|[ \t]*fide[ \t_-]*id[ \t]*=[ \t]*'
    r'(?:\[\[[^\]|]*\|[ \t]*)?(\d{5,10})'
)

BIRTH_PLACE_REGEX = r'(?im)^[ \t]*\|[ \t]*birth[ \t_]*place[ \t]*=[ \t]*([^\r\n]*)'
DEATH_DATE_REGEX = r'(?im)^[ \t]*\|[ \t]*death[ \t_]*date[ \t]*=[ \t]*([^\r\n]*)'
DEATH_PLACE_REGEX = r'(?im)^[ \t]*\|[ \t]*death[ \t_]*place[ \t]*=[ \t]*([^\r\n]*)'

CAREER_REGEX = (
    r'(?ims)^[ \t]*==[ \t]*'
    r'(?:Chess[ \t]+career|Career|Playing[ \t]+career)'
    r'[ \t]*==[ \t]*\r?\n'
    r'(.*?)'
    r'(?=^[ \t]*==(?!=)[^=\r\n][^\r\n]*==[ \t]*\r?$|\Z)'
)
ACHIEVEMENTS_REGEX = (
    r'(?ims)^[ \t]*==[ \t]*'
    r'(?:Achievements|Honours|Honors|Notable[ \t]+results|'
    r'Tournament[ \t]+(?:and[ \t]+match[ \t]+)?results|'
    r'Major[ \t]+achievements)'
    r'[ \t]*==[ \t]*\r?\n'
    r'(.*?)'
    r'(?=^[ \t]*==(?!=)[^=\r\n][^\r\n]*==[ \t]*\r?$|\Z)'
)

WIKI_SCHEMA = T.StructType([
    T.StructField('id', T.LongType()),
    T.StructField('title', T.StringType()),
    T.StructField('ns', T.LongType()),
    T.StructField('revision', T.StructType([
        T.StructField('text', T.StructType([
            T.StructField('_VALUE', T.StringType())
        ]))
    ]))
])


def create_spark():
    spark = (
        SparkSession.builder
        .appName('VINF_Chess_Wikipedia')
        .master('local[4]')
        .config('spark.driver.memory', '6g')
        .config('spark.sql.shuffle.partitions', '8')
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel('ERROR')
    return spark


def process_wiki_file(spark, file):
    output = OUTPUT_DIR / 'wiki_parts' / file.stem
    if (output / '_SUCCESS').exists():
        print(f'PRESKAKUJEM: {file.name}', flush=True)
        return

    print(f'\nSPRACUVAM: {file.name}', flush=True)
    wiki = (
        spark.read.format('xml')
        .option('rowTag', 'page')
        .schema(WIKI_SCHEMA)
        .load(str(file))
    )
    extracted = (
        wiki.filter(F.col('ns') == 0)
        .select(
            F.col('id').alias('wiki_page_id'),
            F.col('title').alias('wiki_title'),
            F.col('revision.text._VALUE').alias('wiki_text')
        )
        .filter(F.col('wiki_text').isNotNull())
        .withColumn('fide_id_string', F.regexp_extract('wiki_text', FIDE_REGEX, 1))
        .filter(F.col('fide_id_string') != '')
        .withColumn('wiki_birth_place', F.trim(F.regexp_extract('wiki_text', BIRTH_PLACE_REGEX, 1)))
        .withColumn('wiki_death_date', F.trim(F.regexp_extract('wiki_text', DEATH_DATE_REGEX, 1)))
        .withColumn('wiki_death_place', F.trim(F.regexp_extract('wiki_text', DEATH_PLACE_REGEX, 1)))
        .withColumn('wiki_career_section', F.substring(F.regexp_extract('wiki_text', CAREER_REGEX, 1), 1, 2000))
        .withColumn('wiki_achievements_section', F.substring(F.regexp_extract('wiki_text', ACHIEVEMENTS_REGEX, 1), 1, 2000))
        .withColumn('wiki_url', F.concat(F.lit('https://en.wikipedia.org/wiki/'), F.regexp_replace(F.col('wiki_title'), ' ', '_')))
        .select(
            'wiki_page_id', 'wiki_title',
            F.col('fide_id_string').cast('long').alias('fide_id'),
            'wiki_birth_place', 'wiki_death_date', 'wiki_death_place',
            'wiki_career_section', 'wiki_achievements_section', 'wiki_url'
        )
        .dropDuplicates(['wiki_page_id', 'fide_id'])
    )
    extracted.write.mode('overwrite').parquet(str(output))
    print(f'ULOZENE: {output}', flush=True)


def join_players(spark):
    parts = OUTPUT_DIR / 'wiki_parts'
    if not parts.exists():
        print('Zatial nemame spracovane wiki data.', flush=True)
        return
    completed = sorted(
        str(p) for p in parts.iterdir()
        if p.is_dir() and (p / '_SUCCESS').exists()
    )
    if not completed:
        print('Zatial nemame spracovane wiki data.', flush=True)
        return
    players = (
        spark.read.option('header', True).option('sep', '\t')
        .csv('data/players.tsv')
        .withColumn('fide_id', F.col('fide_id').cast('long'))
    )
    wiki = spark.read.parquet(*completed).dropDuplicates(['wiki_page_id', 'fide_id'])
    output = OUTPUT_DIR / 'joined_players'
    players.join(wiki, on='fide_id', how='inner').write.mode('overwrite').parquet(str(output))
    result = spark.read.parquet(str(output))
    print('\n=== VYSLEDKY JOIN ===', flush=True)
    print('Spojene zaznamy:', result.count(), flush=True)
    print('Unikatne Wikipedia stranky:', result.select('wiki_page_id').distinct().count(), flush=True)
    print('Unikatni hraci:', result.select('fide_id').distinct().count(), flush=True)
    result.select('fide_id', 'name', 'wiki_title', 'federation', 'standard').show(20, truncate=False)
    for column in ('wiki_birth_place', 'wiki_death_date', 'wiki_death_place', 'wiki_career_section', 'wiki_achievements_section'):
        count = result.filter(F.length(F.trim(F.col(column))) > 0).count()
        print(f'Vyplnene {column}: {count}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Spracovat vsetky Wikipedia dumpy')
    parser.add_argument('--join-only', action='store_true', help='Iba vykonat JOIN z ulozenych dat')
    args = parser.parse_args()
    spark = create_spark()
    try:
        if not args.join_only:
            files = sorted(WIKI_DIR.glob('*.xml.bz2'))
            if not files:
                raise FileNotFoundError('Chybaju Wikipedia dumpy.')
            if not args.all:
                files = files[:1]
            print(f'Pocet suborov: {len(files)}', flush=True)
            for file in files:
                process_wiki_file(spark, file)
        join_players(spark)
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
