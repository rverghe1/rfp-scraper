"""Read-only dashboard for browsing scraped RFPs.

Talks to the same Google Sheet run_all.py writes to. The spreadsheet ID and
tab names are duplicated from core/sheets.py rather than imported: this app
deploys as its own Railway build root (web/), independent of the rest of
the repo, with no access to core/ at build time. Keep these in sync if the
sheet ID or tab names ever change over there.
"""
import os
from pathlib import Path

import gspread
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
# so these two living in columns P/Q is safe from being clobbered by a
# future scraper run. Not in core.sheets.HEADER on purpose: this is
# dashboard-user state (did I post this in Slack), not scraper output.
SHARED_COL = "Shared in Slack"
LINK_COL = "Slack Thread Link"

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
    return {"header": header, "rows": rows}


class SlackShareUpdate(BaseModel):
    portal: str
    title: str
    agency: str
    shared: bool
    link: str = ""


def _ensure_tracking_columns(ws, header):
    """Appends SHARED_COL/LINK_COL to the header row if they're not there
    yet (first write ever). Returns their 1-indexed column numbers."""
    to_add = [c for c in (SHARED_COL, LINK_COL) if c not in header]
    if to_add:
        ws.update(
            gspread.utils.rowcol_to_a1(1, len(header) + 1),
            [to_add],
            value_input_option="USER_ENTERED",
        )
        header = header + to_add
    return header.index(SHARED_COL) + 1, header.index(LINK_COL) + 1


@app.post("/api/rfps/slack")
def update_slack_share(update: SlackShareUpdate, tab: str = "results"):
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

    shared_col, link_col = _ensure_tracking_columns(ws, header)
    link = update.link if update.shared else ""
    ws.batch_update(
        [
            {"range": gspread.utils.rowcol_to_a1(row_num, shared_col), "values": [["Yes" if update.shared else ""]]},
            {"range": gspread.utils.rowcol_to_a1(row_num, link_col), "values": [[link]]},
        ],
        value_input_option="USER_ENTERED",
    )
    return {"shared": update.shared, "link": link}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
