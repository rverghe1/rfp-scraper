"""Adapter for Bonfire / Euna Supplier Network (vendor.bonfirehub.com).

Login is centralized: one account works across every org's *.bonfirehub.com
portal. Credential comes from the 'Bonfire Hub' row in secrets.json (the
'Bonfire (NM DPS)' row's saved password is a doc error - it's literally the
email address, not a real password - so don't use that one).

Design note: earlier versions of this adapter tried to read the results
table's DOM (or drive its live search box) and hit two dead ends:
  1. Multiple tab panels (Open/Past/My Opportunities/Public Contracts) keep
     tables in the DOM simultaneously; picking the "active" one reliably
     needed Bootstrap's own '.tab-pane.active' class, not generic visibility
     checks.
  2. Even with that fixed, DOM-polling for "table has stopped changing" could
     converge on 0 rows *during* the gap before the tab's AJAX call had even
     resolved - a false negative that would silently under-report results.
  Root cause: each tab is backed by a plain JSON endpoint
  (getOpenPublicOpportunitiesSectionData / getPastPublicOpportunitiesSectionData)
  returning every project in one response, no pagination. Intercepting that
  response directly sidesteps all DOM-timing guesswork entirely.
"""
from core.secrets_loader import get_portal

LOGIN_URL = "https://vendor.bonfirehub.com/"

_STATUS_NAMES = {"1": "Draft", "2": "Scheduled", "3": "Cancelled",
                  "4": "Awarded", "5": "Closed", "6": "Open"}


def login(page, cred=None):
    if cred is None:
        cred = get_portal("Bonfire Hub")[0]
    page.goto(LOGIN_URL, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.click("text=Log In")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.fill("input[name=email]", cred["Username"])
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    page.fill("input[name=password]", cred["Password"])
    page.click("button:has-text('Log In')")
    page.wait_for_timeout(3000)
    if "dashboard" not in page.url:
        raise RuntimeError(f"Bonfire login did not reach dashboard, ended at {page.url}")


def discover_org_portals(page):
    """Returns {org_name: base_url} from the logged-in dashboard's My Network list."""
    page.goto("https://vendor.bonfirehub.com/dashboard", timeout=30000)
    page.wait_for_timeout(2000)
    orgs = {}
    for link in page.query_selector_all("a"):
        href = link.get_attribute("href") or ""
        txt = link.inner_text().strip()
        if href.endswith(".bonfirehub.com") and txt:
            orgs[txt] = href
    return orgs


def fetch_all(page, org_base_url, tab="open"):
    """Loads the full (unfiltered) Open or Past opportunities list for one org
    by intercepting the section-data JSON call the tab click fires."""
    endpoint_fragment = ("getPastPublicOpportunitiesSectionData" if tab == "past"
                          else "getOpenPublicOpportunitiesSectionData")

    try:
        if tab == "open":
            # Open is the default tab shown on initial load, so its section-data
            # request fires as part of the page load itself - wrap the goto.
            with page.expect_response(lambda r: endpoint_fragment in r.url, timeout=20000) as resp_info:
                page.goto(f"{org_base_url}/portal", timeout=30000)
        else:
            page.goto(f"{org_base_url}/portal", timeout=30000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            with page.expect_response(lambda r: endpoint_fragment in r.url, timeout=20000) as resp_info:
                tab_el = page.query_selector("text=Past Public Opportunities")
                if tab_el:
                    tab_el.click()
        data = resp_info.value.json()
    except Exception:
        return []

    projects = (data.get("payload") or {}).get("projects") or {}
    results = []
    for p in projects.values():
        status_id = p.get("ProjectStatusID", "")
        results.append({
            "title": p.get("ProjectName", ""),
            "ref": p.get("ReferenceID", ""),
            "close_date": p.get("DateClose", ""),
            "status": _STATUS_NAMES.get(status_id, status_id),
            "link": f"{org_base_url}/opportunities/{p.get('ProjectID', '')}",
        })
    return results


def filter_keyword(rows, keyword):
    """Word-boundary match, not substring - a bare 'rn' as a substring matches
    inside 'Warning', 'Furness', 'Iron', etc. and produced heavy false
    positives in an earlier version of this sweep."""
    import re
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return [r for r in rows if pattern.search(r["title"]) or pattern.search(r["ref"])]
