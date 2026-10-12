"""Adapter for PassaicBids (passaicbids.com) - Passaic County, NJ's public
purchasing portal. Fully public search, no login required.

Relevant because Preakness Healthcare Center (Passaic County-owned, an
active CBH account) posts here - confirmed directly, e.g. "Podiatrists for
Preakness Healthcare Center" and the vital-sign-machine maintenance re-bid.
"""

BASE_URL = "https://www.passaicbids.com"
STATUSES = ["current", "pending", "awarded", "cancelled-rejected"]


def search(page, keyword, status="current"):
    page.goto(f"{BASE_URL}/bids/search?rfp_filter_status={status}", timeout=30000)
    page.wait_for_timeout(2000)
    sb = page.query_selector("input[type=search]")
    if sb is None:
        return []
    sb.fill(keyword)
    page.wait_for_timeout(1500)

    results = []
    for r in page.query_selector_all("table tbody tr"):
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        if not cells or "No matching records found" in cells[0]:
            continue
        link_el = r.query_selector("a")
        href = link_el.get_attribute("href") if link_el else ""
        results.append({
            "ref": cells[0] if len(cells) > 0 else "",
            "status": cells[1] if len(cells) > 1 else status,
            "title": cells[2] if len(cells) > 2 else "",
            "posted": cells[3] if len(cells) > 3 else "",
            "close_date": cells[4] if len(cells) > 4 else "",
            "link": BASE_URL + href if href.startswith("/") else href,
        })
    return results
