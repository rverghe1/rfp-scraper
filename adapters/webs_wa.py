"""Adapter for Washington's WEBS (pr-webs-vendor.des.wa.gov) - the state's
public bid calendar. The open-solicitations list (BidCalendar.aspx) needs no
login, despite Mike's docx listing a WEBS vendor credential: only the
per-bid detail page (Search_BidDetails.aspx) redirects to the vendor login,
and the list rows already carry the close date, title, reference number and
a description snippet, so detail pages are never fetched.

The list is an ASP.NET WebForms grid that pages through postbacks and has no
keyword search, and its rows don't name the posting organization. Iterating
the "Filter by Government Organization" dropdown (ddlOrgNames, auto-
postback) gets both: each org's postings come back on one page, tagged with
that org's name. Same fetch-all-then-filter-by-keyword pattern as
adapters/eva_virginia.py.
"""
import re

LIST_URL = "https://pr-webs-vendor.des.wa.gov/BidCalendar.aspx"
BASE_URL = "https://pr-webs-vendor.des.wa.gov/"

_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{2}")
_REF_RE = re.compile(r"Ref#:\s*(\S+)")


def _parse_rows(page, org):
    # One atomic evaluate() instead of per-element handles: each pager/search
    # click is a full-page postback, and handles taken mid-navigation die
    # with "execution context was destroyed".
    raw = page.evaluate("""() => [...document.querySelectorAll('tr.grid_item')].map(tr => {
        const a = tr.querySelector("a[href*='Search_BidDetails']");
        return a ? {title: a.innerText.trim(), href: a.getAttribute('href'), text: tr.innerText} : null;
    }).filter(Boolean)""")
    results = []
    for r in raw:
        before_title = r["text"].split(r["title"])[0]
        dates = _DATE_RE.findall(before_title) or _DATE_RE.findall(r["text"])
        ref = _REF_RE.search(r["text"])
        results.append({
            "title": r["title"],
            "description": " ".join(r["text"].split()),
            "org": org,
            "close_date": dates[0] if dates else "",
            "status": "Open",
            "ref": ref.group(1) if ref else r["href"].split("ID=")[-1],
            "link": BASE_URL + r["href"],
        })
    return results


def _page_through(page, org):
    """Collects the org's rows from page 1, then follows the numeric pager
    links (each a __doPostBack) one page at a time until no unvisited page
    number is left."""
    results = _parse_rows(page, org)
    visited = {"1"}
    while True:
        nxt = next((a for a in page.query_selector_all("a[href*=doPostBack]")
                    if a.inner_text().strip().isdigit() and a.inner_text().strip() not in visited), None)
        if nxt is None:
            return results
        visited.add(nxt.inner_text().strip())
        with page.expect_navigation(timeout=30000):
            nxt.click()
        results.extend(_parse_rows(page, org))


def fetch_all_open(page):
    page.goto(LIST_URL, timeout=40000)
    page.wait_for_selector("#ddlOrgNames", timeout=20000)
    orgs = [o.inner_text().strip() for o in page.query_selector_all("#ddlOrgNames option")]
    results = []
    for org in orgs:
        if org.startswith("<"):  # the "<All>" placeholder
            continue
        # The dropdown alone doesn't filter - the "Search" image button
        # submits the form with the chosen org.
        page.select_option("#ddlOrgNames", label=org)
        with page.expect_navigation(timeout=30000):
            page.click("#ImageButton1")
        results.extend(_page_through(page, org))
    return results


def filter_keyword(rows, keyword):
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows if pattern.search(r["title"]) or pattern.search(r["description"])]
