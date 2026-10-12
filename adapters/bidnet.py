"""Adapter for BidNet Direct (bidnetdirect.com) - a national third-party
bid aggregator, not specific to any state or county. Fully public search
(/solicitations/open-bids), no login required.

Confirmed blocked for plain headless Playwright (bare 403 on every URL,
even with a spoofed desktop user-agent) - but a real browser and a real
user's Chrome session both loaded it fine, so this is a headless-Chromium
fingerprint check, not a network/IP block or a login wall. Fixed by
requesting core.browser.new_page(stealth=True), which patches the handful
of properties that give headless Chromium away (navigator.webdriver,
missing plugins/languages/window.chrome).

Timing note (found after this consistently timed out on Railway's cron
run while never failing locally): page.goto() defaults to
wait_until="load", which blocks until every last resource on the page
finishes - including this aggregator's third-party ad/analytics/chat-
widget scripts, which can hang well past 30s on a more resource-
constrained container even though the results table itself (all this
adapter actually reads) is ready much earlier. Switched to
wait_until="domcontentloaded" plus one retry on timeout, rather than
assuming an IP-level block and reaching for a proxy - a real fix if this
was in fact a slow-third-party-resource issue, cheap to try either way.

Purchasing groups: BidNet also hosts state/regional "purchasing groups" -
sub-sites at /<group>/solicitations/open-bids with the same row markup,
where a state's LOCAL governments post. The one that matters for us is
MITN (Michigan Inter-governmental Trade Network - ~200 Michigan local
governments, including the counties that run our 29 MI medical care
facilities). Michigan's own SIGMA VSS (adapters/michigan.py) is the STATE
board and never sees county business, and the national search's 3-page
cap means Michigan rows get crowded out there (confirmed: page 1 of a
national "nursing" search had zero Michigan rows while the MITN-scoped
search had two). search(group="mitn") scopes the sweep to that sub-site.
The group pages' results table has no #solicitationsList id, so rows are
located via each a.solicitation-link's enclosing <tr> - which works on
the national page too. MITN's page says "register or Login to access
open bids", but the open-solicitations list itself renders publicly (25
rows/page, paginated /page2 etc.) - only the documents are gated.
"""
import re

BASE_URL = "https://www.bidnetdirect.com"
MAX_PAGES = 3  # ~26 results/page; caps the sweep rather than pulling all
               # of BidNet's national volume (125+ results for one keyword)


def _parse_row(row):
    a = row.query_selector("a.solicitation-link")
    if not a:
        return None
    text = row.inner_text()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # Expected shape: [Title, State, "Published", pub_date, "Closing", close_date, ref]
    if len(lines) < 6:
        return None
    title = lines[0]
    state = lines[1]
    close_date = ""
    m = re.search(r"Closing\s*\n?\s*(\S+)", text)
    if m:
        close_date = m.group(1)
    href = a.get_attribute("href") or ""
    return {
        "title": title,
        "org": state,
        "ref": lines[-1],
        "close_date": close_date,
        "link": BASE_URL + href if href.startswith("/") else href,
    }


def _goto_with_retry(page, url):
    """One retry on timeout - cheap insurance against a one-off slow load
    (shared-CPU container, transient network blip) rather than a real
    block, which would fail the same way twice."""
    for attempt in (1, 2):
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            return
        except Exception:
            if attempt == 2:
                raise
            page.wait_for_timeout(3000)


def _rows(page):
    """Result-row <tr>s, located from their a.solicitation-link anchors.
    Works on both the national page (#solicitationsList) and purchasing-
    group pages (no table id) - see module docstring."""
    trs = []
    for a in page.query_selector_all("a.solicitation-link"):
        tr = a.evaluate_handle("e => e.closest('tr')").as_element()
        if tr is not None:
            trs.append(tr)
    return trs


def search(page, keyword, group=None, max_pages=MAX_PAGES):
    """group: None for the national search, or a purchasing-group slug
    (e.g. "mitn") to scope to that sub-site."""
    prefix = f"/{group}" if group else ""
    results = []
    for page_num in range(1, max_pages + 1):
        path = (f"{prefix}/solicitations/open-bids" if page_num == 1
                else f"{prefix}/solicitations/open-bids/page{page_num}")
        url = f"{BASE_URL}{path}?keywords={keyword}&target=search"
        _goto_with_retry(page, url)
        page.wait_for_timeout(3000)

        rows = _rows(page)
        if not rows:
            break
        got_any = False
        for r in rows:
            parsed = _parse_row(r)
            if parsed:
                results.append(parsed)
                got_any = True
        if not got_any:
            break
    return results
