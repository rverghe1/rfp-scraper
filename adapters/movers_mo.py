"""Adapter for MissouriBUYS powered by MOVERS - Missouri's state procurement
board since 2024-09-18 (Oracle Fusion Cloud Sourcing, "Solicitation
Abstracts" public listing). Replaces the old run_all.py note that Missouri
had "no public bid content at all" - that was true of the legacy
MissouriBUYS/WebProcure system, but the MOVERS abstract board is fully
public (missouribuys.mo.gov/faqs: "You do not need to be registered to view
the Bid Board"), and better still, the Redwood UI is backed by an
ANONYMOUS JSON REST endpoint (confirmed 2026-09-18: plain `requests` with no
cookies/auth returns 200), so this adapter needs no browser at all.

Scope: STATE agencies only (OA Division of Purchasing, MoDOT, DMH, DHSS,
DOC, Missouri Veterans Commission's 7 skilled-nursing veterans homes, etc.).
Missouri's 17 county-owned nursing homes in data/facilities.csv procure
through their counties / nursing home districts, which do NOT post here -
see adapters/instantmarkets_mo.py (the state-sponsored Bid Locator that
crawls local-government sites) for that layer.

API shape (Oracle Fusion REST, discovered from the listing page's own XHR):
  GET {BASE}/supplierNegotiationAbstracts
      ?finder=RowFinderByBU;ProcurementBUId=<BU>&fields=...&limit=100&offset=N
  -> {"items":[...], "count":N, "hasMore":bool}
~500 abstracts total across all statuses (ACTIVE/AMENDED/CLOSED/CANCELLED)
as of first build, so fetch_all() just pages through everything (5 calls)
and keyword-filters locally - same design as adapters/bonfire.py, and it
sidesteps the API's `q=` LIKE filter, which is title-only and case-fiddly.

Two fetch paths, merged by ref:
  1. fetch_all(): the 500 most-recently-posted abstracts (the finder hard-
     caps at 500 rows - offset>=500 returns nothing even though
     totalResults reports ~3,800). Every ACTIVE/AMENDED posting is recent
     enough to fall inside that window, so this is the complete OPEN set.
  2. search_title(): a server-side `q=upper(NegotiationTitle) like '%STEM%'`
     call per keyword stem, which reaches past the 500 cap into the older
     CLOSED/CANCELLED history (e.g. DESE's "1:1 Nursing Services" and
     DHSS's "Public Health Staff Augmentation QVL" from 2025). q= only
     accepts ONE simple condition - no `or`, no `;` chaining (both 400) -
     and only on the pinned API prefix (see API_BASE_PINNED).
Callers then apply filter_keyword() (word-boundary) on top, so stem hits
like 'Nursery' get dropped again before reaching the LLM.
"""
import re

import requests

LISTING_URL = ("https://ewqg.fa.us8.oraclecloud.com/fscmUI/redwood/negotiation-abstracts/"
               "view/abstractlisting?prcBuId=300000005255687&ojSpLang=en")
# Two anonymous REST prefixes, used for different things (confirmed 2026-09-18):
#  - resources/latest: versionless, so it won't rotate on Oracle upgrades -
#    used for the plain paged fetch. BUT its `q=` filter silently returns 0
#    rows for LIKE and 400s on upper()/UPPER(), so it can't do title search.
#  - the Redwood UI's own pinned rv:<uuid>/en/<ver> prefix: `q=upper(...)
#    like` works here (15 hits for NURS in testing). Will rotate on a
#    Fusion upgrade -> search_title() 404s/400s -> caught and logged in
#    fetch_everything_relevant(); the open set from fetch_all() still flows.
#    To refresh: open LISTING_URL with devtools -> Network, filter
#    "supplierNegotiationAbstracts", copy the new /rest/rv:.../ prefix here.
API_BASE = "https://ewqg.fa.us8.oraclecloud.com/fscmRestApi/resources/latest"
API_BASE_PINNED = ("https://ewqg.fa.us8.oraclecloud.com/fscmRestApi/rest/"
                   "rv:cfdb84a6-7cd9-4f59-ac64-1ef8545579f2/en/11.13.18.05:9")
PROCUREMENT_BU_ID = "300000005255687"  # 34-PROC OA DIVISION OF PURCHASING (the statewide board)

_FIELDS = ",".join([
    "AttachmentsCount", "AuctionHeaderId", "BuyerName", "CloseDate", "Negotiation",
    "NegotiationStatus", "NegotiationStatusCode", "NegotiationTitle", "NegotiationType",
    "OpenDate", "PostingDate", "ProcurementBUId", "ProcurementBUName", "PublishDate", "Synopsis",
])
_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
PAGE_SIZE = 100
MAX_PAGES = 20  # hard stop well above the ~5 pages seen at build time


