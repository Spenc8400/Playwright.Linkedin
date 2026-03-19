import argparse
import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def login_linkedin(page, username: str, password: str):
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("button[type='submit']")
    try:
        page.wait_for_url("**linkedin.com/feed/**", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    current_url = page.url
    if "checkpoint" in current_url or "challenge" in current_url:
        raise RuntimeError("LinkedIn login challenge encountered; open non-headless to complete manually.")
    if "linkedin.com/login" in current_url:
        raise RuntimeError("LinkedIn login failed. Check credentials.")


def extract_top5_people_urls(page, query: str):
    search_url = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(query)
    page.goto(search_url, wait_until="domcontentloaded")
    page.wait_for_selector("div.search-results-container", timeout=15000)
    time.sleep(1.2)

    hrefs = []
    anchors = page.locator("a.app-aware-link")
    count = anchors.count()
    for i in range(count):
        if len(hrefs) >= 5:
            break
        url = anchors.nth(i).get_attribute("href") or ""
        if "/in/" in url:
            clean = url.split("?")[0]
            if clean.endswith("/"):
                clean = clean[:-1]
            if clean not in hrefs:
                hrefs.append(clean)
    return hrefs


def quote_plus(s: str) -> str:
    from urllib.parse import quote_plus as qp
    return qp(s)


def main():
    parser = argparse.ArgumentParser(description="LinkedIn people search top 5 profile URLs")
    parser.add_argument("--query", default="OpenClaw", help="People search term")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()

    user = os.getenv("LINKEDIN_USER")
    pwd = os.getenv("LINKEDIN_PASS")
    if not user or not pwd:
        raise RuntimeError("Set LINKEDIN_USER and LINKEDIN_PASS environment variables.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        login_linkedin(page, user, pwd)
        urls = extract_top5_people_urls(page, args.query)
        print("Top 5 people URLs:")
        for u in urls:
            print(u)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
