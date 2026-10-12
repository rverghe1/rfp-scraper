"""Adapter for ARBuy (arbuy.arkansas.gov) - Arkansas's statewide
eProcurement system. Same Ivalua-family "bso" search endpoint as
NJSTART/BidBuy/COMMBUYS/OregonBuys (adapters/ivalua.py) - public, no login
required, despite Mike's docx listing a login credential - but with a
DIFFERENT column layout, so it can't reuse ivalua.search() as-is:
confirmed live, ARBuy's table is Bid Solicitation # | Organization Name |
Blanket # | Buyer | Description | Bid Opening Date | Awarded Vendor(s) |
Status | Alternate Id (9 header columns, 11 <td> cells once you count the
row's leading duplicate-ref cell and a trailing blank cell) - status sits
at cells[9], not cells[10] like the other four states on this platform.

Same status-vocabulary caveat as adapters/ivalua.py: status is a workflow-
stage label ("Opened" seen in testing), not a plain open/closed flag -
callers should lean on the LLM's lifecycle classification (which also
looks at the bid opening date) rather than trusting this string alone.
"""
from urllib.parse import quote_plus

BASE_URL = "https://arbuy.arkansas.gov"


def search(page, keyword):
    url = (f"{BASE_URL}/bso/view/search/external/advancedSearchBid.xhtml"
           f"?q={quote_plus(keyword)}&currentDocType=bids")
    page.goto(url, timeout=30000)
    page.wait_for_timeout(3000)

    tables = page.query_selector_all("table")
    if not tables:
        return []
    results = []
    for r in tables[0].query_selector_all("tbody tr"):
        cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
        if len(cells) < 10:
            continue
        link_el = r.query_selector("a")
        href = link_el.get_attribute("href") if link_el else ""
        results.append({
            "ref": cells[0],
            "org": cells[2],
            "title": cells[6],
            "close_date": cells[7],
            "status": cells[9],
            "link": BASE_URL + href if href.startswith("/") else href,
        })
    return results
