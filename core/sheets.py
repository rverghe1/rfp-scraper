"""Writes scrape results to the shared Google Sheet via a service account.

Two tabs, two different jobs:
  - "Scraper Results": open RFPs relevant to services we provide (nursing/
    clinical/hospital staffing), gated by the LLM go/no-go call - NOT by
    facility match. Facility Match is shown when it happens to match one of
    our 314 known facilities, but a real match to a *new* facility/hospital
    we don't have on file yet is just as valid a result here.
  - "Awarded-Historical RFPs": closed/awarded postings that DO match one of
    our 314 facilities and were relevant to our services - used to track
    when an existing contract might expire, to help predict the next RFP
    cycle at that facility. Not gated by the LLM (facility match + keyword
    relevance is the whole point of this tab), and it doesn't compete with
    the main tab for space.
"""
import os

import gspread
from google.oauth2.service_account import Credentials

# SERVICE_ACCOUNT_KEY_PATH env var lets this run in a container (Railway's
# entrypoint.sh writes the key there from a GOOGLE_SERVICE_ACCOUNT_JSON env
# var - see the Dockerfile/entrypoint.sh) without changing anything for the
# existing local setup, which never set that env var and keeps using the
# original hardcoded path.
SERVICE_ACCOUNT_KEY = os.environ.get(
    "SERVICE_ACCOUNT_KEY_PATH",
    "/Users/rohanverghese/Desktop/chow-scraper/service_account_key.json",
)
SPREADSHEET_ID = "1D9N2erN_JEk4WVjeneQOzN2U7mKDls7Sxabx-JOrSBE"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

RESULTS_SHEET_NAME = "Scraper Results"
TRACKER_SHEET_NAME = "Awarded-Historical RFPs"
TEST_PROMPT_SHEET_NAME = "Scraper Results (Test Prompt)"
# Healthcare-staffing-screened subset of RESULTS_SHEET_NAME (see
# core/relevance_screen.py) - what the dashboard reads, so postings that
# passed the go/no-go framework (including permissive ESCALATEs) but aren't
# actually clinical staffing don't show up there. Same schema/HEADER as the
# main tab; this is a filtered view, not a different kind of row.
DASHBOARD_SHEET_NAME = "Scraper Results (Healthcare Only)"

HEADER = [
    "Portal", "Facility", "Bucket", "Title", "Agency", "State",
    "Status", "Closing Date", "Link", "Notes", "Scraped At",
    "LLM Bucket", "LLM Reasoning", "LLM Disqualifier", "LLM Lifecycle",
]

TRACKER_HEADER = [
    "Portal", "Facility", "State", "Title", "Agency", "Status",
    "Award/Close Date", "Link", "Notes", "Scraped At",
    "LLM Bucket", "LLM Reasoning", "LLM Lifecycle",
    "Contract End Date (LLM extracted)",
    "Contract End Date (manual)", "Predicted Next RFP Window (manual)",
]


def get_client():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(sheet_name=RESULTS_SHEET_NAME, header=HEADER):
    gc = get_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=len(header))
        ws.append_row(header)
    return ws


_STATE_NAME_TO_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _normalize_cross_portal_key(title, state, closing_date):
    """Same real-world posting often gets scraped from more than one portal
    (confirmed: a Passaic County, NJ posting appeared on both BidNet Direct
    and PassaicBids with an identical title and closing date, but one
    portal wrote the state as "New Jersey" and the other as "NJ" - only
    Agency/Link/Portal/State-spelling differed). Normalized (lowercased,
    whitespace-collapsed) title + state-abbreviation + closing date is
    distinctive enough to catch that pattern without the false-positive
    risk of matching on title alone (several portals post genuinely
    different RFPs under generic titles like "Nurse Staffing" in different
    states)."""
    norm_title = " ".join((title or "").lower().split())
    state_clean = (state or "").strip().lower()
    norm_state = _STATE_NAME_TO_ABBREV.get(state_clean, state_clean)
    norm_date = (closing_date or "").strip().lower()
    return (norm_title, norm_state, norm_date)


