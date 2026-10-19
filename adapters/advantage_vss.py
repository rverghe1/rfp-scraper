"""Shared adapter logic for CGI Advantage VSS (Vendor Self Service) - the
state eProcurement platform used (at least) by Michigan (SIGMA VSS),
Kentucky (Kentucky VSS), and Colorado (CGI Advantage VSS). All three expose
the exact same "View Published Solicitations" Angular grid with identical
data-qa-id selectors - confirmed live against all three (2026-10-19) - so
the per-state adapters (michigan.py, kentucky_vss.py, colorado_vss.py) are
thin wrappers around this one `search()`, same pattern as adapters/ivalua.py
for the Ivalua-platform states.

Fully public "View Published Solicitations" search, no login required
despite Mike's docx listing a vendor credential for each of these - same
as Michigan's existing note. Needs core.browser.new_page(stealth=True) -
plain headless Playwright gets a WAF block page on at least Michigan's
instance; stealth clears it the same way it does for BidNet Direct.

Row data uses stable, predictable data-qa-id selectors
(vss.page.VVSSX10019.gridView1.group1.cardGrid.grid1.rows.N.*) rather than
text parsing - this grid renders real per-row elements (unlike MAGIC's
single-blob WebDynpro grid), just not as plain <tr>/<td> HTML.
"""
_ROW_PREFIX = "vss.page.VVSSX10019.gridView1.group1.cardGrid.grid1.rows"


def search(page, base_url, keyword):
    page.goto(base_url, timeout=30000)
    page.wait_for_timeout(2500)
    heading = page.get_by_text("View Published Solicitations", exact=False).first
    heading.evaluate_handle("e => e.parentElement").as_element().click()
    page.wait_for_timeout(3000)

    kw_input = page.query_selector(
        "[data-qa-id='vss.page.VVSSX10019.gridView1.group1.cardSearch.search1.QRY_SRCH_STRING']"
    )
    kw_input.fill(keyword)
    page.query_selector(
        "[data-qa-id='vss.page.VVSSX10019.gridView1.group1.cardSearch.searchActions.search.button']"
    ).click()
    page.wait_for_timeout(3000)

    results = []
    i = 1
    while True:
        desc_el = page.query_selector(f"[data-qa-id='{_ROW_PREFIX}.{i}.DOC_DSCR']")
        if desc_el is None:
            break
        dept_el = page.query_selector(f"[data-qa-id='{_ROW_PREFIX}.{i}.DeptBuyr.DEPT_NM']")
        ref_el = page.query_selector(f"[data-qa-id='{_ROW_PREFIX}.{i}.solNumTypCat.DOC_REF']")
        close_el = page.query_selector(
            f"[data-qa-id='{_ROW_PREFIX}.{i}.closeDateTimeSta.SO_CLSNG_DT_TM.readOnlyDate']"
        )
        status_el = page.query_selector(f"[data-qa-id='{_ROW_PREFIX}.{i}.closeDateTimeSta.SO_STA']")
        results.append({
            "title": desc_el.inner_text().strip(),
            "org": dept_el.inner_text().strip() if dept_el else "",
            "ref": ref_el.inner_text().strip() if ref_el else "",
            "close_date": close_el.inner_text().strip() if close_el else "",
            "status": status_el.inner_text().strip() if status_el else "",
            # No direct per-solicitation URL found - the row's number opens
            # an in-page detail view, not a real link. Point at the search
            # page itself as the best available reference.
            "link": base_url,
        })
        i += 1
    return results
