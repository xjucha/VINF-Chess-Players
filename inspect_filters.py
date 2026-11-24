from playwright.sync_api import sync_playwright

URL = "https://fideratings.com/federations?fed=POL"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.locator("#compareCountryLimit").wait_for(timeout=30000)
    page.locator("#compareCountryLimit").select_option("2000")
    page.wait_for_timeout(2500)

    print("\n=== INPUTY ===")
    for item in page.locator("input").evaluate_all("els => els.map(e => ({outerHTML:e.outerHTML, parent:e.parentElement?.outerHTML.slice(0,700)}))"):
        print(item)

    print("\n=== SELECTY ===")
    for item in page.locator("select").evaluate_all("els => els.map(e => ({id:e.id, html:e.outerHTML.slice(0,1000)}))"):
        print(item)

    print("\n=== TLAČIDLÁ SÚVISIACE S FILTRAMI ===")
    for item in page.locator("button").evaluate_all("els => els.map(e => ({id:e.id, text:e.innerText.trim(), html:e.outerHTML.slice(0,500)}))"):
        if any(s in (item["text"] + item["id"]).lower() for s in ["apply", "filter", "reset", "clear"]):
            print(item)

    print("\n=== POČET ODKAZOV NA HRÁČOV ===")
    import re
    html = page.content()
    links = set(re.findall(r'href=["\']([^"\']*/player/\d+/[^"\']+)["\']', html))
    print(len(links))
    browser.close()
