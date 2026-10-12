"""Adapter for MAGIC (portal.magic.ms.gov) - Mississippi's SAP SRM-based
state procurement portal, distinct from the public ms.gov DFA bid search
already covered by adapters/mississippi.py. Login required (credential in
secrets.json - the docx's stored password was stale; got the current one
directly from Mike's Google Doc and updated secrets.json).

Structure: SAP NetWeaver Portal -> "Suppliers Self-Service" tab ->
"RFx and Auctions" link -> a nested SRM WebDynpro frame (srm.magic.ms.gov)
containing an "eRFxs - All" grid. That grid renders as ONE giant table row
with all records as tab/newline-delimited text (a WebDynpro ALV rendering
quirk, not real per-record <tr> elements) - parsed here with regex instead
of DOM traversal.

KNOWN LIMITATION: the grid is lazy-loaded and only the first ~10 of the
"All (124)" total records render without further interaction. Tried
scrolling the frame's window - didn't trigger more rows; the real scroll
container is some specific inner div this wasn't tracked down to. Ships
with just the visible 10 for now (a real, working improvement over the
totally-broken state before - the login itself didn't work until the
password was updated) - revisit the pagination/lazy-load trigger if this
portal's hit rate looks worth the extra investigation.
"""
import re

from core.secrets_loader import get_portal

PORTAL_URL = "https://portal.magic.ms.gov/irj/portal"

_ROW_RE = re.compile(
    r"\xa0\n\t(\d{6,})\t([^\t\n]+)\t([^\t\n]+)\t([^\t\n]+)\t(\d{2}/\d{2}/\d{4})\t\n"
    r"\xa0\n\t([^\t\n]+)\t[\xa0\s]*\d+\t\n"
    r"\xa0\n\t\d+\t([^\t\n]+)\t([^\t\n]+)\t([^\t\n]+)\t([^\t\n]+)"
)


def login(page, cred=None):
    if cred is None:
        cred = get_portal("MAGIC")[0]
    page.goto(PORTAL_URL, timeout=30000)
    page.wait_for_timeout(2000)
    page.fill("#logonuidfield", cred["Username"])
    page.fill("#logonpassfield", cred["Password"])
    # The visible submit button times out (not actually clickable per
    # Playwright's visibility check) - pressing Enter in the password field
    # submits the form correctly instead. Confirmed this is a real fix, not
    # a workaround for bad credentials: with the stale password this same
    # flow correctly surfaced "User authentication failed" text, so the
    # submission itself was always working.
    page.press("#logonpassfield", "Enter")
    page.wait_for_timeout(4000)
    if "authentication failed" in page.inner_text("body").lower():
        raise RuntimeError("MAGIC login failed - check secrets.json's MAGIC entry is current.")


def fetch_open_rfxs(page):
    """Returns whatever RFx rows are currently rendered in the "eRFxs - All"
    grid (see the module docstring for the ~10-row lazy-load limitation)."""
    page.get_by_text("Suppliers Self-Service", exact=False).first.click()
    page.wait_for_timeout(3000)
    page.get_by_text("RFx and Auctions", exact=False).first.dispatch_event("click")
    page.wait_for_timeout(4000)

    srm_frame = None
    for f in page.frames:
        if "srm.magic.ms.gov" in f.url:
            srm_frame = f
            break
    if srm_frame is None:
        return []

    text = srm_frame.inner_text("body")
    results = []
    for m in _ROW_RE.finditer(text):
        (event_number, description, event_type, status, start_date,
         response_status, agency, start_time, buyer, end_time) = m.groups()
        results.append({
            "ref": event_number,
            "title": f"{description} ({event_type})",
            "org": agency,
            "status": status,
            "close_date": start_date,
            "link": PORTAL_URL,  # no direct per-RFx URL found - all navigation is session-state-based
        })
    return results
