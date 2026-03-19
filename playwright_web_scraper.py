import argparse
import csv
import json
import os
import random
import time
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


@dataclass
class JobListing:
	title: str
	company: str
	location: str
	url: str
	posted: str
	source_page: int


def jitter_sleep(min_seconds: float = 0.8, max_seconds: float = 2.0) -> None:
	"""Sleep a random amount of time to reduce bursty requests."""
	time.sleep(random.uniform(min_seconds, max_seconds))


def login_linkedin(page, username: str, password: str) -> None:
	page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
	page.fill("#username", username)
	page.fill("#password", password)
	page.click('button[type="submit"]')

	# Wait for either a successful redirect or a known challenge surface.
	try:
		page.wait_for_url("**linkedin.com/feed/**", timeout=15000)
	except PlaywrightTimeoutError:
		pass

	current_url = page.url.lower()
	if "checkpoint" in current_url or "challenge" in current_url:
		raise RuntimeError(
			"LinkedIn presented a login challenge (MFA/CAPTCHA). "
			"Complete it manually with --headless false and retry."
		)

	# If still on login page, credentials likely failed.
	if "linkedin.com/login" in current_url:
		raise RuntimeError("Login failed. Check LINKEDIN_USER and LINKEDIN_PASS.")


def build_search_url(keywords: str, location: str, page_index: int) -> str:
	start = page_index * 25
	return (
		"https://www.linkedin.com/jobs/search/"
		f"?keywords={quote_plus(keywords)}"
		f"&location={quote_plus(location)}"
		f"&start={start}"
	)


def first_text(locator, default: str = "") -> str:
	try:
		if locator.count() > 0:
			return locator.first.inner_text().strip()
	except Exception:
		return default
	return default


def extract_cards(page, source_page: int) -> List[JobListing]:
	"""Extract job listing cards from the LinkedIn search results page."""
	card_selector = ",".join(
		[
			"li.jobs-search-results__list-item",
			"div.job-search-card",
			"li.occludable-update",
		]
	)

	page.wait_for_timeout(1000)
	cards = page.locator(card_selector)
	listings: List[JobListing] = []

	count = cards.count()
	for i in range(count):
		card = cards.nth(i)

		title = first_text(
			card.locator(
				"a.job-card-list__title, a.job-search-card__title, a.job-card-container__link"
			),
			default="",
		)
		company = first_text(
			card.locator(
				"a.job-card-container__company-name, h4.job-search-card__subtitle, span.base-search-card__subtitle"
			),
			default="",
		)
		location = first_text(
			card.locator(
				"li.job-card-container__metadata-item, span.job-search-card__location, span.base-search-card__metadata"
			),
			default="",
		)
		posted = first_text(
			card.locator("time, span.job-search-card__listdate, span.job-search-card__listdate--new"),
			default="",
		)

		url = ""
		link = card.locator(
			"a.job-card-list__title, a.job-search-card__title, a.job-card-container__link"
		)
		try:
			if link.count() > 0:
				href = link.first.get_attribute("href")
				if href:
					if href.startswith("http"):
						url = href
					else:
						url = f"https://www.linkedin.com{href}"
		except Exception:
			pass

		if title or company or url:
			listings.append(
				JobListing(
					title=title,
					company=company,
					location=location,
					url=url,
					posted=posted,
					source_page=source_page,
				)
			)

	return listings


def dedupe_listings(listings: List[JobListing]) -> List[JobListing]:
	seen = set()
	deduped = []
	for item in listings:
		key = (item.title.lower(), item.company.lower(), item.url)
		if key in seen:
			continue
		seen.add(key)
		deduped.append(item)
	return deduped


def save_csv(path: str, listings: List[JobListing]) -> None:
	with open(path, "w", newline="", encoding="utf-8") as f:
		writer = csv.DictWriter(
			f,
			fieldnames=["title", "company", "location", "url", "posted", "source_page"],
		)
		writer.writeheader()
		for item in listings:
			writer.writerow(asdict(item))


def save_json(path: str, listings: List[JobListing]) -> None:
	with open(path, "w", encoding="utf-8") as f:
		json.dump([asdict(item) for item in listings], f, indent=2)


def scrape_linkedin_jobs(
	keywords: str,
	location: str,
	max_pages: int,
	headless: bool,
	username: Optional[str],
	password: Optional[str],
) -> List[JobListing]:
	all_listings: List[JobListing] = []

	with sync_playwright() as p:
		browser = p.chromium.launch(headless=headless)
		context = browser.new_context(
			viewport={"width": 1366, "height": 900},
			user_agent=(
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
				"AppleWebKit/537.36 (KHTML, like Gecko) "
				"Chrome/123.0.0.0 Safari/537.36"
			),
		)
		page = context.new_page()

		if username and password:
			login_linkedin(page, username, password)

		for page_index in range(max_pages):
			source_page = page_index + 1
			url = build_search_url(keywords, location, page_index)
			page.goto(url, wait_until="domcontentloaded")

			# Scroll to trigger lazy-loaded cards.
			for _ in range(3):
				page.mouse.wheel(0, random.randint(1200, 2000))
				jitter_sleep(0.3, 0.8)

			page_listings = extract_cards(page, source_page)
			all_listings.extend(page_listings)
			print(f"[page {source_page}] found {len(page_listings)} listings")

			jitter_sleep(1.0, 2.8)

		context.close()
		browser.close()

	return dedupe_listings(all_listings)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Scrape LinkedIn job listings with Playwright")
	parser.add_argument("--keywords", required=True, help="Job keywords, e.g. data engineer")
	parser.add_argument("--location", default="United States", help="Job location")
	parser.add_argument("--pages", type=int, default=3, help="Number of result pages to scrape")
	parser.add_argument("--out-csv", default="linkedin_jobs.csv", help="CSV output path")
	parser.add_argument("--out-json", default="linkedin_jobs.json", help="JSON output path")
	parser.add_argument(
		"--headless",
		action="store_true",
		help="Run browser headless (omit this flag to run with visible browser)",
	)
	parser.add_argument(
		"--use-login",
		action="store_true",
		help="Use LINKEDIN_USER and LINKEDIN_PASS from environment",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()

	username = None
	password = None
	if args.use_login:
		username = os.getenv("LINKEDIN_USER")
		password = os.getenv("LINKEDIN_PASS")
		if not username or not password:
			raise RuntimeError(
				"--use-login was specified, but LINKEDIN_USER/LINKEDIN_PASS are missing."
			)

	listings = scrape_linkedin_jobs(
		keywords=args.keywords,
		location=args.location,
		max_pages=max(1, args.pages),
		headless=args.headless,
		username=username,
		password=password,
	)

	save_csv(args.out_csv, listings)
	save_json(args.out_json, listings)

	print(f"Saved {len(listings)} unique listings")
	print(f"CSV: {args.out_csv}")
	print(f"JSON: {args.out_json}")


if __name__ == "__main__":
	main()
