"""Adapter for Utah's statewide procurement portal (Jaggaer/SciQuest-based,
bids.sciquest.com) - fully public, no login required despite Mike's docx
not having a Utah credential at all. CustomerOrg=StateOfUtah covers state
agencies and participating political subdivisions.
"""
import re

BASE_URL = "https://bids.sciquest.com/apps/Router/PublicEvent"
SEARCH_URL = f"{BASE_URL}?CustomerOrg=StateOfUtah"

_CLOSE_RE = re.compile(r"Close\s*\n?\s*\n?\s*(.+?)\n", re.DOTALL)
_NUMBER_RE = re.compile(r"Number\s*\n?\s*\n?\s*(.+?)\n", re.DOTALL)
_CONTACT_RE = re.compile(r"Contact\s*\n?\s*\n?\s*(.+?)\n", re.DOTALL)


def search(page, keyword):
    page.goto(SEARCH_URL, timeout=30000)
    page.wait_for_timeout(2000)
    page.fill("#SimpleSearch_Keyword", keyword)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)

    results = []
    tables = page.query_selector_all("table")
    if not tables:
        return results
    for r in tables[0].query_selector_all("tbody tr"):
        cells = r.query_selector_all("td")
        if len(cells) < 2:
            continue
        status_cell, detail_cell = cells[0], cells[1]
        status_text = status_cell.inner_text().strip()
        # The "no results" empty-state UI renders as a fake row with junk
        # cell text like "?    X" (a dismiss/help icon) instead of a real
        # status - confirmed happening in testing on a keyword with 0 hits.
        # A real row's status is always one of a known small set of words.
        if status_text not in ("Open", "Closed", "Awarded", "Cancelled", "Draft"):
            continue
        text = detail_cell.inner_text()
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            continue
        title = lines[0].strip()
        # Second non-empty line is "Agency <description...>" run together -
        # keep it whole as a description field since we can't reliably
        # split agency name from description text.
        description = lines[1].strip() if len(lines) > 1 else ""

        close_m = _CLOSE_RE.search(text)
        number_m = _NUMBER_RE.search(text)
        contact_m = _CONTACT_RE.search(text)
        link_el = r.query_selector("a")

        results.append({
            "title": title,
            "org": description,
            "ref": number_m.group(1).strip() if number_m else title,
            "close_date": close_m.group(1).strip() if close_m else "",
            "status": status_text,
            "contact": contact_m.group(1).strip() if contact_m else "",
            "link": link_el.get_attribute("href") if link_el else SEARCH_URL,
        })
    return results
