"""Adapter for Cal eProcure's California State Contracts Register (CSCR)
event search (caleprocure.ca.gov/pages/Events-BS3/event-search.aspx) -
PeopleSoft FSCM-based, same ICPanel/Fluid-grid platform family as
adapters/edison_tn.py and adapters/michigan.py, just a different component.
Fully public, no login required despite Mike's docx listing a Cal eProcure
credential (MikeKCB) - that login is for a supplier's own bidder profile,
not for browsing open events.

Confirmed live (2026-10-19): submitting the Event Name search field with a
keyword filters server-side (370 total open events with a blank search,
1 with "nursing") - no need to page through results client-side like
Edison.TN's small fixed list.

Each result row renders twice in the DOM (a duplicate desktop/mobile
layout, both sharing ids) - only one copy is actually visible at a time,
so rows are filtered by Playwright's is_visible() rather than taking
every `tr[id^="trRESP_INQA_HD_VW_GR"]` match.

No stable per-event deep link exists - the "AUC_ID_COL$N" anchor is a JS
postback (href is null), not a real URL - so link falls back to the
search page itself, same convention as the other postback-only adapters
in this codebase (edison_tn.py, wisconsin.py, michigan.py).
"""
BASE_URL = "https://caleprocure.ca.gov"
SEARCH_URL = f"{BASE_URL}/pages/Events-BS3/event-search.aspx"


def search(page, keyword):
    page.goto(SEARCH_URL, timeout=30000, wait_until="networkidle")
    page.wait_for_timeout(3000)
    page.fill("#RESP_INQA_WK_ZZ_AUC_NAME", keyword)
    page.click("#RESP_INQA_WK_INQ_AUC_GO_PB")
    page.wait_for_timeout(7000)

    results = []
    for row in page.query_selector_all('tr[id^="trRESP_INQA_HD_VW_GR"]'):
        if not row.is_visible():
            continue
        cells = {c.get_attribute("data-if-label"): c.inner_text().strip()
                 for c in row.query_selector_all("td[data-if-label]")}
        if not cells.get("tdEventName"):
            continue
        results.append({
            "ref": cells.get("tdEventId", ""),
            "title": cells.get("tdEventName", ""),
            "org": cells.get("tdDepName", ""),
            "close_date": cells.get("tdEndDate", "").replace("\n", " "),
            "status": cells.get("tdStatus", ""),
            "link": SEARCH_URL,
        })
    return results
