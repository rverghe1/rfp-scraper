"""Adapter for Arkansas Office of State Procurement's public bid board
(sas.arkansas.gov/procurement/bid-opportunities) - fully public DataTable,
no login required. Mike's docx lists an AASIS Vendor Portal credential
(vendor.ark.org), but that URL now redirects to a generic vendor-resources
page with no separate bid search of its own - this OSP page is the real,
current place Arkansas posts solicitations (confirmed 2026-10-19), with
real descriptions and direct per-posting links (SAP Ariba discovery URLs).

Small statewide board (12 rows at check time) - same low-volume pattern as
Edison.TN/NHProcure, fetched once and filtered by keyword client-side
(word-boundary match against title + description) since there's no
server-side search box, just a DataTables client filter.
"""
import re

BASE_URL = "https://sas.arkansas.gov/procurement/bid-opportunities/"


def fetch_all(page):
    page.goto(BASE_URL, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    page.select_option("select[name=table_1_length]", "All")
    page.wait_for_timeout(2000)

    results = []
    for row in page.query_selector_all("#table_1 tbody tr"):
        cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
        if len(cells) < 7:
            continue
        link_el = row.query_selector("a")
        results.append({
            "status": cells[0],
            "title": cells[1],
            "description": cells[2],
            "pub_date": cells[3],
            "close_date": cells[4],
            "org": cells[5],
            "link": link_el.get_attribute("href") if link_el else BASE_URL,
        })
    return results


def search(page, keyword):
    rows = fetch_all(page)
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows if pattern.search(r["title"]) or pattern.search(r["description"])]
