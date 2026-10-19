"""Adapter for Virginia's eVA (Virginia Business Opportunities, running on
CGI's eVA platform at mvendor.cgieva.com) - fully public, no login
required despite Mike's docx listing an eVA Supplier Portal credential
(that login is for a vendor's own account/responses, not for browsing
open opportunities).

The public UI (AllOpportunities.jsp) has NO keyword search box at all -
only status/agency/category facet filters, confirmed by inspecting the
page (2026-10-19). It's backed by a direct Solr JSON API
(solrconnect.jsp) that the page's own JS calls - this adapter talks to
that API directly via plain HTTP (no Playwright needed), fetching every
currently-Open posting (587 at check time) via Solr's cursorMark
pagination, then filters by keyword client-side against the title +
long description, same "fetch-all-then-filter" pattern as
adapters/edison_tn.py and adapters/arkansas_osp.py for other small-ish
boards with no server-side search.

solrconnect.jsp needs an exact literal query string, not a `requests`
params dict - building the URL from a dict changed the encoding just
enough (commas/colons/asterisks in q/sort/cursorMark) to make the
endpoint return an empty body with no error. A warm-up GET on
AllOpportunities.jsp first (for session cookies) is also required - a
cold request to solrconnect.jsp alone returns nothing. Also needs a
real browser User-Agent forced via session.headers.update() (not
.setdefault() - requests.Session() always pre-populates its own
"python-requests/x.x" UA, so .setdefault() silently no-ops and the real
bot-signature UA goes out, which gets a 403). And on page 2+, the
nextCursorMark value must be percent-encoded before being dropped into
the URL - it routinely contains "/" and other characters that otherwise
corrupt the query string into an empty response with no error.

Each posting's real detail link is NOT derivable from the JSON alone -
confirmed by capturing the "View Opportunity" click's resulting URL:
IVDetails.jsp?rfp_id_lot=<externalid>&rfp_id_round=<version>.
"""
import re
import urllib.parse

import requests

BASE_URL = "https://mvendor.cgieva.com/Vendor/public"
LANDING_URL = f"{BASE_URL}/AllOpportunities.jsp"
SOLR_URL = f"{BASE_URL}/solrconnect.jsp"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
PAGE_SIZE = 100


def fetch_all_open(session):
    # requests.Session() always pre-populates a "python-requests/x.x"
    # User-Agent - .setdefault() silently no-ops against it since the key
    # already has a value, leaving the real bot-signature UA on the wire.
    # Confirmed: that got a 403 from this site's WAF; .update() (forcing
    # the override) does not.
    session.headers.update({"User-Agent": _UA})
    session.get(LANDING_URL, timeout=20)  # warm up cookies

    results = []
    cursor = "*"
    while True:
        url = (f"{SOLR_URL}?q=*:*&fq=status:Open&sort=pubdate%20desc,id%20desc"
               f"&rows={PAGE_SIZE}&wt=json&cursorMark={urllib.parse.quote(cursor, safe='')}")
        resp = session.get(url, timeout=20, headers={"Referer": LANDING_URL})
        resp.raise_for_status()
        data = resp.json()
        docs = data["response"]["docs"]
        for doc in docs:
            results.append({
                "title": doc.get("shortdesc", ""),
                "description": doc.get("longdesc", ""),
                "org": doc.get("agencyname", ""),
                "close_date": doc.get("closedate", ""),
                "status": doc.get("status", ""),
                "ref": doc.get("externalid", ""),
                "link": (f"{BASE_URL}/IVDetails.jsp?PageTitle=SO%20Details"
                         f"&rfp_id_lot={doc.get('externalid', '')}"
                         f"&rfp_id_round={doc.get('version', '')}"),
            })
        next_cursor = data.get("nextCursorMark")
        if not docs or next_cursor == cursor:
            break
        cursor = next_cursor
    return results


def filter_keyword(rows, keyword):
    """Word-boundary match against title + description, same convention as
    adapters/bonfire.py's filter_keyword - avoids a bare substring like
    "rn" matching inside unrelated words."""
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows if pattern.search(r["title"]) or pattern.search(r["description"])]
