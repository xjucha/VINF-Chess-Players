
# VINF – Vyhľadávanie informácií o šachových hráčoch

Semestrálny projekt z predmetu Vyhľadávanie informácií (VINF).

Projekt získava profily šachistov zo stránky https://fideratings.com/, extrahuje údaje pomocou regulárnych výrazov (REGEX), vytvára invertovaný index a umožňuje vyhľadávanie pomocou TF-IDF.

## 1. Súbory projektu

- `crawler.py` – získavanie a ukladanie HTML profilov pomocou Playwright/Chromium.
- `extractor.py` – extrakcia údajov z HTML do TSV.
- `indexer.py` – vytvorenie invertovaného indexu.
- `search.py` – vyhľadávanie, filtrovanie a triedenie výsledkov.
- `test_extractor.py` – automatizované testy extraktora.
- Ostatné `.py` – pomocné a diagnostické skripty použité počas vývoja.
- `requirements.txt` – potrebné Python balíky.

Dáta:
- `data/players/` – 100 ukážkových HTML profilov.
- `data/players.tsv` – údaje o 25 409 hráčoch.
- `data/rating_history.tsv` – história štandardného ratingu.
- `data/index/` – kompletný invertovaný index.
- `data/player_urls.tsv` – objavené odkazy na profily, ak je súbor priložený.
- `data/crawler_state.json` – stav crawlera, ak je súbor priložený.

Celý získaný HTML korpus má približne 4,91 GB. Z dôvodu veľkosti obsahuje ZIP iba 100 HTML profilov, ale kompletné TSV údaje a index.

## 2. Inštalácia

```powershell
python -m venv virtualenv
.\virtualenv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Chromium je potrebné iba pre crawler.

## 3. Crawler

Získava HTML profily a ukladá ich do `data/players/` podľa FIDE ID. Odkazy na hráčov ukladá do `data/player_urls.tsv` a HTML stránok federácií do `data/federations/`.

```powershell
python crawler.py
python crawler.py --federation ALL
python crawler.py --federation SVK
python crawler.py --federation NOR --max-players 50
python crawler.py --federation POL --max-players 100
python crawler.py --federation SVK --discover-only
python crawler.py --federation SVK --download-only
python crawler.py --federation POL --delay 5
python crawler.py --resume
python crawler.py --help
```

Parametre:
- `--federation` – kód federácie alebo `ALL` (predvolene `POL`).
- `--max-players` – limit nových stiahnutých profilov; bez parametra neobmedzene.
- `--discover-only` – iba objavovanie odkazov a ukladanie HTML federácií.
- `--download-only` – iba sťahovanie už objavených profilov; vyžaduje `data/player_urls.tsv`.
- `--delay` – základný interval medzi požiadavkami (predvolene 3 s).
- `--resume` – pokračovanie po prerušení, ktoré funguje aj automaticky.

Režimy `--discover-only` a `--download-only` nemožno kombinovať. Crawler kontroluje už uložené profily a podporuje pokračovanie v spracovaní.

## 4. REGEX extraktor

Spracúva HTML profily a vytvára `players.tsv` a `rating_history.tsv`. Pri bežnom spustení kontroluje existujúce FIDE ID a pridáva iba nových hráčov.

```powershell
python extractor.py
python extractor.py --limit 100
python extractor.py --limit 0
python extractor.py --html-dir data/players
python extractor.py --html-dir data/players --output-dir data/demo --rebuild
python extractor.py --help
```

Parametre:
- `--html-dir` – priečinok so vstupnými HTML (predvolene `data/players`).
- `--output-dir` – priečinok pre TSV (predvolene `data`).
- `--limit` – maximálny počet nových profilov (0 = všetky).
- `--rebuild` – vytvorenie TSV nanovo zo všetkých dostupných HTML.

**Pozor:** V odovzdanom ZIP je iba 100 HTML profilov. Pri použití `--rebuild` treba zvoliť samostatný výstupný priečinok, napríklad `data/demo`, aby sa neprepísali kompletné TSV.

## 5. Indexer

Vytvára invertovaný index z `players.tsv`. Indexuje meno, federáciu a titul a ukladá JSON súbory s dokumentmi, termínmi a ich frekvenciami.

```powershell
python indexer.py
python indexer.py --input data/players.tsv
python indexer.py --input data/players.tsv --output-dir data/index_demo
python indexer.py --help
```

Parametre:
- `--input` – vstupný TSV súbor (predvolene `data/players.tsv`).
- `--output-dir` – priečinok pre JSON index (predvolene `data/index`).

## 6. Vyhľadávač

Vyhľadáva nad indexom pomocou TF-IDF. Podporuje AND/OR, Classic/Smooth IDF, ratingové filtre a triedenie.

```powershell
python search.py "Magnus Carlsen"
python search.py "NOR GM" --match all
python search.py "Daniel ARG GM" --match any --method both
python search.py "GM" --method classic
python search.py "GM" --method smooth
python search.py "GM" --min-rating 2500 --max-rating 2800
python search.py "GM" --min-peak 2600 --max-peak 2800
python search.py "USA" --sort peak --rating-type standard
python search.py "GM" --rating-type rapid --sort rating
python search.py "GM" --rating-type blitz --sort rating
python search.py --min-rating 2700 --sort rating --limit 0
python search.py "Magnus" --index-dir data/index
python search.py --help
```

Parametre:
- `query` – nepovinný textový dopyt.
- `--method classic|smooth|both` – metóda IDF (predvolene `both`).
- `--match all|any` – AND/OR (predvolene `all`).
- `--sort relevance|rating|peak` – triedenie (predvolene `relevance`).
- `--rating-type standard|rapid|blitz` – typ ratingu (predvolene `standard`).
- `--min-rating`, `--max-rating` – hranice aktuálneho ratingu.
- `--min-peak`, `--max-peak` – hranice historického maxima.
- `--limit` – počet výsledkov (predvolene 10; 0 = všetky).
- `--index-dir` – priečinok indexu (predvolene `data/index`).

Ratingové hranice sú ostré (`>` a `<`).

Classic IDF: `ln(N / df)`

Smooth IDF: `ln((N + 1) / (df + 1)) + 1`

## 7. Demonštrácia spracovania

Odovzdaných 100 HTML profilov možno použiť na samostatné vyskúšanie extraktora, indexera a vyhľadávača bez prepísania kompletných údajov.

Príkazy spúšťajte v uvedenom poradí:

```powershell
python extractor.py --html-dir data/players --output-dir data/demo --rebuild
python indexer.py --input data/demo/players.tsv --output-dir data/index_demo
python search.py --index-dir data/index_demo --sort rating --limit 10
```

Výsledkom je samostatné TSV a index vytvorený z ukážkových HTML profilov.

Na vyhľadávanie nad kompletnými údajmi o 25 409 hráčoch stačí:

```powershell
python search.py "Magnus Carlsen"
```

Kompletný index je priložený, preto crawler, extraktor ani indexer nie je potrebné opätovne spúšťať.

## 8. Testovanie

Automatizované testy extraktora:

```powershell
python -m unittest test_extractor.py -v
```

Príklady manuálnych negatívnych testov vyhľadávača:

```powershell
python search.py "Magnus" --method xyz
python search.py "GM" --min-rating 2800 --max-rating 2500
python search.py "GM" --limit -5
```

Pomocné Python skripty sú ponechané v projekte ako súčasť vývoja a diagnostiky.
