#!/usr/bin/env python3
"""Search the PyLucene 9.12 chess-player index built by lucene_indexer.py.

Examples:
  python lucene_search.py --query 'Magnus AND Carlsen' --field name
  python lucene_search.py --query 'Carlsen OR Nakamura' --field name
  python lucene_search.py --query '"world chess champion"' --field content --wiki-only
  python lucene_search.py --query 'Carlsn~1' --field name
  python lucene_search.py --min-standard 2700 --max-standard 2900
"""
import argparse
from pathlib import Path

import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.index import DirectoryReader, Term
from org.apache.lucene.queryparser.classic import QueryParser
from org.apache.lucene.search import (
    BooleanClause, BooleanQuery, IndexSearcher, MatchAllDocsQuery, TermQuery,
)
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.document import IntPoint
from org.apache.lucene.store import FSDirectory

TEXT_FIELDS = (
    'name', 'content', 'wiki_title', 'wiki_introduction', 'wiki_early_life',
    'wiki_career_section', 'wiki_tournaments', 'wiki_achievements_section',
    'wiki_birth_place', 'wiki_death_place',
)
NUMERIC_FIELDS = (
    'birth_year', 'standard', 'rapid', 'blitz', 'standard_peak',
    'rapid_peak', 'blitz_peak', 'standard_world_rank', 'rapid_world_rank',
    'blitz_world_rank', 'wiki_birth_year',
)


def make_query(args):
    clauses = BooleanQuery.Builder()
    has_clause = False

    if args.query:
        parser = QueryParser(args.field, StandardAnalyzer())
        parser.setDefaultOperator(QueryParser.Operator.AND if args.match == 'all'
                                  else QueryParser.Operator.OR)
        clauses.add(parser.parse(args.query), BooleanClause.Occur.MUST)
        has_clause = True

    for field in NUMERIC_FIELDS:
        low = getattr(args, 'min_' + field)
        high = getattr(args, 'max_' + field)
        if low is not None or high is not None:
            lo = low if low is not None else -2147483648
            hi = high if high is not None else 2147483647
            if lo > hi:
                raise ValueError(f'{field}: minimum is greater than maximum')
            clauses.add(IntPoint.newRangeQuery(field, lo, hi), BooleanClause.Occur.MUST)
            has_clause = True

    if args.wiki_only:
        clauses.add(TermQuery(Term('has_wikipedia', 'true')), BooleanClause.Occur.MUST)
        has_clause = True

    if args.federation:
        clauses.add(TermQuery(Term('federation', args.federation.upper())),
                    BooleanClause.Occur.MUST)
        has_clause = True

    if args.title:
        clauses.add(TermQuery(Term('title', args.title.upper())),
                    BooleanClause.Occur.MUST)
        has_clause = True

    if not has_clause:
        return MatchAllDocsQuery()
    return clauses.build()



