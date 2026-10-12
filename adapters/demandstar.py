"""Adapter for DemandStar (network.demandstar.com / www.demandstar.com)."""
import re
from core.secrets_loader import get_portal

LOGIN_URL = "https://www.demandstar.com/app/login"
BIDS_URL = "https://www.demandstar.com/app/suppliers/bids"

_CARD_JS = """
() => {
  const anchors = Array.from(document.querySelectorAll("a[href*='/bids/']"));
  return anchors.map(a => {
    let el = a;
    for (let i = 0; i < 8; i++) {
      if (el.parentElement) el = el.parentElement;
      if (el.innerText && el.innerText.includes('Broadcast:')) break;
    }
    return {title: a.innerText.trim(), href: a.getAttribute('href'), block: el.innerText};
  });
}
"""


def login(page, cred=None):
    if cred is None:
        cred = get_portal("DemandStar")[0]
    page.goto(LOGIN_URL, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.fill("#userName", cred["Username"])
    page.fill("#password", cred["Password"])
    page.click("button:has-text('Login')")
    page.wait_for_timeout(3000)
    if "dashboard" not in page.url:
        raise RuntimeError(f"DemandStar login did not reach dashboard, ended at {page.url}")


def _parse_block(block):
    org = ""
    m = re.search(r"\n(.+?)\n\nID:", block)
    if m:
        org = m.group(1).strip()
    due = ""
    m = re.search(r"Due:\s*(.+)", block)
    if m:
        due = m.group(1).strip()
    ref = ""
    m = re.search(r"ID:\s*(.+)", block)
    if m:
        ref = m.group(1).strip()
    status = "Active" if "Active" in block else ""
    return org, ref, due, status


def search(page, keyword, include_closed=False):
    page.goto(BIDS_URL, timeout=30000)
    page.wait_for_timeout(3000)
    field = page.query_selector("input[placeholder='Bid Name']")
    field.click()
    field.fill("")
    field.type(keyword, delay=30)
    if include_closed:
        # "Bid Status" filter defaults to Active only; leave as-is for now -
        # DemandStar's closed-bid history requires a separate flow per agency.
        pass
    page.click("button:has-text('Search')")
    page.wait_for_timeout(3000)

    cards = page.evaluate(_CARD_JS)
    results = []
    for c in cards:
        org, ref, due, status = _parse_block(c["block"])
        results.append({
            "title": c["title"],
            "org": org,
            "ref": ref,
            "close_date": due,
            "status": status,
            "link": "https://www.demandstar.com" + c["href"] if c["href"].startswith("/") else c["href"],
        })
    return results
