#!/usr/bin/env python3
"""Build a PyLucene 9.12 index from Spark-joined chess-player Parquet files.

Run inside the WSL Python environment containing PyLucene and PyArrow.
"""
import argparse
from pathlib import Path

import pyarrow.parquet as pq
import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import Document, Field, IntPoint, StoredField, StringField, TextField
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.store import FSDirectory

TEXT_FIELDS = (
    "name", "wiki_title", "wiki_introduction", "wiki_early_life",
    "wiki_career_section", "wiki_tournaments", "wiki_achievements_section",
    "wiki_birth_place", "wiki_death_place",
)
EXACT_FIELDS = (
    "federation", "gender", "title", "wiki_url", "match_method",
    "wiki_birth_date", "wiki_death_date", "standard_peak_date",
    "rapid_peak_date", "blitz_peak_date",
)
NUMERIC_FIELDS = (
    "birth_year", "standard", "rapid", "blitz",
    "standard_world_rank", "standard_national_rank", "standard_peak",
    "rapid_world_rank", "rapid_national_rank", "rapid_peak",
    "blitz_world_rank", "blitz_national_rank", "blitz_peak",
    "wiki_birth_year", "wiki_page_id",
)
WIKI_TEXT_FIELDS = (
    "wiki_title", "wiki_introduction", "wiki_early_life",
    "wiki_career_section", "wiki_tournaments", "wiki_achievements_section",
)


def as_text(value):
    return "" if value is None else str(value).strip()


def as_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def build_document(row):
    doc = Document()
    fide_id = as_int(row.get("fide_id"))
    if fide_id is None:
        raise ValueError("Missing/invalid fide_id")

    # Exact, stored identifier (used for lookups and comparison with old search).
    doc.add(StringField("fide_id", str(fide_id), Field.Store.YES))

    # Analyzed and stored text, searchable with Term/Phrase/Fuzzy queries.
    for field in TEXT_FIELDS:
        value = as_text(row.get(field))
        if value:
            doc.add(TextField(field, value, Field.Store.YES))

    # Combined full-text field; intentionally not stored to avoid duplicating long wiki text.
    combined = [as_text(row.get("name"))]
    combined.extend(as_text(row.get(field)) for field in WIKI_TEXT_FIELDS)
    combined = [value for value in combined if value]
    if combined:
        doc.add(TextField("content", "\n".join(combined), Field.Store.NO))

    # Keyword fields are exact-match and stored. Normalize categorical values.
    for field in EXACT_FIELDS:
        value = as_text(row.get(field))
        if value:
            if field in ("federation", "gender", "title", "match_method"):
                value = value.upper()
            doc.add(StringField(field, value, Field.Store.YES))

    # Point index for numeric ranges, plus separate stored value for displaying results.
    for field in NUMERIC_FIELDS:
        value = as_int(row.get(field))
        if value is not None:
            doc.add(IntPoint(field, value))
            doc.add(StoredField(field, value))

    # Explicit marker for documents enriched with Wikipedia.
    has_wiki = row.get("wiki_page_id") is not None
    doc.add(StringField("has_wikipedia", "true" if has_wiki else "false", Field.Store.YES))
    return doc, has_wiki


def main():
    parser = argparse.ArgumentParser(description="Index joined chess-player Parquet files using PyLucene")
    parser.add_argument("--input", default="data/spark_output_v4/joined_players")
    parser.add_argument("--output", default="data/lucene_index")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")

    source = Path(args.input)
    files = sorted(source.rglob("*.parquet")) if source.is_dir() else ([source] if source.suffix == ".parquet" else [])
    if not files:
        parser.error(f"No Parquet files found: {source}")

    lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    directory = FSDirectory.open(Paths.get(str(target.resolve())))
    analyzer = StandardAnalyzer()
    config = IndexWriterConfig(analyzer)
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    config.setSimilarity(BM25Similarity())
    writer = IndexWriter(directory, config)

    total = wiki_count = skipped = 0
    seen_ids = set()
    try:
        for file in files:
            parquet = pq.ParquetFile(file)
            for batch in parquet.iter_batches(batch_size=args.batch_size):
                for row in batch.to_pylist():
                    fide_id = as_int(row.get("fide_id"))
                    if fide_id is None:
                        skipped += 1
                        continue
                    if fide_id in seen_ids:
                        raise ValueError(f"Duplicate FIDE ID: {fide_id}")
                    seen_ids.add(fide_id)
                    doc, has_wiki = build_document(row)
                    writer.addDocument(doc)
                    total += 1
                    wiki_count += int(has_wiki)
                if total and total % 5000 < args.batch_size:
                    print(f"Indexed {total:,} players...", flush=True)
        writer.commit()
    finally:
        writer.close()
        directory.close()

    print("\nIndex completed")
    print(f"Documents: {total:,}")
    print(f"With Wikipedia: {wiki_count:,}")
    print(f"Without Wikipedia: {total - wiki_count:,}")
    print(f"Skipped invalid IDs: {skipped:,}")
    print(f"Index directory: {target.resolve()}")


if __name__ == "__main__":
    main()