def print_details(doc, max_chars):
    """Display stored FIDE and Wikipedia attributes; omit missing values."""
    def value(key):
        raw = doc.get(key)
        return str(raw).strip() if raw is not None and str(raw).strip() else None

    def line(label, key, indent='     '):
        v = value(key)
        if v:
            print(f'{indent}{label}: {v}')

    def excerpt(label, key):
        v = value(key)
        if not v:
            return
        v = ' '.join(v.split())
        if max_chars and len(v) > max_chars:
            v = v[:max_chars].rsplit(' ', 1)[0].rstrip() + '...'
        print(f'     {label}: {v}')

    print('     --- FIDE DETAILS ---')
    line('Rok narodenia', 'birth_year')
    line('Pohlavie', 'gender')
    for prefix, label in (('standard', 'STANDARD'), ('rapid', 'RAPID'), ('blitz', 'BLITZ')):
        fields = (
            ('Aktualny rating', prefix),
            ('Svetove poradie', prefix + '_world_rank'),
            ('Narodne poradie', prefix + '_national_rank'),
            ('Historicke maximum', prefix + '_peak'),
            ('Datum maxima', prefix + '_peak_date'),
        )
        if any(value(key) for _, key in fields):
            print(f'     {label}:')
            for field_label, key in fields:
                line(field_label, key, indent='       ')

    print('     --- WIKIPEDIA ---')
    if value('has_wikipedia') != 'true':
        print('     Pre tohto hraca sa nenasiel prepojeny clanok na Wikipedii.')
        return
    for label, key in (
        ('Clanok', 'wiki_title'),
        ('Datum narodenia', 'wiki_birth_date'),
        ('Rok narodenia', 'wiki_birth_year'),
        ('Miesto narodenia', 'wiki_birth_place'),
        ('Datum umrtia', 'wiki_death_date'),
        ('Miesto umrtia', 'wiki_death_place'),
    ):
        line(label, key)
    for label, key in (
        ('Uvod', 'wiki_introduction'),
        ('Detstvo a mladost', 'wiki_early_life'),
        ('Kariera', 'wiki_career_section'),
        ('Turnaje', 'wiki_tournaments'),
        ('Uspechy', 'wiki_achievements_section'),
    ):
        excerpt(label, key)
    line('URL', 'wiki_url')
    line('Sposob sparovania', 'match_method')


def main():
    ap = argparse.ArgumentParser(description='Search chess players using PyLucene 9.12')
    ap.add_argument('--index', default='data/lucene_index')
    ap.add_argument('--query', '-q', help='Lucene query: AND, OR, "phrase", fuzzy~1')
    ap.add_argument('--field', choices=TEXT_FIELDS, default='content')
    ap.add_argument('--match', choices=('all', 'any'), default='any',
                    help='Default operator between terms without explicit AND/OR')
    ap.add_argument('--federation', help='Exact federation code, e.g. NOR')
    ap.add_argument('--title', help='Exact title, e.g. GM')
    ap.add_argument('--wiki-only', action='store_true')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--details', action='store_true',
                    help='Show stored FIDE and Wikipedia attributes')
    ap.add_argument('--detail-chars', type=int, default=300,
                    help='Maximum characters per Wikipedia text section; 0 = full text')
    for field in NUMERIC_FIELDS:
        ap.add_argument('--min-' + field.replace('_', '-'), type=int, dest='min_' + field)
        ap.add_argument('--max-' + field.replace('_', '-'), type=int, dest='max_' + field)
    args = ap.parse_args()
    if args.limit < 1:
        ap.error('--limit must be >= 1')
    if args.detail_chars < 0:
        ap.error('--detail-chars must be >= 0')
    index_path = Path(args.index)
    if not index_path.is_dir():
        ap.error(f'Index directory not found: {index_path}')

    lucene.initVM(vmargs=['-Djava.awt.headless=true'])
    directory = FSDirectory.open(Paths.get(str(index_path.resolve())))
    try:
        reader = DirectoryReader.open(directory)
        try:
            searcher = IndexSearcher(reader)
            searcher.setSimilarity(BM25Similarity())
            query = make_query(args)
            print('Lucene query:', query.toString())
            hits = searcher.search(query, args.limit)
            print('Total matches:', hits.totalHits.value)
            for rank, score_doc in enumerate(hits.scoreDocs, 1):
                doc = searcher.doc(score_doc.doc)
                def get(key):
                    return doc.get(key) or '-'
                print(f"{rank:>3}. {get('name')} | FIDE {get('fide_id')} | "
                      f"{get('federation')} | {get('title')} | "
                      f"STD {get('standard')} | RAPID {get('rapid')} | "
                      f"BLITZ {get('blitz')} | score {score_doc.score:.4f}")
                if get('wiki_title') != '-':
                    print(f"     Wikipedia: {get('wiki_title')} | {get('wiki_url')}")
                if args.details:
                    print_details(doc, args.detail_chars)
                    print()
        finally:
            reader.close()
    finally:
        directory.close()


if __name__ == '__main__':
    main()
