"""One-time (or periodic) backfill: screens every row currently in
"Scraper Results" for actual healthcare/nursing/clinical staffing relevance
(core/relevance_screen.py) and pushes the ones that pass into
"Scraper Results (Healthcare Only)" (core/sheets.DASHBOARD_SHEET_NAME), the
tab the dashboard reads.

Safe to re-run: append_dashboard_rows upserts on (Portal, Title, Agency), so
rows already screened and pushed just get refreshed in place, not
duplicated. Going forward, run_all.py's per-run pipeline screens new rows
automatically (see _classify_and_route) - this script exists to catch up
the backlog that predates that, and as a manual re-screen if the screening
prompt itself changes later.
"""
import sys

import gspread
from google.oauth2.service_account import Credentials

from core.relevance_screen import screen_healthcare_staffing
from core.sheets import SERVICE_ACCOUNT_KEY, SCOPES, SPREADSHEET_ID, RESULTS_SHEET_NAME, append_dashboard_rows


def load_results_rows():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SPREADSHEET_ID).worksheet(RESULTS_SHEET_NAME)
    values = ws.get_all_values()
    if not values:
        return []
    header, *data_rows = values
    return [dict(zip(header, row)) for row in data_rows]


def main():
    rows = load_results_rows()
    print(f"Loaded {len(rows)} row(s) from '{RESULTS_SHEET_NAME}'.")

    passed = []
    errors = 0
    for i, r in enumerate(rows, start=1):
        title = r.get("Title", "")
        try:
            result = screen_healthcare_staffing(
                title=title, agency=r.get("Agency", ""), description=r.get("Notes", ""),
            )
            if result.is_healthcare_staffing:
                passed.append(r)
        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(rows)}] SCREEN FAILED ({type(e).__name__}): {title!r} - excluding")
            continue
        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] screened, {len(passed)} passed so far"
                  + (f", {errors} error(s)" if errors else ""))

    print(f"\n{len(passed)}/{len(rows)} row(s) passed the healthcare-staffing screen "
          f"({errors} screening error(s), excluded as a precaution).")

    if not passed:
        print("Nothing to push.")
        return

    n = append_dashboard_rows(passed)
    print(f"Wrote {n} new row(s) to the dashboard tab (existing matches refreshed in place).")


if __name__ == "__main__":
    sys.exit(main())
