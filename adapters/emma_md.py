"""Adapter for eMaryland Marketplace Advantage (emma.maryland.gov) -
Maryland's statewide procurement system, running on the Ivalua platform
(same family as NJSTART/BidBuy/COMMBUYS/OregonBuys) but a different, richer
skin - a live ASP.NET-style postback grid, not the plain HTML table the
shared adapters/ivalua.py logic expects. So this gets its own adapter
rather than wrapping ivalua.py.

Login IS required here, unlike the other four Ivalua states: hitting the
"Public Solicitations" page unauthenticated throws a reCAPTCHA "browser
check" that blocks Playwright outright. An authenticated session skips it
entirely (confirmed against a real logged-in session's page source), so
login() runs first, using the MD credential in secrets.json. Once logged
in, the Status filter still defaults to "Open" on this page - exactly the
scope this scraper wants (open-only; awarded/closed tracking was dropped
from the pipeline).

Row shape confirmed from live DOM (table#body_x_grid_grd tbody tr): cells
are icon | ID | Title (link) | Status | Due/Close Date | Publish Date |
Main Category | Solicitation Type | Issuing Agency - i.e. cells[1]=ref,
cells[2]=title+link, cells[3]=status, cells[4]=close_date, cells[8]=org.
"""
from core.secrets_loader import get_portal

BASE_URL = "https://emma.maryland.gov"
LOGIN_URL = f"{BASE_URL}/page.aspx/en/usr/login"
SEARCH_URL = f"{BASE_URL}/page.aspx/en/rfp/request_browse_public"


def login(page, cred=None):
    if cred is None:
        cred = get_portal("eMMA")[0]
    page.goto(LOGIN_URL, timeout=30000)
    page.wait_for_timeout(2000)
    page.fill("#body_x_txtLogin", cred["Username"])
    page.fill("#body_x_txtPass", cred["Password"])
    page.click("#body_x_btnLogin")
    page.wait_for_timeout(3000)


def search(page, keyword):
    page.goto(SEARCH_URL, timeout=30000)
    page.wait_for_timeout(3000)

    page.fill("#body_x_txtBpmCodeCalculated_3", keyword)
    page.click("#body_x_prxFilterBar_x_cmdSearchBtn")
    page.wait_for_timeout(4000)

    results = []
    for row in page.query_selector_all("#body_x_grid_grd tbody tr"):
        cells = row.query_selector_all("td")
        if len(cells) < 9:
            continue
        ref = cells[1].inner_text().strip()
        title_link = cells[2].query_selector("a")
        title = title_link.inner_text().strip() if title_link else cells[2].inner_text().strip()
        href = title_link.get_attribute("href") if title_link else ""
        results.append({
            "ref": ref,
            "title": title,
            "status": cells[3].inner_text().strip(),
            "close_date": cells[4].inner_text().strip(),
            "org": cells[8].inner_text().strip(),
            "link": BASE_URL + href if href.startswith("/") else href,
        })
    return results
