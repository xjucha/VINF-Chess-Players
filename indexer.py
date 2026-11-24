import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def tokenize(text):
    """Rozdelenie textu na slová pomocou regulárneho výrazu."""
    return re.findall(r"[^\W_]+", str(text).lower(), flags=re.UNICODE)


def build_index(input_file, output_dir):
    input_file = Path(input_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # term -> fide_id -> pocet vyskytov
    inverted_index = defaultdict(dict)
    documents = {}
    document_lengths = {}

    with input_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")

        required = {"fide_id", "name", "federation", "title"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("V players.tsv chybaju pozadovane stlpce.")

        for player in reader:
            fide_id = player["fide_id"].strip()

            if not fide_id:
                continue

            if fide_id in documents:
                raise ValueError(f"Duplicitne FIDE ID: {fide_id}")

            # Textove polia pre fulltextove vyhladavanie
            searchable_text = " ".join([
                player.get("name", "") or "",
                player.get("federation", "") or "",
                player.get("title", "") or "",
            ])

            tokens = tokenize(searchable_text)
            term_counts = Counter(tokens)

            documents[fide_id] = player
            document_lengths[fide_id] = len(tokens)

            for term, tf in term_counts.items():
                inverted_index[term][fide_id] = tf

    # DF = pocet dokumentov, v ktorych sa termin nachadza
    document_frequency = {
        term: len(postings)
        for term, postings in inverted_index.items()
    }

    # Ulozenie indexu
    files = {
        "inverted_index.json": dict(inverted_index),
        "documents.json": documents,
        "document_lengths.json": document_lengths,
        "document_frequency.json": document_frequency,
        "metadata.json": {
            "document_count": len(documents),
            "vocabulary_size": len(inverted_index),
            "indexed_fields": ["name", "federation", "title"],
            "tf_method": "raw_term_frequency",
        },
    }

    for filename, data in files.items():
        path = output_dir / filename
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    print("Index vytvoreny.")
    print(f"Dokumenty: {len(documents)}")
    print(f"Unikatne terminy: {len(inverted_index)}")
    print(f"Ulozene do: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="indexer sachovych hracov"
    )
    parser.add_argument(
        "--input",
        default="data/players.tsv",
        help="Vstupny TSV subor"
    )
    parser.add_argument(
        "--output-dir",
        default="data/index",
        help="Priecinok pre index"
    )

    args = parser.parse_args()
    build_index(args.input, args.output_dir)
