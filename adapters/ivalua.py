"""Shared adapter logic for the Ivalua-based state eProcurement platform
used (at least) by New Jersey (NJSTART), Illinois (BidBuy), Massachusetts
(COMMBUYS), and Oregon (OregonBuys). All four expose the exact same public
`advancedSearchBid.xhtml` search endpoint and results-table structure with
no login required - only the base URL differs, so the per-state adapters
(njstart.py, bidbuy.py, commbuys.py, oregonbuys.py) are thin wrappers around
this one `search()`.

Column mapping is fixed across all four sites (confirmed against live data,
not just the two originally tested): cells[0]=ref, cells[2]=org (agency/
municipality name), cells[6]=title, cells[7]=bid opening date, cells[10]=
status. Status is a workflow-stage label, not open/closed - seen values
include "Sent" (published, not yet opened - i.e. still open for response),
"Opened", "Approved", "Bid to PO", and "Closed" - callers should filter for
"Sent" + a future opening date, not just any non-"Closed" status (see the
run_bidbuy() comment in run_all.py for why the status label alone isn't
reliable).
"""


def search(page, base_url, keyword):
    from urllib.parse import quote_plus
    url = (f"{base_url}/bso/view/search/external/advancedSearchBid.xhtml"
           f"?q={quote_plus(keyword)}&currentDocType=bids")
    page.goto(url, timeout=30000)
    page.wait_for_timeout(3000)

    tables = page.query_selector_all("table")
    if not tables:
        return []
    results = []
    for r in tables[0].query_selector_all("tbody tr"):
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        if len(cells) < 11:
            continue
        link_el = r.query_selector("a")
        href = link_el.get_attribute("href") if link_el else ""
        results.append({
            "ref": cells[0],
            "org": cells[2],
            "title": cells[6],
            "close_date": cells[7],
            "status": cells[10],
            "link": base_url + href if href.startswith("/") else href,
        })
    return results
