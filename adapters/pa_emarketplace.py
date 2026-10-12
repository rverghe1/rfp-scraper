"""Adapter for PA eMarketplace (emarketplace.state.pa.us) - Pennsylvania's
official statewide procurement search, covering state agencies, counties,
municipalities, school districts, and universities. Fully public ASP.NET
WebForms search, no login required - distinct from Mike's docx "PA Supplier
Portal"/"JAGGAER" credentials, which are for a staffing MSP relationship
(22nd Century Technologies), not this RFP board.

Results table has a clean, explicit "Status" column (Open/Closed/etc.) and
a "County" column - unlike the Ivalua-platform sites, no date-based
open/closed inference is needed here.
"""
import re

BASE_URL = "https://www.emarketplace.state.pa.us"
SEARCH_URL = f"{BASE_URL}/Search.aspx"


def search(page, keyword):
    page.goto(SEARCH_URL, timeout=30000)
    page.wait_for_timeout(2000)
    page.fill("#ctl00_MainBody_txtBidTitle", keyword)
    page.click("#ctl00_MainBody_btnSearch")
    page.wait_for_timeout(3000)

    results = []
    seen_refs = set()
    for r in page.query_selector_all("table tbody tr"):
        a = r.query_selector("a[href^='Solicitations.aspx']")
        if not a:
            continue
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        # The header row repeats the same link with an extra leading cell -
        # only keep rows shaped like an actual data row (12 cells).
        if len(cells) != 12:
            continue
        ref = cells[0]
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        href = a.get_attribute("href") or ""
        results.append({
            "ref": ref,
            "title": cells[2],
            "description": cells[3],
            "org": cells[4],
            "county": cells[5],
            "close_date": cells[8],  # Solicitation Due Date
            "status": cells[10],
            "link": f"{BASE_URL}/{href}" if not href.startswith("http") else href,
        })
    return results
