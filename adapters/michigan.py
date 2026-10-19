"""Adapter for Michigan's SIGMA VSS (sigma.michigan.gov) - fully public
"View Published Solicitations" search, no login required despite Mike's
docx listing a vendor credential for this portal.

Needs core.browser.new_page(stealth=True) - plain headless Playwright gets
a WAF "Web Page Blocked!" page on every URL here (confirmed earlier this
project), but a real browser and the user's own logged-in session both
loaded it fine - same headless-fingerprint check as BidNet Direct, not a
network/IP block or a real login requirement.

Thin wrapper around the shared CGI Advantage VSS platform logic in
adapters/advantage_vss.py - see that file for the selector scheme shared
across all states on this platform (MI, KY/Kentucky VSS, CO/CGI Advantage
VSS).
"""
from adapters import advantage_vss

BASE_URL = "https://sigma.michigan.gov/PRDVSS1X1/Advantage4"


def search(page, keyword):
    return advantage_vss.search(page, BASE_URL, keyword)
