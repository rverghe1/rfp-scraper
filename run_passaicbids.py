"""Sweeps PassaicBids for nursing/staffing RFPs across all statuses (current,
pending, awarded, cancelled/rejected), matches against the 314-facility list,
writes to the shared Sheet (local CSV backup first)."""
import csv
import datetime
from pathlib import Path
from core.browser import new_page
from core.matcher import load_facilities, match_facility
from core.sheets import append_rows, HEADER
from adapters import passaicbids

KEYWORDS = ["nursing", "staffing", "CNA", "LPN"]


def main():
    facilities = load_facilities()
    all_results = []

    with new_page(headless=True) as page:
        for status in passaicbids.STATUSES:
            for kw in KEYWORDS:
                rows = passaicbids.search(page, kw, status=status)
                if rows:
                    print(f"[{status}] '{kw}': {len(rows)} hit(s)")
                all_results.extend(rows)

    seen = set()
    deduped = []
    for r in all_results:
        key = r["ref"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    print(f"\n{len(deduped)} unique postings found.")

    sheet_rows = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for r in deduped:
        fac = match_facility(r["title"], "Passaic County", facilities)
        sheet_rows.append({
            "Portal": "PassaicBids",
            "Facility Match": fac["NAME"] if fac else "",
            "Bucket": "REVIEW" if fac else "NO FACILITY MATCH",
            "Title": r["title"],
            "Agency": "Passaic County, NJ",
            "State": fac["STATE"] if fac else "NJ",
            "Status": r["status"],
            "Closing Date": r["close_date"],
            "Link": r["link"],
            "Notes": f"ref={r['ref']} posted={r['posted']}",
            "Scraped At": now,
        })

    if not sheet_rows:
        print("Nothing to write.")
        return

    out_path = Path(__file__).parent / "output" / f"passaicbids_{now.replace(':', '')}.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(sheet_rows)
    print(f"Saved local backup CSV: {out_path}")

    try:
        n = append_rows(sheet_rows)
        print(f"Wrote {n} rows to Google Sheet.")
    except Exception as e:
        print(f"Could not write to Google Sheet ({type(e).__name__}: {e}).")


if __name__ == "__main__":
    main()
