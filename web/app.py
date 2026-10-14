"""Read-only dashboard for browsing scraped RFPs.

Talks to the same Google Sheet run_all.py writes to. The spreadsheet ID and
tab names are duplicated from core/sheets.py rather than imported: this app
deploys as its own Railway build root (web/), independent of the rest of
the repo, with no access to core/ at build time. Keep these in sync if the
sheet ID or tab names ever change over there.
"""
import os
import re
from pathlib import Path
from typing import Optional

import gspread
from dateutil import parser as date_parser
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from google.oauth2.service_account import Credentials
from pydantic import BaseModel

SERVICE_ACCOUNT_KEY = os.environ.get(
    "SERVICE_ACCOUNT_KEY_PATH",
    "/Users/rohanverghese/Desktop/chow-scraper/service_account_key.json",
)
SPREADSHEET_ID = "1D9N2erN_JEk4WVjeneQOzN2U7mKDls7Sxabx-JOrSBE"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TABS = {
    # Points at the healthcare-staffing-screened subset of "Scraper Results"
    # (see core/relevance_screen.py on the scraper side), not the raw tab -
    # "Scraper Results" includes permissive ESCALATE rows and postings
    # scraped before a classifier prompt tightening, some of which aren't
    # actually clinical staffing. The dashboard should only show postings
    # that passed that second, narrower screen.
    "results": "Scraper Results (Healthcare Only)",
}

# Dashboard-owned tracking columns, appended to the right of whatever
# run_all.py/core.sheets.append_rows writes (15 columns, A:O) - that upsert
# path only ever reads/writes column indices up to its own header length,
# so these living in columns P onward are safe from being clobbered by a
# future scraper run. Not in core.sheets.HEADER on purpose: this is
# dashboard-user state (did I post this in Slack, what did we decide),
# not scraper output.
SHARED_COL = "Shared in Slack"
LINK_COL = "Slack Thread Link"
VERDICT_COL = "Verdict"
LAZY_TRACKING_COLS = (SHARED_COL, LINK_COL, VERDICT_COL)

# "Facility" is NOT lazily created like the above - it's core.sheets.HEADER's
# own column (position 2), always present from the scraper's own writes.
# The dashboard can still overwrite it (e.g. a manual facility name for a
# posting with no Salesforce match), it just never needs _ensure_tracking_
# columns to create it.
FACILITY_COL = "Facility"
_HYPERLINK_RE = re.compile(r'=HYPERLINK\("([^"]+)"\s*,\s*"(.*)"\)\s*$', re.IGNORECASE | re.DOTALL)

# Every portal formats its own closing date and state text differently
# ("10/08/2026", "1/31/2031 9:00:00 AM", "2026-10-08 16:00", "Oct 26, 2026";
# "California" vs "CA") - normalized here, at display time, rather than at
# scrape time, so the original scraped text stays intact in the sheet for
# auditing while the dashboard always shows one consistent format.
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


def _normalize_state(value):
    if not value:
        return value
    abbrev = _STATE_NAME_TO_ABBREV.get(value.strip().lower())
    return abbrev if abbrev else value.strip()


def _normalize_date(value):
    if not value or not value.strip():
        return value
    try:
        # dateutil defaults ambiguous text to today's date/time for missing
        # fields, which is fine here since every source string already has
        # a real month/day/year - only the clock time (if any) gets dropped.
        d = date_parser.parse(value.strip(), fuzzy=True)
    except (ValueError, OverflowError):
        return value  # leave anything unparseable exactly as scraped
    return f"{d.strftime('%b')} {d.day}, {d.year}"


app = FastAPI(title="RFP Scraper Dashboard")


def get_worksheet(sheet_name):
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)


@app.get("/api/rfps")
def get_rfps(tab: str = "results"):
    if tab not in TABS:
        raise HTTPException(400, f"Unknown tab '{tab}' - expected one of {list(TABS)}")
    try:
        ws = get_worksheet(TABS[tab])
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(404, f"Sheet tab '{TABS[tab]}' doesn't exist yet")
    values = ws.get_all_values()
    if not values:
        return {"header": [], "rows": []}
    header, *data_rows = values
    rows = [dict(zip(header, row)) for row in data_rows]

    for row in rows:
        if "Closing Date" in row:
            row["Closing Date"] = _normalize_date(row["Closing Date"])
        if "State" in row:
            row["State"] = _normalize_state(row["State"])

    # get_all_values() returns the FORMATTED display value - a
    # =HYPERLINK(url,"name") formula shows up as just "name", losing the
    # url. Re-fetch that one column in FORMULA mode to recover it, exposed
    # to the frontend as a sibling "Facility Link" field (see
    # web/static/script.js's facility cell renderer).
    if FACILITY_COL in header and data_rows:
        fi = header.index(FACILITY_COL)
        col_a1 = gspread.utils.rowcol_to_a1(1, fi + 1)
        col_letter = "".join(c for c in col_a1 if c.isalpha())
        formula_range = f"{col_letter}2:{col_letter}{len(data_rows) + 1}"
        formulas = ws.get(formula_range, value_render_option=gspread.utils.ValueRenderOption.formula)
        for row, frow in zip(rows, formulas):
            cell = frow[0] if frow else ""
            m = _HYPERLINK_RE.match(cell.strip())
            if m:
                row["Facility Link"] = m.group(1)

    return {"header": header, "rows": rows}


