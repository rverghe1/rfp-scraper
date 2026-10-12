"""Adapter for the Indiana IDOA Current Business Opportunities page
(secure.in.gov/idoa/procurement) - statewide, fully public, no login needed.

Indiana has no dedicated vendor-portal credential in Mike's docx (only a
CAI MSP shift-management registration, which isn't an RFP board) - this
public listing is the real source for Indiana state solicitations.
"""

BASE_URL = "https://secure.in.gov/idoa/procurement/current-business-opportunities"


def search(page, keyword):
    page.goto(BASE_URL, timeout=30000)
    page.wait_for_timeout(2500)
    sb = page.query_selector('input[aria-controls="events-table"]')
    if sb is None:
        return []
    sb.fill(keyword)
    page.wait_for_timeout(1500)

    results = []
    for r in page.query_selector_all("#events-table tbody tr"):
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        if not cells or "No matching records found" in cells[0]:
            continue
        title_cell = r.query_selector("td:first-child")
        title = title_cell.inner_text().split("\n")[0].strip() if title_cell else cells[0]
        doc_link = None
        for a in r.query_selector_all("a"):
            if "Bid Documents" in a.inner_text():
                doc_link = a.get_attribute("href")
                break
        results.append({
            "title": title,
            "org": cells[1] if len(cells) > 1 else "",
            "ref": cells[2] if len(cells) > 2 else "",
            "description": cells[3] if len(cells) > 3 else "",
            "close_date": cells[4] if len(cells) > 4 else "",
            "link": ("https://secure.in.gov" + doc_link) if doc_link and doc_link.startswith("/")
                    else (doc_link or BASE_URL),
        })
    return results
