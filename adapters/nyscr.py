"""Adapter for the NYS Contract Reporter (nyscr.ny.gov) - New York's
official statewide procurement bulletin. Aggregates solicitations from
all state agencies, authorities, SUNY/CUNY, and many municipalities/
school districts/nonprofits - much broader than OGS's own bid calendar
(ogs.ny.gov/procurement/bid-opportunities), which only lists OGS's own
~5 centralized contracts at any given time and explicitly tells vendors
to watch the Contract Reporter instead for the full statewide picture.

No login required for search, despite secrets.json holding a credential
for this portal: keyword search results (title, agency, dates, category)
are fully public at /Ads/Search?Keyword=... - only the full opportunity
detail page is gated (redirects to /Account/Login), and the search
result card already gives everything this scraper needs, so login is
skipped entirely.

The results list defaults to open opportunities only ("All Open
Opportunities: N" heading) - no separate status filter needed, and every
row is stamped "Open" here since a closed one wouldn't appear.

Row shape confirmed from live DOM: each result is a div.opp-list-item
with a data-ad-id attribute. The title lives in a child div carrying a
title="Full Title: ..." HTML attribute (also handy since it holds the
untruncated title, unlike the visible, CSS-truncated text) - NOT the
first line of item.inner_text(), which shifts depending on viewport
width: several label divs are responsive (Bootstrap d-none/d-lg-block),
including a rank-number cell that renders ahead of the title at wide
viewports (confirmed: Playwright's default 1280px context renders it,
a narrower interactive-browser check earlier did not, so it's a real
trap, not a one-off). Agency/Due date are pulled the same DOM-structural
way - label div's exact text, then its next sibling's text - rather than
relying on inner_text() line order at all.
"""
from urllib.parse import quote_plus

BASE_URL = "https://nyscr.ny.gov"

_EXTRACT_JS = """
() => {
  const labelValue = (item, label) => {
    const divs = Array.from(item.querySelectorAll('div'));
    const labelDiv = divs.find(d => d.children.length === 0 && d.textContent.trim() === label);
    const sib = labelDiv ? labelDiv.nextElementSibling : null;
    return sib ? sib.textContent.trim() : '';
  };
  return Array.from(document.querySelectorAll('.opp-list-item')).map(item => {
    const titleEl = item.querySelector('[title^="Full Title:"]');
    const title = titleEl
      ? titleEl.getAttribute('title').replace(/^Full Title:\\s*/, '')
      : (item.querySelector('.bg-primary')?.textContent.trim() || '');
    return {
      ref: item.getAttribute('data-ad-id') || '',
      title: title,
      org: labelValue(item, 'Agency:'),
      close_date: labelValue(item, 'Due date:'),
    };
  });
}
"""


def search(page, keyword):
    url = f"{BASE_URL}/Ads/Search?Keyword={quote_plus(keyword)}"
    page.goto(url, timeout=30000)
    page.wait_for_timeout(2000)

    raw = page.evaluate(_EXTRACT_JS)
    results = []
    for r in raw:
        if not r["ref"]:
            continue
        results.append({
            "ref": r["ref"],
            "title": r["title"],
            "org": r["org"],
            "close_date": r["close_date"],
            "status": "Open",
            "link": f"{BASE_URL}/Ads/Details/{r['ref']}",
        })
    return results
