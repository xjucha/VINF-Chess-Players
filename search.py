
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from indexer import tokenize


def load_index(index_dir):
    index_dir = Path(index_dir)

    def load(filename):
        with (index_dir / filename).open("r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "inverted": load("inverted_index.json"),
        "documents": load("documents.json"),
        "df": load("document_frequency.json"),
        "metadata": load("metadata.json"),
    }


def calculate_idf(term, df, document_count, method):
    frequency = df.get(term, 0)

    if frequency == 0:
        return 0.0

    if method == "classic":
        return math.log(document_count / frequency)

    if method == "smooth":
        return math.log((document_count + 1) / (frequency + 1)) + 1

    raise ValueError(f"Neznama IDF metoda: {method}")


def parse_rating(value):
    """Prevedie rating na cislo. Neplatne hodnoty vrati ako None."""
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def search(
    query,
    index,
    method="classic",
    match="all",
    rating_type="standard",
    min_rating=None,
    max_rating=None,
    limit=10,
    sort="relevance",
    min_peak=None,
    max_peak=None,
):
    tokens = list(dict.fromkeys(tokenize(query)))

    inverted = index["inverted"]
    documents = index["documents"]
    df = index["df"]
    document_count = index["metadata"]["document_count"]

    scores = defaultdict(float)

    if tokens:
        if match == "all":
            # Prienik: dokument musi obsahovat vsetky terminy
            posting_sets = [
                set(inverted.get(term, {}).keys())
                for term in tokens
            ]
            candidates = set.intersection(*posting_sets)

        else:
            # Zjednotenie: staci aspon jeden termin
            candidates = set()
            for term in tokens:
                candidates.update(inverted.get(term, {}).keys())

        for term in tokens:
            idf = calculate_idf(term, df, document_count, method)

            for fide_id, tf in inverted.get(term, {}).items():
                if fide_id in candidates:
                    scores[fide_id] += tf * idf

    else:
        # Bez textoveho dopytu prehladavame vsetkych hracov
        candidates = set(documents.keys())

        for fide_id in candidates:
            scores[fide_id] = 0.0

    results = []

    peak_field = f"{rating_type}_peak"

    for fide_id in candidates:
        player = documents[fide_id]

        rating = parse_rating(player.get(rating_type))
        peak = parse_rating(player.get(peak_field))

        # Filtrovanie aktualneho ratingu
        if min_rating is not None:
            if rating is None or rating <= min_rating:
                continue

        if max_rating is not None:
            if rating is None or rating >= max_rating:
                continue

        # Filtrovanie historickeho maxima
        if min_peak is not None:
            if peak is None or peak <= min_peak:
                continue

        if max_peak is not None:
            if peak is None or peak >= max_peak:
                continue

        results.append({
            "fide_id": fide_id,
            "name": player.get("name", ""),
            "federation": player.get("federation", ""),
            "title": player.get("title", ""),
            "rating": rating,
            "peak": peak,
            "score": scores[fide_id],
        })

    # Zoradenie podla zvolenej metody
    if sort == "peak":
        results.sort(
            key=lambda p: (
                p["peak"] is None,
                -(p["peak"] if p["peak"] is not None else 0),
                p["fide_id"],
            )
        )

    elif sort == "rating" or not tokens:
        results.sort(
            key=lambda p: (
                p["rating"] is None,
                -(p["rating"] if p["rating"] is not None else 0),
                p["fide_id"],
            )
        )

    else:
        results.sort(
            key=lambda p: (-p["score"], p["fide_id"])
        )

    total = len(results)

    if limit > 0:
        results = results[:limit]

    return results, total


def print_results(results, total, method, rating_type):
    print(f"\nIDF metoda: {method}")
    print(f"Najdenych hracov: {total}")
    print("-" * 120)

    if not results:
        print("Ziadne vysledky.")
        return

    for position, player in enumerate(results, start=1):
        rating = player["rating"]
        peak = player["peak"]

        rating_text = (
            f"{rating:.0f}" if rating is not None else "-"
        )

        peak_text = (
            f"{peak:.0f}" if peak is not None else "-"
        )

        print(
            f"{position:3}. "
            f"{player['name'][:32]:32} "
            f"{player['federation']:5} "
            f"{player['title']:5} "
            f"{rating_type.upper()}: {rating_text:5} "
            f"PEAK: {peak_text:5} "
            f"Score: {player['score']:.4f} "
            f"ID: {player['fide_id']}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Vyhladavanie sachistov pomocou TF-IDF a filtrov"
    )

    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Textovy vyhladavaci dopyt"
    )

    parser.add_argument(
        "--method",
        choices=["classic", "smooth", "both"],
        default="both",
        help="Metoda IDF"
    )

    parser.add_argument(
        "--match",
        choices=["all", "any"],
        default="all",
        help="all = AND, any = OR"
    )

    parser.add_argument(
        "--rating-type",
        choices=["standard", "rapid", "blitz"],
        default="standard",
        help="Typ ratingu"
    )

    parser.add_argument(
        "--min-rating",
        type=float,
        default=None,
        help="Rating musi byt vacsi ako zadana hodnota"
    )

    parser.add_argument(
        "--max-rating",
        type=float,
        default=None,
        help="Rating musi byt mensi ako zadana hodnota"
    )

    parser.add_argument(
        "--min-peak",
        type=float,
        default=None,
        help="Historicke maximum musi byt vacsie ako zadana hodnota"
    )

    parser.add_argument(
        "--max-peak",
        type=float,
        default=None,
        help="Historicke maximum musi byt mensie ako zadana hodnota"
    )

    parser.add_argument(
        "--sort",
        choices=["relevance", "rating", "peak"],
        default="relevance",
        help="Zoradenie: relevance = TF-IDF, rating = aktualny rating, peak = historicke maximum"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Pocet vysledkov; 0 = vsetky"
    )

    parser.add_argument(
        "--index-dir",
        default="data/index",
        help="Priecinok s indexom"
    )

    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit nemoze byt zaporny")

    if (
        args.min_rating is not None
        and args.max_rating is not None
        and args.min_rating >= args.max_rating
    ):
        parser.error(
            "--min-rating musi byt mensi ako --max-rating"
        )

    if (
        args.min_peak is not None
        and args.max_peak is not None
        and args.min_peak >= args.max_peak
    ):
        parser.error(
            "--min-peak musi byt mensi ako --max-peak"
        )

    index = load_index(args.index_dir)

    # Overenie dostupnosti peak udajov v indexe
    if args.sort == "peak" or args.min_peak is not None or args.max_peak is not None:
        peak_field = f"{args.rating_type}_peak"

        if index["documents"] and not any(
            peak_field in player
            for player in index["documents"].values()
        ):
            parser.error(
                f"Index neobsahuje pole '{peak_field}'. "
                "Dopln peak atributy do indexer.py a znovu vytvor index."
            )

    methods = (
        ["classic", "smooth"]
        if args.method == "both"
        else [args.method]
    )

    for method in methods:
        results, total = search(
            args.query,
            index,
            method=method,
            match=args.match,
            rating_type=args.rating_type,
            min_rating=args.min_rating,
            max_rating=args.max_rating,
            limit=args.limit,
            sort=args.sort,
            min_peak=args.min_peak,
            max_peak=args.max_peak,
        )

        print_results(results, total, method, args.rating_type)


if __name__ == "__main__":
    main()
