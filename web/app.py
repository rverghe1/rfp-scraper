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

SERVICE_ACCOUNT_KEY = os.environ.get(
    "SERVICE_ACCOUNT_KEY_PATH",
    "/Users/rohanverghese/Desktop/chow-scraper/service_account_key.json",
)
SPREADSHEET_ID = "1D9N2erN_JEk4WVjeneQOzN2U7mKDls7Sxabx-JOrSBE"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TABS = {
    "results": "Scraper Results",
    "tracker": "Awarded-Historical RFPs",
}

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


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
