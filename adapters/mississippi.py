"""Adapter for the Mississippi DFA statewide procurement bid search
(www.ms.gov/dfa/contract_bid_search) - fully public, no login required.

Mike's docx lists a "MAGIC" portal credential for Mississippi, but MAGIC
(magic.mississippi.org) has a broken/invalid TLS cert and appears to be a
vendor-registration system, not a searchable public bid board. This ms.gov
search covers statewide agency bids (including "Nursing Home" postings
directly) with a simple URL-param keyword search - no login needed.
"""

BASE_URL = "https://www.ms.gov/dfa/contract_bid_search"
SITE_ROOT = "https://www.ms.gov"


def search(page, keyword):
    from urllib.parse import quote_plus
    page.goto(f"{BASE_URL}/Search?keyword={quote_plus(keyword)}", timeout=30000)
    page.wait_for_timeout(2500)

    results = []
    for block in page.query_selector_all(".well.well-large"):
        link_el = block.query_selector("h2 a")
        title_el = block.query_selector("p")
        status_el = block.query_selector("span")
        if not link_el:
            continue
        href = link_el.get_attribute("href") or ""
        results.append({
            "ref": link_el.inner_text().replace("BID:", "").strip(),
            "title": title_el.inner_text().strip() if title_el else link_el.inner_text().strip(),
            "org": "",  # not shown on the search results list, only on the detail page
            "status": status_el.inner_text().replace("Status:", "").strip() if status_el else "",
            # href already comes back as a full path (e.g.
            # "/dfa/contract_bid_search/Bid/Details/12031") - just prepend
            # the site root, not BASE_URL, or "/dfa" ends up doubled.
            "link": (SITE_ROOT + href) if href.startswith("/") else href,
        })
    return results
