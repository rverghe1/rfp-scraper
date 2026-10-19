"""Adapter for Colorado's CGI Advantage VSS (prd.co.cgiadvantage.com) -
fully public "View Published Solicitations" search, no login required
despite Mike's docx listing a vendor credential for this portal (same as
Michigan's SIGMA VSS). Thin wrapper around the shared CGI Advantage VSS
platform logic in adapters/advantage_vss.py - confirmed live (2026-10-19)
with the exact same selector scheme as Michigan/Kentucky."""
from adapters import advantage_vss

BASE_URL = "https://prd.co.cgiadvantage.com/PRDVSS1X1/Advantage4"


def search(page, keyword):
    return advantage_vss.search(page, BASE_URL, keyword)
