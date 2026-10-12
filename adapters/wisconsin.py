"""Adapter for the Wisconsin Bidder Portal (esupplier.wi.gov) - PeopleSoft-based,
statewide, and fully public (no login needed to search solicitations).

The search UI lives inside a nested iframe (frames[1] once the Current
Solicitations page has loaded), and results are extracted via the anchor
elements PeopleSoft renders per row (id="BID_REF$N"), walking up to the row's
<tr> for the rest of the cell values - much more robust than guessing which
of a dozen nested PeopleSoft grid <table> elements is "the" results table.
"""

BASE_URL = "https://esupplier.wi.gov"

_EXTRACT_JS = """
() => {
  const anchors = Array.from(document.querySelectorAll("a[id^='BID_REF']"));
  return anchors.map(a => {
    const tr = a.closest('tr');
    const cells = tr ? Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()) : [];
    return {ref: a.innerText.trim(), cells: cells};
  });
}
"""


def _get_search_frame(page):
    page.goto(BASE_URL, timeout=30000)
    page.wait_for_timeout(3000)
    page.click("text=Wisconsin Bidder Portal")
    page.wait_for_timeout(3000)
    page.click("text=Current Solicitations")
    page.wait_for_timeout(4000)
    return page.frames[1]


def search(page, keyword):
    fr = _get_search_frame(page)
    fr.fill("#WI_SS_BIDR_SRCH_WI_SS_SRCH_KEYWORD", keyword)
    fr.click("#WI_SS_BIDR_SRCH_SEARCH")
    page.wait_for_timeout(3000)
    fr2 = page.frames[1]

    raw = fr2.evaluate(_EXTRACT_JS)
    results = []
    for r in raw:
        cells = r["cells"]
        if len(cells) < 7:
            continue
        results.append({
            "event_number": cells[0],
            "ref": cells[1],
            "type": cells[2],
            "title": cells[3],
            "org": cells[4],
            "status": cells[5],
            "close_date": cells[6],
            # PeopleSoft renders row actions as postback JS, not a stable
            # deep link - the search page itself is the closest usable URL.
            "link": f"{BASE_URL}/psc/esupplier/SUPPLIER/ERP/c/WI_SS_BIDDER_FLU.WI_SS_BDR_BIDS_FLU.GBL",
        })
    return results
