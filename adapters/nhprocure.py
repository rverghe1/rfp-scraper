"""Adapter for NHProcure / NH DAS Statewide Bids and Proposals
(apps.das.nh.gov/bidscontracts/bids.aspx) - fully public ASP.NET WebForms
search, no login required despite Mike's docx listing an NHProcure vendor
credential for this portal (confirmed 2026-10-19: a blank search returns
results with no auth wall).

Small statewide board (1 open bid at check time) - same low-volume
pattern as Edison.TN's board, not a sign anything is broken.
"""
BASE_URL = "https://apps.das.nh.gov/bidscontracts/bids.aspx"


def search(page, keyword):
    page.goto(BASE_URL, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    page.fill("#ctl00_ContentPlaceHolder_TextBoxSearchName", keyword)
    page.click("#ctl00_ContentPlaceHolder_ButtonSearch")
    page.wait_for_timeout(4000)

    tables = page.query_selector_all("table")
    if not tables:
        return []
    rows = tables[0].query_selector_all("tr")[1:]  # skip header row
    results = []
    for r in rows:
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        if len(cells) < 9 or not cells[0] or "no bids found" in cells[0].lower():
            continue
        results.append({
            "title": cells[0],
            "ref": cells[1],
            "close_date": cells[4],
            "status": cells[6],
            "org": cells[8],
            "link": BASE_URL,
        })
    return results
