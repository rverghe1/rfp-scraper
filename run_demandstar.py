"""Sweeps DemandStar for nursing/staffing RFPs, matches against the 314-facility
county-owned SNF list, and writes results to the shared Google Sheet (with a
local CSV safety-net backup)."""
import csv
import datetime
from pathlib import Path
from core.browser import new_page
from core.matcher import load_facilities, match_facility
from core.sheets import append_rows, HEADER
from adapters import demandstar

KEYWORDS = ["nursing", "staffing", "CNA", "LPN", "skilled nursing", "long term care"]


def main():
    facilities = load_facilities()
    all_results = []

    with new_page(headless=True) as page:
        demandstar.login(page)
        for kw in KEYWORDS:
            try:
                rows = demandstar.search(page, kw)
            except Exception as e:
                print(f"ERROR keyword={kw}: {e}")
                continue
            for r in rows:
                r["_keyword"] = kw
            all_results.extend(rows)
            print(f"'{kw}': {len(rows)} hit(s)")

    seen = set()
    deduped = []
    for r in all_results:
        key = r["link"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    print(f"\n{len(deduped)} unique postings found.")

    sheet_rows = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for r in deduped:
        fac = match_facility(r["title"], r["org"], facilities)
        sheet_rows.append({
            "Portal": "DemandStar",
            "Facility Match": fac["NAME"] if fac else "",
            "Bucket": "REVIEW" if fac else "NO FACILITY MATCH",
            "Title": r["title"],
            "Agency": r["org"],
            "State": fac["STATE"] if fac else "",
            "Status": r["status"],
            "Closing Date": r["close_date"],
            "Link": r["link"],
            "Notes": f"matched keyword='{r['_keyword']}'",
            "Scraped At": now,
        })

    if not sheet_rows:
        print("Nothing to write.")
        return

    out_path = Path(__file__).parent / "output" / f"demandstar_{now.replace(':', '')}.csv"
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
