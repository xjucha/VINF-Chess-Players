import requests
import time
import os
import re
from urllib.parse import urlparse

SITEMAP_URL = "https://fideratings.com/sitemap-players.xml"

HEADERS = {
    "User-Agent": "VINF-University-Project-Crawler/1.0"
}

TIMEOUT = 15
SLEEP = 2
NUMBER_OF_PLAYERS = 10

OUTPUT_DIR = "html_sample"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --------------------------------------------------
# 1. Stiahnutie sitemap
# --------------------------------------------------

print("Stahujem sitemap...")

response = requests.get(
    SITEMAP_URL,
    headers=HEADERS,
    timeout=TIMEOUT
)

response.raise_for_status()

sitemap = response.text

# URL adresy profilov
urls = re.findall(r"<loc>(https://fideratings\.com/player/[^<]+)</loc>", sitemap)

print(f"Pocet profilov v sitemap: {len(urls):,}")


# --------------------------------------------------
# 2. Vyber prvych 10 profilov
# --------------------------------------------------

sample_urls = urls[:NUMBER_OF_PLAYERS]

print(f"\nStiahnem {len(sample_urls)} profilov.\n")

sizes = []


# --------------------------------------------------
# 3. Stiahnutie profilov
# --------------------------------------------------

for i, url in enumerate(sample_urls, start=1):

    print("=" * 70)
    print(f"[{i}/{NUMBER_OF_PLAYERS}] {url}")

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

        print("HTTP status:", response.status_code)

        if response.status_code != 200:
            print("Stranka sa neulozila.")
            continue

        # vytvorenie bezpecneho nazvu suboru z URL
        path = urlparse(url).path.strip("/")
        filename = path.replace("/", "_") + ".html"

        filepath = os.path.join(OUTPUT_DIR, filename)

        # ukladame RAW bytes presne tak, ako prisli zo servera
        with open(filepath, "wb") as f:
            f.write(response.content)

        size = len(response.content)
        sizes.append(size)

        print("Subor:", filepath)
        print(f"Velkost: {size:,} B ({size / 1024:.2f} KiB)")

    except requests.RequestException as e:
        print("CHYBA:", e)

    # posledny request uz nepotrebuje sleep
    if i < len(sample_urls):
        time.sleep(SLEEP)


# --------------------------------------------------
# 4. Statistiky
# --------------------------------------------------

print("\n" + "=" * 70)
print("STATISTIKY")

if sizes:

    total_size = sum(sizes)
    average_size = total_size / len(sizes)

    print(f"Uspesne stiahnutych profilov: {len(sizes)}")
    print(f"Spolu: {total_size:,} B")
    print(f"Spolu: {total_size / 1024 / 1024:.2f} MiB")

    print(f"\nPriemer jedneho profilu:")
    print(f"{average_size:,.0f} B")
    print(f"{average_size / 1024:.2f} KiB")

    print(f"\nNajmensi profil:")
    print(f"{min(sizes):,} B ({min(sizes) / 1024:.2f} KiB)")

    print(f"\nNajvacsi profil:")
    print(f"{max(sizes):,} B ({max(sizes) / 1024:.2f} KiB)")

    # hruby odhad pre celu sitemap
    estimated_total = average_size * len(urls)

    print("\nODHAD PRE VSETKY PROFILY")
    print(f"Pocet profilov: {len(urls):,}")
    print(f"Odhad: {estimated_total / 1024 / 1024:.2f} MiB")
    print(f"Odhad: {estimated_total / 1024 / 1024 / 1024:.2f} GiB")

else:
    print("Nepodarilo sa stiahnut ziadny profil.")