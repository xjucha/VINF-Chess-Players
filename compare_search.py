#!/usr/bin/env python3
"""Férové porovnanie TF-IDF a PyLucene BM25 v jednom procese (WSL).

Spustenie:
    python compare_search.py
    python compare_search.py --repeat 100 --limit 10

Nemení existujúce indexy ani vyhľadávače. Časy nezahŕňajú štart JVM,
načítanie indexov ani tlač výsledkov. Meria vyhľadanie a získanie FIDE ID.
"""
import argparse
import statistics
import time
from pathlib import Path

from search import load_index, search

# PyLucene je nainštalovaný vo WSL, nie vo Windows interpretri.
import lucene
from java.nio.file import Paths
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.store import FSDirectory
from lucene_search import make_query


TESTS = [
    ("AND: Magnus Carlsen", "Magnus Carlsen", "all",
     {"query": "Magnus AND Carlsen", "field": "name"}),
    ("OR: Carlsen Nakamura", "Carlsen Nakamura", "any",
     {"query": "Carlsen OR Nakamura", "field": "name"}),
    ("NOR + GM (presné filtre)", "NOR GM", "all",
     {"federation": "NOR", "title": "GM"}),
]


class QueryArgs:
    """Atribúty očakávané existujúcou lucene_search.make_query()."""
    def __init__(self, overrides):
        from lucene_search import NUMERIC_FIELDS
        self.query = None
        self.field = "content"
        self.match = "any"
        self.federation = None
        self.title = None
        self.wiki_only = False
        for field in NUMERIC_FIELDS:
            setattr(self, "min_" + field, None)
            setattr(self, "max_" + field, None)
        for key, value in overrides.items():
            setattr(self, key, value)


def timed(fn, repeat):
    # Warm-up: prvé vyhľadávanie môže zahŕňať inicializáciu/cache.
    fn()
    times = []
    result = None
    for _ in range(repeat):
        start = time.perf_counter_ns()
        result = fn()
        times.append((time.perf_counter_ns() - start) / 1_000_000)
    return result, statistics.mean(times), statistics.median(times)


def main():
    parser = argparse.ArgumentParser(description="TF-IDF vs. PyLucene BM25 (bez JVM startup)")
    parser.add_argument("--index-dir", default="data/index")
    parser.add_argument("--lucene-index", default="data/lucene_index")
    parser.add_argument("--repeat", type=int, default=100)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.repeat < 1 or args.limit < 1:
        parser.error("--repeat a --limit musia byť kladné čísla")
    if not Path(args.lucene_index).is_dir():
        parser.error(f"Lucene index neexistuje: {args.lucene_index}")

    t0 = time.perf_counter()
    old_index = load_index(args.index_dir)
    old_load_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    vm_ms = (time.perf_counter() - t0) * 1000
    directory = FSDirectory.open(Paths.get(str(Path(args.lucene_index).resolve())))
    try:
        t0 = time.perf_counter()
        reader = DirectoryReader.open(directory)
        lucene_open_ms = (time.perf_counter() - t0) * 1000
        try:
            searcher = IndexSearcher(reader)
            searcher.setSimilarity(BM25Similarity())
            print(f"Dokumenty: TF-IDF {old_index['metadata']['document_count']}, "
                  f"Lucene {reader.numDocs()}")
            print(f"Inicializácia mimo merania: TF-IDF načítanie {old_load_ms:.1f} ms, "
                  f"JVM {vm_ms:.1f} ms, Lucene otvorenie {lucene_open_ms:.1f} ms")
            print(f"Opakovaní: {args.repeat}; zobrazených: {args.limit}; "
                  "čas = vyhľadanie + získanie FIDE ID (bez výpisu)\n")

            for title, old_query, match, lucene_params in TESTS:
                query = make_query(QueryArgs(lucene_params))

                def run_old():
                    # 0 znamená všetky výsledky; potrebujeme celú množinu ID.
                    results, total = search(old_query, old_index, method="classic",
                                            match=match, limit=0)
                    return [(p["fide_id"], p["score"]) for p in results], total

                def run_lucene():
                    # Všetky zásahy kvôli korektnému porovnaniu množín.
                    hits = searcher.search(query, reader.numDocs())
                    ids_scores = []
                    for hit in hits.scoreDocs:
                        doc = searcher.doc(hit.doc)
                        ids_scores.append((doc.get("fide_id"), hit.score))
                    return ids_scores, hits.totalHits.value

                (old_rows, old_total), old_avg, old_med = timed(run_old, args.repeat)
                (new_rows, new_total), new_avg, new_med = timed(run_lucene, args.repeat)
                old_ids = {id_ for id_, _ in old_rows}
                new_ids = {id_ for id_, _ in new_rows}
                overlap = old_ids & new_ids

                print("=" * 80)
                print(title)
                print(f"TF-IDF: {old_total} | priemer {old_avg:.3f} ms | medián {old_med:.3f} ms")
                print(f"Lucene: {new_total} | priemer {new_avg:.3f} ms | medián {new_med:.3f} ms")
                print(f"Zhoda FIDE ID: {len(overlap)} spoločných, "
                      f"{len(old_ids - new_ids)} iba TF-IDF, "
                      f"{len(new_ids - old_ids)} iba Lucene")
                print("Top TF-IDF:", ", ".join(id_ for id_, _ in old_rows[:args.limit]))
                print("Top Lucene:", ", ".join(id_ for id_, _ in new_rows[:args.limit]))
                if old_ids != new_ids:
                    print("Iba TF-IDF:", sorted(old_ids - new_ids)[:20])
                    print("Iba Lucene:", sorted(new_ids - old_ids)[:20])
                print()

            print("POZNÁMKY:")
            print("- Skóre TF-IDF a BM25 nie je priamo porovnateľné.")
            print("- NOR/GM: TF-IDF hľadá termíny v zlúčenom texte, Lucene filtruje presné polia.")
            print("- Časy sú orientačné, merané v rovnakom procese, nie však úplne identickými algoritmami.")
            print("- Phrase/fuzzy/Wikipedia/range sú rozšírenia Lucene, porovnávajú sa osobitne.")
        finally:
            reader.close()
    finally:
        directory.close()


if __name__ == "__main__":
    main()
