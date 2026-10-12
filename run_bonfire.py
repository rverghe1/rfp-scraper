"""Sweeps Bonfire (Euna Supplier Network) org portals for nursing/staffing RFPs,
matches hits against the 314-facility county-owned SNF list, and writes results
to the shared Google Sheet (with a local CSV safety-net backup).

Fetches each org's Open + Past opportunity list once (via direct JSON API
interception - see adapters/bonfire.py) and filters locally across all
keywords, rather than one live search per keyword.
"""
import csv
import datetime
from pathlib import Path
from core.browser import new_page
from core.matcher import load_facilities, match_facility
from core.sheets import append_rows, HEADER
from adapters import bonfire

KEYWORDS = ["nursing", "staffing", "cna", "lpn", "rn", "clinical"]


def main():
    facilities = load_facilities()
    all_results = []

    with new_page(headless=True) as page:
        bonfire.login(page)
        orgs = bonfire.discover_org_portals(page)
        print(f"Discovered {len(orgs)} connected org portals:")
        for name, url in orgs.items():
            print(f"  - {name}: {url}")

        for org_name, org_url in orgs.items():
            org_url = org_url.rstrip("/")
            for tab in ("open", "past"):
                rows = bonfire.fetch_all(page, org_url, tab=tab)
                if not rows:
                    print(f"  {org_name} [{tab}]: 0 total (or fetch failed)")
                    continue
                hits = []
                seen_refs = set()
                for kw in KEYWORDS:
                    for r in bonfire.filter_keyword(rows, kw):
                        key = r["ref"] or r["title"]
                        if key in seen_refs:
                            continue
                        seen_refs.add(key)
                        r2 = dict(r)
                        r2["_org"] = org_name
                        r2["_tab"] = tab
                        hits.append(r2)
                print(f"  {org_name} [{tab}]: {len(rows)} total, {len(hits)} keyword hit(s)")
                all_results.extend(hits)

    print(f"\n{len(all_results)} unique postings found across all orgs/tabs.")

    sheet_rows = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for r in all_results:
        fac = match_facility(r["title"], r["_org"], facilities)
        sheet_rows.append({
            "Portal": "Bonfire",
            "Facility Match": fac["NAME"] if fac else "",
            "Bucket": "REVIEW" if fac else "NO FACILITY MATCH",
            "Title": r["title"],
            "Agency": r["_org"],
            "State": fac["STATE"] if fac else "",
            "Status": r["status"],
            "Closing Date": r["close_date"],
            "Link": r["link"],
            "Notes": f"tab={r['_tab']} ref={r['ref']}",
            "Scraped At": now,
        })

    if not sheet_rows:
        print("Nothing to write.")
        return

    out_path = Path(__file__).parent / "output" / f"bonfire_{now.replace(':', '')}.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(sheet_rows)
    print(f"Saved local backup CSV: {out_path}")

    try:
        n = append_rows(sheet_rows)
        print(f"Wrote {n} rows to Google Sheet.")
    except Exception as e:
        print(f"Could not write to Google Sheet ({type(e).__name__}: {e}). "
              f"Data is safe in {out_path} - rerun push_pending.py once sheet access is fixed.")


if __name__ == "__main__":
    main()