def fetch_all(session=None):
    """Every abstract on the board, all statuses. Returns the pipeline's usual
    row dicts plus a 'description' (the Synopsis, which often carries the
    real requesting department - 'Issued on behalf of: Department of Mental
    Health' - since the BU name is just the central purchasing division)."""
    s = session or requests.Session()
    items = []
    for page_num in range(MAX_PAGES):
        params = {
            "finder": f"RowFinderByBU;ProcurementBUId={PROCUREMENT_BU_ID}",
            "orderBy": "PostingDate:desc",
            "fields": _FIELDS,
            "limit": PAGE_SIZE,
            "offset": page_num * PAGE_SIZE,
        }
        r = s.get(f"{API_BASE}/supplierNegotiationAbstracts", params=params,
                  headers=_HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("hasMore"):
            break

    return _to_rows(items)


# Title stems searched server-side (upper-cased LIKE), chosen to cover
# run_all.KEYWORDS while reaching past the 500-row cap - see module docstring.
TITLE_STEMS = ["NURS", "STAFF", "CNA", "LPN", "CLINIC", "LONG TERM CARE", "LONG-TERM CARE",
               "VETERANS HOME", "HOSPITAL"]


def search_title(stem, session=None, limit=200):
    """All abstracts (any status, any age) whose title contains `stem`,
    case-insensitively. One simple condition per call is all the API takes."""
    s = session or requests.Session()
    params = {
        "finder": f"RowFinderByBU;ProcurementBUId={PROCUREMENT_BU_ID}",
        "q": f"upper(NegotiationTitle) like '%{stem.upper()}%'",
        "orderBy": "PostingDate:desc",
        "fields": _FIELDS,
        "limit": limit,
    }
    r = s.get(f"{API_BASE_PINNED}/supplierNegotiationAbstracts", params=params,
              headers=_HEADERS, timeout=45)
    r.raise_for_status()
    return _to_rows(r.json().get("items", []))


def fetch_everything_relevant(session=None):
    """fetch_all() + search_title() over TITLE_STEMS, merged by ref. This is
    what run_all.py calls; it still applies filter_keyword() afterwards."""
    s = session or requests.Session()
    by_ref = {}
    for r in fetch_all(s):
        by_ref.setdefault(r["ref"] or r["title"], r)
    for stem in TITLE_STEMS:
        try:
            for r in search_title(stem, s):
                by_ref.setdefault(r["ref"] or r["title"], r)
        except requests.RequestException as e:
            print(f"  [movers_mo] title search for {stem!r} failed: {type(e).__name__}: {e}")
    return list(by_ref.values())


def _to_rows(items):
    results = []
    for p in items:
        synopsis = (p.get("Synopsis") or "").strip()
        # Prefer the requesting department named in the synopsis over the
        # central purchasing BU, when present - it's the real "agency".
        m = re.search(r"(?:issued\s+on\s+behalf\s+of|on\s+behalf\s+of)\s*:?\s*(?:the\s+)?([^\n.]+)",
                      synopsis, re.IGNORECASE)
        org = m.group(1).strip().rstrip(",") if m else (p.get("ProcurementBUName") or "")
        results.append({
            "ref": p.get("Negotiation", ""),
            "title": p.get("NegotiationTitle", "") or "",
            "org": org,
            "bu": p.get("ProcurementBUName", ""),
            "type": p.get("NegotiationType", ""),
            "status": p.get("NegotiationStatus", "") or p.get("NegotiationStatusCode", ""),
            "close_date": (p.get("CloseDate") or "").replace("T", " ")[:16],
            "posted": p.get("PostingDate", "") or "",
            "attachments": p.get("AttachmentsCount", 0),
            "description": synopsis,
            # The Redwood detail view is session-state routed (no id in the
            # URL), so the public listing is the best stable deep link -
            # same situation as Wisconsin/MAGIC. `ref` in Notes is what a
            # human searches for on that page.
            "link": LISTING_URL,
        })
    return results


def filter_keyword(rows, keyword):
    """Word-boundary match on title + synopsis, not substring - 'nursing' must
    not match 'Nursery' (MDC's tree nursery posts are the top false
    positives on this board), and a bare 'cna'/'lpn' must not match inside
    longer words. Same approach as adapters/bonfire.py."""
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows
            if pattern.search(r["title"]) or pattern.search(r.get("description", ""))]
