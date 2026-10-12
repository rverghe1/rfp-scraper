"""Adapter for the MissouriBUYS "Bid Locator Tool" - the State of Missouri's
branded InstantMarkets widget (instantmarkets.com/widget/<state account id>),
a search engine that crawls Missouri public-sector sites: state agencies,
CITIES and COUNTIES, school districts, higher ed, and federal postings sited
in Missouri (VA medical centers, DoD). Free, no login (missouribuys.mo.gov:
"no charge to vendors", the widget renders results anonymously).

Why this exists alongside adapters/movers_mo.py: MOVERS is the STATE board.
All 17 Missouri facilities in data/facilities.csv are county-owned nursing
homes / nursing home districts, which procure through their counties and
never post to the state board - the Bid Locator is the only consolidated
place their solicitations show up (its filters list Boone, Buchanan,
Camden, Cape Girardeau... counties as sources). It also picks up Missouri
VA medical centers via SAM.gov, which the go/no-go framework flags as a GO
signal and nothing else in this pipeline covered.

How it's read: the widget is an Angular SPA calling
POST api.instantmarkets.com/api/v2/opp/searchOnWeb2 (anonymous, but the
API rejects the same request replayed outside the browser with
401 "Unauthorized request source" - it checks request provenance, not a
login). So this adapter navigates the real widget URL per keyword/page
with Playwright and captures that XHR's JSON response instead of parsing
the rendered cards - same intercept-the-data-call pattern as
adapters/bonfire.py, no DOM-timing guesswork. 10 results per page,
paginated via the URL's `pg=` param; response carries maxPageCount and
totalHits.

Status filter: `os=Active` by default. All-statuses ("") works too, but
the Locator's archive is dominated by unrelated federal/municipal
volume, and the awarded-history tracker tab is currently disabled
(see run_all._classify_and_route), so paying LLM calls for closed rows
here isn't worth it yet - flip to "" if that tab comes back.
"""
import datetime
from urllib.parse import quote

WIDGET_ACCOUNT = "8756e79e-eb39-441a-9e25-08d8638a4e92"  # "State of Missouri" widget
WIDGET_BASE = f"https://www.instantmarkets.com/widget/{WIDGET_ACCOUNT}/q/"
MAX_PAGES = 5  # 10 results/page


def _widget_url(keyword, page_num, status):
    return (f"{WIDGET_BASE}{quote(keyword)}?ss=Missouri&c=US&pg={page_num}"
            f"&sort=im_doc_posttimestamp&os={status}")


def _capture_search(page, url, timeout_ms=25000):
    """Navigates to a widget URL and returns the parsed searchOnWeb2 JSON the
    page fires, or None if it never arrived."""
    out = {}

    def on_resp(r):
        if "searchOnWeb2" in r.url and "data" not in out:
            try:
                out["data"] = r.json()
            except Exception:
                pass

    page.on("response", on_resp)
    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        waited = 0
        while "data" not in out and waited < timeout_ms:
            page.wait_for_timeout(500)
            waited += 500
    finally:
        page.remove_listener("response", on_resp)
    return out.get("data")


def _field(opp, name):
    for f in opp.get("additionalFields") or []:
        if f.get("name") == name and f.get("value"):
            return f["value"][0]
    return ""


def _close_date(opp):
    # events[] carries real epoch-ms dates; formattedResponsedate is a
    # human string ("Oct 01, 2026 (in 13 days)") - prefer the epoch.
    for ev in opp.get("events") or []:
        if ev.get("type") == "Close Date" and ev.get("date"):
            try:
                return datetime.datetime.utcfromtimestamp(ev["date"] / 1000).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OverflowError):
                pass
    fr = opp.get("formattedResponsedate") or ""
    return fr.split(" (")[0].strip() if fr else _field(opp, "dueDate")


def search(page, keyword, status="Active", max_pages=MAX_PAGES):
    results = []
    for page_num in range(1, max_pages + 1):
        data = _capture_search(page, _widget_url(keyword, page_num, status))
        if not data:
            break
        resp = data.get("response") or {}
        opps = resp.get("opps") or []
        for o in opps:
            loc = (o.get("locations") or [{}])[0] or {}
            agency = _field(o, "agency") or o.get("agency") or o.get("name") or ""
            results.append({
                "ref": _field(o, "solicitationNumber") or o.get("docID", ""),
                "title": o.get("title", "") or "",
                "org": agency,
                "customer_type": o.get("customerType", ""),  # State / County / City / Federal / ...
                "city": loc.get("city", "") or "",
                "status": o.get("soltype", "") or "",  # "Bid Notification" / "Pre-Bid Notification"
                "close_date": _close_date(o),
                "description": _field(o, "summary") or "",
                # linkURL is the source agency's own page for the posting -
                # a real per-posting deep link, unlike MOVERS/Wisconsin.
                "link": o.get("linkURL") or "",
            })
        if page_num >= int(resp.get("maxPageCount") or 1):
            break
    return results