class TrackingUpdate(BaseModel):
    portal: str
    title: str
    agency: str
    # None = leave that field untouched; only fields explicitly provided
    # (not None) get written. "shared" is the one exception that's always
    # meaningful as a bool, not Optional, since the UI always sends it
    # alongside a link edit (see web/static/script.js).
    shared: Optional[bool] = None
    link: Optional[str] = None
    facility: Optional[str] = None
    verdict: Optional[str] = None


def _ensure_tracking_columns(ws, header):
    """Appends any of LAZY_TRACKING_COLS missing from the header row (first
    write ever for that column), plus FACILITY_COL as a defensive fallback
    in case this tab somehow predates the core.sheets.HEADER rename.
    Returns a dict of {col_name: 1-indexed column number}.

    Worksheets created via core.sheets.get_or_create_worksheet are sized to
    exactly len(HEADER) columns (the sheet grid itself, not just the header
    row) - writing past that hits "exceeds grid limits" from the Sheets API
    even though the header row logically has room to grow, so the grid has
    to be widened first."""
    wanted = (*LAZY_TRACKING_COLS, FACILITY_COL)
    to_add = [c for c in wanted if c not in header]
    if to_add:
        needed_cols = len(header) + len(to_add)
        if ws.col_count < needed_cols:
            ws.resize(cols=needed_cols)
        ws.update(
            gspread.utils.rowcol_to_a1(1, len(header) + 1),
            [to_add],
            value_input_option="USER_ENTERED",
        )
        header = header + to_add
    return {c: header.index(c) + 1 for c in wanted}


@app.post("/api/rfps/slack")
def update_tracking(update: TrackingUpdate, tab: str = "results"):
    if tab not in TABS:
        raise HTTPException(400, f"Unknown tab '{tab}' - expected one of {list(TABS)}")
    try:
        ws = get_worksheet(TABS[tab])
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(404, f"Sheet tab '{TABS[tab]}' doesn't exist yet")

    values = ws.get_all_values()
    if not values:
        raise HTTPException(404, "Sheet has no rows yet")
    header, *data_rows = values
    try:
        pi, ti, ai = header.index("Portal"), header.index("Title"), header.index("Agency")
    except ValueError:
        raise HTTPException(500, "Sheet is missing Portal/Title/Agency columns")

    row_num = None
    for i, row in enumerate(data_rows, start=2):
        key = (row[pi] if pi < len(row) else "", row[ti] if ti < len(row) else "",
               row[ai] if ai < len(row) else "")
        if key == (update.portal, update.title, update.agency):
            row_num = i
            break
    if row_num is None:
        raise HTTPException(404, "No matching row found (Portal/Title/Agency didn't match any row)")

    cols = _ensure_tracking_columns(ws, header)
    if update.shared is True:
        link = update.link if update.link is not None else ""
    elif update.shared is False:
        link = ""  # unsharing clears the link too
    else:
        link = update.link  # None = don't touch; a string = explicit link-only update

    result = {}
    batch = []
    if update.shared is not None:
        batch.append({"range": gspread.utils.rowcol_to_a1(row_num, cols[SHARED_COL]),
                       "values": [["Yes" if update.shared else "No"]]})
        result["shared"] = update.shared
    if link is not None:
        batch.append({"range": gspread.utils.rowcol_to_a1(row_num, cols[LINK_COL]), "values": [[link]]})
        result["link"] = link
    if update.facility is not None:
        batch.append({"range": gspread.utils.rowcol_to_a1(row_num, cols[FACILITY_COL]),
                       "values": [[update.facility]]})
        result["facility"] = update.facility
    if update.verdict is not None:
        batch.append({"range": gspread.utils.rowcol_to_a1(row_num, cols[VERDICT_COL]),
                       "values": [[update.verdict]]})
        result["verdict"] = update.verdict

    if batch:
        ws.batch_update(batch, value_input_option="USER_ENTERED")
    return result


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
