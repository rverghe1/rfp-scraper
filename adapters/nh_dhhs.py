"""Adapter for New Hampshire DHHS's open RFP list
(dhhs.nh.gov/doing-business-dhhs/contracts-procurement-opportunities/open-
requests-proposals) - fully public, no login required.

Needs core.browser.new_page(headless=False) - this site sits behind an
Akamai WAF that returns a bare 403 to any headless request (confirmed with
both plain headless and headless+stealth=True, consistently, across
multiple attempts). Unlike BidNet Direct/Michigan SIGMA (simpler headless-
JS-property checks that stealth=True fixes), Akamai typically also
fingerprints at the network/TLS layer, which JS patches can't touch -
but running a real (non-headless) browser window cleared it immediately,
no stealth flags even needed. This DOES mean a visible Chrome window will
open on-screen while this adapter runs.

No keyword search found on this page (the "Filter by Keyword" field didn't
change results when tested) - like Mississippi's MAGIC portal, this just
pages through everything (~50 total across 5 pages) and lets our own
keyword/LLM pipeline downstream decide relevance, rather than relying on
a portal-side search that may not actually work.

Scope note: this is NH DHHS's *general* contracting board (behavioral
health, child welfare, IT, consulting programs), not a dedicated staffing
board - spot-checked ~20 of the ~50 listings manually before building this
and found zero direct nursing/staffing RFPs. Built anyway since the user
asked for it, but don't expect a high hit rate.
"""
BASE_URL = "https://www.dhhs.nh.gov"
LIST_URL = f"{BASE_URL}/doing-business-dhhs/contracts-procurement-opportunities/open-requests-proposals"
MAX_PAGES = 5


def fetch_all(page):
    page.goto(LIST_URL, timeout=30000)
    page.wait_for_timeout(2500)

    results = []
    seen_hrefs = set()
    for _ in range(MAX_PAGES):
        for card in page.query_selector_all(".news__list__table__entry"):
            a = card.query_selector(".title a")
            if a is None:
                continue
            href = a.get_attribute("href") or ""
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            desc_el = card.query_selector(".description")
            date_el = card.query_selector(".date")
            results.append({
                "title": a.inner_text().strip(),
                "description": desc_el.inner_text().strip() if desc_el else "",
                "close_date": date_el.inner_text().replace("Date:", "").strip() if date_el else "",
                "link": BASE_URL + href if href.startswith("/") else href,
            })

        next_btn = page.query_selector("[aria-label='Next Page']")
        if next_btn is None or not next_btn.is_enabled():
            break
        next_btn.click()
        page.wait_for_timeout(2000)

    return results
