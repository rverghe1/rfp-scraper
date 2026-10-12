"""Adapter for Tennessee's Edison Supplier Portal (hub.edison.tn.gov) -
the state's PeopleSoft-based ERP/procurement system. The "Public Bidding
Events" list is fully public (no login required) despite secrets.json
holding an Edison.TN credential - login is only needed to register as a
supplier or receive award notifications, not to browse open events.

This is a small statewide list (currently ~18 rows) with no site-side
keyword search box on the public page, so this fetches everything once
and filters by keyword itself (word-boundary match, same convention as
adapters/bonfire.py's filter_keyword, to avoid e.g. "rn" matching inside
"Warning").

Row data uses PeopleSoft's per-field element IDs suffixed with a 0-based
row index ($0, $1, ...) rather than plain HTML table cells - the same
per-field-ID pattern as adapters/michigan.py's CGI Advantage VSS grid,
just PeopleSoft's own flavor of it. No stable per-event deep link was
found (the "Details" chevron is a JS postback, not a real href), so link
points at the search page itself, same fallback as several other
postback-only adapters (wisconsin.py, michigan.py).
"""
import re

BASE_URL = "https://hub.edison.tn.gov"
SEARCH_URL = f"{BASE_URL}/psc/fsprd/SUPPLIER/ERP/c/SCP_PUBLIC_MENU_FL.SCP_PUB_BID_CMP_FL.GBL"


def fetch_all(page):
    page.goto(SEARCH_URL, timeout=30000)
    page.wait_for_timeout(4000)

    results = []
    i = 0
    while True:
        title_el = page.query_selector(f"#SCP_PUB_AUC_VW_AUC_NAME\\${i}")
        if title_el is None:
            break
        org_el = page.query_selector(f"#BUS_UNIT_AUC_VW_DESCR\\${i}")
        ref_el = page.query_selector(f"#SCP_PUB_AUC_VW_AUC_ID\\${i}")
        close_el = page.query_selector(f"#SCP_COSP_WK_FL_SCP_END_DATE_CHAR\\${i}")
        results.append({
            "title": title_el.inner_text().strip(),
            "org": org_el.inner_text().strip() if org_el else "",
            "ref": ref_el.inner_text().strip() if ref_el else "",
            "close_date": close_el.inner_text().strip() if close_el else "",
            # This page only ever lists currently-open public bidding events -
            # no separate status field is rendered.
            "status": "Open",
            "link": SEARCH_URL,
        })
        i += 1
    return results


def search(page, keyword):
    rows = fetch_all(page)
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows if pattern.search(r["title"])]
