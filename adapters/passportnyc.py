"""Adapter for PASSPort NYC (passport.cityofnewyork.us) - New York City's
citywide procurement system (covers all agencies incl. NYC Health +
Hospitals). Fully public search, no login required, despite Mike's docx
listing a login - the Browse Public RFx page doesn't need it.
"""

BASE_URL = "https://passport.cityofnewyork.us"
BROWSE_URL = f"{BASE_URL}/page.aspx/en/rfp/request_browse_public"


def search(page, keyword):
    page.goto(BROWSE_URL, timeout=30000)
    page.wait_for_timeout(3000)
    page.fill("#body_x_txtQuery", keyword)
    page.click("#body_x_prxFilterBar_x_cmdSearchBtn")
    page.wait_for_timeout(3000)

    # Results table is the one whose rows have a link into /bpm/process_manage_extranet/
    results = []
    for t in page.query_selector_all("table"):
        rows = t.query_selector_all("tbody tr")
        for r in rows:
            link_el = r.query_selector("a[href*='process_manage_extranet']")
            if not link_el:
                continue
            cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
            if len(cells) < 12:
                continue
            results.append({
                "title": cells[4] if len(cells) > 4 else cells[0],
                "org": cells[5] if len(cells) > 5 else "",
                "status": cells[6] if len(cells) > 6 else "",
                "ref": cells[3] if len(cells) > 3 else "",
                "close_date": cells[11] if len(cells) > 11 else "",
                "link": BASE_URL + link_el.get_attribute("href"),
            })
    return results