def append_rows(rows, sheet_name=RESULTS_SHEET_NAME, header=HEADER, cross_portal_dedupe=False):
    """rows: list of dicts matching `header` keys (missing keys become '').
    Upserts on (Portal, Title, Agency): a row whose key already exists in
    the sheet gets its scraper/LLM-owned columns overwritten in place with
    the fresh data (status, LLM bucket/reasoning/lifecycle, etc. can change
    between runs - e.g. an RFP moving from open to awarded); a genuinely new
    key gets appended. Columns whose header name contains "(manual)" (the
    tracker tab's "Contract End Date (manual)" / "Predicted Next RFP Window
    (manual)") are NEVER touched by an update - the scraper doesn't
    populate them anyway, so a blind full-row overwrite would silently wipe
    out anything a human typed in there. Updates are sent as one batched
    call, not one API call per row, to avoid the Sheets rate limit hit
    earlier in this project from many individual writes.

    cross_portal_dedupe=True additionally checks title+state+closing-date
    against every existing row regardless of portal (see
    _normalize_cross_portal_key) - a match SKIPS the incoming row entirely
    rather than overwriting, so whichever portal's row already exists stays
    as the one source of truth instead of being clobbered by a later
    portal's version of the same posting. Only used for the dashboard tab
    (append_dashboard_rows) - the raw "Scraper Results" tab stays a complete
    per-portal log."""
    ws = get_or_create_worksheet(sheet_name, header=header)
    existing = ws.get_all_values()
    if not existing:
        ws.append_row(header)
        existing = [header]
    elif existing[0] != header and all(h in header for h in existing[0]):
        # Sheet predates a column we've since added - extend its header in
        # place. Only does this when every existing column name is still
        # present in `header` (i.e. we only ever appended new columns, never
        # renamed/removed one) - otherwise leave it alone rather than risk
        # misaligning old data.
        ws.update("A1", [header])
        existing[0] = header

    sheet_header = existing[0]
    try:
        pi = sheet_header.index("Portal")
        ti = sheet_header.index("Title")
        ai = sheet_header.index("Agency")
    except ValueError:
        pi = ti = ai = None

    si = sheet_header.index("State") if "State" in sheet_header else None
    ci = sheet_header.index("Closing Date") if "Closing Date" in sheet_header else None

    # key -> 1-indexed sheet row number, for targeting an update.
    key_to_row_num = {}
    cross_portal_keys = set()
    if pi is not None:
        for i, row in enumerate(existing[1:], start=2):
            key = (row[pi] if pi < len(row) else "",
                   row[ti] if ti < len(row) else "",
                   row[ai] if ai < len(row) else "")
            key_to_row_num[key] = i
            if cross_portal_dedupe and si is not None and ci is not None:
                cross_portal_keys.add(_normalize_cross_portal_key(
                    row[ti] if ti < len(row) else "",
                    row[si] if si < len(row) else "",
                    row[ci] if ci < len(row) else "",
                ))

    # Columns we never overwrite on update - manual/human-owned cells.
    manual_cols = {h for h in header if "(manual)" in h.lower()}

    new_row_values = []
    batch_updates = []  # [{"range": "A5:O5", "values": [[...]]}]
    seen_this_batch = set()
    cross_portal_seen_this_batch = set()
    updated_count = 0
    skipped_duplicates = []
    for r in rows:
        key = (r.get("Portal", ""), r.get("Title", ""), r.get("Agency", ""))
        if key in seen_this_batch:
            continue  # dedupe within this batch too, not just against the sheet
        seen_this_batch.add(key)

        if cross_portal_dedupe:
            xkey = _normalize_cross_portal_key(r.get("Title", ""), r.get("State", ""), r.get("Closing Date", ""))
            # Only treat it as a cross-portal duplicate if it's NOT the exact
            # same (Portal, Title, Agency) row we're about to update in
            # place below - that's a legitimate refresh, not a duplicate.
            is_known_row = key in key_to_row_num
            if not is_known_row and (xkey in cross_portal_keys or xkey in cross_portal_seen_this_batch):
                skipped_duplicates.append((r.get("Portal", ""), r.get("Title", "")))
                continue
            cross_portal_seen_this_batch.add(xkey)

        row_values = [r.get(h, "") for h in header]
        existing_row_num = key_to_row_num.get(key)
        if existing_row_num is None:
            new_row_values.append(row_values)
            continue

        # Update in place, but skip manual columns - overlay the fresh
        # scraper values onto whatever's already in the sheet for those
        # cells rather than blanking them.
        existing_row = existing[existing_row_num - 1] if existing_row_num - 1 < len(existing) else []
        merged = []
        for col_idx, h in enumerate(header):
            if h in manual_cols:
                merged.append(existing_row[col_idx] if col_idx < len(existing_row) else "")
            else:
                merged.append(row_values[col_idx])
        end_cell = gspread.utils.rowcol_to_a1(existing_row_num, len(header))
        start_cell = gspread.utils.rowcol_to_a1(existing_row_num, 1)
        batch_updates.append({"range": f"{start_cell}:{end_cell}", "values": [merged]})
        updated_count += 1

    if batch_updates:
        ws.batch_update(batch_updates, value_input_option="USER_ENTERED")
    if new_row_values:
        # Write to an explicit A1 range (next empty row through len(header)
        # columns) instead of ws.append_rows()'s table-autodetection -
        # confirmed on the dashboard tab (2026-10-19) that autodetection
        # picks the wrong starting column once the sheet has extra blank-
        # header columns to the right (from the dashboard's lazily-added
        # tracking columns outgrowing a prior ws.resize()): each append got
        # shifted further right than the last (offsets of 14, 28, 28, 42
        # columns for 4 consecutive appends), landing new rows' data under
        # the wrong headers entirely. An explicit range removes any
        # ambiguity about which column a new row starts at.
        start_row = len(existing) + 1
        start_cell = gspread.utils.rowcol_to_a1(start_row, 1)
        end_cell = gspread.utils.rowcol_to_a1(start_row + len(new_row_values) - 1, len(header))
        ws.update(f"{start_cell}:{end_cell}", new_row_values, value_input_option="USER_ENTERED")

    if skipped_duplicates:
        print(f"  ({len(skipped_duplicates)} cross-portal duplicate(s) skipped: "
              + ", ".join(f'{p}/{t[:40]}' for p, t in skipped_duplicates) + ")")

    if updated_count:
        print(f"  ({updated_count} existing row(s) updated in place)")
    return len(new_row_values)


def append_tracker_rows(rows):
    """Convenience wrapper for the Awarded-Historical RFPs tab."""
    return append_rows(rows, sheet_name=TRACKER_SHEET_NAME, header=TRACKER_HEADER)


def append_test_prompt_rows(rows):
    """Convenience wrapper for the "Scraper Results (Test Prompt)" tab -
    an A/B testing surface, separate from production. Same schema as the
    real "Scraper Results" tab (HEADER), just a different sheet name, so a
    candidate classifier prompt's output can be compared side-by-side
    without touching production data."""
    return append_rows(rows, sheet_name=TEST_PROMPT_SHEET_NAME, header=HEADER)


def append_dashboard_rows(rows):
    """Convenience wrapper for DASHBOARD_SHEET_NAME - see its definition.
    cross_portal_dedupe=True since this is the tab users actually read - a
    posting scraped from two portals (e.g. the same NJ county RFP listed on
    both BidNet Direct and PassaicBids) should only show up once here."""
    return append_rows(rows, sheet_name=DASHBOARD_SHEET_NAME, header=HEADER, cross_portal_dedupe=True)
