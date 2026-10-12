"""Adapter for South Carolina Business Opportunities (scbo.sc.gov) - SC's
statewide bid-notice board. Fully public, free registration NOT required to
view (per procurement.sc.gov). Distinct from Mike's docx "SCEIS / SC
Procurement" credential, which is for the state's SAP-based ERP/contract
system, not this notice board.

Two-step scrape: the search page (/adsearch) returns only title+type, with
no date or status - the actual deadline and agency live on each result's
detail page (/online-edition?s=<id>), and the deadline FIELD LABEL varies
by ad type ("Resume Deadline", "Bid/Submittal Due Date", etc.), so
fetch_detail() extracts it with a generic regex instead of a fixed selector.

search() and fetch_detail() are split deliberately so a caller sweeping
multiple keywords can dedupe by link BEFORE paying for the detail-page
fetch - a naive per-keyword fetch-everything loop is what made the first
version of this adapter time out (100 results x 4 keywords, largely
overlapping, each fetching its own detail page with no cross-keyword dedup).
"""
import re

BASE_URL = "https://scbo.sc.gov"
DATE_RE = re.compile(
    r"(?:Deadline|Due Date)\s*:\s*\n?\s*([A-Z][a-z]+ \d{1,2}, \d{4})", re.MULTILINE
)
AGENCY_RE = re.compile(
    r"(?:Agency/Owner|Purchasing Agent/Entity)\s*:\s*\n?\s*(.+)"
)


def search(page, keyword):
    """Cheap - one page load. Returns title/link/type only, no date/org."""
    page.goto(f"{BASE_URL}/adsearch", timeout=30000)
    page.wait_for_timeout(1500)
    page.fill("#srcbx", keyword)
    page.click("input[name='tsubmit']")
    page.wait_for_timeout(3000)

    hits = []
    for row in page.query_selector_all(".src_hres"):
        a = row.query_selector("a[href*='/online-edition']")
        if not a:
            continue
        cols = row.query_selector_all(".src_rcol")
        hits.append({
            "title": a.get_attribute("title") or a.inner_text().strip(),
            "link": BASE_URL + a.get_attribute("href"),
            "type": cols[-1].inner_text().strip() if cols else "",
        })
    return hits


def fetch_detail(page, hit):
    """Expensive - one page load per call. Adds org/close_date to a hit
    dict from search(). Call this only after deduping by link."""
    page.goto(hit["link"], timeout=20000)
    page.wait_for_timeout(500)  # static server-rendered page, no JS to settle
    body = page.inner_text("body")
    date_m = DATE_RE.search(body)
    org_m = AGENCY_RE.search(body)
    return {
        **hit,
        "org": org_m.group(1).strip() if org_m else "",
        "close_date": date_m.group(1).strip() if date_m else "",
    }
