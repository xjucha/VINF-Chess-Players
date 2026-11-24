
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    response = page.goto(
        "https://fideratings.com/federations?fed=POL",
        wait_until="domcontentloaded",
        timeout=60000
    )

    print("=== HTTP REQUEST HEADERS ===")
    for name, value in sorted(response.request.all_headers().items()):
        print(f"{name}: {value}")

    print("\n=== HTTP RESPONSE ===")
    print("Status:", response.status)

    print("\n=== HTTP RESPONSE HEADERS ===")
    for name, value in sorted(response.all_headers().items()):
        print(f"{name}: {value}")

    browser.close()
